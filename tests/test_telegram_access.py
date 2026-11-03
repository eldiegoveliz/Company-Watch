from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from company_watch.config import Settings
from company_watch.daemon import CompanyWatchDaemon


class TelegramAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.settings = Settings(
            database_path=root / "test.sqlite",
            sources_path=root / "sources.json",
            watchlist_path=root / "watched_tickers.txt",
            telegram_bot_token="test-token",
            telegram_chat_id="",
            openai_api_key="",
            gemini_api_key="",
        )
        self.daemon = CompanyWatchDaemon(self.settings)
        self.addCleanup(self.daemon.close)
        self.daemon.telegram = Mock(token="test-token")

    def receive(self, chat_id, text, chat_type="private"):
        self.daemon.telegram.get_updates.return_value = [{
            "update_id": 17,
            "message": {
                "chat": {"id": chat_id, "type": chat_type},
                "text": text,
            },
        }]
        self.daemon.handle_telegram_updates()

    def test_unbound_bot_only_pairs_on_private_start(self):
        for text, chat_type in [
            ("hello", "private"),
            ("/watch SRPT", "private"),
            ("/start", "group"),
        ]:
            with self.subTest(text=text, chat_type=chat_type):
                self.receive("222", text, chat_type)
                self.assertEqual(self.daemon.alert_chat_id(), "")
                self.assertEqual(self.daemon.db.watched_companies(), [])
                self.daemon.telegram.send_message.assert_not_called()

    def test_private_start_pairs_and_survives_restart(self):
        self.receive("111", "/start")
        self.assertEqual(self.daemon.alert_chat_id(), "111")
        self.daemon.telegram.send_message.assert_called_once()
        restarted = CompanyWatchDaemon(self.settings)
        try:
            self.assertEqual(restarted.alert_chat_id(), "111")
        finally:
            restarted.close()

    def test_other_chat_cannot_replace_owner_or_run_commands(self):
        self.daemon.db.set_setting("telegram_chat_id", "111")
        for text in ["/start", "/watch SRPT"]:
            with self.subTest(text=text):
                self.receive("222", text)
                self.assertEqual(self.daemon.alert_chat_id(), "111")
                self.assertEqual(self.daemon.db.watched_companies(), [])
                self.daemon.telegram.send_message.assert_not_called()
        self.assertEqual(self.daemon.telegram_offset, 18)

    def test_explicit_chat_id_rejects_other_chats(self):
        from dataclasses import replace

        self.daemon.settings = replace(self.settings, telegram_chat_id="111")
        self.receive("222", "/watch SRPT")
        self.assertEqual(self.daemon.db.watched_companies(), [])
        self.daemon.telegram.send_message.assert_not_called()

    def test_owner_can_run_commands(self):
        self.daemon.db.set_setting("telegram_chat_id", "111")
        self.receive("111", "/watch SRPT")
        self.assertEqual([c.ticker for c in self.daemon.db.watched_companies()], ["SRPT"])
        self.daemon.telegram.send_message.assert_called_once_with("Watching SRPT.", chat_id="111")


if __name__ == "__main__":
    unittest.main()
