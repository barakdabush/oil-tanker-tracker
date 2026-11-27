#!/usr/bin/env python3
"""AI Market Briefing Agent runner (zero external pip dependencies).

1. Fetches unified analytics + market intel + news intel from:
   GET <API_BASE_URL>/api/agent-intel/context?days=<N>&top_events=<M>
2. Calls an OpenAI-compatible LLM (reusing existing repo secrets:
   OPENROUTER_API_KEY, GROQ_API_KEY, or GEMINI_API_KEY) to produce an
   executive summary, key points, and watch items.
3. Writes the Markdown report to --output (for $GITHUB_STEP_SUMMARY / optional issue).
4. Optionally posts the structured briefing to:
   POST <API_BASE_URL>/api/agent-intel/briefings
   so it appears directly inside the Oil Tanker Monitor UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


def resolve_api_base_url(explicit: str = "") -> str:
    """Resolve backend base URL from explicit arg, API_BASE_URL, HEALTHCHECK_URL, or SERVER_HOST."""
    raw = (explicit or os.getenv("API_BASE_URL", "")).strip().rstrip("/")
    if raw:
        return raw
    hc = os.getenv("HEALTHCHECK_URL", "").strip()
    if hc:
        parsed = urllib.parse.urlparse(hc)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    host = os.getenv("SERVER_HOST", "").strip()
    if host:
        return f"http://{host}:8000"
    return ""


def resolve_llm_providers(preferred: str = "auto") -> list[tuple[str, str, str, str]]:
    """Return ordered list of (provider_name, url, api_key, model) candidates.

    Priority in ``auto`` mode: Gemini (paid/funded) -> OpenRouter -> Groq.
    If ``preferred`` is a specific provider name, only that provider is returned.
    """
    explicit_url = os.getenv("AGENT_BASE_URL", "").strip()
    explicit_model = os.getenv("AGENT_MODEL", "").strip()
    pref = (preferred or "auto").lower().strip()

    candidates: list[tuple[str, str, str, str]] = []
    if os.getenv("GEMINI_API_KEY", "").strip():
        candidates.append((
            "gemini",
            explicit_url or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            os.getenv("GEMINI_API_KEY", "").strip(),
            explicit_model or "gemini-2.5-flash",
        ))
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        candidates.append((
            "openrouter",
            explicit_url or "https://openrouter.ai/api/v1/chat/completions",
            os.getenv("OPENROUTER_API_KEY", "").strip(),
            explicit_model or "openrouter/free",
        ))
    if os.getenv("GROQ_API_KEY", "").strip():
        candidates.append((
            "groq",
            explicit_url or "https://api.groq.com/openai/v1/chat/completions",
            os.getenv("GROQ_API_KEY", "").strip(),
            explicit_model or "llama-3.3-70b-versatile",
        ))

    if pref != "auto":
        return [c for c in candidates if c[0] == pref]
    return candidates


def resolve_llm_provider() -> Optional[tuple[str, str, str]]:
    """Backwards-compatible helper returning the top (url, api_key, model) provider."""
    providers = resolve_llm_providers("auto")
    if not providers:
        return None
    _, url, key, model = providers[0]
    return (url, key, model)


def http_json(
    url: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 45,
) -> dict:
    req_headers = {"User-Agent": "oil-tanker-tracker-market-agent/1.0", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body.strip() else {}


def parse_briefing_output(raw_text: str) -> tuple[str, dict]:
    """Split markdown report and trailing BRIEFING_JSON object."""
    text = (raw_text or "").strip()
    parsed: dict = {}

    match = re.search(r"BRIEFING_JSON:\s*(\{[^\n]*\})\s*$", text, re.MULTILINE)
    if match:
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            pass
        text = text[: match.start()].strip()

    bias = str(parsed.get("market_bias") or "neutral").lower().strip()
    if bias not in ("bullish", "bearish", "neutral", "mixed"):
        bias = "neutral"

    conf_raw = parsed.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf_raw))) if conf_raw is not None else None
    except (TypeError, ValueError):
        conf = None

    summary = str(parsed.get("executive_summary") or "").strip()
    if not summary:
        # Fallback: first non-heading paragraph of markdown
        paras = [p.strip() for p in text.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        summary = paras[0][:1000] if paras else "Automated market briefing generated."

    key_points = [str(x).strip() for x in (parsed.get("key_points") or []) if str(x).strip()]
    watch_items = [str(x).strip() for x in (parsed.get("watch_items") or []) if str(x).strip()]

    structured = {
        "market_bias": bias,
        "confidence": conf,
        "executive_summary": summary,
        "key_points": key_points,
        "watch_items": watch_items,
    }
    return text, structured


def build_deterministic_fallback(ctx: dict) -> tuple[str, dict]:
    """Fallback summary if no LLM API key is configured, so the workflow never fails hard."""
    prices = (ctx.get("market_intel") or {}).get("prices") or {}
    analytics = ctx.get("analytics") or {}
    news = ctx.get("news_intel") or {}

    brent = prices.get("brent_usd")
    wti = prices.get("wti_usd")
    brent_1d = prices.get("brent_change_1d_pct")
    pressure = news.get("pressure_label") or "Balanced / Neutral"
    net_p = news.get("net_pressure_24h", 0.0)

    bias = "bullish" if net_p >= 0.4 else ("bearish" if net_p <= -0.4 else "neutral")
    summary = (
        f"Brent sits at ${brent if brent is not None else 'N/A'} "
        f"({f'{brent_1d:+.2f}%' if brent_1d is not None else 'n/a'} 1d) and WTI at "
        f"${wti if wti is not None else 'N/A'}. News flow over the last 24h shows "
        f"{news.get('events_24h', 0)} clustered events ({pressure}, score {net_p:+.2f}) "
        f"with {analytics.get('total_vessels', 0)} tracked tankers and "
        f"{analytics.get('currently_dark', 0)} active dark-fleet alerts."
    )
    key_points = [
        f"Fleet status: {analytics.get('in_transit', 0)} in transit, {analytics.get('at_port', 0)} at port, {analytics.get('currently_dark', 0)} dark, {analytics.get('ongoing_sts', 0)} ongoing STS.",
        f"24h News Intel: {news.get('events_24h', 0)} events ({news.get('high_impact_events_24h', 0)} high-rank), net pressure {net_p:+.2f} ({pressure}).",
    ]
    top_evs = news.get("top_events") or []
    if top_evs:
        top = top_evs[0]
        key_points.append(
            f"Top ranked event (Rank {round(top.get('prior_rank_score', 0))}): {top.get('headline')}."
        )
    watch_items = [
        "Configure OPENROUTER_API_KEY, GROQ_API_KEY, or GEMINI_API_KEY in GitHub Secrets for full AI synthesis."
    ]
    md = (
        "### Executive Summary\n\n"
        + summary
        + "\n\n### Key Points\n\n"
        + "\n".join(f"- {p}" for p in key_points)
        + "\n\n### What to Watch Next\n\n"
        + "\n".join(f"- {w}" for w in watch_items)
    )
    return md, {
        "market_bias": bias,
        "confidence": None,
        "executive_summary": summary,
        "key_points": key_points,
        "watch_items": watch_items,
    }


def call_llm_analyst(
    prompt_md: str,
    ctx: dict,
    provider: tuple[str, str, str],
    retry_delays: tuple[float, ...] = (8.0, 20.0),
) -> tuple[str, dict]:
    url, api_key, model = provider
    user_content = (
        "Analyze the following Oil Tanker Monitor intelligence snapshot and produce the briefing:\n\n"
        "<market_intel_context>\n"
        + json.dumps(ctx, indent=2, default=str)[:28000]
        + "\n</market_intel_context>"
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": prompt_md},
            {"role": "user", "content": user_content},
        ],
    }

    last_err: Optional[Exception] = None
    for attempt in range(len(retry_delays) + 1):
        try:
            resp = http_json(
                url,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                payload=payload,
                timeout=90,
            )
            choices = resp.get("choices") or []
            if not choices:
                raise RuntimeError(f"LLM returned no choices: {resp}")
            content = str((choices[0].get("message") or {}).get("content") or "").strip()
            usage = resp.get("usage") or {}
            return content, {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or (int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0))),
            }
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {e.code} {e.reason}: {err_body}")
            if e.code in (429, 500, 502, 503, 504) and attempt < len(retry_delays):
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait_s = min(max(float(retry_after), 2.0), 45.0) if retry_after else retry_delays[attempt]
                except (TypeError, ValueError):
                    wait_s = retry_delays[attempt]
                print(f"Transient HTTP {e.code} from {model}; retrying in {wait_s:.0f}s ...", file=sys.stderr)
                time.sleep(wait_s)
                continue
            raise last_err from e
        except Exception as e:
            last_err = e
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
                continue
            raise
    assert last_err is not None
    raise last_err


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Oil Tanker Monitor AI Market Briefing Agent")
    parser.add_argument("--api-base-url", default="", help="Backend base URL (e.g. http://<host>:8000)")
    parser.add_argument("--days", type=int, default=14, help="Lookback window in days")
    parser.add_argument("--top-events", type=int, default=15, help="Top ranked news events to include")
    parser.add_argument("--provider", default="auto", choices=["auto", "gemini", "openrouter", "groq"])
    parser.add_argument("--prompt-file", default=".github/agents/market_analyst.md")
    parser.add_argument("--output", default="briefing.md")
    parser.add_argument("--post-briefing", action="store_true", help="POST briefing back to /api/agent-intel/briefings")
    args = parser.parse_args()

    base_url = resolve_api_base_url(args.api_base_url)
    if not base_url:
        print("No API_BASE_URL, HEALTHCHECK_URL, or SERVER_HOST configured; skipping briefing.", file=sys.stderr)
        Path(args.output).write_text(
            "### ⚠️ Briefing Skipped\n\nConfigure `API_BASE_URL` (repo variable) or `SERVER_HOST` (repo secret) so the GitHub Action can reach `/api/agent-intel/context`.\n",
            encoding="utf-8",
        )
        return 0

    ctx_url = f"{base_url}/api/agent-intel/context?days={args.days}&top_events={args.top_events}"
    print(f"Fetching intelligence context from {ctx_url} ...")
    ctx = http_json(ctx_url, method="GET", timeout=120)

    providers = resolve_llm_providers(args.provider)
    model_used = "deterministic-fallback"
    markdown = ""
    structured: dict = {}
    usage_stats: dict = {}

    if not providers:
        print(f"No LLM API key found for provider={args.provider}; using deterministic fallback.")
        markdown, structured = build_deterministic_fallback(ctx)
    else:
        prompt_md = Path(args.prompt_file).read_text(encoding="utf-8")
        succeeded = False
        for pname, url, api_key, model in providers:
            print(f"Calling AI Market Analyst [{pname}] ({model}) ...")
            try:
                raw_reply, usage_stats = call_llm_analyst(prompt_md, ctx, (url, api_key, model))
                markdown, structured = parse_briefing_output(raw_reply)
                model_used = model
                succeeded = True
                break
            except Exception as e:
                print(f"Provider [{pname}] ({model}) failed: {e}", file=sys.stderr)
        if not succeeded:
            print("All configured LLM providers failed; falling back to deterministic briefing.", file=sys.stderr)
            markdown, structured = build_deterministic_fallback(ctx)

    tok_part = ""
    if usage_stats.get("total_tokens"):
        tok_part = (
            f" · **Tokens:** `{usage_stats['total_tokens']:,}` "
            f"(`{usage_stats['prompt_tokens']:,}` in / `{usage_stats['completion_tokens']:,}` out)"
        )

    header = (
        f"## 🛢️ Oil Tanker Monitor — AI Market Briefing\n\n"
        f"**Bias:** `{structured['market_bias'].upper()}` · **Model:** `{model_used}` · "
        f"**Window:** `{args.days}d`{tok_part} · **Generated:** `{ctx.get('generated_at', '')}`\n\n"
    )
    full_md = header + markdown + "\n"
    Path(args.output).write_text(full_md, encoding="utf-8")
    print(f"Wrote briefing to {args.output} ({len(full_md)} chars).")

    if args.post_briefing:
        post_url = f"{base_url}/api/agent-intel/briefings"
        post_headers: dict[str, str] = {}
        token = os.getenv("AGENT_BRIEFING_TOKEN", "").strip()
        if token:
            post_headers["Authorization"] = f"Bearer {token}"
        body = {
            "window_days": args.days,
            "model_used": model_used,
            "market_bias": structured["market_bias"],
            "confidence": structured["confidence"],
            "executive_summary": structured["executive_summary"],
            "key_points": structured["key_points"],
            "watch_items": structured["watch_items"],
            "full_markdown": full_md,
            "context_snapshot": {
                "generated_at": ctx.get("generated_at"),
                "prices": (ctx.get("market_intel") or {}).get("prices"),
                "events_24h": (ctx.get("news_intel") or {}).get("events_24h"),
                "net_pressure_24h": (ctx.get("news_intel") or {}).get("net_pressure_24h"),
            },
        }
        try:
            saved = http_json(post_url, method="POST", payload=body, headers=post_headers, timeout=30)
            print(f"Stored briefing id={saved.get('id')} at {post_url}.")
        except Exception as e:
            print(f"Warning: failed to POST briefing back to {post_url}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
