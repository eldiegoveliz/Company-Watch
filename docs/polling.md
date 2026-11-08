# Daily polling schedule

The bot checks for unannounced news every day at fixed Eastern times. You do not
need to enter an earnings date, trial readout, or other catalyst first.

## Default times

The targets are 06:00, 06:30, 07:00, 07:30, 08:00, 08:30, 09:00, 09:30, 13:00,
16:00, 16:05, 16:30, 17:00, and 17:30 in `America/New_York`.

For each target, polling speeds up one minute beforehand and continues every
15 seconds until ten minutes afterward. Checks align to the clock, so a poll at
06:59:59 does not postpone the 07:00 check. Overlapping windows share completed
checks. Missed slots do not cause a burst of catch-up requests.

The schedule follows Eastern daylight saving time. It runs on weekends too,
because company news can appear when EDGAR is closed. These are fixed daily
times, not a downloaded exchange or earnings calendar. The 13:00 check covers
early market closes, and the usual afternoon checks still run on those days.

Outside these windows, the existing source intervals apply. Announced catalysts
and `/hot` can still make a source faster. Error backoff takes precedence over
clock boundaries. Nothing is sent just because a scheduled check ran.

Use `/sources` in Telegram, or `python3 -m company_watch status`, to see the daily
times and the current mode and interval for each source.

## Why these times

The strongest anchors for this biotech-focused setup are the start of EDGAR
dissemination, morning company news, and the period after the regular market
close. Examples and rules checked on 8 November 2026:

- [SEC operating hours](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgar-its-three-websites)
  are 06:00–22:00 ET on weekdays except federal holidays.
- [Nektar's 25 September 2026 filing](https://www.sec.gov/Archives/edgar/data/906709/000121390026103215/0001213900-26-103215-index.htm)
  was accepted at 07:00:23. Its [issuer-authored press releases](https://www.prnewswire.com/news/nektar-therapeutics/)
  also show 07:00 ET announcements.
- Sarepta published its [Q2](https://investorrelations.sarepta.com/news-releases/news-release-details/sarepta-therapeutics-announces-second-quarter-2026-financial)
  and [Q3 2026 results](https://investorrelations.sarepta.com/news-releases/news-release-details/sarepta-therapeutics-announces-third-quarter-2026-financial)
  at 16:05 ET.
- [NYSE's core session](https://www.nyse.com/trade/hours-calendars) closes at
  16:00 ET, with 13:00 closes on specified half days.
- [SEC filing-date guidance](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/determine-status-my-filing)
  explains the 17:30 cutoff for most submissions. Some forms can be disseminated
  later, so baseline polling continues after that time.

These support practical polling targets, not a measured ranking of all biotech
release times. A press release and its SEC filing can arrive at different times.
The half-hour targets provide additional coverage around the verified examples.

## Change the schedule

Edit your local `.env`, then restart the service:

```env
DAILY_POLL_TIMES_ET=06:00,06:30,07:00,07:30,08:00,08:30,09:00,09:30,13:00,16:00,16:05,16:30,17:00,17:30
DAILY_POLL_BEFORE_SECONDS=60
DAILY_POLL_AFTER_SECONDS=600
DAILY_POLL_INTERVAL_SECONDS=15
POLL_FETCH_WORKERS=4
```

Leave `DAILY_POLL_TIMES_ET` empty to disable the daily windows. Other polling and
catalyst scheduling still work. Set `"daily_polling": false` on an individual
source in `sources.json` to exclude it from daily windows.

## Timing and delivery

Feed fetching, filing-document analysis, and Telegram commands run independently.
Each database worker owns its SQLite connection. Up to four source requests run
at once, with at most one scheduled request in flight per source. Equally urgent
sources with older attempts get priority, and company-specific sources precede
broad feeds. SEC requests are spaced to four per second within this process,
below the SEC's [published limit](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

New items awaiting analysis are stored in SQLite. Interrupted work is recovered
after a restart. Fresh material SEC forms are read before their final
classification and model analysis. A failed processing job is marked
`analysis_error` and logged; it does not stop polling. One analysis worker runs
at a time, so several simultaneous items can wait in the queue.

The clock determines when a check becomes due. Network timeouts, request pacing,
an in-flight fetch, publisher delays, and the model's response time can still
delay detection or delivery. This is a best-effort polling schedule, not a
guarantee of a notification at exactly the release time. Existing deduplication,
freshness checks, materiality filtering, and model fallback remain in effect.
