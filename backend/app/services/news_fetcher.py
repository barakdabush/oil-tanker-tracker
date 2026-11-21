"""Free-source maritime & oil news ingestion + canonical event clustering.

Sources (100% free, zero API keys required):
- Tier 1: Straits.live maritime events API, EIA Today in Energy RSS, OPEC Press Releases RSS
- Tier 2: gCaptain RSS, Splash247 RSS, Hellenic Shipping News RSS, OilPrice.com RSS, Rigzone RSS
- Tier 3: GDELT DOC 2.0 `artlist` API (spaced out with 429 retry/backoff)

Pipeline:
1. Fetch raw items from all configured free sources.
2. Deduplicate by normalized `url_hash` against `news_articles`.
3. Filter & classify via `news_clusterer.classify_article`.
4. Match against existing `news_events` within a 36h rolling window or create a new canonical event.
5. Recompute `novelty_score`, `distinct_sources`, `max_source_tier`, and `prior_rank_score`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
from sqlalchemy import delete, select, update

from app.config import settings
from app.database import async_session
from app.models.news import NewsArticle, NewsEvent
from app.services.news_clusterer import (
    align_to_market_time,
    classify_article,
    compute_cluster_similarity,
    compute_prior_rank,
    make_cluster_key,
    normalize_url,
    tokenize_for_clustering,
    url_hash,
)

logger = logging.getLogger(__name__)

CLUSTER_WINDOW_HOURS = 36
CLUSTER_SIMILARITY_THRESHOLD = 0.36
NOVELTY_LOOKBACK_HOURS = 72
GDELT_MIN_INTERVAL_SECONDS = 30 * 60
RATE_LIMIT_COOLDOWN_SECONDS = 60 * 60

STRAITS_EVENTS_URL = "https://straits.live/api/v1/events?limit=200"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Curated free RSS feeds: (source_name, source_tier, url)
RSS_FEEDS: list[tuple[str, int, str]] = [
    ("EIA Today in Energy", 1, "https://www.eia.gov/rss/todayinenergy.xml"),
    ("OPEC Press Releases", 1, "https://www.opec.org/opec_web/en/press_room/28.htm?format=rss"),
    ("gCaptain", 2, "https://gcaptain.com/feed/"),
    ("Splash247", 2, "https://splash247.com/feed/"),
    ("OilPrice.com", 2, "https://oilprice.com/rss/main"),
    ("Hellenic Shipping News", 2, "https://www.hellenicshippingnews.com/category/shipping-news/tanker-shipping-news/feed/"),
    ("Rigzone", 2, "https://www.rigzone.com/news/rss/rigzone_latest.aspx"),
]

# Focused GDELT DOC 2.0 queries (spaced >= 6s apart to respect GDELT's ~1 req / 5s limit)
GDELT_QUERIES: list[tuple[str, str]] = [
    (
        "chokepoints",
        '("Strait of Hormuz" OR "Bab el-Mandeb" OR "Suez Canal" OR "Strait of Malacca" OR "Panama Canal" OR "Bosphorus") '
        '(tanker OR oil OR shipping OR attack OR blockade OR congestion) sourcelang:english',
    ),
    (
        "opec_supply",
        '(OPEC OR "crude oil" OR "Brent crude" OR "refinery outage" OR "oil pipeline") '
        '(cut OR hike OR outage OR sanction OR "shadow fleet" OR "dark fleet" OR tanker) sourcelang:english',
    ),
]

_RE_HTML_TAGS = re.compile(r"<[^>]+>")


@dataclass
class RawNewsItem:
    url: str
    source: str
    source_tier: int
    title: str
    summary: str
    published_at: datetime
    gdelt_tone: Optional[float] = None


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _RE_HTML_TAGS.sub(" ", text)).strip()[:1200]


def _parse_datetime(val) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        ts = val / 1000.0 if val > 1e12 else float(val)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        # GDELT format: YYYYMMDDTHHMMSSZ
        if len(s) >= 15 and s[:8].isdigit() and s[8] == "T" and s[9:15].isdigit():
            try:
                return datetime(
                    int(s[0:4]), int(s[4:6]), int(s[6:8]),
                    int(s[9:11]), int(s[11:13]), int(s[13:15]),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(s)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def parse_rss_xml(xml_text: str, source_name: str, source_tier: int) -> list[RawNewsItem]:
    """Parse RSS 2.0 or Atom XML into RawNewsItem list using stdlib ElementTree."""
    items: list[RawNewsItem] = []
    if not xml_text or "<" not in xml_text:
        return items
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return items

    now = datetime.now(timezone.utc)

    # RSS 2.0: channel/item
    for item in root.findall(".//item"):
        title = _strip_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or "")
        pub = (
            _parse_datetime(item.findtext("pubDate"))
            or _parse_datetime(item.findtext("{http://purl.org/dc/elements/1.1/}date"))
            or now
        )
        if title and link:
            items.append(RawNewsItem(
                url=link,
                source=source_name,
                source_tier=source_tier,
                title=title,
                summary=desc,
                published_at=pub,
            ))

    # Atom: {http://www.w3.org/2005/Atom}entry
    if not items:
        atom_ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f".//{atom_ns}entry"):
            title = _strip_html(entry.findtext(f"{atom_ns}title") or "")
            link_el = entry.find(f"{atom_ns}link")
            link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
            summary = _strip_html(
                entry.findtext(f"{atom_ns}summary")
                or entry.findtext(f"{atom_ns}content")
                or ""
            )
            pub = (
                _parse_datetime(entry.findtext(f"{atom_ns}published"))
                or _parse_datetime(entry.findtext(f"{atom_ns}updated"))
                or now
            )
            if title and link:
                items.append(RawNewsItem(
                    url=link,
                    source=source_name,
                    source_tier=source_tier,
                    title=title,
                    summary=summary,
                    published_at=pub,
                ))
    return items


class NewsFetcher:
    """Periodic free-source oil & maritime news fetcher and event clusterer."""

    def __init__(self):
        self._source_cooldowns: dict[str, datetime] = {}
        self._last_gdelt_fetch: Optional[datetime] = None

    def _is_in_cooldown(self, source_key: str, now: datetime) -> bool:
        until = self._source_cooldowns.get(source_key)
        return until is not None and now < until

    def _set_cooldown(self, source_key: str, now: datetime, retry_after_s: Optional[float] = None) -> None:
        seconds = max(float(RATE_LIMIT_COOLDOWN_SECONDS), retry_after_s or 0.0)
        seconds = min(seconds, 4 * 3600.0)
        self._source_cooldowns[source_key] = now + timedelta(seconds=seconds)

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> Optional[float]:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(1.0, float(raw.strip()))
        except ValueError:
            return None

    async def _get_with_rate_limit_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Optional[dict] = None,
        backoff_delays: tuple[float, ...] = (10.0, 20.0, 40.0),
    ) -> httpx.Response:
        resp: Optional[httpx.Response] = None
        for attempt in range(len(backoff_delays) + 1):
            resp = await client.get(url, params=params)
            if resp.status_code != 429 or attempt == len(backoff_delays):
                break
            retry_after = self._parse_retry_after(resp)
            delay = min(max(retry_after or backoff_delays[attempt], 5.0), 60.0)
            await asyncio.sleep(delay)
        assert resp is not None
        resp.raise_for_status()
        return resp

    async def run_periodic(self):
        if not settings.NEWS_FETCH_ENABLED:
            logger.info("NewsFetcher disabled (NEWS_FETCH_ENABLED=false).")
            return
        await asyncio.sleep(35)
        interval_s = max(5, settings.NEWS_FETCH_INTERVAL_MINUTES) * 60
        while True:
            try:
                await self.fetch_and_store(timespan="3d")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"NewsFetcher error: {type(e).__name__}: {e}")
                await asyncio.sleep(5 * 60)
                continue
            await asyncio.sleep(interval_s)

    async def _fetch_rss_feed(
        self, client: httpx.AsyncClient, source_name: str, tier: int, url: str
    ) -> list[RawNewsItem]:
        resp = await self._get_with_rate_limit_retry(
            client, url, backoff_delays=(5.0, 15.0)
        )
        return parse_rss_xml(resp.text, source_name, tier)

    async def _fetch_straits_events(self, client: httpx.AsyncClient) -> list[RawNewsItem]:
        resp = await self._get_with_rate_limit_retry(
            client, STRAITS_EVENTS_URL, backoff_delays=(5.0, 15.0)
        )
        payload = resp.json()
        items: list[RawNewsItem] = []
        for ev in payload.get("events") or []:
            ev_id = ev.get("id") or ev.get("eventId") or ""
            title = (ev.get("title") or ev.get("summary") or ev.get("type") or "").strip()
            desc = (ev.get("description") or ev.get("details") or ev.get("summary") or "").strip()
            pub = _parse_datetime(ev.get("occurredAt") or ev.get("date") or ev.get("createdAt"))
            if not title or not pub:
                continue
            link = ev.get("url") or ev.get("sourceUrl") or f"https://straits.live/events/{ev_id or pub.isoformat()}"
            # Ensure chokepoint context is explicit for Straits.live Hormuz/Gulf incidents
            if "hormuz" not in title.lower() and "red sea" not in title.lower():
                title = f"Strait of Hormuz maritime incident: {title}"
            items.append(RawNewsItem(
                url=link,
                source="Straits.live",
                source_tier=1,
                title=title,
                summary=desc,
                published_at=pub,
            ))
        return items

    async def _fetch_gdelt_articles(
        self, client: httpx.AsyncClient, query: str, timespan: str = "3d", max_records: int = 75
    ) -> list[RawNewsItem]:
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(max_records),
            "timespan": timespan,
            "format": "json",
            "sort": "DateDesc",
        }
        resp = await self._get_with_rate_limit_retry(
            client, GDELT_DOC_URL, params=params, backoff_delays=(10.0, 20.0, 40.0)
        )
        data = resp.json()
        items: list[RawNewsItem] = []
        for art in data.get("articles") or []:
            url = (art.get("url") or "").strip()
            title = (art.get("title") or "").strip()
            pub = _parse_datetime(art.get("seendate"))
            if not url or not title or not pub:
                continue
            domain = (art.get("domain") or "GDELT").strip()
            tone_raw = art.get("tone")
            tone: Optional[float] = None
            if tone_raw is not None:
                try:
                    tone = float(str(tone_raw).split(",")[0])
                except (ValueError, TypeError):
                    tone = None
            # Promote known Tier-1/2 maritime & wire domains even when discovered via GDELT
            dom_l = domain.lower()
            if any(d in dom_l for d in ("reuters.com", "apnews.com", "eia.gov", "opec.org", "ukmto.org")):
                tier = 1
            elif any(d in dom_l for d in ("gcaptain.com", "splash247.com", "oilprice.com", "lloydslist", "tradewinds", "rigzone.com", "hellenicshippingnews")):
                tier = 2
            else:
                tier = 3
            items.append(RawNewsItem(
                url=url,
                source=domain,
                source_tier=tier,
                title=title,
                summary="",
                published_at=pub,
                gdelt_tone=tone,
            ))
        return items

    def _handle_fetch_error(self, source_key: str, label: str, exc: Exception, now: datetime) -> None:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            retry_after = self._parse_retry_after(exc.response)
            self._set_cooldown(source_key, now, retry_after)
            logger.warning(f"{label} rate-limited (429); cooling down for >=60m.")
        else:
            logger.warning(f"{label} failed: {type(exc).__name__}: {exc}")

    async def fetch_and_store(self, timespan: str = "3d", include_rss: bool = True) -> dict[str, int]:
        """Fetch free news sources, deduplicate articles, and cluster into canonical events."""
        raw_items: list[RawNewsItem] = []
        now = datetime.now(timezone.utc)
        headers = {
            "User-Agent": "oil-tanker-tracker/1.0 (https://github.com/barakdabush/oil-tanker-tracker)",
            "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
        }

        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            if include_rss:
                for idx, (source_name, tier, url) in enumerate(RSS_FEEDS):
                    key = f"rss:{source_name}"
                    if self._is_in_cooldown(key, now):
                        continue
                    if idx > 0:
                        await asyncio.sleep(0.5)
                    try:
                        fetched = await self._fetch_rss_feed(client, source_name, tier, url)
                        raw_items.extend(fetched)
                    except Exception as e:
                        self._handle_fetch_error(key, f"NewsFetcher RSS[{source_name}]", e, now)

                if not self._is_in_cooldown("straits", now):
                    try:
                        raw_items.extend(await self._fetch_straits_events(client))
                    except Exception as e:
                        self._handle_fetch_error("straits", "NewsFetcher[Straits.live]", e, now)

            gdelt_due = (
                self._last_gdelt_fetch is None
                or (now - self._last_gdelt_fetch).total_seconds() >= GDELT_MIN_INTERVAL_SECONDS
            )
            if gdelt_due:
                self._last_gdelt_fetch = now
                for idx, (qname, qtext) in enumerate(GDELT_QUERIES):
                    key = f"gdelt:{qname}"
                    if self._is_in_cooldown(key, now):
                        continue
                    if idx > 0:
                        await asyncio.sleep(10.0)
                    try:
                        raw_items.extend(await self._fetch_gdelt_articles(client, qtext, timespan=timespan))
                    except Exception as e:
                        self._handle_fetch_error(key, f"NewsFetcher GDELT[{qname}]", e, now)

        stats = await self.ingest_items(raw_items)
        logger.info(
            f"NewsFetcher complete: fetched={len(raw_items)}, "
            f"new_articles={stats['new_articles']}, new_events={stats['new_events']}, "
            f"clustered_updates={stats['clustered_updates']}"
        )
        return stats

    async def ingest_items(self, raw_items: list[RawNewsItem]) -> dict[str, int]:
        """Deduplicate, classify, cluster, and rank a list of RawNewsItem objects."""
        if not raw_items:
            return {"new_articles": 0, "new_events": 0, "clustered_updates": 0}

        # Sort oldest -> newest so first_seen_at locks accurately to the earliest report
        ordered = sorted(raw_items, key=lambda x: x.published_at)

        # Deduplicate within the batch by url_hash
        batch_by_hash: dict[str, RawNewsItem] = {}
        for item in ordered:
            h = url_hash(item.url)
            if h and h not in batch_by_hash:
                batch_by_hash[h] = item

        hashes = list(batch_by_hash.keys())
        new_articles = 0
        new_events = 0
        clustered_updates = 0

        async with async_session() as session:
            existing_hashes = set(
                (
                    await session.execute(
                        select(NewsArticle.url_hash).where(NewsArticle.url_hash.in_(hashes))
                    )
                ).scalars().all()
            )

            # Load recent events for clustering & novelty checks
            min_pub = min(i.published_at for i in batch_by_hash.values())
            lookback_start = min_pub - timedelta(hours=NOVELTY_LOOKBACK_HOURS)
            recent_events: list[NewsEvent] = list(
                (
                    await session.execute(
                        select(NewsEvent)
                        .where(NewsEvent.first_seen_at >= lookback_start)
                        .order_by(NewsEvent.first_seen_at.desc())
                    )
                ).scalars().all()
            )

            # Precompute tokens & source sets for recent events
            event_tokens: dict[int, set[str]] = {
                ev.id: tokenize_for_clustering(f"{ev.headline} {ev.summary or ''}")
                for ev in recent_events
            }
            event_sources: dict[int, set[str]] = {}
            if recent_events:
                ev_ids = [ev.id for ev in recent_events]
                art_rows = (
                    await session.execute(
                        select(NewsArticle.event_id, NewsArticle.source)
                        .where(NewsArticle.event_id.in_(ev_ids))
                    )
                ).all()
                for eid, src in art_rows:
                    event_sources.setdefault(eid, set()).add(src)

            now = datetime.now(timezone.utc)

            for h, item in batch_by_hash.items():
                if h in existing_hashes:
                    continue

                target_event, created, updated = await self._assign_to_event(
                    session=session,
                    title=item.title,
                    summary=item.summary,
                    source=item.source,
                    source_tier=item.source_tier,
                    published_at=item.published_at,
                    gdelt_tone=item.gdelt_tone,
                    recent_events=recent_events,
                    event_tokens=event_tokens,
                    event_sources=event_sources,
                    now=now,
                )
                if created:
                    new_events += 1
                elif updated:
                    clustered_updates += 1

                # Always persist the raw article in Bronze (event_id=None if currently irrelevant)
                session.add(NewsArticle(
                    url_hash=h,
                    url=normalize_url(item.url),
                    source=item.source[:128],
                    source_tier=item.source_tier,
                    title=item.title[:500],
                    summary=(item.summary or None),
                    published_at=item.published_at,
                    gdelt_tone=item.gdelt_tone,
                    event_id=target_event.id if target_event is not None else None,
                ))
                existing_hashes.add(h)
                new_articles += 1

            await session.commit()

        return {
            "new_articles": new_articles,
            "new_events": new_events,
            "clustered_updates": clustered_updates,
        }

    async def _assign_to_event(
        self,
        session,
        title: str,
        summary: str,
        source: str,
        source_tier: int,
        published_at: datetime,
        gdelt_tone: Optional[float],
        recent_events: list[NewsEvent],
        event_tokens: dict[int, set[str]],
        event_sources: dict[int, set[str]],
        now: datetime,
        preserved_llm: Optional[dict[str, dict]] = None,
    ) -> tuple[Optional[NewsEvent], bool, bool]:
        """Classify and assign a single raw article into a canonical NewsEvent cluster.

        Returns (target_event_or_None, created_new, updated_existing).
        """
        cls = classify_article(title, summary or "", gdelt_tone=gdelt_tone)
        if not cls.relevant:
            return None, False, False

        art_tokens = tokenize_for_clustering(f"{title} {summary or ''}")
        best_ev: Optional[NewsEvent] = None
        best_sim = 0.0
        same_cat_recent = 0

        for ev in recent_events:
            delta_h = abs((published_at - ev.first_seen_at).total_seconds()) / 3600.0
            if ev.event_type == cls.event_type and delta_h <= NOVELTY_LOOKBACK_HOURS:
                same_cat_recent += 1
            if delta_h > CLUSTER_WINDOW_HOURS:
                continue
            sim = compute_cluster_similarity(
                art_tokens,
                event_tokens.get(ev.id, set()),
                cls.chokepoints,
                ev.chokepoints or [],
                cls.event_type,
                ev.event_type,
            )
            if sim > best_sim:
                best_sim = sim
                best_ev = ev

        if best_ev is not None and best_sim >= CLUSTER_SIMILARITY_THRESHOLD:
            best_ev.article_count = (best_ev.article_count or 1) + 1
            srcs = event_sources.setdefault(best_ev.id, set())
            srcs.add(source)
            best_ev.distinct_sources = max(best_ev.distinct_sources or 1, len(srcs))
            best_ev.max_source_tier = min(best_ev.max_source_tier or 3, source_tier)
            if published_at < best_ev.first_seen_at:
                best_ev.first_seen_at = published_at
                best_ev.market_aligned_at = align_to_market_time(published_at)
            best_ev.last_updated_at = now
            merged_cps = sorted(set(best_ev.chokepoints or []) | set(cls.chokepoints))
            merged_ents = sorted(set(best_ev.entities or []) | set(cls.entities))
            best_ev.chokepoints = merged_cps
            best_ev.entities = merged_ents
            best_ev.prior_rank_score = compute_prior_rank(
                max_source_tier=best_ev.max_source_tier,
                distinct_sources=best_ev.distinct_sources,
                chokepoints=merged_cps,
                severity=best_ev.effective_severity,
                novelty_score=best_ev.novelty_score,
                llm_confidence=best_ev.llm_confidence,
            )
            return best_ev, False, True

        # Create new canonical event
        novelty = round(max(0.25, 1.0 / (1.0 + 0.25 * same_cat_recent)), 3)
        aligned_at = align_to_market_time(published_at)
        ckey = make_cluster_key(cls.event_type, art_tokens, published_at)

        eff_dir = cls.direction
        eff_sev = cls.severity
        llm_fields: dict = {}
        if preserved_llm and ckey in preserved_llm:
            llm_fields = preserved_llm[ckey]
            conf = llm_fields.get("llm_confidence")
            if conf is not None and llm_fields.get("llm_direction") is not None and llm_fields.get("llm_severity") is not None:
                w = 0.4 + 0.5 * conf
                eff_dir = round(w * llm_fields["llm_direction"] + (1.0 - w) * cls.direction, 3)
                eff_sev = round(w * llm_fields["llm_severity"] + (1.0 - w) * cls.severity, 3)

        prior_rank = compute_prior_rank(
            max_source_tier=source_tier,
            distinct_sources=1,
            chokepoints=cls.chokepoints,
            severity=eff_sev,
            novelty_score=novelty,
            llm_confidence=llm_fields.get("llm_confidence"),
        )
        target_event = NewsEvent(
            cluster_key=ckey,
            headline=title[:500],
            summary=(summary or None),
            event_type=llm_fields.get("event_type") or cls.event_type,
            chokepoints=cls.chokepoints,
            entities=cls.entities,
            primary_region=cls.primary_region,
            first_seen_at=published_at,
            market_aligned_at=aligned_at,
            last_updated_at=now,
            article_count=1,
            distinct_sources=1,
            max_source_tier=source_tier,
            novelty_score=novelty,
            rule_direction=cls.direction,
            rule_severity=cls.severity,
            llm_direction=llm_fields.get("llm_direction"),
            llm_severity=llm_fields.get("llm_severity"),
            llm_confidence=llm_fields.get("llm_confidence"),
            llm_estimated_mbpd=llm_fields.get("llm_estimated_mbpd"),
            llm_rationale=llm_fields.get("llm_rationale"),
            llm_enriched_at=llm_fields.get("llm_enriched_at"),
            effective_direction=eff_dir,
            effective_severity=eff_sev,
            prior_rank_score=prior_rank,
        )
        session.add(target_event)
        await session.flush()
        recent_events.append(target_event)
        event_tokens[target_event.id] = art_tokens
        event_sources[target_event.id] = {source}
        return target_event, True, False

    async def rebuild_events_from_raw(self, days: Optional[int] = None) -> dict[str, int]:
        """Lossless Bronze -> Silver replay: rebuild news_events from raw news_articles.

        Preserves any existing optional LLM enrichments keyed by ``cluster_key`` so
        re-running rule/clustering logic never wastes LLM calls.
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)) if days is not None else None

        async with async_session() as session:
            # 1. Preserve existing LLM enrichments by cluster_key
            llm_q = select(NewsEvent).where(NewsEvent.llm_enriched_at.is_not(None))
            if cutoff is not None:
                llm_q = llm_q.where(NewsEvent.first_seen_at >= cutoff)
            existing_llm_events = (await session.execute(llm_q)).scalars().all()
            preserved_llm: dict[str, dict] = {
                ev.cluster_key: {
                    "event_type": ev.event_type,
                    "llm_direction": ev.llm_direction,
                    "llm_severity": ev.llm_severity,
                    "llm_confidence": ev.llm_confidence,
                    "llm_estimated_mbpd": ev.llm_estimated_mbpd,
                    "llm_rationale": ev.llm_rationale,
                    "llm_enriched_at": ev.llm_enriched_at,
                }
                for ev in existing_llm_events
            }

            # 2. Load raw Bronze articles chronologically
            art_q = select(NewsArticle).order_by(NewsArticle.published_at.asc(), NewsArticle.id.asc())
            if cutoff is not None:
                art_q = art_q.where(NewsArticle.published_at >= cutoff)
            articles: list[NewsArticle] = list((await session.execute(art_q)).scalars().all())

            # 3. Unlink articles & remove Silver events in scope (cascades to price impacts)
            if cutoff is not None:
                await session.execute(
                    update(NewsArticle)
                    .where(NewsArticle.published_at >= cutoff)
                    .values(event_id=None)
                )
                await session.execute(
                    delete(NewsEvent).where(NewsEvent.first_seen_at >= cutoff)
                )
            else:
                await session.execute(update(NewsArticle).values(event_id=None))
                await session.execute(delete(NewsEvent))
            await session.flush()

            # 4. Replay every raw article through current classification, clustering & ranking
            rebuilt_events: list[NewsEvent] = []
            event_tokens: dict[int, set[str]] = {}
            event_sources: dict[int, set[str]] = {}
            unmatched = 0
            llm_restored = 0

            for art in articles:
                # Prune sliding window list to keep replay fast on large archives
                window_floor = art.published_at - timedelta(hours=NOVELTY_LOOKBACK_HOURS)
                active_window = [e for e in rebuilt_events if e.first_seen_at >= window_floor]

                target_event, created, _ = await self._assign_to_event(
                    session=session,
                    title=art.title,
                    summary=art.summary or "",
                    source=art.source,
                    source_tier=art.source_tier,
                    published_at=art.published_at,
                    gdelt_tone=art.gdelt_tone,
                    recent_events=active_window,
                    event_tokens=event_tokens,
                    event_sources=event_sources,
                    now=now,
                    preserved_llm=preserved_llm,
                )
                if target_event is not None:
                    art.event_id = target_event.id
                    if created:
                        rebuilt_events.append(target_event)
                        if target_event.llm_enriched_at is not None:
                            llm_restored += 1
                else:
                    art.event_id = None
                    unmatched += 1

            await session.commit()

        stats = {
            "articles_replayed": len(articles),
            "events_rebuilt": len(rebuilt_events),
            "articles_unmatched": unmatched,
            "llm_preserved": llm_restored,
        }
        logger.info(f"NewsFetcher Bronze->Silver rebuild complete: {stats}")
        return stats

