from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profiles import get_profile

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class WatchCompany:
    ticker: str
    company_name: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def terms(self) -> tuple[str, ...]:
        terms = [self.ticker]
        if self.company_name:
            terms.append(self.company_name)
        terms.extend(self.aliases)
        return tuple(t for t in terms if t)


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    type: str
    url: str
    ticker: str | None = None
    normal_interval_seconds: int = 900
    warm_interval_seconds: int = 180
    hot_interval_seconds: int = 20
    timeout_seconds: int = 12
    enabled: bool = True
    link_url_contains: str = ""


@dataclass(frozen=True)
class Settings:
    database_path: Path
    sources_path: Path
    watchlist_path: Path
    telegram_bot_token: str
    telegram_chat_id: str
    openai_api_key: str
    gemini_api_key: str
    analysis_provider: str = "codex"
    codex_command: str = "codex"
    codex_model: str = "gpt-6-luna"
    codex_reasoning_effort: str = "medium"
    codex_profile: str = ""
    codex_timeout_seconds: int = 45
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: int = 20
    gemini_max_output_tokens: int = 1024
    openai_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "low"
    openai_timeout_seconds: int = 20
    openai_max_output_tokens: int = 350
    user_agent: str = "CompanyWatch/0.1 contact=private-research"
    sec_user_agent: str = "CompanyWatch/0.1 contact=private-research"
    baseline_loop_seconds: int = 2
    alert_threshold: int = 55
    max_alert_item_age_hours: int = 72
    watch_profile: str = "general"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def default_database_path(root: Path = ROOT) -> Path:
    """Preserve an existing database from before the Company Watch rename."""
    legacy = root / "biotech_newswatch.sqlite"
    return legacy if legacy.exists() else root / "company_watch.sqlite"


def get_settings() -> Settings:
    load_dotenv()
    configured_path = os.environ.get("DATABASE_PATH")
    database_path = Path(configured_path) if configured_path else default_database_path(ROOT)
    default_profile = "biotech" if database_path.name == "biotech_newswatch.sqlite" else "general"
    watch_profile = os.environ.get("WATCH_PROFILE", default_profile).strip().lower()
    get_profile(watch_profile)
    return Settings(
        database_path=database_path,
        sources_path=Path(os.environ.get("SOURCES_PATH", ROOT / "sources.json")),
        watchlist_path=Path(os.environ.get("WATCHLIST_PATH", ROOT / "watched_tickers.txt")),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")),
        analysis_provider=os.environ.get("ANALYSIS_PROVIDER", "codex"),
        codex_command=os.environ.get("CODEX_COMMAND", "codex"),
        codex_model=os.environ.get("CODEX_MODEL", "gpt-6-luna"),
        codex_reasoning_effort=os.environ.get("CODEX_REASONING_EFFORT", "medium"),
        codex_profile=os.environ.get("CODEX_PROFILE", ""),
        codex_timeout_seconds=int(os.environ.get("CODEX_TIMEOUT_SECONDS", "45")),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_timeout_seconds=int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "20")),
        gemini_max_output_tokens=int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "1024")),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        openai_reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "low"),
        openai_timeout_seconds=int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20")),
        openai_max_output_tokens=int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "350")),
        user_agent=os.environ.get("POLL_USER_AGENT", "CompanyWatch/0.1 contact=private-research"),
        sec_user_agent=os.environ.get(
            "SEC_USER_AGENT",
            os.environ.get("POLL_USER_AGENT", "CompanyWatch/0.1 contact=private-research"),
        ),
        baseline_loop_seconds=int(os.environ.get("BASELINE_LOOP_SECONDS", "2")),
        alert_threshold=int(os.environ.get("ALERT_THRESHOLD", "55")),
        max_alert_item_age_hours=int(os.environ.get("MAX_ALERT_ITEM_AGE_HOURS", "72")),
        watch_profile=watch_profile,
    )


def load_watchlist(path: Path) -> list[WatchCompany]:
    if not path.exists():
        return []
    companies: list[WatchCompany] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        ticker = parts[0].upper()
        company_name = parts[1] if len(parts) > 1 else ""
        aliases = tuple(a.strip() for a in parts[2].split(",") if a.strip()) if len(parts) > 2 else ()
        companies.append(WatchCompany(ticker=ticker, company_name=company_name, aliases=aliases))
    return companies


def load_sources(path: Path) -> list[SourceConfig]:
    if not path.exists():
        return []
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for item in data.get("sources", []):
        if not item.get("id") or not item.get("type") or not item.get("url"):
            continue
        sources.append(
            SourceConfig(
                id=item["id"],
                name=item.get("name", item["id"]),
                type=item["type"],
                url=item["url"],
                ticker=item.get("ticker"),
                normal_interval_seconds=_int_setting(item.get("normal_interval_seconds"), 900),
                warm_interval_seconds=_int_setting(item.get("warm_interval_seconds"), 180),
                hot_interval_seconds=_int_setting(item.get("hot_interval_seconds"), 20),
                timeout_seconds=_int_setting(item.get("timeout_seconds"), 12),
                enabled=bool(item.get("enabled", True)),
                link_url_contains=item.get("link_url_contains", ""),
            )
        )
    return [s for s in sources if s.enabled]


def _int_setting(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)
