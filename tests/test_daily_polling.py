from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from company_watch.config import Settings, SourceConfig, parse_daily_poll_times
from company_watch.db import Database
from company_watch.pollers import RequestPacer, _pace_sec_request
from company_watch.scheduler import EASTERN, AdaptiveScheduler, DailyPollSchedule


def eastern(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=EASTERN).astimezone(UTC)


class DailyPollingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "test.sqlite")
        self.addCleanup(self.db.close)
        self.source = SourceConfig("test", "Test SEC", "sec_submissions", "https://example.test", ticker="TEST", normal_interval_seconds=1800)
        self.scheduler = AdaptiveScheduler(self.db, [self.source])

    def last_poll(self, when: datetime, failed=False):
        if failed:
            self.db.mark_source_error("test", "synthetic error", polled_at=when.isoformat())
        else:
            self.db.mark_source_success("test", None, None, 200, polled_at=when.isoformat())

    def test_prechecks_and_exact_target_ignore_interval_drift(self):
        for clock in ["2026-11-09T06:59:00", "2026-11-09T07:00:00", "2026-11-09T16:00:00", "2026-11-09T16:05:00"]:
            with self.subTest(clock=clock):
                now = eastern(clock)
                self.last_poll(now - timedelta(seconds=1))
                due = self.scheduler.due_sources(now=now)
                self.assertEqual(len(due), 1)
                self.assertEqual(due[0].mode, "daily")
                self.assertEqual(due[0].interval_seconds, 15)
                self.assertEqual(due[0].slot_at, now)

    def test_poll_once_per_slot_and_catch_up_without_a_burst(self):
        self.last_poll(eastern("2026-11-09T07:00:01"))
        self.assertEqual(self.scheduler.due_sources(now=eastern("2026-11-09T07:00:14")), [])
        self.assertEqual(len(self.scheduler.due_sources(now=eastern("2026-11-09T07:00:15"))), 1)
        # A slow loop catches the current slot, not every historical slot it missed.
        self.assertEqual(len(self.scheduler.due_sources(now=eastern("2026-11-09T07:02:00"))), 1)

    def test_window_edges_return_to_baseline(self):
        for clock in ["2026-11-09T06:58:59", "2026-11-09T07:10:01", "2026-11-09T12:00:00"]:
            with self.subTest(clock=clock):
                now = eastern(clock)
                self.last_poll(now - timedelta(seconds=1))
                self.assertEqual(self.scheduler.due_sources(now=now), [])

    def test_eastern_schedule_tracks_dst_and_runs_without_catalysts_on_weekends(self):
        for clock, hour in [("2026-01-15T07:00:00", 12), ("2026-07-15T07:00:00", 11), ("2026-11-08T07:00:00", 12)]:
            with self.subTest(clock=clock):
                now = eastern(clock)
                self.assertEqual(now.hour, hour)
                self.last_poll(now - timedelta(seconds=1))
                self.assertEqual(len(self.scheduler.due_sources(now=now)), 1)
                self.assertEqual(self.db.active_catalysts(), [])

    def test_half_day_check_does_not_remove_regular_afternoon_checks(self):
        for clock in ["2026-11-27T13:00:00", "2026-11-27T16:00:00", "2026-11-27T17:30:00"]:
            with self.subTest(clock=clock):
                now = eastern(clock)
                self.last_poll(now - timedelta(seconds=1))
                self.assertEqual(len(self.scheduler.due_sources(now=now)), 1)

    def test_error_backoff_is_not_overridden_by_the_clock(self):
        now = eastern("2026-11-09T07:00:00")
        self.last_poll(now - timedelta(seconds=1), failed=True)
        self.assertEqual(self.scheduler.due_sources(now=now), [])
        due = self.scheduler.due_sources(now=now + timedelta(seconds=30))
        self.assertEqual(due[0].interval_seconds, 30)
        self.assertIsNone(due[0].slot_at)

    def test_faster_catalyst_still_wins(self):
        now = eastern("2026-11-09T07:00:00")
        source = replace(self.source, hot_interval_seconds=5)
        self.db.add_catalyst("TEST", "Synthetic event", now + timedelta(minutes=5))
        scheduler = AdaptiveScheduler(self.db, [source])
        self.last_poll(now - timedelta(seconds=1))
        due = scheduler.due_sources(now=now)
        self.assertEqual(due[0].mode, "hot")
        self.assertEqual(due[0].interval_seconds, 5)

    def test_source_can_opt_out_and_global_schedule_can_be_disabled(self):
        now = eastern("2026-11-09T07:00:00")
        self.last_poll(now - timedelta(seconds=1))
        scheduler = AdaptiveScheduler(self.db, [replace(self.source, daily_polling=False)])
        self.assertEqual(scheduler.due_sources(now=now), [])
        self.scheduler.daily = DailyPollSchedule(times=())
        self.assertEqual(self.scheduler.due_sources(now=now), [])

    def test_overlapping_windows_do_not_repeat_a_completed_slot(self):
        now = eastern("2026-11-09T16:04:01")
        self.last_poll(now - timedelta(seconds=1))
        self.assertEqual(self.scheduler.due_sources(now=now), [])

    def test_sleep_is_shortened_before_a_clock_boundary(self):
        daily = DailyPollSchedule(times=(time(7),))
        self.assertAlmostEqual(daily.next_wakeup_delay(2, eastern("2026-11-09T06:58:59.800000")), 0.2)
        self.assertAlmostEqual(daily.next_wakeup_delay(2, eastern("2026-11-09T06:59:59.500000")), 0.5)
        self.assertEqual(daily.next_wakeup_delay(60, eastern("2026-11-09T07:00:00")), 15)

    def test_window_can_start_on_previous_day(self):
        daily = DailyPollSchedule(times=(time(0),))
        before = eastern("2026-11-09T23:59:00")
        self.assertIsNotNone(daily.active_slot(before))
        self.assertEqual(daily.active_slot(eastern("2026-11-10T00:00:00"))[0], eastern("2026-11-10T00:00:00"))

    def test_times_parse_explicitly_and_invalid_settings_fail(self):
        self.assertEqual(parse_daily_poll_times("08:00,07:00,07:00"), (time(7), time(8)))
        self.assertEqual(parse_daily_poll_times(""), ())
        for value in ["7am", "07", "0700", "25:00", "07:00:30", "07:00+00:00"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_daily_poll_times(value)
        base = Settings(Path("unused"), Path("unused"), Path("unused"), "", "", "", "")
        for changes in [{"daily_poll_interval_seconds": 0}, {"daily_poll_before_seconds": -1},
                        {"daily_poll_after_seconds": 86400}, {"poll_fetch_workers": 0}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(base, **changes)


class SecPacingTests(unittest.TestCase):
    def test_sec_requests_are_spaced_even_when_they_are_ready_together(self):
        clock = [0.0]
        starts = []
        def sleep(seconds):
            clock[0] += seconds
        with patch("company_watch.pollers.time.monotonic", side_effect=lambda: clock[0]), \
             patch("company_watch.pollers.time.sleep", side_effect=sleep):
            pacer = RequestPacer()
            for _ in range(5):
                pacer.wait()
                starts.append(clock[0])
        self.assertEqual(starts, [0.0, 0.25, 0.5, 0.75, 1.0])

    def test_only_sec_hosts_use_the_shared_pacer(self):
        with patch("company_watch.pollers.SEC_REQUEST_PACER.wait") as wait:
            for url in ["https://sec.gov/a", "https://www.sec.gov/a", "https://data.sec.gov/a"]:
                _pace_sec_request(url)
            self.assertEqual(wait.call_count, 3)
            _pace_sec_request("https://sec.gov.example.test/a")
            _pace_sec_request("https://example.test/a")
            self.assertEqual(wait.call_count, 3)


if __name__ == "__main__":
    unittest.main()
