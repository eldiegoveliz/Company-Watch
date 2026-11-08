from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, time
from zoneinfo import ZoneInfo

from .config import DEFAULT_DAILY_POLL_TIMES_ET, Settings, SourceConfig, parse_daily_poll_times
from .db import Database
from .timeutils import parse_datetime, utc_now


EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DueSource:
    source: SourceConfig
    mode: str
    interval_seconds: int
    reason: str
    slot_at: datetime | None = None


@dataclass(frozen=True)
class DailyPollSchedule:
    times: tuple[time, ...] = parse_daily_poll_times(DEFAULT_DAILY_POLL_TIMES_ET)
    before_seconds: int = 60
    after_seconds: int = 600
    interval_seconds: int = 15

    @classmethod
    def from_settings(cls, settings: Settings) -> DailyPollSchedule:
        return cls(settings.daily_poll_times_et, settings.daily_poll_before_seconds,
                   settings.daily_poll_after_seconds, settings.daily_poll_interval_seconds)

    def windows(self, now: datetime):
        local_day = now.astimezone(EASTERN).date()
        # Adjacent dates cover windows spanning midnight, in either direction.
        for offset in (-1, 0, 1):
            day = local_day + timedelta(days=offset)
            for target in self.times:
                anchor = datetime.combine(day, target, tzinfo=EASTERN).astimezone(UTC)
                yield (anchor - timedelta(seconds=self.before_seconds),
                       anchor + timedelta(seconds=self.after_seconds), anchor)

    def active_slot(self, now: datetime) -> tuple[datetime, str] | None:
        now = now.astimezone(UTC)
        active = []
        for start, end, anchor in self.windows(now):
            if start <= now <= end:
                step = int((now - anchor).total_seconds() // self.interval_seconds)
                slot = max(start, anchor + timedelta(seconds=step * self.interval_seconds))
                active.append((slot, anchor))
        if not active:
            return None
        slot, anchor = max(active)
        return slot, f"daily {anchor.astimezone(EASTERN):%H:%M} ET"

    def next_wakeup_delay(self, maximum: float, now: datetime) -> float:
        now = now.astimezone(UTC)
        delays = [maximum]
        for start, end, anchor in self.windows(now):
            if start > now:
                delays.append((start - now).total_seconds())
            elif now <= end:
                step = int((now - anchor).total_seconds() // self.interval_seconds) + 1
                next_slot = anchor + timedelta(seconds=step * self.interval_seconds)
                if next_slot <= end:
                    delays.append((next_slot - now).total_seconds())
        return max(0.01, min(delays))


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
        if warm_start <= now <= warm_end:
            best = ("warm", f"{ticker}: {catalyst['title']}")
    return best


def interval_for_mode(source: SourceConfig, mode: str) -> int:
    if mode == "hot":
        return source.hot_interval_seconds
    if mode == "warm":
        return source.warm_interval_seconds
    return source.normal_interval_seconds


class AdaptiveScheduler:
    def __init__(self, db: Database, sources: list[SourceConfig], settings: Settings | None = None):
        self.db = db
        self.sources = sources
        self.daily = DailyPollSchedule.from_settings(settings) if settings else DailyPollSchedule()

    def decision(self, source: SourceConfig, catalysts: list[dict], now: datetime, state: dict) -> DueSource:
        mode, reason = catalyst_mode_for_source(source, catalysts, now)
        interval = min(source.normal_interval_seconds, interval_for_mode(source, mode))
        slot_at = None
        daily = self.daily.active_slot(now) if source.daily_polling else None
        if daily:
            slot_at, daily_reason = daily
            if self.daily.interval_seconds < interval:
                mode, reason, interval = "daily", daily_reason, self.daily.interval_seconds
            else:
                reason += f"; {daily_reason}"
        fail_count = int(state.get("fail_count") or 0)
        if fail_count:
            interval = min(interval * (2 ** min(fail_count, 4)), 3600)
            reason += f"; backoff after {fail_count} failure(s)"
            slot_at = None  # Clock boundaries must not override error backoff.
        return DueSource(source, mode, interval, reason, slot_at)

    def due_sources(self, force_ticker: str | None = None, now: datetime | None = None) -> list[DueSource]:
        now = now or utc_now()
        catalysts = self.db.active_catalysts()
        due = []
        for source in self.sources:
            if force_ticker and source.ticker and source.ticker.upper() != force_ticker.upper():
                continue
            state = self.db.source_state(source.id)
            decision = self.decision(source, catalysts, now, state)
            if force_ticker:
                due.append(DueSource(source, "manual", 0, "manual poll"))
                continue
            last_polled = state.get("last_polled_at")
            if not last_polled:
                due.append(decision)
                continue
            try:
                last = parse_datetime(last_polled)
            except (ValueError, TypeError):
                due.append(decision)
                continue
            if ((decision.slot_at is not None and last < decision.slot_at)
                    or (now - last).total_seconds() >= decision.interval_seconds):
                due.append(decision)
        return due

    def status_lines(self) -> list[str]:
        now = utc_now()
        catalysts = self.db.active_catalysts()
        times = ", ".join(t.strftime("%H:%M") for t in self.daily.times)
        lines = [f"Daily checks (Eastern): {times or 'disabled'}"]
        for source in self.sources:
            state = self.db.source_state(source.id)
            decision = self.decision(source, catalysts, now, state)
            lines.append(f"{source.id}: {decision.mode} every {decision.interval_seconds}s; "
                         f"last={state.get('last_polled_at') or 'never'}; {decision.reason}")
        return lines
