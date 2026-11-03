from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .profiles import get_profile


PROMPT_VERSION = "company-watch-alert-v1"


SYSTEM_PROMPT = """You are a fast company-event triage analyst.
Return JSON only. Do not include markdown.
Separate sourced facts from interpretation. Be concise.
Classify thesis_impact as bullish, bearish, mixed, neutral, or unknown.
Classify urgency as immediate, today, this_week, watch_only.
If the item is not material, say alert=false."""


def analysis_system_prompt(settings: Settings) -> str:
    return SYSTEM_PROMPT + "\n" + get_profile(settings.watch_profile).analysis_context


def analysis_configuration(settings: Settings) -> tuple[str, str, str]:
    """Return the selected provider, model, and reasoning effort."""
    provider = settings.analysis_provider.strip().lower()
    if provider in {"gemini", "gemini_api", "google"}:
        return "gemini", settings.gemini_model, "low"
    if provider == "openai_api":
        return provider, settings.openai_model, settings.openai_reasoning_effort
    return provider, settings.codex_model, settings.codex_reasoning_effort


def analysis_setup_issues(settings: Settings) -> list[str]:
    """Check local prerequisites without a model call or exposing credentials."""
    provider, model, _ = analysis_configuration(settings)
    if provider not in {"codex", "openai_api", "gemini"}:
        return ["Unsupported ANALYSIS_PROVIDER. Choose codex, openai_api, or gemini, "
                "or add an adapter for your preferred provider. See docs/analysis.md."]
    issues = []
    if not model.strip():
        issues.append("Set the model for your selected provider in .env.")
    if provider == "codex" and not shutil.which(settings.codex_command):
        issues.append("Codex executable not found. Set CODEX_COMMAND to its executable path, "
                      "or choose an API provider in .env. See docs/analysis.md.")
    elif provider == "openai_api" and not settings.openai_api_key.strip():
        issues.append("OPENAI_API_KEY is empty. Add it locally in .env, or choose another provider.")
    elif provider == "gemini" and not settings.gemini_api_key.strip():
        issues.append("GEMINI_API_KEY is empty. Add it locally in .env, or choose another provider.")
    return issues


def _fallback_analysis(item: dict[str, Any]) -> dict[str, Any]:
    score = int(item.get("event_score") or 0)
    event_type = item.get("event_type") or "watch"
    impact = "unknown"
    if event_type in {"approval", "positive_trial", "mna"}:
        impact = "bullish"
    elif event_type in {"crl", "clinical_hold", "trial_halt", "failed_trial", "safety", "financing"}:
        impact = "bearish"
    return {
        "alert": score >= 55,
        "company": item.get("ticker") or "UNKNOWN",
        "ticker": item.get("ticker"),
        "event": item.get("title", "")[:220],
        "event_type": event_type,
        "why_it_matters": [
            "Matched high-value company event language.",
            "Needs source verification before treating model interpretation as fact.",
        ],
        "thesis_impact": impact,
        "urgency": "immediate" if score >= 80 else "today",
        "confidence": "medium",
        "uncertainty": "Model analysis unavailable; local rules generated this triage.",
        "source": item.get("url") or item.get("source_id"),
    }


def _extract_output_text(response: dict[str, Any]) -> str:
    if response.get("output_text"):
        return str(response["output_text"])
    parts: list[str] = []
    for output in response.get("output", []):
        for content in output.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts)


