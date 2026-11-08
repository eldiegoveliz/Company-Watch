from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import WatchCompany
from .timeutils import iso_now, parse_datetime, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS watched_companies (
    ticker TEXT PRIMARY KEY,
    company_name TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    last_polled_at TEXT,
    etag TEXT,
    last_modified TEXT,
    last_status INTEGER,
    last_error TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raw_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    ticker TEXT,
    title TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    summary TEXT,
    raw_text TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    item_hash TEXT NOT NULL UNIQUE,
    event_type TEXT,
    event_score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalysts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    title TEXT NOT NULL,
    expected_at_utc TEXT NOT NULL,
    warm_before_minutes INTEGER NOT NULL DEFAULT 1440,
    warm_after_minutes INTEGER NOT NULL DEFAULT 1440,
    hot_before_minutes INTEGER NOT NULL DEFAULT 120,
    hot_after_minutes INTEGER NOT NULL DEFAULT 360,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_item_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_item_id INTEGER NOT NULL,
    ticker TEXT,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    telegram_message_id TEXT
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_items_seen ON raw_items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_raw_items_ticker ON raw_items(ticker);
CREATE INDEX IF NOT EXISTS idx_raw_items_pending_analysis
ON raw_items(event_score DESC, id) WHERE status='pending_analysis';
CREATE INDEX IF NOT EXISTS idx_catalysts_ticker_time ON catalysts(ticker, expected_at_utc);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def sync_watchlist(self, companies: list[WatchCompany]) -> None:
        now = iso_now()
        for company in companies:
            self.conn.execute(
                """
                INSERT INTO watched_companies (ticker, company_name, aliases_json, active, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    company_name=excluded.company_name,
                    aliases_json=excluded.aliases_json,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (company.ticker, company.company_name, json.dumps(company.aliases), now),
            )
        self.conn.commit()

    def add_watch(self, ticker: str, company_name: str = "", aliases: list[str] | None = None) -> None:
        now = iso_now()
        self.conn.execute(
            """
            INSERT INTO watched_companies (ticker, company_name, aliases_json, active, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(ticker) DO UPDATE SET active=1, company_name=?, aliases_json=?, updated_at=?
            """,
            (
                ticker.upper(),
                company_name,
                json.dumps(aliases or []),
                now,
                company_name,
                json.dumps(aliases or []),
                now,
            ),
        )
        self.conn.commit()

    def unwatch(self, ticker: str) -> None:
        self.conn.execute(
            "UPDATE watched_companies SET active=0, updated_at=? WHERE ticker=?",
            (iso_now(), ticker.upper()),
        )
        self.conn.commit()

    def watched_companies(self) -> list[WatchCompany]:
        rows = self.conn.execute(
            "SELECT ticker, company_name, aliases_json FROM watched_companies WHERE active=1 ORDER BY ticker"
        ).fetchall()
        return [
            WatchCompany(
                ticker=row["ticker"],
                company_name=row["company_name"] or "",
                aliases=tuple(json.loads(row["aliases_json"] or "[]")),
            )
            for row in rows
        ]

    def source_state(self, source_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM source_state WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else {}

    def mark_source_success(self, source_id: str, etag: str | None, last_modified: str | None, status: int | None,
                            polled_at: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO source_state (source_id, last_polled_at, etag, last_modified, last_status, last_error, fail_count)
            VALUES (?, ?, ?, ?, ?, NULL, 0)
            ON CONFLICT(source_id) DO UPDATE SET
                last_polled_at=excluded.last_polled_at,
                etag=COALESCE(excluded.etag, source_state.etag),
                last_modified=COALESCE(excluded.last_modified, source_state.last_modified),
                last_status=excluded.last_status,
                last_error=NULL,
                fail_count=0
            """,
            (source_id, polled_at or iso_now(), etag, last_modified, status),
        )
        self.conn.commit()

    def mark_source_error(self, source_id: str, error: str, polled_at: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO source_state (source_id, last_polled_at, last_error, fail_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(source_id) DO UPDATE SET
                last_polled_at=excluded.last_polled_at,
                last_error=excluded.last_error,
                fail_count=source_state.fail_count + 1
            """,
            (source_id, polled_at or iso_now(), error[:800]),
        )
        self.conn.commit()

    def insert_raw_item(self, item: dict[str, Any]) -> tuple[int, bool]:
        now = iso_now()
        try:
            cur = self.conn.execute(
                """
                INSERT INTO raw_items (
                    source_id, ticker, title, url, published_at, summary, raw_text,
                    metadata_json, item_hash, event_type, event_score, status,
                    first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["source_id"],
                    item.get("ticker"),
                    item["title"],
                    item.get("url"),
                    item.get("published_at"),
                    item.get("summary"),
                    item.get("raw_text"),
                    json.dumps(item.get("metadata", {}), sort_keys=True),
                    item["item_hash"],
                    item.get("event_type"),
                    int(item.get("event_score", 0)),
                    item.get("status", "new"),
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid), True
        except sqlite3.IntegrityError:
            self.conn.execute("UPDATE raw_items SET last_seen_at=? WHERE item_hash=?", (now, item["item_hash"]))
            self.conn.commit()
            row = self.conn.execute("SELECT id FROM raw_items WHERE item_hash=?", (item["item_hash"],)).fetchone()
            return int(row["id"]), False

    def has_item_hash(self, item_hash: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM raw_items WHERE item_hash=? LIMIT 1", (item_hash,)).fetchone()
        return row is not None

    def raw_item(self, raw_item_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM raw_items WHERE id=?", (raw_item_id,)).fetchone()
        return dict(row) if row else None

    def recover_pending_analysis(self) -> None:
        self.conn.execute("UPDATE raw_items SET status='pending_analysis' WHERE status='processing_analysis'")
        self.conn.commit()

    def claim_pending_analysis(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id FROM raw_items WHERE status='pending_analysis' "
            "ORDER BY event_score DESC, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        changed = self.conn.execute(
            "UPDATE raw_items SET status='processing_analysis' WHERE id=? AND status='pending_analysis'",
            (row["id"],),
        ).rowcount
        self.conn.commit()
        return self.raw_item(row["id"]) if changed else None

    def update_item_classification(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE raw_items SET ticker=?, event_type=?, event_score=?, summary=?, raw_text=?, metadata_json=? WHERE id=?",
            (item.get("ticker"), item.get("event_type"), item.get("event_score", 0), item.get("summary"),
             item.get("raw_text"), json.dumps(item.get("metadata") or {}), item["id"]),
        )
        self.conn.commit()

    def finish_pending_analysis(self, raw_item_id: int, failed: bool = False) -> None:
        self.conn.execute(
            "UPDATE raw_items SET status=? WHERE id=? AND status='processing_analysis'",
            ("analysis_error" if failed else "processed", raw_item_id),
        )
        self.conn.commit()

    def latest_items(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM raw_items ORDER BY first_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_catalyst(
        self,
        ticker: str,
        title: str,
        expected_at: datetime,
        warm_before_minutes: int = 1440,
        warm_after_minutes: int = 1440,
        hot_before_minutes: int = 120,
        hot_after_minutes: int = 360,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO catalysts (
                ticker, title, expected_at_utc, warm_before_minutes, warm_after_minutes,
                hot_before_minutes, hot_after_minutes, active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                ticker.upper(),
                title,
                expected_at.isoformat(timespec="seconds"),
                warm_before_minutes,
                warm_after_minutes,
                hot_before_minutes,
                hot_after_minutes,
                iso_now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_hot(self, ticker: str, duration: timedelta, title: str = "Manual hot mode") -> int:
        expected = utc_now()
        return self.add_catalyst(
            ticker=ticker,
            title=title,
            expected_at=expected,
            warm_before_minutes=0,
            warm_after_minutes=max(1, int(duration.total_seconds() // 60)),
            hot_before_minutes=0,
            hot_after_minutes=max(1, int(duration.total_seconds() // 60)),
        )

    def deactivate_catalysts(self, ticker: str) -> int:
        cur = self.conn.execute("UPDATE catalysts SET active=0 WHERE ticker=? AND active=1", (ticker.upper(),))
        self.conn.commit()
        return int(cur.rowcount)

    def active_catalysts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM catalysts
            WHERE active=1
            ORDER BY expected_at_utc ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def create_analysis_job(
        self,
        raw_item_id: int,
        prompt_version: str,
        model: str,
        reasoning_effort: str,
        input_payload: dict[str, Any],
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO analysis_jobs (
                raw_item_id, status, prompt_version, model, reasoning_effort,
                input_json, created_at
            )
            VALUES (?, 'running', ?, ?, ?, ?, ?)
            """,
            (raw_item_id, prompt_version, model, reasoning_effort, json.dumps(input_payload), iso_now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_analysis_job(self, job_id: int, output: dict[str, Any] | None, error: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE analysis_jobs
            SET status=?, output_json=?, error=?, finished_at=?
            WHERE id=?
            """,
            ("error" if error else "done", json.dumps(output or {}), error, iso_now(), job_id),
        )
        self.conn.commit()

    def save_alert(self, raw_item_id: int, ticker: str | None, message: str, telegram_message_id: str | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO alerts (raw_item_id, ticker, message, sent_at, telegram_message_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (raw_item_id, ticker, message, iso_now(), telegram_message_id),
        )
        self.conn.execute("UPDATE raw_items SET status='alerted' WHERE id=?", (raw_item_id,))
        self.conn.commit()
        return int(cur.lastrowid)

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, iso_now()),
        )
        self.conn.commit()
