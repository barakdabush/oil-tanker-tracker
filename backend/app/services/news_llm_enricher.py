"""Strictly optional LLM event enricher for top-ranked news clusters.

Design guarantees:
- If ``NEWS_LLM_ENABLED`` is False or ``NEWS_LLM_API_KEY`` is missing/blank, this service
  cleanly no-ops with zero errors — the entire news & price-learning pipeline runs 100%
  locally on rule + statistical scoring.
- Supports both OpenAI-compatible Chat Completions (``NEWS_LLM_PROVIDER="openai"``) and
  Google Gemini REST API (``NEWS_LLM_PROVIDER="gemini"``) via plain ``httpx`` (no extra SDKs).
- Only enriches top clusters (``prior_rank_score >= NEWS_LLM_MIN_RANK``) up to a strict daily
  budget (``NEWS_LLM_MAX_EVENTS_PER_DAY``).
- Any API error, quota limit, timeout, or malformed JSON is caught and logged as a warning so
  it can never crash or block the scheduler worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.news import NewsEvent
from app.services.news_clusterer import EVENT_CATEGORIES, compute_prior_rank

logger = logging.getLogger(__name__)

RUN_INTERVAL_SECONDS = 30 * 60
VALID_CATEGORIES = set(EVENT_CATEGORIES.keys())

_SYSTEM_PROMPT = (
    "You are a senior crude oil & maritime shipping market analyst. "
    "Given a clustered news event headline and summary, return ONLY a valid JSON object "
    "with these exact keys:\n"
    "- event_type: one of " + ", ".join(sorted(VALID_CATEGORIES)) + "\n"
    "- direction: float in [-1.0, 1.0] (+1.0 strongly bullish for Brent/WTI oil price, -1.0 strongly bearish, 0.0 neutral)\n"
    "- severity: float in [0.0, 1.0] (market supply/demand disruption severity)\n"
    "- confidence: float in [0.0, 1.0]\n"
    "- estimated_mbpd_at_risk: float or null (estimated million barrels/day affected, if applicable)\n"
    "- rationale: concise 1-sentence explanation of why and on what time horizon this affects oil prices."
)


def is_llm_configured() -> bool:
    """Return True only when optional LLM enrichment is explicitly enabled AND has an API key."""
    return bool(
        settings.NEWS_LLM_ENABLED
        and settings.NEWS_LLM_API_KEY
        and settings.NEWS_LLM_API_KEY.strip()
    )


def parse_llm_json(raw_text: str) -> Optional[dict]:
    """Safely parse and validate structured JSON returned by an LLM."""
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    try:
        direction = max(-1.0, min(1.0, float(obj.get("direction", 0.0))))
        severity = max(0.0, min(1.0, float(obj.get("severity", 0.5))))
        confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
    except (TypeError, ValueError):
        return None

    ev_type = obj.get("event_type")
    if ev_type not in VALID_CATEGORIES:
        ev_type = None

    mbpd = obj.get("estimated_mbpd_at_risk")
    try:
        mbpd_val = float(mbpd) if mbpd is not None else None
    except (TypeError, ValueError):
        mbpd_val = None

    rationale = str(obj.get("rationale") or "").strip()[:500] or None
    return {
        "event_type": ev_type,
        "direction": round(direction, 3),
        "severity": round(severity, 3),
        "confidence": round(confidence, 3),
        "estimated_mbpd": mbpd_val,
        "rationale": rationale,
    }


class NewsLLMEnricher:
    """Optional background enricher that upgrades top-ranked events when an LLM key is provided."""

    def __init__(self):
        self._logged_disabled = False

    async def run_periodic(self):
        await asyncio.sleep(50)
        while True:
            try:
                if not is_llm_configured():
                    if not self._logged_disabled:
                        logger.info(
                            "NewsLLMEnricher inactive (NEWS_LLM_ENABLED=false or NEWS_LLM_API_KEY not set); "
                            "running in zero-cost local rule + empirical lag mode."
                        )
                        self._logged_disabled = True
                else:
                    await self.enrich_pending_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"NewsLLMEnricher run failed (non-fatal): {type(e).__name__}: {e}")
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def _call_llm(self, client: httpx.AsyncClient, event: NewsEvent) -> Optional[dict]:
        user_msg = (
            f"Headline: {event.headline}\n"
            f"Summary: {event.summary or 'N/A'}\n"
            f"Chokepoints: {', '.join(event.chokepoints or []) or 'None'}\n"
            f"Rule Category: {event.event_type} | Sources: {event.distinct_sources}"
        )
        provider = (settings.NEWS_LLM_PROVIDER or "openai").lower().strip()
        api_key = settings.NEWS_LLM_API_KEY.strip()

        if provider == "gemini":
            model = (settings.NEWS_LLM_MODEL or "gemini-2.0-flash").strip()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            resp = await client.post(
                url,
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": f"{_SYSTEM_PROMPT}\n\n{user_msg}"}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
            raw_text = parts[0].get("text", "") if parts else ""
            return parse_llm_json(raw_text)

        # Default: OpenAI-compatible Chat Completions API
        model = (settings.NEWS_LLM_MODEL or "gpt-4o-mini").strip()
        base_url = (settings.NEWS_LLM_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        raw_text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        return parse_llm_json(raw_text)

    async def enrich_pending_events(self, limit: int = 10) -> int:
        """Enrich top-ranked unenriched events within the configured daily cap. No-ops if unconfigured."""
        if not is_llm_configured():
            return 0

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)

        async with async_session() as session:
            enriched_today = (
                await session.execute(
                    select(func.count())
                    .select_from(NewsEvent)
                    .where(NewsEvent.llm_enriched_at >= cutoff_24h)
                )
            ).scalar() or 0

            remaining_budget = max(0, settings.NEWS_LLM_MAX_EVENTS_PER_DAY - enriched_today)
            if remaining_budget == 0:
                return 0

            events = list(
                (
                    await session.execute(
                        select(NewsEvent)
                        .where(
                            NewsEvent.llm_enriched_at.is_(None),
                            NewsEvent.prior_rank_score >= settings.NEWS_LLM_MIN_RANK,
                        )
                        .order_by(NewsEvent.prior_rank_score.desc(), NewsEvent.first_seen_at.desc())
                        .limit(min(limit, remaining_budget))
                    )
                ).scalars().all()
            )
            if not events:
                return 0

            enriched_count = 0
            async with httpx.AsyncClient(timeout=25.0) as client:
                for ev in events:
                    try:
                        parsed = await self._call_llm(client, ev)
                    except Exception as e:
                        logger.warning(f"NewsLLMEnricher API call failed for event {ev.id}: {type(e).__name__}: {e}")
                        break  # stop batch on quota/auth/network error

                    if not parsed:
                        continue

                    if parsed["event_type"]:
                        ev.event_type = parsed["event_type"]
                    ev.llm_direction = parsed["direction"]
                    ev.llm_severity = parsed["severity"]
                    ev.llm_confidence = parsed["confidence"]
                    ev.llm_estimated_mbpd = parsed["estimated_mbpd"]
                    ev.llm_rationale = parsed["rationale"]
                    ev.llm_enriched_at = now

                    # Confidence-weighted blend between local rules and LLM output
                    w = 0.4 + 0.5 * parsed["confidence"]
                    ev.effective_direction = round(w * parsed["direction"] + (1.0 - w) * ev.rule_direction, 3)
                    ev.effective_severity = round(w * parsed["severity"] + (1.0 - w) * ev.rule_severity, 3)
                    ev.prior_rank_score = compute_prior_rank(
                        max_source_tier=ev.max_source_tier,
                        distinct_sources=ev.distinct_sources,
                        chokepoints=ev.chokepoints or [],
                        severity=ev.effective_severity,
                        novelty_score=ev.novelty_score,
                        llm_confidence=ev.llm_confidence,
                    )
                    enriched_count += 1

            await session.commit()

        if enriched_count:
            logger.info(f"NewsLLMEnricher: enriched {enriched_count} high-rank event(s).")
        return enriched_count
