# Contribute to Company Watch

Use Python 3.11 or later and run commands from the project root. The bot uses
the Python standard library. Linux with system timezone data is the baseline
environment.

Before submitting a change, run the offline checks:

```bash
python3 -m unittest discover -s tests -v
python3 -m scripts.check_release
```

For parser or classifier changes, include a small synthetic fixture that shows
the expected result. Keep tests offline and use temporary databases.

Keep credentials, chat IDs, watchlists, and fetched source content out of pull
requests. Change `sources.example.json` or `watched_tickers.example.txt` to update
the public examples. Their local counterparts are ignored by Git.

Describe the behavior you changed and the checks you ran. Source fetching and
model calls can send data to external services. Mock them in tests.

Submit contributions under the project's GNU GPL version 3 only license
(`GPL-3.0-only`).
