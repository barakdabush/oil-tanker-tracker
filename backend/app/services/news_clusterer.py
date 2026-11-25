"""Domain taxonomy, clustering similarity, market-hours alignment, and prior ranking for oil & maritime news.

Pure, fast, zero-external-dependency logic (fully unit-testable) that runs for every article
before any optional LLM enrichment.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# ---------------------------------------------------------------------------
# Event Taxonomy (10 domain categories with default lag half-life & direction)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CategorySpec:
    code: str
    label: str
    default_half_life_hours: float
    base_severity: float
    default_direction: float  # +1 bullish for oil price, -1 bearish, 0 ambiguous
    keywords: tuple[str, ...]
    bearish_cues: tuple[str, ...] = ()
    bullish_cues: tuple[str, ...] = ()


EVENT_CATEGORIES: dict[str, CategorySpec] = {
    "chokepoint_attack_blockade": CategorySpec(
        code="chokepoint_attack_blockade",
        label="Chokepoint Attack / Blockade",
        default_half_life_hours=36.0,
        base_severity=0.85,
        default_direction=0.85,
        keywords=(
            "tanker attack", "ship attack", "vessel struck", "missile", "drone strike",
            "houthi", "seized tanker", "tanker seized", "hijack", "blockade", "mine",
            "ukmto", "maritime incident", "closure of hormuz", "close hormuz",
            "hormuz blockade", "hormuz attack", "hormuz war", "avoid strait of hormuz",
            "tanker fire", "tanker explosion", "tankers explode", "catches fire",
            "suez blocked", "canal blocked", "grounded in suez", "red sea attack",
            "piracy", "sunk", "sink ships", "ships will be sunk",
        ),
        bearish_cues=("reopen", "reopened", "cleared", "resumed transit", "freed", "released", "truce", "ceasefire", "walks back", "never existed", "false alarm"),
        bullish_cues=("attack", "struck", "hit", "seized", "blockade", "closed", "suspended", "explosion", "explode", "fire", "avoid", "sunk"),
    ),
    "opec_production_decision": CategorySpec(
        code="opec_production_decision",
        label="OPEC+ Production Decision",
        default_half_life_hours=72.0,
        base_severity=0.75,
        default_direction=0.0,
        keywords=(
            "opec", "opec+", "saudi aramco", "production cut", "output cut",
            "output hike", "production increase", "quota", "voluntary cut",
            "unwind cuts", "barrel cut", "spare capacity", "more oil reaches market",
        ),
        bearish_cues=("increase", "hike", "boost output", "raise output", "unwind", "add barrels", "oversupply", "pump more", "more oil reaches market", "exports rise"),
        bullish_cues=("cut", "extend cuts", "deepen cuts", "curb", "slash output", "tighten", "compliance"),
    ),
    "refinery_pipeline_outage": CategorySpec(
        code="refinery_pipeline_outage",
        label="Refinery / Pipeline Outage",
        default_half_life_hours=48.0,
        base_severity=0.70,
        default_direction=0.65,
        keywords=(
            "pipeline outage", "pipeline halt", "pipeline shut", "refinery outage",
            "refinery fire", "fire at refinery", "refinery shutdown", "export terminal",
            "force majeure", "terminal fire", "crude spill", "oil leak",
            "cpc pipeline", "druzhba", "keystone", "outage", "shutdown",
        ),
        bearish_cues=("restarts", "resumes", "restored", "reopened", "back online", "more oil reaches market"),
        bullish_cues=("halt", "halted", "shut", "shutdown", "outage", "force majeure", "fire", "explosion", "disrupted"),
    ),
    "inventory_macro_surprise": CategorySpec(
        code="inventory_macro_surprise",
        label="Inventory / SPR Surprise",
        default_half_life_hours=24.0,
        base_severity=0.55,
        default_direction=0.0,
        keywords=(
            "eia", "crude inventories", "oil inventories", "crude stockpiles",
            "oil stockpiles", "api crude", "cushing", "strategic petroleum reserve",
            "spr release", "spr refill", "weekly petroleum status",
        ),
        bearish_cues=("build", "rose", "increase", "surplus", "spr release", "larger build", "stockpiles rise"),
        bullish_cues=("draw", "fell", "drop", "decline", "spr refill", "tighter", "stockpiles fall"),
    ),
    "sanctions_enforcement": CategorySpec(
        code="sanctions_enforcement",
        label="Sanctions & Shadow Fleet",
        default_half_life_hours=120.0,
        base_severity=0.65,
        default_direction=0.60,
        keywords=(
            "ofac", "sanction", "sanctions", "shadow fleet", "dark fleet",
            "price cap", "g7 cap", "russian oil", "iranian oil", "venezuelan oil",
            "pdvsa", "sovcomflot", "blacklisted tanker", "sanctioned vessel",
            "ship-to-ship", "sts transfer", "ais spoofing",
        ),
        bearish_cues=("waiver", "eased", "lifted", "relaxed", "license granted", "exemption"),
        bullish_cues=("new sanctions", "blacklisted", "crackdown", "tighten", "designated", "seized", "ban", "embargo"),
    ),
    "rerouting_freight_surge": CategorySpec(
        code="rerouting_freight_surge",
        label="Rerouting & Freight Surge",
        default_half_life_hours=96.0,
        base_severity=0.60,
        default_direction=0.55,
        keywords=(
            "cape of good hope", "reroute", "rerouting", "divert", "diverted",
            "vlcc", "suezmax", "aframax", "tanker rates", "freight rates",
            "war risk insurance", "war-risk", "ton-mile", "suspend transit",
            "avoid red sea", "avoid suez",
        ),
        bearish_cues=("return to red sea", "resume suez", "rates fall", "freight drops", "normalization"),
        bullish_cues=("reroute", "divert", "surge", "spike", "jump", "suspend", "war risk", "soar"),
    ),
    "geopolitical_escalation": CategorySpec(
        code="geopolitical_escalation",
        label="Geopolitical Escalation",
        default_half_life_hours=72.0,
        base_severity=0.65,
        default_direction=0.55,
        keywords=(
            "iran", "israel", "middle east", "persian gulf", "gulf of oman",
            "gulf of aden", "black sea", "ukraine strike", "russian refinery",
            "naval", "warship", "retaliation", "ceasefire", "peace talks",
            "escalation", "military strike",
        ),
        bearish_cues=("ceasefire", "de-escalation", "truce", "peace deal", "talks progress", "easing tensions"),
        bullish_cues=("strike", "escalation", "retaliation", "warning", "threat", "conflict", "attack", "warship"),
    ),
    "port_canal_congestion": CategorySpec(
        code="port_canal_congestion",
        label="Port / Canal Congestion",
        default_half_life_hours=120.0,
        base_severity=0.45,
        default_direction=0.35,
        keywords=(
            "panama canal", "drought", "transit slots", "bosphorus", "bosporus",
            "turkish straits", "port congestion", "port strike", "dockworkers",
            "tanker queue", "anchored waiting", "fog delay", "export delay",
        ),
        bearish_cues=("eased", "cleared", "strike ends", "slots increased", "normal operations"),
        bullish_cues=("congestion", "queue", "delay", "strike", "drought", "restrictions", "bottleneck"),
    ),
    "floating_storage_contango": CategorySpec(
        code="floating_storage_contango",
        label="Floating Storage & Curve",
        default_half_life_hours=168.0,
        base_severity=0.45,
        default_direction=-0.35,
        keywords=(
            "floating storage", "oil on water", "tankers storing", "contango",
            "backwardation", "glut", "unsold crude", "stranded crude",
            "supertanker storage",
        ),
        bearish_cues=("contango", "glut", "floating storage rises", "surplus", "unsold"),
        bullish_cues=("backwardation", "storage falls", "destocking", "tight market"),
    ),
    "demand_shock_macro": CategorySpec(
        code="demand_shock_macro",
        label="Demand & Macro Shock",
        default_half_life_hours=168.0,
        base_severity=0.50,
        default_direction=0.0,
        keywords=(
            "oil demand", "crude demand", "china imports", "china refinery",
            "iea forecast", "opec demand", "recession", "stimulus",
            "crude imports", "jet fuel demand", "global oil consumption",
        ),
        bearish_cues=("slump", "weak demand", "cut forecast", "slowdown", "recession", "lower demand", "falls"),
        bullish_cues=("strong demand", "stimulus", "raise forecast", "record imports", "rebound", "higher demand"),
    ),
    "us_policy_tariffs_trump": CategorySpec(
        code="us_policy_tariffs_trump",
        label="US Policy, Tariffs & Trump",
        default_half_life_hours=72.0,
        base_severity=0.70,
        default_direction=0.0,
        keywords=(
            "trump", "white house", "tariff", "tariffs", "trade war",
            "drill baby drill", "energy emergency", "us sanctions",
            "federal reserve", "fomc", "rate cut", "rate hike",
            "maximum pressure", "secondary tariffs",
        ),
        bearish_cues=("tariffs", "trade war", "drill", "boost us output", "lower oil prices", "rate hike", "waiver", "ceasefire", "peace deal"),
        bullish_cues=("maximum pressure", "sanctions on iran", "secondary tariffs", "rate cut", "stimulus", "spr refill", "military strike", "threatens"),
    ),
    "oil_majors_noc_guidance": CategorySpec(
        code="oil_majors_noc_guidance",
        label="Big Oil Majors & NOCs",
        default_half_life_hours=96.0,
        base_severity=0.55,
        default_direction=0.0,
        keywords=(
            "exxon", "exxonmobil", "chevron", "shell", "bp ", "totalenergies",
            "conocophillips", "petrobras", "aramco", "official selling price",
            "osp", "adnoc", "qatar energy", "oil major", "capex cut", "capex hike",
        ),
        bearish_cues=("cuts osp", "lowers osp", "boosts output", "record production", "ramps up", "expands output", "raises capex"),
        bullish_cues=("raises osp", "hikes osp", "cuts output", "capex cut", "slashes spending", "halts production", "output drop", "force majeure"),
    ),
}

# Chokepoints & criticality weights (aligned with monitored chokepoints in oil-tanker-tracker)
CHOKEPOINT_PATTERNS: dict[str, tuple[float, tuple[str, ...]]] = {
    "Strait of Hormuz": (1.00, ("hormuz", "persian gulf", "bandar abbas", "fujairah")),
    "Strait of Malacca": (0.85, ("malacca", "singapore strait")),
    "Bab el-Mandeb": (0.85, ("bab el-mandeb", "bab al-mandab", "red sea", "gulf of aden", "houthi", "yemen")),
    "Suez Canal": (0.80, ("suez canal", "suez", "sumed")),
    "Turkish Straits": (0.65, ("bosphorus", "bosporus", "dardanelles", "turkish straits", "black sea")),
    "Cape of Good Hope": (0.55, ("cape of good hope", "south africa route")),
    "Panama Canal": (0.50, ("panama canal", "gatun")),
    "Danish Straits": (0.45, ("danish straits", "baltic sea", "skagerrak", "kattegat")),
}

# Relevance gate: at least one core oil/tanker/maritime term must appear
CORE_RELEVANCE_TERMS: tuple[str, ...] = (
    "oil", "crude", "brent", "wti", "tanker", "vlcc", "suezmax", "aframax",
    "opec", "refinery", "pipeline", "petroleum", "hormuz", "bab el-mandeb",
    "bab al-mandab", "suez", "malacca", "bosphorus", "bosporus", "panama canal",
    "red sea", "eia", "spr", "shadow fleet", "dark fleet", "ofac", "shipping",
    "vessel", "maritime", "fuel", "diesel", "gasoline", "lng", "cargo",
    "aramco", "energy", "barrels", "bpd", "exxon", "chevron", "shell", "totalenergies",
)

ENTITY_KEYWORDS: tuple[str, ...] = (
    "OPEC", "EIA", "IEA", "OFAC", "UKMTO", "MARAD", "Aramco", "ExxonMobil", "Chevron",
    "Shell", "BP", "TotalEnergies", "Trump", "Fed", "Iran", "Saudi Arabia",
    "Russia", "China", "US", "Houthis", "Venezuela", "Libya", "Iraq", "UAE", "EU",
)

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "for", "of", "in", "on", "to", "at", "by", "from",
    "with", "as", "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "it", "its", "into", "over", "after", "amid", "says", "said", "report", "reports",
    "news", "update", "new", "more", "will", "has", "have", "had", "not", "but", "about",
})

_RE_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_RE_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# URL & Text Normalization
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Strip tracking query params (utm_*, fbclid, etc.) and fragments for deduplication."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    clean_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_", "ref"))
    ]
    clean = parsed._replace(
        scheme=(parsed.scheme or "https").lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(clean_query),
        fragment="",
    )
    return urlunparse(clean)


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def tokenize_for_clustering(text: str) -> set[str]:
    """Return normalized meaningful word tokens for fast headline/summary similarity."""
    cleaned = _RE_NON_ALNUM.sub(" ", (text or "").lower())
    tokens = [t for t in _RE_WS.split(cleaned.strip()) if len(t) >= 3 and t not in _STOPWORDS]
    return set(tokens)


def compute_cluster_similarity(
    tokens_a: set[str],
    tokens_b: set[str],
    chokepoints_a: list[str],
    chokepoints_b: list[str],
    event_type_a: str,
    event_type_b: str,
) -> float:
    """Compute [0, 1] similarity between an incoming article and an existing recent event cluster."""
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 0.0

    # Boost if both articles reference the exact same chokepoint and event category
    cp_overlap = bool(set(chokepoints_a) & set(chokepoints_b))
    same_type = event_type_a == event_type_b

    score = jaccard
    if cp_overlap and same_type:
        score = min(1.0, score + 0.18)
    elif same_type and jaccard >= 0.20:
        score = min(1.0, score + 0.08)
    return round(score, 4)


def make_cluster_key(event_type: str, tokens: set[str], first_seen: datetime) -> str:
    top_tokens = sorted(tokens)[:6]
    day_bucket = first_seen.strftime("%Y-%m-%d")
    raw = f"{event_type}|{day_bucket}|{' '.join(top_tokens)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Classification, Entity/Chokepoint Extraction & Prior Ranking
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    relevant: bool
    event_type: str
    direction: float         # [-1.0 .. +1.0]
    severity: float          # [0.0 .. 1.0]
    chokepoints: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    primary_region: str = "global"


def extract_chokepoints(text_lower: str) -> list[str]:
    found: list[str] = []
    for name, (_, patterns) in CHOKEPOINT_PATTERNS.items():
        if any(p in text_lower for p in patterns):
            found.append(name)
    return found


def extract_entities(text: str) -> list[str]:
    text_lower = text.lower()
    found: list[str] = []
    for ent in ENTITY_KEYWORDS:
        # Word-boundary match for short acronyms like US / EU / EIA / IEA
        if len(ent) <= 4:
            if re.search(rf"\b{re.escape(ent.lower())}\b", text_lower):
                found.append(ent)
        elif ent.lower() in text_lower:
            found.append(ent)
    return found


# Non-market educational/FAQ/digest titles to reject unless strong shock cues appear
NOISE_TITLE_PATTERNS: tuple[str, ...] = (
    "how does eia",
    "what is ",
    "explainer:",
    "energy briefs",
    "podcast:",
    "webinar",
    "quiz:",
    "careers in ",
    "oil & gas jobs",
)


def classify_article(
    title: str,
    summary: str = "",
    gdelt_tone: Optional[float] = None,
) -> ClassificationResult:
    """Classify an article into our 10 oil/tanker categories with local direction & severity."""
    combined = f"{title or ''} {summary or ''}".strip()
    text_lower = combined.lower()
    title_lower = (title or "").lower()

    if any(pat in title_lower for pat in NOISE_TITLE_PATTERNS):
        return ClassificationResult(
            relevant=False,
            event_type="other",
            direction=0.0,
            severity=0.0,
        )

    if not any(term in text_lower for term in CORE_RELEVANCE_TERMS):
        return ClassificationResult(
            relevant=False,
            event_type="other",
            direction=0.0,
            severity=0.0,
        )

    chokepoints = extract_chokepoints(text_lower)
    entities = extract_entities(combined)

    # Score every category by keyword hits (title matches count 2x)
    best_code = "geopolitical_escalation"
    best_score = 0.0

    for code, spec in EVENT_CATEGORIES.items():
        hits = 0.0
        for kw in spec.keywords:
            if kw in title_lower:
                hits += 2.0
            elif kw in text_lower:
                hits += 1.0
        if hits > best_score:
            best_score = hits
            best_code = code

    if best_score <= 0.0:
        # Relevant oil/maritime mention, but didn't hit a specific strong category
        if chokepoints:
            best_code = "geopolitical_escalation"
            best_score = 1.0
        else:
            return ClassificationResult(
                relevant=False,
                event_type="other",
                direction=0.0,
                severity=0.0,
                chokepoints=chokepoints,
                entities=entities,
            )

    spec = EVENT_CATEGORIES[best_code]

    # Directional polarity: count bullish vs bearish cues + category default + optional GDELT tone
    bull_hits = sum(1 for c in spec.bullish_cues if c in text_lower)
    bear_hits = sum(1 for c in spec.bearish_cues if c in text_lower)

    if bull_hits > bear_hits:
        direction = min(1.0, 0.45 + 0.20 * (bull_hits - bear_hits))
    elif bear_hits > bull_hits:
        direction = max(-1.0, -0.45 - 0.20 * (bear_hits - bull_hits))
    else:
        direction = spec.default_direction

    # Negative GDELT tone on supply/chokepoint/geopolitical stories reinforces bullish risk premium
    if gdelt_tone is not None and best_code in (
        "chokepoint_attack_blockade",
        "geopolitical_escalation",
        "refinery_pipeline_outage",
        "sanctions_enforcement",
    ):
        if gdelt_tone < -3.0:
            direction = min(1.0, direction + 0.15)
        elif gdelt_tone > 3.0:
            direction = max(-1.0, direction - 0.15)

    # Severity: category baseline + hit density + chokepoint criticality boost
    cp_boost = 0.0
    if chokepoints:
        cp_boost = 0.15 * max(CHOKEPOINT_PATTERNS[cp][0] for cp in chokepoints)
    hit_boost = min(0.15, 0.03 * max(0.0, best_score - 1.0))
    severity = min(1.0, round(spec.base_severity + cp_boost + hit_boost, 3))

    primary_region = chokepoints[0] if chokepoints else (entities[0] if entities else "global")

    return ClassificationResult(
        relevant=True,
        event_type=best_code,
        direction=round(direction, 3),
        severity=severity,
        chokepoints=chokepoints,
        entities=entities,
        primary_region=primary_region,
    )


# ---------------------------------------------------------------------------
# Market-Hours Alignment & Prior Rank Formula
# ---------------------------------------------------------------------------

def align_to_market_time(dt_utc: datetime) -> datetime:
    """Align a UTC event timestamp to the next tradeable Brent/WTI futures hour.

    Crude futures (BZ=F / CL=F) trade Sun ~22:00 UTC through Fri ~21:00 UTC.
    Events occurring over the weekend are aligned to Sunday 22:00 UTC so +1h/+4h
    measure the actual market-open reaction rather than flat weekend prices.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(timezone.utc)

    wd = dt_utc.weekday()  # Mon=0 .. Fri=4, Sat=5, Sun=6
    # Friday >= 21:00 UTC -> Sunday 22:00 UTC
    if wd == 4 and dt_utc.hour >= 21:
        days_to_sun = 2
        sun = (dt_utc + timedelta(days=days_to_sun)).replace(hour=22, minute=0, second=0, microsecond=0)
        return sun
    # Saturday (all day) -> Sunday 22:00 UTC
    if wd == 5:
        sun = (dt_utc + timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        return sun
    # Sunday < 22:00 UTC -> Sunday 22:00 UTC
    if wd == 6 and dt_utc.hour < 22:
        return dt_utc.replace(hour=22, minute=0, second=0, microsecond=0)

    return dt_utc


def compute_prior_rank(
    max_source_tier: int,
    distinct_sources: int,
    chokepoints: list[str],
    severity: float,
    novelty_score: float = 1.0,
    llm_confidence: Optional[float] = None,
) -> float:
    """Compute real-time importance rank on [0, 100] available immediately at t0.

    Weights:
    - Severity (domain impact): 35 pts
    - Source credibility tier: 20 pts
    - Corroboration / velocity (distinct sources): 20 pts
    - Chokepoint criticality: 15 pts
    - Novelty multiplier: scales final score [0.55 .. 1.0]
    - Optional LLM confidence adjustment: +/- 5 pts
    """
    tier_weights = {1: 1.0, 2: 0.78, 3: 0.52}
    tier_w = tier_weights.get(max_source_tier, 0.5)

    corroboration = min(1.0, math.log1p(max(1, distinct_sources)) / math.log1p(8))
    cp_crit = max((CHOKEPOINT_PATTERNS.get(cp, (0.0, ()))[0] for cp in chokepoints), default=0.25)

    raw = (
        35.0 * max(0.0, min(1.0, severity))
        + 20.0 * tier_w
        + 20.0 * corroboration
        + 15.0 * cp_crit
        + 10.0 * max(0.0, min(1.0, novelty_score))
    )

    # Penalize recycled stories with very low novelty
    novelty_mult = 0.55 + 0.45 * max(0.0, min(1.0, novelty_score))
    score = raw * novelty_mult

    if llm_confidence is not None:
        # High-confidence LLM confirmation nudges up slightly; low confidence nudges down
        score += (max(0.0, min(1.0, llm_confidence)) - 0.5) * 10.0

    return round(max(0.0, min(100.0, score)), 2)
