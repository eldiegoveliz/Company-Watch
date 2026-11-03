from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from company_watch.analyzer import analysis_setup_issues, analyze_item, format_alert
from company_watch.config import Settings
from company_watch.db import Database
from scripts.check_analysis import main


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.settings = Settings(
            database_path=root / "test.sqlite",
            sources_path=root / "sources.json",
            watchlist_path=root / "watched_tickers.txt",
            telegram_bot_token="",
            telegram_chat_id="",
            openai_api_key="synthetic-openai-secret",
            gemini_api_key="synthetic-gemini-secret",
            codex_command=sys.executable,
        )
        self.db = Database(self.settings.database_path)
        self.addCleanup(self.db.close)
        item_id, _ = self.db.insert_raw_item({
            "source_id": "test", "ticker": "SRPT", "title": "Synthetic clinical hold",
            "url": "https://example.test/event", "item_hash": "test-event",
            "event_type": "clinical_hold", "event_score": 85,
        })
        self.item = self.db.raw_item(item_id)

    def test_checker_accepts_supported_choices_without_external_calls_or_key_output(self):
        for provider in ["codex", "openai_api", "gemini", "gemini_api", " GOOGLE "]:
            with self.subTest(provider=provider):
                settings = replace(self.settings, analysis_provider=provider)
                output = io.StringIO()
                with patch("scripts.check_analysis.get_settings", return_value=settings), \
                     patch("company_watch.analyzer.urllib.request.urlopen") as request, \
                     patch("company_watch.analyzer.subprocess.run") as command, \
                     redirect_stdout(output):
                    self.assertEqual(main(), 0)
                request.assert_not_called()
                command.assert_not_called()
                self.assertNotIn(settings.openai_api_key, output.getvalue())
                self.assertNotIn(settings.gemini_api_key, output.getvalue())
                self.assertIn("not verified", output.getvalue())

    def test_checker_reports_missing_keys_and_unknown_provider(self):
        cases = [
            (replace(self.settings, analysis_provider="openai_api", openai_api_key=""), "OPENAI_API_KEY"),
            (replace(self.settings, analysis_provider="gemini", gemini_api_key=" "), "GEMINI_API_KEY"),
            (replace(self.settings, analysis_provider="unimplemented"), "Unsupported ANALYSIS_PROVIDER"),
            (replace(self.settings, codex_model=""), "Set the model"),
        ]
        for settings, message in cases:
            with self.subTest(message=message):
                output = io.StringIO()
                with patch("scripts.check_analysis.get_settings", return_value=settings), redirect_stdout(output):
                    self.assertEqual(main(), 1)
                self.assertIn(message, output.getvalue())

    def test_missing_executable_uses_and_records_local_fallback(self):
        settings = replace(self.settings, codex_command=str(Path(self.tmp.name) / "missing-codex"))
        with patch("company_watch.analyzer.call_codex") as codex, \
             patch("company_watch.analyzer.call_openai") as openai, \
             patch("company_watch.analyzer.call_gemini") as gemini:
            output = analyze_item(self.db, settings, self.item)
        for adapter in [codex, openai, gemini]:
            adapter.assert_not_called()
        self.assertIn("Codex executable not found", output["uncertainty"])
        job = self.db.conn.execute("SELECT * FROM analysis_jobs").fetchone()
        self.assertEqual(job["status"], "error")
        self.assertIsNotNone(job["finished_at"])
        self.assertEqual(json.loads(job["output_json"]), output)
        self.assertIn("local fallback", format_alert(output, self.item))

    def test_executable_start_failure_after_precheck_also_uses_fallback(self):
        for error in [FileNotFoundError("removed"), PermissionError("denied")]:
            with self.subTest(error=type(error).__name__), \
                 patch("company_watch.analyzer.subprocess.run", side_effect=error):
                output = analyze_item(self.db, self.settings, self.item)
            self.assertIn("Could not start Codex", output["uncertainty"])
        self.assertEqual(
            [row[0] for row in self.db.conn.execute("SELECT status FROM analysis_jobs")],
            ["error", "error"],
        )

    def test_each_provider_routes_to_its_adapter_and_saves_analysis(self):
        response = {"alert": True, "event": "Synthetic event", "why_it_matters": ["Test result"]}
        for provider, selected in [("codex", 0), ("openai_api", 1), ("gemini", 2), ("google", 2)]:
            with self.subTest(provider=provider), \
                 patch("company_watch.analyzer.call_codex", return_value=response) as codex, \
                 patch("company_watch.analyzer.call_openai", return_value=response) as openai, \
                 patch("company_watch.analyzer.call_gemini", return_value=response) as gemini:
                output = analyze_item(self.db, replace(self.settings, analysis_provider=provider), self.item)
                for index, adapter in enumerate([codex, openai, gemini]):
                    self.assertEqual(adapter.call_count, int(index == selected))
                self.assertEqual(output, response)
        jobs = self.db.conn.execute("SELECT status, output_json FROM analysis_jobs").fetchall()
        self.assertTrue(all(row["status"] == "done" for row in jobs))
        self.assertTrue(all(json.loads(row["output_json"]) == response for row in jobs))

    def test_missing_api_key_does_not_switch_to_another_provider(self):
        settings = replace(self.settings, analysis_provider="openai_api", openai_api_key="")
        with patch("company_watch.analyzer.call_codex") as codex, \
             patch("company_watch.analyzer.call_openai") as openai, \
             patch("company_watch.analyzer.call_gemini") as gemini:
            output = analyze_item(self.db, settings, self.item)
        for adapter in [codex, openai, gemini]:
            adapter.assert_not_called()
        self.assertIn("OPENAI_API_KEY is empty", output["uncertainty"])


if __name__ == "__main__":
    unittest.main()
