# Configure and use Company Watch

## Set up Telegram

Set `TELEGRAM_BOT_TOKEN` and your `TELEGRAM_CHAT_ID` in `.env` before starting.
If the chat ID is empty, the first private `/start` pairs the bot with that chat
and saves it in SQLite. Pair it yourself before sharing the bot's username.
Commands from other chats are ignored after pairing. Without a Telegram token,
alerts print to stdout.

Restart the bot after changing its configuration. For model settings, see
[analyzer setup](analysis.md).

## Configure companies and sources

Fresh installs use `WATCH_PROFILE=general` for company events across sectors.
Set `WATCH_PROFILE=biotech` to add clinical-trial, FDA, and drug-safety rules.
See [monitoring profiles](profiles.md) for examples and customization.

Edit `watched_tickers.txt` using `TICKER|Company Name|comma-separated aliases`.
Edit `sources.json` to add RSS, SEC submissions, or HTML watch sources. The
default examples include Microsoft SEC submissions. The biotech examples
include Sarepta SEC submissions, its newsroom, and FDA releases. These are sample
configurations.

Each source defines `normal_interval_seconds`, `warm_interval_seconds`, and
`hot_interval_seconds`. A catalyst's default warm window runs from 24 hours
before to 24 hours after the event. Its hot window runs from 2 hours before to
6 hours after. Respect the source's terms and request limits.

Run the optional live source check without sending alerts or changing the database:

```bash
python3 -m scripts.check_sources
```

It checks parsing, SEC ticker mappings, and dedicated feed coverage for watched
companies. A nonzero exit status means a check failed. Adding a company with
`/watch` does not automatically create its dedicated feeds.

## Telegram commands

```text
/status
/sources
/watch SRPT Sarepta Therapeutics
/unwatch SRPT
/catalyst SRPT 2027-06-20 08:00 ET Phase 3 readout
/hot SRPT 6h
/calm SRPT
/poll_now SRPT
/latest
/analyze 12
/whoami
```

Use your own catalyst dates. The date above demonstrates the command format.

## Run a single poll

Run commands from the project root. These commands initialize the database,
show source status, or fetch a single batch:

```bash
python3 -m company_watch init-db
python3 -m company_watch status
python3 -m company_watch poll
```

Polling can call your selected model provider and send alerts. Failed model
analysis falls back to local rules and records the failure in the analysis job.

For continuous polling, use `python3 -m company_watch run`. The bot creates its
SQLite database automatically. See [systemd setup](deployment.md) to run it as a
service.
