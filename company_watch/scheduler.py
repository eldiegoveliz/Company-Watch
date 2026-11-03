from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import SourceConfig
from .db import Database
from .timeutils import parse_datetime, utc_now


@dataclass(frozen=True)
class DueSource:
    source: SourceConfig
    mode: str
    interval_seconds: int
    reason: str


def catalyst_mode_for_source(source: SourceConfig, catalysts: list[dict], now: datetime) -> tuple[str, str]:
    best = ("normal", "baseline")
    for catalyst in catalysts:
        ticker = str(catalyst["ticker"]).upper()
        if source.ticker and source.ticker.upper() != ticker:
            continue

        expected = parse_datetime(catalyst["expected_at_utc"])
        warm_start = expected - timedelta(minutes=int(catalyst["warm_before_minutes"]))
        warm_end = expected + timedelta(minutes=int(catalyst["warm_after_minutes"]))
        hot_start = expected - timedelta(minutes=int(catalyst["hot_before_minutes"]))
        hot_end = expected + timedelta(minutes=int(catalyst["hot_after_minutes"]))

        if hot_start <= now <= hot_end:
            return "hot", f"{ticker}: {catalyst['title']}"
        if warm_start <= now <= warm_end and best[0] != "hot":
            best = ("warm", f"{ticker}: {catalyst['title']}")
    return best


def interval_for_mode(source: SourceConfig, mode: str) -> int:
    if mode == "hot":
        return source.hot_interval_seconds
    if mode == "warm":
        return source.warm_interval_seconds
    return source.normal_interval_seconds


class AdaptiveScheduler:
    def __init__(self, db: Database, sources: list[SourceConfig]):
        self.db = db
        self.sources = sources

    def due_sources(self, force_ticker: str | None = None) -> list[DueSource]:
        now = utc_now()
        catalysts = self.db.active_catalysts()
        due: list[DueSource] = []
        for source in self.sources:
            if force_ticker and source.ticker and source.ticker.upper() != force_ticker.upper():
                continue
            mode, reason = catalyst_mode_for_source(source, catalysts, now)
            interval = interval_for_mode(source, mode)

            state = self.db.source_state(source.id)
            fail_count = int(state.get("fail_count") or 0)
            if fail_count:
                interval = min(interval * (2**min(fail_count, 4)), 3600)

            if force_ticker:
                due.append(DueSource(source=source, mode="manual", interval_seconds=0, reason="manual poll"))
                continue

            last_polled = state.get("last_polled_at")
            if not last_polled:
                due.append(DueSource(source=source, mode=mode, interval_seconds=interval, reason=reason))
                continue

            try:
                elapsed = (now - parse_datetime(last_polled)).total_seconds()
            except Exception:
                elapsed = interval + 1
            if elapsed >= interval:
                due.append(DueSource(source=source, mode=mode, interval_seconds=interval, reason=reason))
        return due

    def status_lines(self) -> list[str]:
        now = utc_now()
        catalysts = self.db.active_catalysts()
        lines = []
        for source in self.sources:
            mode, reason = catalyst_mode_for_source(source, catalysts, now)
            interval = interval_for_mode(source, mode)
            state = self.db.source_state(source.id)
            lines.append(f"{source.id}: {mode} every {interval}s; last={state.get('last_polled_at') or 'never'}; {reason}")
        return lines
