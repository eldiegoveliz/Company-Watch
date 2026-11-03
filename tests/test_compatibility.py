from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from company_watch.config import ROOT, default_database_path, get_settings
from company_watch.db import Database


class CompatibilityTests(unittest.TestCase):
    def test_old_imports_refer_to_the_same_implementation(self):
        from biotech_newswatch.config import Settings as LegacySettings
        from biotech_newswatch.daemon import NewswatchDaemon
        from company_watch.config import Settings
        from company_watch.daemon import CompanyWatchDaemon

        self.assertIs(LegacySettings, Settings)
        self.assertIs(NewswatchDaemon, CompanyWatchDaemon)

    def test_existing_database_wins_over_a_new_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(default_database_path(root), root / "company_watch.sqlite")
            legacy = root / "biotech_newswatch.sqlite"
            legacy.touch()
            self.assertEqual(default_database_path(root), legacy)
            (root / "company_watch.sqlite").touch()
            self.assertEqual(default_database_path(root), legacy)

    def test_profile_and_database_settings_preserve_existing_installations(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("company_watch.config.ROOT", Path(tmp)), \
             patch("company_watch.config.load_dotenv"), \
             patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            settings = get_settings()
            self.assertEqual(settings.database_path, root / "company_watch.sqlite")
            self.assertEqual(settings.watch_profile, "general")
            (root / "biotech_newswatch.sqlite").touch()
            settings = get_settings()
            self.assertEqual(settings.database_path, root / "biotech_newswatch.sqlite")
            self.assertEqual(settings.watch_profile, "biotech")
            os.environ["WATCH_PROFILE"] = "general"
            self.assertEqual(get_settings().watch_profile, "general")
            os.environ["DATABASE_PATH"] = str(root / "custom.sqlite")
            self.assertEqual(get_settings().database_path, root / "custom.sqlite")
            os.environ["WATCH_PROFILE"] = "invalid"
            with self.assertRaisesRegex(ValueError, "WATCH_PROFILE"):
                get_settings()

    def test_both_commands_reuse_legacy_data_in_a_clean_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            for package in ["company_watch", "biotech_newswatch"]:
                shutil.copytree(ROOT / package, checkout / package, ignore=shutil.ignore_patterns("__pycache__"))
            legacy = checkout / "biotech_newswatch.sqlite"
            db = Database(legacy)
            db.add_watch("ACME", "Acme Manufacturing")
            db.set_setting("telegram_chat_id", "synthetic-owner")
            db.close()
            env = {name: os.environ[name] for name in ["PATH", "LANG", "TZ"] if name in os.environ}
            for package in ["company_watch", "biotech_newswatch"]:
                result = subprocess.run(
                    [sys.executable, "-m", package, "init-db"], cwd=checkout, env=env,
                    capture_output=True, text=True, check=True,
                )
                self.assertIn(str(legacy), result.stdout)
            self.assertFalse((checkout / "company_watch.sqlite").exists())
            with sqlite3.connect(legacy) as db:
                self.assertEqual(db.execute("SELECT ticker FROM watched_companies").fetchall(), [("ACME",)])
                owner = db.execute("SELECT value FROM bot_settings WHERE key='telegram_chat_id'").fetchone()[0]
                self.assertEqual(owner, "synthetic-owner")


if __name__ == "__main__":
    unittest.main()
