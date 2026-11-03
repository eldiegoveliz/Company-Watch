from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from company_watch.analyzer import analysis_system_prompt, call_codex, call_gemini, call_openai
from company_watch.classifiers import classify_text
from company_watch.config import Settings, SourceConfig, WatchCompany
from company_watch.daemon import CompanyWatchDaemon
from company_watch.pollers import FetchResult, parse_source
from company_watch.profiles import get_profile


class ProfileTests(unittest.TestCase):
    def test_general_profile_recognizes_non_biotech_events(self):
        companies = [WatchCompany("ACME", "Acme Manufacturing")]
        cases = [
            ("Acme Manufacturing reports quarterly earnings", "earnings"),
            ("ACME cuts full-year revenue guidance", "guidance"),
            ("ACME acquires a software company", "mna"),
            ("ACME announces a private placement", "financing"),
            ("ACME files for bankruptcy", "distress"),
            ("ACME names a new CEO", "leadership"),
            ("ACME confirms a data breach", "cybersecurity"),
            ("ACME announces a share repurchase", "capital_return"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                result = classify_text(title, "", companies)
                self.assertEqual(result.event_type, expected)
                self.assertGreaterEqual(result.score, 55)
        noise = classify_text("ACME unveils a new pipe product", "", companies)
        self.assertLess(noise.score, 55)

    def test_biotech_rules_are_selected_explicitly(self):
        companies = [WatchCompany("ACME")]
        title = "ACME announces Phase 3 topline data"
        general = classify_text(title, "", companies, profile="general")
        biotech = classify_text(title, "", companies, profile="biotech")
        self.assertLess(general.score, 55)
        self.assertEqual(biotech.event_type, "topline_data")
        self.assertGreaterEqual(biotech.score, 82)
        earnings = classify_text("ACME reports earnings", "", companies, profile="biotech")
        self.assertGreaterEqual(earnings.score, 55)

    def test_profiles_apply_to_sec_materiality_and_html_links(self):
        source = SourceConfig("acme_ir", "Acme IR", "html_watch", "https://example.test/", ticker="ACME")
        page = '<a href="/earnings">Quarterly earnings</a><a href="/trial">Phase 3 topline data</a>'
        general = parse_source(source, page, profile="general")
        biotech = parse_source(source, page, profile="biotech")
        self.assertEqual([item["url"] for item in general], ["https://example.test/earnings"])
        self.assertEqual(len(biotech), 2)
        for profile, should_alert in [("general", False), ("biotech", True)]:
            with self.subTest(profile=profile):
                result = classify_text(
                    "ACME filed 8-K", "FDA clinical hold", [WatchCompany("ACME")],
                    metadata={"source_type": "sec_submissions", "form": "8-K"}, profile=profile,
                )
                self.assertEqual(result.score >= 55, should_alert)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "WATCH_PROFILE"):
            get_profile("misspelled")

    def test_each_adapter_sends_the_selected_profile_context(self):
        settings = Settings(
            database_path=Path("unused.sqlite"), sources_path=Path("unused.json"),
            watchlist_path=Path("unused.txt"), telegram_bot_token="", telegram_chat_id="",
            openai_api_key="synthetic", gemini_api_key="synthetic",
        )
        for profile in ["general", "biotech"]:
            with self.subTest(profile=profile):
                selected = replace(settings, watch_profile=profile)
                expected = analysis_system_prompt(selected)
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({
                    "output_text": '{"alert": false}',
                }).encode()
                with patch("company_watch.analyzer.urllib.request.urlopen", return_value=response) as request:
                    self.assertEqual(call_openai(selected, {}), {"alert": False})
                    body = json.loads(request.call_args.args[0].data)
                    self.assertEqual(body["input"][0]["content"], expected)

                response.__enter__.return_value.read.return_value = json.dumps({
                    "candidates": [{"content": {"parts": [{"text": '{"alert": false}'}]}}],
                }).encode()
                with patch("company_watch.analyzer.urllib.request.urlopen", return_value=response) as request:
                    self.assertEqual(call_gemini(selected, {}), {"alert": False})
                    body = json.loads(request.call_args.args[0].data)
                    self.assertTrue(body["contents"][0]["parts"][0]["text"].startswith(expected))

                process = SimpleNamespace(returncode=0, stdout='{"alert": false}', stderr="")
                with patch("company_watch.analyzer.subprocess.run", return_value=process) as command:
                    self.assertEqual(call_codex(selected, {}), {"alert": False})
                    self.assertTrue(command.call_args.kwargs["input"].startswith(expected))

    def test_source_to_saved_analysis_and_stdout_alert_for_both_profiles(self):
        for profile, title in [
            ("general", "ACME reports quarterly earnings"),
            ("biotech", "ACME announces Phase 3 topline data"),
        ]:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "watched_tickers.txt").write_text("ACME|Acme Manufacturing|\n")
                (root / "sources.json").write_text(json.dumps({"sources": [{
                    "id": "acme", "name": "Acme", "type": "rss", "url": "https://example.test/feed",
                }]}))
                settings = Settings(
                    database_path=root / "test.sqlite", sources_path=root / "sources.json",
                    watchlist_path=root / "watched_tickers.txt", telegram_bot_token="",
                    telegram_chat_id="", openai_api_key="", gemini_api_key="",
                    codex_command=sys.executable, watch_profile=profile,
                )
                self.assertNotIn("biotech", analysis_system_prompt(replace(settings, watch_profile="general")))
                self.assertIn("biotech", analysis_system_prompt(replace(settings, watch_profile="biotech")))
                daemon = CompanyWatchDaemon(settings)
                try:
                    rss = f'<rss><channel><item><title>{title}</title><link>https://example.test/event</link></item></channel></rss>'
                    result = FetchResult(200, rss, None, None)
                    response = {"alert": True, "event": title, "why_it_matters": ["Synthetic test event"]}
                    output = io.StringIO()
                    with patch("company_watch.daemon.fetch_url", return_value=result), \
                         patch("company_watch.analyzer.call_codex", return_value=response) as model, \
                         redirect_stdout(output):
                        self.assertEqual(daemon.poll_source("acme"), 1)
                        self.assertEqual(daemon.poll_source("acme"), 0)
                    model.assert_called_once()
                    self.assertEqual(model.call_args.args[1]["watch_profile"], profile)
                    self.assertIn("COMPANY WATCH ALERT:", output.getvalue())
                    self.assertNotIn("BIOTECH ALERT:", output.getvalue())
                    job = daemon.db.conn.execute("SELECT * FROM analysis_jobs").fetchone()
                    self.assertEqual(job["status"], "done")
                    self.assertEqual(job["prompt_version"], "company-watch-alert-v1")
                    self.assertEqual(json.loads(job["input_json"])["watch_profile"], profile)
                    raw = daemon.db.conn.execute("SELECT metadata_json FROM raw_items").fetchone()
                    self.assertEqual(json.loads(raw[0])["watch_profile"], profile)
                    self.assertEqual(daemon.db.conn.execute("SELECT count(*) FROM alerts").fetchone()[0], 1)
                finally:
                    daemon.close()


if __name__ == "__main__":
    unittest.main()
