"""Similarity-based News Memory & Per-Event Oil Price Predictor.

Learns from historical ``news_events`` paired with materialized ``news_event_price_impacts``
(Brent ``BZ=F`` and WTI ``CL=F``) to predict the expected price move, confidence band,
and peak time window (4h / 24h / 3d / 7d) whenever a new event arrives.

Similarity engine:
- Default (zero external API keys): hybrid TF-IDF (unigrams + bigrams) cosine similarity
  blended with structured maritime metadata (chokepoints, entities, event_type, direction).
- Optional upgrade: if dense semantic ``embedding`` vectors are stored on both events,
  dense cosine similarity automatically replaces TF-IDF for the text component.

Strict anti-leakage:
- Only historical events whose ``market_aligned_at`` precedes the target event AND whose
  outcome horizon has matured before the reference time are eligible as analogs.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import NewsEvent, NewsEventPriceImpact, NewsImpactPrior
from app.schemas.news import EventPricePrediction, HorizonPrediction, SimilarNewsAnalog
from app.services.news_clusterer import EVENT_CATEGORIES

PREDICTION_HORIZONS: tuple[tuple[str, int, str], ...] = (
    ("4h", 4, "ret_4h"),
    ("24h", 24, "ret_24h"),
    ("3d", 72, "ret_3d"),
    ("7d", 168, "ret_7d"),
)

DEFAULT_DAILY_VOL_PCT = 2.0
MIN_ANALOG_SIMILARITY = 0.18
ANALOG_SHRINKAGE_WEIGHT = 2.5
MAX_ANALOGS = 12
TOP_ANALOGS_RETURNED = 5

_RE_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "for", "of", "in", "on", "to", "at", "by", "from",
    "with", "as", "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "it", "its", "into", "over", "after", "amid", "says", "said", "report", "reports",
    "news", "update", "new", "more", "will", "has", "have", "had", "not", "but", "about",
    "than", "how", "what", "who", "why", "when", "where", "after", "before", "under",
})


@dataclass
class HistoricalEventRecord:
    """In-memory representation of a NewsEvent + its materialized price reactions."""

    id: int
    headline: str
    summary: str
    event_type: str
    chokepoints: tuple[str, ...]
    entities: tuple[str, ...]
    first_seen_at: datetime
    market_aligned_at: datetime
    effective_direction: float
    effective_severity: float
    prior_rank_score: float
    embedding: Optional[list[float]] = None
    # Horizon -> raw log-return (e.g., 0.015 for ~+1.5%)
    returns: dict[str, float] = field(default_factory=dict)
    vol_20d: Optional[float] = None


def _label_for_category(code: str) -> str:
    spec = EVENT_CATEGORIES.get(code)
    return spec.label if spec else code.replace("_", " ").title()


def _extract_ngrams(text: str) -> list[str]:
    """Extract normalized unigrams + bigrams for TF-IDF similarity."""
    raw_tokens = [
        t for t in _RE_TOKEN.findall((text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS and not t.isdigit()
    ]
    if not raw_tokens:
        return []
    bigrams = [f"{raw_tokens[i]}_{raw_tokens[i + 1]}" for i in range(len(raw_tokens) - 1)]
    return raw_tokens + bigrams


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _cosine_sparse(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    dot = sum(val * vec_b[k] for k, val in vec_a.items() if k in vec_b)
    return max(0.0, min(1.0, dot))


def _cosine_dense(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    na = float(np.linalg.norm(vec_a))
    nb = float(np.linalg.norm(vec_b))
    if na <= 1e-9 or nb <= 1e-9:
        return 0.0
    cos = float(np.dot(vec_a, vec_b) / (na * nb))
    return max(0.0, min(1.0, (cos + 1.0) / 2.0 if cos < 0 else cos))


def _to_pct(log_ret: Optional[float]) -> Optional[float]:
    if log_ret is None:
        return None
    return round((math.exp(log_ret) - 1.0) * 100.0, 2)


class NewsAnalogMemory:
    """In-memory similarity index over historical NewsEvents and their oil price reactions."""

    def __init__(
        self,
        records: list[HistoricalEventRecord],
        priors_by_cat: Optional[dict[str, dict[str, float]]] = None,
    ):
        self.records = records
        self.by_id: dict[int, HistoricalEventRecord] = {r.id: r for r in records}
        # priors_by_cat[event_type][horizon] = mean_ret (log return)
        self.priors_by_cat: dict[str, dict[str, float]] = priors_by_cat or {}

        # Build corpus IDF + L2-normalized TF-IDF vectors
        doc_freq: Counter[str] = Counter()
        term_counts: dict[int, Counter[str]] = {}
        for r in records:
            grams = _extract_ngrams(f"{r.headline} {r.summary}")
            counts = Counter(grams)
            term_counts[r.id] = counts
            for term in counts.keys():
                doc_freq[term] += 1

        n_docs = max(1, len(records))
        self.idf: dict[str, float] = {
            term: math.log((n_docs + 1.0) / (df + 1.0)) + 1.0
            for term, df in doc_freq.items()
        }

        self.tfidf_vecs: dict[int, dict[str, float]] = {}
        self.dense_vecs: dict[int, np.ndarray] = {}
        for r in records:
            vec = self._vectorize_counts(term_counts[r.id])
            self.tfidf_vecs[r.id] = vec
            if r.embedding and len(r.embedding) >= 8:
                self.dense_vecs[r.id] = np.asarray(r.embedding, dtype=np.float32)

    def _vectorize_counts(self, counts: Counter[str]) -> dict[str, float]:
        if not counts:
            return {}
        weighted = {
            term: (1.0 + math.log(cnt)) * self.idf.get(term, 1.0)
            for term, cnt in counts.items()
        }
        norm = math.sqrt(sum(v * v for v in weighted.values()))
        if norm <= 1e-9:
            return {}
        return {term: val / norm for term, val in weighted.items()}

    def _similarity(
        self,
        target: HistoricalEventRecord,
        candidate: HistoricalEventRecord,
    ) -> tuple[float, str]:
        """Compute hybrid [0, 1] similarity between target and historical candidate."""
        t_dense = self.dense_vecs.get(target.id)
        c_dense = self.dense_vecs.get(candidate.id)
        if t_dense is not None and c_dense is not None and t_dense.shape == c_dense.shape:
            text_sim = _cosine_dense(t_dense, c_dense)
            mode = "dense_embedding"
        else:
            text_sim = _cosine_sparse(
                self.tfidf_vecs.get(target.id, {}),
                self.tfidf_vecs.get(candidate.id, {}),
            )
            mode = "hybrid_tfidf_structured"

        cp_sim = _jaccard(target.chokepoints, candidate.chokepoints)
        ent_sim = _jaccard(target.entities, candidate.entities)
        cat_sim = 1.0 if target.event_type == candidate.event_type else 0.0
        dir_sim = max(0.0, (1.0 + target.effective_direction * candidate.effective_direction) / 2.0)

        sim = (
            0.55 * text_sim
            + 0.15 * cp_sim
            + 0.15 * ent_sim
            + 0.10 * cat_sim
            + 0.05 * dir_sim
        )
        return round(max(0.0, min(1.0, sim)), 4), mode

    def predict_for_event(
        self,
        event: HistoricalEventRecord,
        symbol: str = "BZ=F",
        reference_time: Optional[datetime] = None,
    ) -> EventPricePrediction:
        """Predict oil price reaction for ``event`` using strictly prior matured analogs."""
        ref_t = reference_time or datetime.now(timezone.utc)
        sym_label = "Brent" if symbol == "BZ=F" else "WTI"

        # Strict anti-leakage: only events seen earlier than the target event with matured outcomes
        scored: list[tuple[float, HistoricalEventRecord, str]] = []
        used_dense = False
        for cand in self.records:
            if cand.id == event.id:
                continue
            if cand.market_aligned_at >= event.market_aligned_at:
                continue
            if not cand.returns:
                continue
            sim, mode = self._similarity(event, cand)
            if sim >= MIN_ANALOG_SIMILARITY:
                if mode == "dense_embedding":
                    used_dense = True
                scored.append((sim, cand, mode))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_matches = scored[:MAX_ANALOGS]

        # Volatility fallback for range estimation
        daily_vol_pct = (
            (event.vol_20d * 100.0)
            if (event.vol_20d and event.vol_20d > 1e-5)
            else DEFAULT_DAILY_VOL_PCT
        )

        # Rule-based weak directional prior (used only as gentle fallback when history is sparse)
        rule_prior_scale = event.effective_direction * max(0.25, event.effective_severity) * 0.45

        horizons_out: dict[str, HorizonPrediction] = {}
        best_horizon = "24h"
        best_score = -1.0

        for h_code, h_hours, _ in PREDICTION_HORIZONS:
            # Eligible analogs whose horizon matured before ref_t (and before event for backtest)
            maturity_cutoff = min(ref_t, event.market_aligned_at)
            eligible = [
                (sim, cand.returns[h_code])
                for sim, cand, _ in top_matches
                if h_code in cand.returns
                and (cand.market_aligned_at + timedelta(hours=h_hours)) <= maturity_cutoff
            ]
            # For live recent events, also allow matured analogs whose outcome closed before ref_t
            if not eligible and ref_t > event.market_aligned_at:
                eligible = [
                    (sim, cand.returns[h_code])
                    for sim, cand, _ in top_matches
                    if h_code in cand.returns
                    and (cand.market_aligned_at + timedelta(hours=h_hours)) <= ref_t
                ]

            prior_log_ret = self.priors_by_cat.get(event.event_type, {}).get(h_code)
            horizon_scale = math.sqrt(h_hours / 24.0)
            prior_pct = (
                (math.exp(prior_log_ret) - 1.0) * 100.0
                if prior_log_ret is not None
                else rule_prior_scale * horizon_scale
            )

            sim_sum = sum(s for s, _ in eligible)
            n_analogs = len(eligible)
            if n_analogs > 0 and sim_sum > 1e-6:
                pct_rets = [(math.exp(r) - 1.0) * 100.0 for _, r in eligible]
                weights = [s for s, _ in eligible]
                analog_mean = sum(w * r for w, r in zip(weights, pct_rets)) / sim_sum
                # Weighted std dev
                var = sum(w * ((r - analog_mean) ** 2) for w, r in zip(weights, pct_rets)) / sim_sum
                analog_std = math.sqrt(max(0.0, var))

                # Empirical Bayes shrinkage toward category prior
                w_analog = sim_sum / (sim_sum + ANALOG_SHRINKAGE_WEIGHT)
                expected_pct = w_analog * analog_mean + (1.0 - w_analog) * prior_pct

                # Directional agreement across analogs
                pred_sign = 1 if expected_pct >= 0 else -1
                agree = sum(1 for r in pct_rets if (r >= 0 and pred_sign > 0) or (r < 0 and pred_sign < 0))
                agreement_pct = round((agree / n_analogs) * 100.0, 1)

                # Confidence combines effective sample mass, similarity quality, and agreement
                avg_sim_h = sim_sum / n_analogs
                mass_factor = min(1.0, sim_sum / 4.0)
                agree_factor = max(0.25, (agreement_pct / 100.0))
                confidence_pct = round(
                    min(92.0, max(18.0, 100.0 * (0.45 * mass_factor + 0.35 * agree_factor + 0.20 * avg_sim_h))),
                    1,
                )
                spread = max(analog_std, daily_vol_pct * horizon_scale * 0.45)
            else:
                expected_pct = prior_pct * 0.5
                agreement_pct = None
                confidence_pct = 20.0 if prior_log_ret is not None else 12.0
                spread = daily_vol_pct * horizon_scale * 0.75

            expected_pct = round(max(-15.0, min(15.0, expected_pct)), 2)
            low_pct = round(max(-20.0, expected_pct - spread), 2)
            high_pct = round(min(20.0, expected_pct + spread), 2)

            hp = HorizonPrediction(
                horizon=h_code,
                expected_return_pct=expected_pct,
                range_low_pct=low_pct,
                range_high_pct=high_pct,
                confidence_pct=confidence_pct,
                directional_agreement_pct=agreement_pct,
                analog_count=n_analogs,
            )
            horizons_out[h_code] = hp

            # Peak horizon favors meaningful expected move backed by real analog confidence
            h_score = abs(expected_pct) * (confidence_pct / 100.0) * (1.0 if n_analogs > 0 else 0.35)
            if h_score > best_score:
                best_score = h_score
                best_horizon = h_code

        peak = horizons_out[best_horizon]
        if peak.expected_return_pct >= 0.25:
            direction = "bullish"
        elif peak.expected_return_pct <= -0.25:
            direction = "bearish"
        else:
            direction = "neutral"

        top_analog_items: list[SimilarNewsAnalog] = [
            SimilarNewsAnalog(
                event_id=cand.id,
                headline=cand.headline,
                event_type=cand.event_type,
                event_type_label=_label_for_category(cand.event_type),
                first_seen_at=cand.first_seen_at,
                similarity=round(sim, 3),
                ret_4h_pct=_to_pct(cand.returns.get("4h")),
                ret_24h_pct=_to_pct(cand.returns.get("24h")),
                ret_3d_pct=_to_pct(cand.returns.get("3d")),
                ret_7d_pct=_to_pct(cand.returns.get("7d")),
            )
            for sim, cand, _ in top_matches[:TOP_ANALOGS_RETURNED]
        ]

        avg_sim = (
            round(sum(s for s, _, _ in top_matches) / len(top_matches), 3)
            if top_matches
            else 0.0
        )

        sign_str = "+" if peak.expected_return_pct >= 0 else ""
        if peak.analog_count > 0:
            agree_clause = (
                f" ({peak.directional_agreement_pct:.0f}% directional agreement)"
                if peak.directional_agreement_pct is not None
                else ""
            )
            narrative = (
                f"Based on {peak.analog_count} similar past event"
                f"{'s' if peak.analog_count != 1 else ''} (avg {avg_sim * 100:.0f}% match), "
                f"{sym_label} is projected {sign_str}{peak.expected_return_pct:.2f}% "
                f"over the next {best_horizon}{agree_clause}."
            )
        else:
            narrative = (
                f"No close historical analogs matured yet; category prior points to "
                f"{sym_label} {sign_str}{peak.expected_return_pct:.2f}% over the next {best_horizon} "
                f"(low confidence)."
            )

        return EventPricePrediction(
            symbol=symbol,
            peak_horizon=best_horizon,
            direction=direction,
            expected_return_pct=peak.expected_return_pct,
            range_low_pct=peak.range_low_pct,
            range_high_pct=peak.range_high_pct,
            confidence_pct=peak.confidence_pct,
            analog_count=peak.analog_count,
            avg_similarity=avg_sim,
            similarity_mode="dense_embedding" if used_dense else "hybrid_tfidf_structured",
            narrative=narrative,
            horizons=horizons_out,
            analogs=top_analog_items,
        )


async def build_news_analog_memory(
    db: AsyncSession,
    symbol: str = "BZ=F",
    lookback_days: int = 1825,
) -> NewsAnalogMemory:
    """Load historical NewsEvents, price impacts, and latest category priors into a NewsAnalogMemory."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    events = list(
        (
            await db.execute(
                select(NewsEvent)
                .where(NewsEvent.first_seen_at >= cutoff)
                .order_by(NewsEvent.first_seen_at.desc())
                .limit(10000)
            )
        ).scalars().all()
    )

    event_ids = [e.id for e in events]
    impacts_by_id: dict[int, NewsEventPriceImpact] = {}
    if event_ids:
        imp_rows = (
            await db.execute(
                select(NewsEventPriceImpact).where(
                    NewsEventPriceImpact.event_id.in_(event_ids),
                    NewsEventPriceImpact.symbol == symbol,
                )
            )
        ).scalars().all()
        impacts_by_id = {imp.event_id: imp for imp in imp_rows}

    # Latest category priors for Empirical Bayes shrinkage
    latest_prior_date = (
        await db.execute(
            select(func.max(NewsImpactPrior.as_of_date)).where(NewsImpactPrior.symbol == symbol)
        )
    ).scalar()
    priors_by_cat: dict[str, dict[str, float]] = defaultdict(dict)
    if latest_prior_date is not None:
        pr_rows = (
            await db.execute(
                select(NewsImpactPrior).where(
                    NewsImpactPrior.as_of_date == latest_prior_date,
                    NewsImpactPrior.symbol == symbol,
                    NewsImpactPrior.region_scope == "global",
                )
            )
        ).scalars().all()
        for pr in pr_rows:
            if pr.mean_ret is not None and pr.sample_count >= 2:
                priors_by_cat[pr.event_type][pr.horizon] = float(pr.mean_ret)

    records: list[HistoricalEventRecord] = []
    for e in events:
        imp = impacts_by_id.get(e.id)
        returns: dict[str, float] = {}
        vol_20d = None
        if imp is not None:
            vol_20d = imp.vol_20d_t0
            for h_code, _, ret_col in PREDICTION_HORIZONS:
                val = getattr(imp, ret_col, None)
                if val is not None:
                    returns[h_code] = float(val)
        records.append(
            HistoricalEventRecord(
                id=e.id,
                headline=e.headline,
                summary=e.summary or "",
                event_type=e.event_type,
                chokepoints=tuple(e.chokepoints or ()),
                entities=tuple(e.entities or ()),
                first_seen_at=e.first_seen_at,
                market_aligned_at=e.market_aligned_at,
                effective_direction=float(e.effective_direction or 0.0),
                effective_severity=float(e.effective_severity or 0.3),
                prior_rank_score=float(e.prior_rank_score or 0.0),
                embedding=e.embedding if isinstance(e.embedding, list) else None,
                returns=returns,
                vol_20d=vol_20d,
            )
        )

    return NewsAnalogMemory(records=records, priors_by_cat=dict(priors_by_cat))
