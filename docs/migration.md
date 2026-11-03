# Keep an existing installation working after the rename

The project is now Company Watch. New commands use `python3 -m company_watch`.
The old `python3 -m biotech_newswatch` entry point and imports remain available
through a compatibility package.

Keep your existing `.env`, source list, watchlist, and database in place. Do not
replace them with the examples during an update.

## Preserve database and profile selection

An explicit `DATABASE_PATH` still takes precedence. When it is unset, Company
Watch reuses `biotech_newswatch.sqlite` if that file exists in the checkout.
Otherwise, it uses `company_watch.sqlite`. The rename does not move database
files or rewrite stored alerts and analysis jobs.

If `WATCH_PROFILE` is unset and the selected database is named
`biotech_newswatch.sqlite`, the bot uses the biotech profile. Other database names
default to the general profile. Set `WATCH_PROFILE=biotech` explicitly if your
existing biotech installation uses a custom database name. An explicit profile
always wins.

## Update a service

The new service template is `systemd/company-watch.service`. The installed
`biotech-newswatch` service can continue using its original command and paths.
The checkout directory can keep its existing name too.

To adopt the new service name, copy your current working directory, environment
file, Python executable, and service account settings into the new unit. Stop
and disable the old service before starting the new one. Do not run both against
the same database.

Restart the service when you are ready to load the updated code. Installing the
new source code alone does not change the already-running Python process.
