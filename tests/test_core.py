from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from company_watch.classifiers import classify_text
from company_watch.config import SourceConfig, WatchCompany
from company_watch.db import Database
from company_watch.pollers import parse_html_watch
from company_watch.scheduler import AdaptiveScheduler
from company_watch.timeutils import utc_now


class CoreTests(unittest.TestCase):
    def test_newsroom_only_accepts_release_links(self):
        source = SourceConfig(
            id="srpt_ir", name="Sarepta Newsroom", type="html_watch",
            url="https://www.sarepta.com/newsroom", ticker="SRPT",
            link_url_contains="/news-releases/news-release-details/",
        )
        release = "https://investorrelations.sarepta.com/news-releases/news-release-details/new-announcement"
        page = '<a href="/products-pipeline">Our Products &amp; Pipeline</a>'
        page += '<a href="/newsroom/story">Clinical trial story</a>' * 125
        page += f'<a href="{release}">Company announces agreement</a>' * 2
        items = parse_html_watch(source, page)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], release)

    def test_classifier_detects_binary_event(self):
        companies = [WatchCompany("SRPT", "Sarepta Therapeutics", ("ELEVIDYS",))]
        result = classify_text(
            "Sarepta announces Phase 3 topline data for ELEVIDYS",
            "Study met primary endpoint with statistically significant improvement.",
            companies,
            profile="biotech",
        )
        self.assertEqual(result.ticker, "SRPT")
        self.assertGreaterEqual(result.score, 78)
        self.assertIn(result.event_type, {"topline_data", "positive_trial"})

    def test_scheduler_hot_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite")
            db.add_watch("SRPT", "Sarepta Therapeutics")
            now = datetime(2026, 11, 9, 17, tzinfo=UTC)
            db.add_catalyst("SRPT", "Phase 3 readout", now + timedelta(minutes=10))
            source = SourceConfig(
                id="srpt_sec",
                name="SRPT SEC",
                type="sec_submissions",
                ticker="SRPT",
                url="https://example.test",
                normal_interval_seconds=1800,
                warm_interval_seconds=120,
                hot_interval_seconds=20,
            )
            scheduler = AdaptiveScheduler(db, [source])
            due = scheduler.due_sources(now=now)
            self.assertTrue(due)
            self.assertEqual(due[0].mode, "hot")
            self.assertEqual(due[0].interval_seconds, 20)
            db.close()

    def test_generic_sec_8k_does_not_alert(self):
        companies = [WatchCompany("SRPT", "Sarepta Therapeutics")]
        result = classify_text(
            "SRPT filed 8-K: 8-K",
            "8-K",
            companies,
            source_ticker="SRPT",
            metadata={"source_type": "sec_submissions", "form": "8-K"},
            profile="biotech",
        )
        self.assertEqual(result.event_type, "sec_generic")
        self.assertLess(result.score, 55)

    def test_substantive_sec_8k_alerts_after_reading_content(self):
        companies = [WatchCompany("SRPT", "Sarepta Therapeutics")]
        result = classify_text(
            "SRPT filed 8-K: 8-K",
            "Sarepta reports FDA clinical hold after phase 3 safety signal.",
            companies,
            source_ticker="SRPT",
            metadata={"source_type": "sec_submissions", "form": "8-K"},
            profile="biotech",
        )
        self.assertEqual(result.event_type, "sec_material")
        self.assertGreaterEqual(result.score, 65)


if __name__ == "__main__":
    unittest.main()
