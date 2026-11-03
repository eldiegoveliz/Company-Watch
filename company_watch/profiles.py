"""Event rules and analysis context for the supported monitoring profiles."""
from __future__ import annotations

import re
from dataclasses import dataclass


GENERAL_KEYWORDS: tuple[tuple[str, str, int], ...] = (
    ("earnings", r"\b(earnings|financial results|quarterly results|annual results|reports? revenue)\b", 65),
    ("guidance", r"\b(raises?|raised|cuts?|lowers?|lowered|withdraws?|updates?|updated)\s+(?:its\s+)?(?:full-year\s+|annual\s+|quarterly\s+)?(?:revenue\s+|earnings\s+|profit\s+)?guidance\b", 75),
    ("financing", r"\b(public offering|registered direct|atm offering|at-the-market|private placement|(?-i:PIPE))\b", 65),
    ("mna", r"\b(acquire[sd]?|acquisition|merger|tender offer|definitive agreement)\b", 82),
    ("distress", r"\b(going concern|bankruptcy|chapter 11|insolvency|debt restructuring)\b", 85),
    ("leadership", r"\b(?:(?:ceo|cfo|chief executive officer|chief financial officer).{0,60}(?:resigns?|steps down|retires?|appoint)|(?:appoints?|names|resignation of).{0,60}(?:ceo|cfo|chief executive officer|chief financial officer))\b", 65),
    ("cybersecurity", r"\b(data breach|cyberattack|cyber attack|ransomware)\b", 80),
    ("capital_return", r"\b(share repurchase|stock repurchase|share buyback|dividend)\b", 60),
    ("sec_material", r"\b(material definitive agreement|termination|resignation)\b", 65),
)

BIOTECH_KEYWORDS: tuple[tuple[str, str, int], ...] = (
    ("approval", r"\b(fda approved|approved by the fda|approval|accelerated approval)\b", 95),
    ("crl", r"\b(complete response letter|crl)\b", 98),
    ("clinical_hold", r"\b(clinical hold|partial clinical hold|trial hold)\b", 95),
    ("trial_halt", r"\b(trial halted|halts trial|study halted|stops trial|discontinued study)\b", 90),
    ("topline_data", r"\b(topline|top-line|pivotal data|phase 3 data|phase iii data|readout)\b", 82),
    ("failed_trial", r"\b(failed to meet|did not meet|missed primary endpoint|not statistically significant)\b", 88),
    ("positive_trial", r"\b(met primary endpoint|statistically significant|positive results|significant improvement)\b", 78),
    ("safety", r"\b(safety signal|serious adverse|death|deaths|boxed warning|black box)\b", 82),
    ("regulatory", r"\b(pdufa|advisory committee|adcom|nda|bla|maa|label expansion)\b", 72),
)

GENERAL_SEC_TERMS = (
    r"merger|acquisition|definitive agreement|termination|license|partnership|"
    r"offering|atm|private placement|going concern|bankruptcy|restructuring|"
    r"resignation|ceo|cfo|earnings|financial results|guidance|dividend|repurchase|"
    r"data breach|cyberattack|ransomware"
)
BIOTECH_SEC_TERMS = r"clinical|trial|phase|topline|top-line|endpoint|fda|approval|crl|hold|chief medical|data|results"
GENERAL_HTML_TERMS = (
    "earnings", "revenue", "guidance", "acquisition", "merger", "offering",
    "results", "dividend", "repurchase", "appoint", "resign", "breach",
    "bankruptcy", "update", "agreement",
)


@dataclass(frozen=True)
class EventProfile:
    keywords: tuple[tuple[str, str, int], ...]
    sec_substantive: re.Pattern[str]
    html_terms: tuple[str, ...]
    analysis_context: str


PROFILES = {
    "general": EventProfile(
        keywords=GENERAL_KEYWORDS,
        sec_substantive=re.compile(r"\b(" + GENERAL_SEC_TERMS + r")\b", re.I),
        html_terms=GENERAL_HTML_TERMS,
        analysis_context="Evaluate material company events across sectors, including earnings, "
        "guidance, financing, acquisitions, leadership changes, and cybersecurity incidents.",
    ),
    "biotech": EventProfile(
        keywords=GENERAL_KEYWORDS + BIOTECH_KEYWORDS,
        sec_substantive=re.compile(r"\b(" + GENERAL_SEC_TERMS + "|" + BIOTECH_SEC_TERMS + r")\b", re.I),
        html_terms=GENERAL_HTML_TERMS + ("data", "phase", "trial", "fda", "approval", "pipeline", "clinical"),
        analysis_context="Also evaluate biotech events, including clinical trials, FDA decisions, "
        "drug safety, and clinical or regulatory catalysts.",
    ),
}


def get_profile(name: str) -> EventProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("WATCH_PROFILE must be general or biotech") from None
