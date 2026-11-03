from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class TelegramClient:
    def __init__(self, token: str, chat_id: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _request(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
        if not self.token:
            return {"ok": False, "description": "missing TELEGRAM_BOT_TOKEN"}
        data = urllib.parse.urlencode(payload or {}).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/{method}", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send_message(self, text: str, chat_id: str | None = None) -> str | None:
        target = chat_id or self.chat_id
        if not self.token or not target:
            print(text)
            return None
        response = self._request(
            "sendMessage",
            {
                "chat_id": target,
                "text": text[:3900],
                "disable_web_page_preview": "true",
            },
        )
        result = response.get("result") or {}
        message_id = result.get("message_id")
        return str(message_id) if message_id is not None else None

    def get_updates(self, offset: int | None, timeout: int = 10) -> list[dict[str, Any]]:
        if not self.token:
            return []
        payload: dict[str, Any] = {"timeout": str(timeout), "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            payload["offset"] = str(offset)
        response = self._request("getUpdates", payload, timeout=timeout + 5)
        return list(response.get("result") or [])
