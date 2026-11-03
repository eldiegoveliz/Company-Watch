# Select a monitoring profile

Set `WATCH_PROFILE` in your local `.env`, then restart the bot to apply it.

## Monitor companies across sectors

Use `WATCH_PROFILE=general`. This is the default for fresh installations.

The general rules recognize earnings, guidance changes, financing, acquisitions,
financial distress, leadership changes, cybersecurity incidents, and capital
returns. They also filter routine SEC filings and low-value conference notices.
Not every collected filing triggers an alert.

The default `sources.example.json` and `watched_tickers.example.txt` demonstrate
Microsoft SEC submissions. Add your own company feeds and tickers to the local
configuration files.

## Add biotech events

Use `WATCH_PROFILE=biotech` to add clinical-trial results, FDA decisions, drug
safety, and other clinical or regulatory events to the general company rules.
This profile also adds biotech context to model prompts and HTML link filtering.

For a fresh installation, copy the biotech examples instead of the defaults:

```bash
cp sources.biotech.example.json sources.json
cp watched_tickers.biotech.example.txt watched_tickers.txt
```

Then set `WATCH_PROFILE=biotech` in `.env`. These examples include Sarepta SEC
submissions, its newsroom, and FDA releases. They are not investment recommendations.

For an existing installation, keep your local source and watchlist files.
Change only the profile if you want a different set of rules.

## Customize the rules

Profiles are defined in `company_watch/profiles.py`. Each profile supplies event
patterns and scores, material SEC language, HTML link terms, and model context.
An agent can adapt these for another sector. Add offline examples of relevant
events and noise before changing the rules.

One profile applies to the whole bot. Switching profiles does not reclassify
stored items. Existing installations using `biotech_newswatch.sqlite` default to
the biotech profile when `WATCH_PROFILE` is unset. See the
[migration guide](migration.md) for the rename behavior.