def _parse_jsonish(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def call_openai(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": settings.openai_model,
        "reasoning": {"effort": settings.openai_reasoning_effort},
        "max_output_tokens": settings.openai_max_output_tokens,
        "input": [
            {"role": "system", "content": analysis_system_prompt(settings)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=settings.openai_timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    output_text = _extract_output_text(data)
    return _parse_jsonish(output_text)


def _extract_gemini_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in response.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            if part.get("text"):
                parts.append(str(part["text"]))
    return "\n".join(parts)


def call_gemini(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = "\n".join(
        [
            analysis_system_prompt(settings),
            "",
            "Analyze only the JSON item below and return final JSON matching this shape:",
            '{"alert": true, "company": "...", "ticker": "...", "event": "...", '
            '"event_type": "...", "why_it_matters": ["...", "..."], '
            '"thesis_impact": "bullish|bearish|mixed|neutral|unknown", '
            '"urgency": "immediate|today|this_week|watch_only", '
            '"confidence": "high|medium|low", "uncertainty": "...", "source": "..."}',
            "",
            json.dumps(payload, ensure_ascii=False),
        ]
    )
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": settings.gemini_max_output_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    model = urllib.parse.quote(settings.gemini_model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=settings.gemini_timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    output_text = _extract_gemini_output_text(data)
    if not output_text.strip():
        raise RuntimeError("Gemini returned no text")
    return _parse_jsonish(output_text)


def call_codex(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = "\n".join(
        [
            analysis_system_prompt(settings),
            "",
            "Do not browse. Do not run shell commands.",
            "Analyze only the JSON item below and return final JSON matching this shape:",
            '{"alert": true, "company": "...", "ticker": "...", "event": "...", '
            '"event_type": "...", "why_it_matters": ["...", "..."], '
            '"thesis_impact": "bullish|bearish|mixed|neutral|unknown", '
            '"urgency": "immediate|today|this_week|watch_only", '
            '"confidence": "high|medium|low", "uncertainty": "...", "source": "..."}',
            "",
            json.dumps(payload, ensure_ascii=False),
        ]
    )
    with tempfile.TemporaryDirectory(prefix="company-watch-codex-") as tmp:
        output_path = Path(tmp) / "last_message.txt"
        cmd = [
            settings.codex_command,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-m",
            settings.codex_model,
            "-c",
            f'model_reasoning_effort={json.dumps(settings.codex_reasoning_effort)}',
            "-o",
            str(output_path),
            "-",
        ]
        if settings.codex_profile:
            cmd[2:2] = ["--profile", settings.codex_profile]
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=settings.codex_timeout_seconds,
                check=False,
            )
        except OSError as exc:
            raise RuntimeError("Could not start Codex. Run python3 -m scripts.check_analysis "
                               "and check CODEX_COMMAND and executable permissions.") from exc
        if output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="replace")
        else:
            text = proc.stdout
        if proc.returncode != 0 and not text.strip():
            raise RuntimeError((proc.stderr or proc.stdout or "codex exec failed")[:1000])
        return _parse_jsonish(text)


def analyze_item(db: Database, settings: Settings, item: dict[str, Any]) -> dict[str, Any]:
    analysis_input = {
        "source_id": item.get("source_id"),
        "ticker": item.get("ticker"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "summary": item.get("summary"),
        "source_text_excerpt": (item.get("raw_text") or "")[:12000],
        "event_type": item.get("event_type"),
        "event_score": item.get("event_score"),
        "watch_profile": settings.watch_profile,
    }
    provider, model, reasoning_effort = analysis_configuration(settings)
    job_id = db.create_analysis_job(
        raw_item_id=int(item["id"]),
        prompt_version=PROMPT_VERSION,
        model=model,
        reasoning_effort=reasoning_effort,
        input_payload=analysis_input,
    )
    try:
        issues = analysis_setup_issues(settings)
        if issues:
            raise RuntimeError(" ".join(issues))
        if provider == "openai_api":
            output = call_openai(settings, analysis_input)
        elif provider == "gemini":
            output = call_gemini(settings, analysis_input)
        else:
            output = call_codex(settings, analysis_input)
        db.finish_analysis_job(job_id, output)
        return output
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        output = _fallback_analysis(item)
        output["uncertainty"] = f"{settings.analysis_provider} analysis failed; local fallback used: {exc}"
        db.finish_analysis_job(job_id, output, error=str(exc))
        return output


def format_alert(analysis: dict[str, Any], item: dict[str, Any]) -> str:
    ticker = analysis.get("ticker") or item.get("ticker") or "UNKNOWN"
    company = analysis.get("company") or ticker
    bullets = analysis.get("why_it_matters") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    analysis_source = str(analysis.get("source") or "")
    item_source = item.get("url") or item.get("source_id")
    source = item_source if item.get("url") and not analysis_source.startswith(("http://", "https://")) else analysis_source or item_source
    bullet_text = "\n".join(f"- {str(b)[:240]}" for b in bullets[:4])
    return "\n".join(
        [
            f"COMPANY WATCH ALERT: {company} ({ticker})",
            "",
            f"Event: {analysis.get('event') or item.get('title')}",
            f"Impact: {analysis.get('thesis_impact', 'unknown')}",
            f"Urgency: {analysis.get('urgency', 'watch_only')}",
            "Why:",
            bullet_text or "- Material company event detected.",
            "",
            f"Confidence: {analysis.get('confidence', 'unknown')}",
            f"Uncertainty: {analysis.get('uncertainty', 'source review required')}",
            f"Source: {source}",
        ]
    )
