from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import WatchCompany
from .profiles import get_profile


@dataclass(frozen=True)
class Classification:
    ticker: str | None
    event_type: str
    score: int
    matched_terms: tuple[str, ...]


NEGATIVE_NOISE = re.compile(r"\b(podcast|webcast replay|conference participation|fireside chat|investor conference)\b")


def match_company(text: str, companies: list[WatchCompany]) -> tuple[str | None, tuple[str, ...]]:
    lowered = text.lower()
    for company in companies:
        matched: list[str] = []
        if re.search(rf"(?<![A-Z0-9]){re.escape(company.ticker)}(?![A-Z0-9])", text, re.I):
            matched.append(company.ticker)
        for term in company.terms:
            if term == company.ticker:
                continue
            if term and term.lower() in lowered:
                matched.append(term)
        if matched:
            return company.ticker, tuple(matched)
    return None, ()


def classify_text(
    title: str,
    summary: str,
    companies: list[WatchCompany],
    source_ticker: str | None = None,
    metadata: dict[str, Any] | None = None,
    profile: str = "general",
) -> Classification:
    rules = get_profile(profile)
    text = f"{title}\n{summary}".strip()
    ticker, matched_terms = match_company(text, companies)
    ticker = ticker or (source_ticker.upper() if source_ticker else None)

    best_type = "watch"
    best_score = 0
    lowered = text.lower()
    for event_type, pattern, score in rules.keywords:
        if re.search(pattern, lowered, re.I) and score > best_score:
            best_type = event_type
            best_score = score

    if best_score == 0 and ticker:
        best_score = 20

    metadata = metadata or {}
    source_type = str(metadata.get("source_type") or "")
    form = str(metadata.get("form") or "").upper()
    if source_type == "sec_submissions":
        if form in {"4", "5", "144", "DEF 14A", "DEFA14A", "ARS", "SC 13G", "SC 13D"}:
            best_type = "sec_routine"
            best_score = min(best_score, 15)
        elif form in {"10-K", "10-Q"}:
            best_type = "sec_periodic"
            best_score = min(best_score if best_score >= 55 else 35, 54)
        elif form in {"8-K", "6-K"}:
            if rules.sec_substantive.search(text):
                best_type = "sec_material"
                best_score = max(best_score, 65)
            else:
                best_type = "sec_generic"
                best_score = min(best_score if best_score else 45, 45)

    if NEGATIVE_NOISE.search(lowered):
        best_score = max(0, best_score - 35)
        if best_score < 55:
            best_type = "noise"

    return Classification(ticker=ticker, event_type=best_type, score=best_score, matched_terms=matched_terms)
