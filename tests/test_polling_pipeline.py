from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from company_watch.config import Settings
from company_watch.daemon import CompanyWatchDaemon
from company_watch.db import Database
from company_watch.pollers import FetchResult
from company_watch.scheduler import EASTERN
from company_watch.timeutils import utc_now
from test_daily_polling import eastern


class PollingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "watched_tickers.txt").write_text("TEST|Synthetic Biotech|\n")
        (self.root / "sources.json").write_text(json.dumps({"sources": [{
            "id": "test", "name": "Synthetic feed", "type": "rss", "url": "https://example.test/rss",
        }]}))
        self.settings = Settings(
            database_path=self.root / "test.sqlite", sources_path=self.root / "sources.json",
            watchlist_path=self.root / "watched_tickers.txt", telegram_bot_token="", telegram_chat_id="",
            openai_api_key="", gemini_api_key="", codex_command=sys.executable,
            watch_profile="biotech", baseline_loop_seconds=0.01,
        )

    def feed(self, number=1):
        return FetchResult(200, f'<rss><channel><item><title>TEST announces a clinical hold</title>'
                           f'<link>https://example.test/{number}</link></item></channel></rss>', None, None)

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_clock_polling_continues_while_model_and_telegram_are_blocked(self):
        clock = [eastern("2026-11-09T07:00:00")]
        model_started = threading.Event()
        telegram_started = threading.Event()
        release_model = threading.Event()
        release_telegram = threading.Event()
        fetched_again = threading.Event()
        holder = {}
        counter = [0]
        errors = []

        def fetch(*args, **kwargs):
            counter[0] += 1
            if counter[0] >= 2:
                fetched_again.set()
            return self.feed(counter[0])

        def analyze(*args):
            model_started.set()
            if not release_model.wait(5):
                raise RuntimeError("synthetic model never released")
            return {"alert": False, "event": "Synthetic test"}

        def telegram():
            telegram_started.set()
            release_telegram.wait(5)

        def run():
            daemon = CompanyWatchDaemon(self.settings)
            holder["daemon"] = daemon
            try:
                daemon.run_forever()
            except Exception as exc:
                errors.append(exc)
            finally:
                daemon.close()

        with patch("company_watch.scheduler.utc_now", side_effect=lambda: clock[0]), \
             patch("company_watch.daemon.iso_now", side_effect=lambda: clock[0].isoformat()), \
             patch("company_watch.daemon.fetch_url", side_effect=fetch), \
             patch("company_watch.analyzer.call_codex", side_effect=analyze) as model, \
             patch.object(CompanyWatchDaemon, "handle_telegram_updates", side_effect=telegram):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(model_started.wait(4))
                self.assertTrue(telegram_started.wait(4))
                clock[0] += timedelta(seconds=15)
                self.assertTrue(fetched_again.wait(4), "Next slot was blocked by the slow model or Telegram")
                self.assertFalse(release_model.is_set())
                self.assertFalse(release_telegram.is_set())

                def queued_second_item():
                    with sqlite3.connect(self.settings.database_path) as connection:
                        return connection.execute("SELECT count(*) FROM raw_items").fetchone()[0] >= 2
                self.assertTrue(self.wait_for(queued_second_item))
                with sqlite3.connect(self.settings.database_path) as connection:
                    self.assertEqual(connection.execute("SELECT status FROM analysis_jobs").fetchall(), [("running",)])
                release_model.set()
                release_telegram.set()

                def completed():
                    with sqlite3.connect(self.settings.database_path) as connection:
                        return connection.execute("SELECT count(*) FROM analysis_jobs WHERE status='done'").fetchone()[0] == 2
                self.assertTrue(self.wait_for(completed))
                self.assertEqual(model.call_count, 2)
            finally:
                release_model.set()
                release_telegram.set()
                if "daemon" in holder:
                    holder["daemon"]._stop.set()
                    holder["daemon"]._analysis_wakeup.set()
                thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_same_source_does_not_overlap_and_late_completion_keeps_start_time(self):
        started = threading.Event()
        release = threading.Event()
        clock = [eastern("2026-11-09T06:59:59")]
        daemon = CompanyWatchDaemon(self.settings)
        self.addCleanup(daemon.close)
        def fetch(*args, **kwargs):
            started.set()
            release.wait(3)
            return FetchResult(200, "<rss><channel></channel></rss>", None, None)
        with patch("company_watch.scheduler.utc_now", side_effect=lambda: clock[0]), \
             patch("company_watch.daemon.iso_now", side_effect=lambda: clock[0].isoformat()), \
             patch("company_watch.daemon.fetch_url", side_effect=fetch) as request, \
             ThreadPoolExecutor(max_workers=2) as executor:
            try:
                daemon._schedule_fetches(executor)
                self.assertTrue(started.wait(2))
                daemon._schedule_fetches(executor)
                self.assertEqual(request.call_count, 1)
                clock[0] = eastern("2026-11-09T07:00:02")
                release.set()
                future = daemon._pending_fetches["test"][0]
                future.result(timeout=2)
                daemon._collect_fetches()
                self.assertEqual(daemon.db.source_state("test")["last_polled_at"], eastern("2026-11-09T06:59:59").isoformat())
                self.assertEqual(len(daemon.scheduler.due_sources(now=clock[0])), 1)
            finally:
                release.set()

    def test_queued_sec_document_is_read_before_final_classification(self):
        (self.root / "sources.json").write_text(json.dumps({"sources": [{
            "id": "test", "name": "Synthetic SEC", "type": "sec_submissions",
            "url": "https://example.test/sec", "ticker": "TEST",
        }]}))
        daemon = CompanyWatchDaemon(self.settings)
        self.addCleanup(daemon.close)
        text = json.dumps({"cik": 1, "filings": {"recent": {
            "form": ["8-K"], "accessionNumber": ["0000000000-26-000001"],
            "filingDate": [utc_now().date().isoformat()], "primaryDocument": ["test.htm"],
            "primaryDocDescription": ["8-K"],
        }}})
        with patch("company_watch.daemon.sec_filing_text", return_value=("FDA clinical hold after safety signal", {"sections": []})) as document, \
             patch("company_watch.analyzer.call_codex", return_value={"alert": False}) as model:
            daemon._poll_one(daemon.sources[0], "daily", "daily 07:00 ET", defer_analysis=True,
                             fetched=FetchResult(200, text, None, None))
            document.assert_not_called()
            model.assert_not_called()
            pending = daemon.db.claim_pending_analysis()
            self.assertIsNotNone(pending)
            self.assertLess(pending["event_score"], self.settings.alert_threshold)
            daemon._process_pending_item(pending)
            document.assert_called_once()
            model.assert_called_once()
            processed = daemon.db.raw_item(pending["id"])
            self.assertEqual(processed["status"], "processed")
            self.assertGreaterEqual(processed["event_score"], self.settings.alert_threshold)
            self.assertTrue(json.loads(processed["metadata_json"])["document_read"])

    def test_pending_queue_survives_restart_without_requeueing_delivered_alerts(self):
        daemon = CompanyWatchDaemon(self.settings)
        daemon._poll_one(daemon.sources[0], "daily", "daily 07:00 ET", defer_analysis=True, fetched=self.feed())
        pending = daemon.db.claim_pending_analysis()
        self.assertIsNotNone(pending)
        identifier = pending["id"]
        daemon.close()
        recovered = Database(self.settings.database_path)
        try:
            recovered.recover_pending_analysis()
            self.assertEqual(recovered.claim_pending_analysis()["id"], identifier)
            recovered.save_alert(identifier, "TEST", "Synthetic delivered alert")
            recovered.finish_pending_analysis(identifier)
            recovered.recover_pending_analysis()
            self.assertIsNone(recovered.claim_pending_analysis())
            self.assertEqual(recovered.raw_item(identifier)["status"], "alerted")
        finally:
            recovered.close()

    def test_fetch_capacity_is_bounded_and_waiting_sources_get_the_next_turn(self):
        from concurrent.futures import Future

        sources = [{"id": f"source{i}", "name": f"Source {i}", "type": "rss",
                    "ticker": "TEST", "url": f"https://example.test/{i}"} for i in range(5)]
        self.settings.sources_path.write_text(json.dumps({"sources": sources}))
        daemon = CompanyWatchDaemon(replace(self.settings, poll_fetch_workers=2))
        self.addCleanup(daemon.close)
        executor = Mock()
        executor.submit.side_effect = lambda *args: Future()
        now = eastern("2026-11-09T07:00:00")
        with patch("company_watch.scheduler.utc_now", return_value=now):
            daemon._schedule_fetches(executor)
        self.assertEqual(executor.submit.call_count, 2)
        self.assertEqual(set(daemon._pending_fetches), {"source0", "source1"})
        for future, _ in daemon._pending_fetches.values():
            future.set_result((FetchResult(200, "<rss><channel/></rss>", None, None), None, now.isoformat()))
        daemon._collect_fetches()
        with patch("company_watch.scheduler.utc_now", return_value=now + timedelta(seconds=15)):
            daemon._schedule_fetches(executor)
        self.assertEqual(set(daemon._pending_fetches), {"source2", "source3"})

    def test_routine_news_does_not_queue_model_work(self):
        daemon = CompanyWatchDaemon(self.settings)
        self.addCleanup(daemon.close)
        feed = FetchResult(200, '<rss><channel><item><title>TEST investor conference webcast replay</title>'
                           '<link>https://example.test/routine</link></item></channel></rss>', None, None)
        daemon._poll_one(daemon.sources[0], "daily", "daily 07:00 ET", defer_analysis=True, fetched=feed)
        self.assertIsNone(daemon.db.claim_pending_analysis())
        self.assertEqual(daemon.db.conn.execute("SELECT count(*) FROM analysis_jobs").fetchone()[0], 0)

    def test_real_http_checks_continue_during_a_slow_cli_process(self):
        payload = self.feed().text.encode()
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(time.monotonic())
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        (self.root / "sources.json").write_text(json.dumps({"sources": [{
            "id": "test", "name": "Local synthetic feed", "type": "rss",
            "url": f"http://127.0.0.1:{server.server_port}/rss",
        }]}))
        marker = self.root / "cli-started"
        finished = self.root / "cli-finished"
        executable = self.root / "synthetic-codex"
        executable.write_text(
            f"#!{sys.executable}\nimport sys, time\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('started')\n"
            "sys.stdin.read()\ntime.sleep(2)\n"
            "Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{\"alert\": false}')\n"
            f"Path({str(finished)!r}).write_text('done')\n"
        )
        executable.chmod(0o755)
        now = utc_now().astimezone(EASTERN)
        target = now.time().replace(second=0, microsecond=0, tzinfo=None)
        settings = replace(self.settings, codex_command=str(executable),
                           daily_poll_times_et=(target,), daily_poll_interval_seconds=1,
                           poll_fetch_workers=1)
        holder = {}
        errors = []
        def run():
            daemon = CompanyWatchDaemon(settings)
            holder["daemon"] = daemon
            try:
                daemon.run_forever()
            except Exception as exc:
                errors.append(exc)
            finally:
                daemon.close()
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(self.wait_for(marker.exists))
            before = len(requests)
            self.assertTrue(self.wait_for(lambda: len(requests) > before, timeout=1.5))
            self.assertFalse(finished.exists(), "The test must observe polling while the CLI is still busy")
        finally:
            if "daemon" in holder:
                holder["daemon"]._stop.set()
                holder["daemon"]._analysis_wakeup.set()
            thread.join(timeout=6)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(finished.exists())
        with sqlite3.connect(settings.database_path) as connection:
            self.assertEqual(connection.execute("SELECT status FROM analysis_jobs").fetchall(), [("done",)])


if __name__ == "__main__":
    unittest.main()
