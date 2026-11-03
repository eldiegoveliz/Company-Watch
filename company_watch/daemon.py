from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .analyzer import analyze_item, format_alert
from .classifiers import classify_text
from .config import Settings, get_settings, load_sources, load_watchlist
from .db import Database
from .pollers import fetch_url, parse_source, sec_filing_text
from .scheduler import AdaptiveScheduler
from .telegram_alerts import TelegramClient
from .timeutils import TZ_ALIASES, parse_duration, parse_loose_datetime, utc_now


SEC_FORMS_TO_READ = {"8-K", "6-K", "10-Q", "10-K", "S-1", "424B5", "424B3"}


class CompanyWatchDaemon:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.sync_watchlist(load_watchlist(settings.watchlist_path))
        self.sources = load_sources(settings.sources_path)
        self.scheduler = AdaptiveScheduler(self.db, self.sources)
        self.telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        self.telegram_offset: int | None = None

    def close(self) -> None:
        self.db.close()

    def poll_source(self, source_id: str) -> int:
        source = next((s for s in self.sources if s.id == source_id), None)
        if not source:
            raise ValueError(f"unknown source: {source_id}")
        return self._poll_one(source, mode="manual", reason="manual source poll")

    def poll_due(self, force_ticker: str | None = None) -> int:
        total = 0
        for due in self.scheduler.due_sources(force_ticker=force_ticker):
            total += self._poll_one(due.source, due.mode, due.reason)
        return total

    def _poll_one(self, source, mode: str, reason: str) -> int:
        state = self.db.source_state(source.id)
        try:
            fetched = fetch_url(
                source,
                user_agent=self._user_agent_for_source(source),
                etag=state.get("etag"),
                last_modified=state.get("last_modified"),
            )
            self.db.mark_source_success(source.id, fetched.etag, fetched.last_modified, fetched.status)
            if fetched.not_modified:
                return 0
            raw_items = parse_source(source, fetched.text, profile=self.settings.watch_profile)
        except Exception as exc:
            self.db.mark_source_error(source.id, str(exc))
            return 0

        companies = self.db.watched_companies()
        new_count = 0
        for raw in raw_items:
            if self.db.has_item_hash(raw["item_hash"]):
                continue
            if self._should_read_source_document(raw):
                self._read_source_document(raw, source.timeout_seconds)

            classification = classify_text(
                title=raw.get("title") or "",
                summary=" ".join([raw.get("summary") or "", (raw.get("raw_text") or "")[:6000]]),
                companies=companies,
                source_ticker=source.ticker,
                metadata=raw.get("metadata") or {},
                profile=self.settings.watch_profile,
            )
            raw["ticker"] = classification.ticker
            raw["event_type"] = classification.event_type
            raw["event_score"] = classification.score
            raw.setdefault("metadata", {})
            raw["metadata"].update(
                {
                    "matched_terms": classification.matched_terms,
                    "scheduler_mode": mode,
                    "scheduler_reason": reason,
                    "watch_profile": self.settings.watch_profile,
                }
            )

            raw_item_id, created = self.db.insert_raw_item(raw)
            if not created:
                continue
            new_count += 1
            if (
                classification.ticker
                and classification.score >= self.settings.alert_threshold
                and self._is_fresh_enough_to_alert(raw)
            ):
                item = self.db.raw_item(raw_item_id)
                if item:
                    analysis = analyze_item(self.db, self.settings, item)
                    alert_value = analysis.get("alert", True)
                    should_alert = alert_value if isinstance(alert_value, bool) else str(alert_value).lower() != "false"
                    if should_alert:
                        message = format_alert(analysis, item)
                        telegram_id = self.telegram.send_message(message, chat_id=self.alert_chat_id())
                        self.db.save_alert(raw_item_id, classification.ticker, message, telegram_id)
        return new_count

    def _is_fresh_enough_to_alert(self, raw: dict) -> bool:
        published = parse_loose_datetime(raw.get("published_at"))
        if not published:
            return True
        max_age = timedelta(hours=self.settings.max_alert_item_age_hours)
        return utc_now() - published <= max_age

    def _should_read_source_document(self, raw: dict) -> bool:
        metadata = raw.get("metadata") or {}
        if metadata.get("source_type") != "sec_submissions":
            return False
        if str(metadata.get("form") or "").upper() not in SEC_FORMS_TO_READ:
            return False
        if not raw.get("url"):
            return False
        return self._is_fresh_enough_to_alert(raw)

    def _read_source_document(self, raw: dict, timeout_seconds: int) -> None:
        metadata = raw.setdefault("metadata", {})
        try:
            text, doc_meta = sec_filing_text(
                raw["url"],
                user_agent=self.settings.sec_user_agent,
                timeout_seconds=timeout_seconds,
            )
            if text:
                raw["raw_text"] = text[:80_000]
                raw["summary"] = text[:4_000]
                metadata["document_read"] = True
                metadata["document_chars"] = len(text)
                metadata["document_sections"] = doc_meta.get("sections", [])
        except Exception as exc:
            metadata["document_read"] = False
            metadata["document_error"] = str(exc)[:500]

    def _user_agent_for_source(self, source) -> str:
        if source.type == "sec_submissions":
            return self.settings.sec_user_agent
        return self.settings.user_agent

    def alert_chat_id(self) -> str:
        return self.settings.telegram_chat_id or self.db.get_setting("telegram_chat_id") or ""

    def run_forever(self) -> None:
        while True:
            self.poll_due()
            self.handle_telegram_updates()
            time.sleep(self.settings.baseline_loop_seconds)

    def handle_telegram_updates(self) -> None:
        if not self.telegram.token:
            return
        try:
            updates = self.telegram.get_updates(self.telegram_offset, timeout=1)
        except Exception:
            return
        for update in updates:
            self.telegram_offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            if not chat_id or not text.startswith("/"):
                continue
            owner_chat_id = self.alert_chat_id()
            if not owner_chat_id:
                command = text.split()[0].split("@", 1)[0].lower()
                if command != "/start" or chat.get("type") != "private":
                    continue
                self.db.set_setting("telegram_chat_id", chat_id)
            elif chat_id != owner_chat_id:
                continue
            response = self.handle_command(text)
            if response:
                self.telegram.send_message(response, chat_id=chat_id)

    def handle_command(self, text: str) -> str:
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower()

        if command in {"/start", "/help"}:
            return (
                "/status\n/watch TICKER [Company]\n/unwatch TICKER\n"
                "/catalyst TICKER YYYY-MM-DD HH:MM ET Title\n/hot TICKER 6h\n"
                "/calm TICKER\n/poll_now [TICKER]\n/latest\n/analyze ITEM_ID\n/sources\n/whoami"
            )
        if command == "/whoami":
            chat = self.alert_chat_id()
            return f"Alert chat: {chat or 'not set yet'}"
        if command == "/status":
            watches = ", ".join(c.ticker for c in self.db.watched_companies()) or "none"
            catalysts = self.db.active_catalysts()
            cat_lines = [
                f"{c['ticker']} {c['expected_at_utc']}: {c['title']}" for c in catalysts[:8]
            ] or ["none"]
            return "Watches: " + watches + "\nCatalysts:\n" + "\n".join(cat_lines)
        if command == "/sources":
            return "\n".join(self.scheduler.status_lines())[:3500]
        if command == "/watch" and len(parts) >= 2:
            ticker = parts[1].upper()
            company = " ".join(parts[2:])
            self.db.add_watch(ticker, company)
            return f"Watching {ticker}."
        if command == "/unwatch" and len(parts) >= 2:
            self.db.unwatch(parts[1])
            return f"Stopped watching {parts[1].upper()}."
        if command == "/hot" and len(parts) >= 3:
            ticker = parts[1].upper()
            duration = parse_duration(parts[2])
            self.db.set_hot(ticker, duration)
            return f"{ticker} hot mode for {parts[2]}."
        if command == "/calm" and len(parts) >= 2:
            count = self.db.deactivate_catalysts(parts[1])
            return f"Deactivated {count} active catalyst(s) for {parts[1].upper()}."
        if command == "/poll_now":
            ticker = parts[1].upper() if len(parts) >= 2 else None
            count = self.poll_due(force_ticker=ticker)
            return f"Poll complete. New items: {count}."
        if command == "/latest":
            items = self.db.latest_items(limit=8)
            if not items:
                return "No items yet."
            return "\n".join(
                f"#{i['id']} {i.get('ticker') or '-'} score={i['event_score']} {i['title'][:90]}"
                for i in items
            )
        if command == "/analyze" and len(parts) >= 2:
            item = self.db.raw_item(int(parts[1]))
            if not item:
                return "Item not found."
            analysis = analyze_item(self.db, self.settings, item)
            return format_alert(analysis, item)
        if command == "/catalyst" and len(parts) >= 6:
            ticker = parts[1].upper()
            dt = _parse_command_datetime(parts[2], parts[3], parts[4])
            title = " ".join(parts[5:])
            catalyst_id = self.db.add_catalyst(ticker, title, dt)
            return f"Added catalyst #{catalyst_id}: {ticker} {dt.isoformat(timespec='seconds')} {title}"
        return "Unknown or malformed command. Send /help."


# Keep imports used by existing installations working after the package rename.
NewswatchDaemon = CompanyWatchDaemon


def _parse_command_datetime(date_part: str, time_part: str, tz_part: str) -> datetime:
    naive = datetime.fromisoformat(f"{date_part}T{time_part}")
    tz = TZ_ALIASES.get(tz_part.upper())
    if tz is None:
        tz = ZoneInfo(tz_part)
    return naive.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Company Watch: company news and SEC filing monitor")
    parser.add_argument("command", choices=["run", "poll", "status", "init-db"])
    parser.add_argument("--ticker")
    parser.add_argument("--source")
    args = parser.parse_args()

    daemon = CompanyWatchDaemon(get_settings())
    try:
        if args.command == "run":
            daemon.run_forever()
        elif args.command == "poll":
            if args.source:
                count = daemon.poll_source(args.source)
            else:
                count = daemon.poll_due(force_ticker=args.ticker)
            print(json.dumps({"new_items": count}))
        elif args.command == "status":
            print("\n".join(daemon.scheduler.status_lines()))
        elif args.command == "init-db":
            print(f"initialized {daemon.settings.database_path}")
    finally:
        daemon.close()


if __name__ == "__main__":
    main()
