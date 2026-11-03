"""Fetch and parse all feeds without sending alerts or changing the database.

Run from the project root: python3 -m scripts.check_sources
"""
from concurrent.futures import ThreadPoolExecutor

from company_watch.config import get_settings, load_sources, load_watchlist
from company_watch.pollers import fetch_url, parse_source


def main():
    settings = get_settings()
    sources = load_sources(settings.sources_path)

    def check(source):
        try:
            agent = settings.sec_user_agent if source.type == "sec_submissions" else settings.user_agent
            result = fetch_url(source, agent)
            items = parse_source(source, result.text, profile=settings.watch_profile)
            if not items:
                raise ValueError("Feed returned no parseable news items")
            if source.type == "sec_submissions":
                import json
                if source.ticker not in json.loads(result.text).get("tickers", []):
                    raise ValueError("SEC company does not match configured ticker")
            return True, f"PASS {source.id}: HTTP {result.status}, {len(items)} items"
        except Exception as exc:
            return False, f"FAIL {source.id}: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, sources))
    for _, message in results:
        print(message)
    covered = {s.ticker for s in sources if s.ticker}
    missing = {c.ticker for c in load_watchlist(settings.watchlist_path)} - covered
    if missing:
        print("FAIL Missing company feeds:", ", ".join(sorted(missing)))
    return int(bool(missing) or not all(ok for ok, _ in results))


if __name__ == "__main__":
    raise SystemExit(main())
