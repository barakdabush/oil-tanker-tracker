"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getApiUrl } from "@/lib/config";
import type {
  AIMarketBriefingResponse,
  EventPriceReaction,
  HorizonStatCell,
  ImpactMatrixResponse,
  NewsEventResponse,
  NewsSummaryResponse,
} from "@/lib/types";

const HORIZON_LABELS: Record<string, string> = {
  pre_24h: "-24h",
  pre_4h: "-4h",
  "1h": "+1h",
  "4h": "+4h",
  "24h": "+24h",
  "3d": "+3d",
  "7d": "+7d",
  "14d": "+14d",
};

const CHOKEPOINT_FILTERS = [
  "All Chokepoints",
  "Strait of Hormuz",
  "Bab el-Mandeb",
  "Suez Canal",
  "Strait of Malacca",
  "Turkish Straits",
  "Cape of Good Hope",
  "Panama Canal",
  "Danish Straits",
];

function fmtPct(val: number | null | undefined, fromLogRet = false): string {
  if (val === null || val === undefined) return "—";
  const pct = fromLogRet ? val * 100 : val;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function cellBgForZ(cell: HorizonStatCell | undefined): string {
  if (!cell || cell.sample_count === 0 || cell.mean_z === null) {
    return "rgba(30, 41, 59, 0.45)";
  }
  const intensity = Math.min(0.65, Math.max(0.12, Math.abs(cell.mean_z) * 0.35));
  if (cell.mean_z > 0.05) {
    return `rgba(16, 185, 129, ${intensity.toFixed(2)})`;
  }
  if (cell.mean_z < -0.05) {
    return `rgba(239, 68, 68, ${intensity.toFixed(2)})`;
  }
  return "rgba(100, 116, 139, 0.2)";
}

function directionBadge(dir: number): { label: string; color: string; bg: string } {
  if (dir >= 0.25) {
    return { label: "▲ Bullish", color: "#34d399", bg: "rgba(16, 185, 129, 0.16)" };
  }
  if (dir <= -0.25) {
    return { label: "▼ Bearish", color: "#f87171", bg: "rgba(239, 68, 68, 0.16)" };
  }
  return { label: "● Neutral", color: "#94a3b8", bg: "rgba(148, 163, 184, 0.14)" };
}

function rankColor(rank: number): string {
  if (rank >= 70) return "#ef4444";
  if (rank >= 55) return "#f59e0b";
  if (rank >= 40) return "#06b6d4";
  return "#64748b";
}

function ReactionStrip({ impact, label }: { impact: EventPriceReaction | null; label: string }) {
  const horizons: { key: keyof EventPriceReaction; tag: string }[] = [
    { key: "ret_pre_24h", tag: "-24h" },
    { key: "ret_1h", tag: "+1h" },
    { key: "ret_4h", tag: "+4h" },
    { key: "ret_24h", tag: "+24h" },
    { key: "ret_3d", tag: "+3d" },
    { key: "ret_7d", tag: "+7d" },
    { key: "ret_14d", tag: "+14d" },
  ];

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: "var(--text-muted)",
          minWidth: 42,
        }}
      >
        {label}
      </span>
      {horizons.map(({ key, tag }) => {
        const raw = impact ? (impact[key] as number | null) : null;
        const pct = raw !== null && raw !== undefined ? raw * 100 : null;
        const color =
          pct === null
            ? "var(--text-muted)"
            : pct > 0.15
              ? "#34d399"
              : pct < -0.15
                ? "#f87171"
                : "#cbd5e1";
        return (
          <div
            key={tag}
            style={{
              padding: "3px 7px",
              borderRadius: 6,
              background: "rgba(15, 23, 42, 0.7)",
              border: "1px solid var(--border)",
              fontSize: 11,
              display: "flex",
              gap: 4,
              alignItems: "center",
            }}
          >
            <span style={{ color: "var(--text-muted)" }}>{tag}</span>
            <span style={{ color, fontWeight: 600 }}>
              {pct === null ? "…" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function NewsIntelligencePage() {
  const [summary, setSummary] = useState<NewsSummaryResponse | null>(null);
  const [matrix, setMatrix] = useState<ImpactMatrixResponse | null>(null);
  const [events, setEvents] = useState<NewsEventResponse[]>([]);
  const [briefing, setBriefing] = useState<AIMarketBriefingResponse | null>(null);
  const [symbol, setSymbol] = useState<"BZ=F" | "CL=F">("BZ=F");
  const [days, setDays] = useState<7 | 14 | 30 | 90>(14);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [chokepointFilter, setChokepointFilter] = useState<string>("All Chokepoints");
  const [sortBy, setSortBy] = useState<"rank" | "recent" | "learned">("rank");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [recomputing, setRecomputing] = useState<boolean>(false);

  const loadData = useCallback(async () => {
    const API = getApiUrl();
    try {
      const evParams = new URLSearchParams({
        days: String(days),
        sort_by: sortBy,
        symbol,
        include_predictions: "true",
        limit: "60",
      });
      if (categoryFilter !== "all") evParams.set("event_type", categoryFilter);
      if (chokepointFilter !== "All Chokepoints") evParams.set("chokepoint", chokepointFilter);

      const [sumRes, matRes, evRes, briefRes] = await Promise.allSettled([
        fetch(`${API}/api/news/summary`),
        fetch(`${API}/api/news/impact-matrix?symbol=${encodeURIComponent(symbol)}`),
        fetch(`${API}/api/news/events?${evParams.toString()}`),
        fetch(`${API}/api/agent-intel/briefings/latest`),
      ]);

      if (sumRes.status === "fulfilled" && sumRes.value.ok) {
        setSummary(await sumRes.value.json());
      }
      if (matRes.status === "fulfilled" && matRes.value.ok) {
        setMatrix(await matRes.value.json());
      }
      if (evRes.status === "fulfilled" && evRes.value.ok) {
        setEvents(await evRes.value.json());
      }
      if (briefRes.status === "fulfilled" && briefRes.value.ok) {
        setBriefing(await briefRes.value.json());
      }
    } catch (err) {
      console.error("Failed to load News Intelligence data:", err);
    } finally {
      setLoading(false);
    }
  }, [symbol, days, categoryFilter, chokepointFilter, sortBy]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleRefresh = async () => {
    const API = getApiUrl();
    setRefreshing(true);
    try {
      await fetch(`${API}/api/news/refresh?timespan=7d`, { method: "POST" });
      await loadData();
    } catch (err) {
      console.error("News refresh failed:", err);
    } finally {
      setRefreshing(false);
    }
  };

  const handleRecompute = async () => {
    const API = getApiUrl();
    setRecomputing(true);
    try {
      await fetch(`${API}/api/news/recompute?recalculate_snapshots=true`, { method: "POST" });
      await loadData();
    } catch (err) {
      console.error("News full recompute failed:", err);
    } finally {
      setRecomputing(false);
    }
  };

  const categoryOptions = useMemo(() => {
    if (!matrix) return [];
    return matrix.rows.map((r) => ({ code: r.event_type, label: r.event_type_label }));
  }, [matrix]);

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", height: "100%" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 16,
          marginBottom: 22,
        }}
      >
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>
            📰 News &amp; Event Intelligence
          </h1>
          <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
            Clustered global oil &amp; tanker events ranked in real time and paired with learned
            Brent / WTI price-reaction intervals (-24h to +14d).
          </p>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div
            style={{
              display: "flex",
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {(["BZ=F", "CL=F"] as const).map((sym) => (
              <button
                key={sym}
                onClick={() => setSymbol(sym)}
                style={{
                  padding: "7px 14px",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 600,
                  background: symbol === sym ? "var(--accent-cyan)" : "transparent",
                  color: symbol === sym ? "#0a0e17" : "var(--text-secondary)",
                }}
              >
                {sym === "BZ=F" ? "Brent (BZ=F)" : "WTI (CL=F)"}
              </button>
            ))}
          </div>

          <button
            onClick={handleRefresh}
            disabled={refreshing || recomputing}
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              border: "1px solid var(--accent-blue)",
              background: "rgba(59, 130, 246, 0.16)",
              color: "#93c5fd",
              fontSize: 12,
              fontWeight: 600,
              cursor: refreshing ? "wait" : "pointer",
            }}
          >
            {refreshing ? "Fetching & Analyzing…" : "↻ Fetch New"}
          </button>

          <button
            onClick={handleRecompute}
            disabled={recomputing || refreshing}
            title="Replay all raw Bronze articles through current classification, clustering, price-impact learning, and ML snapshots"
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--bg-card)",
              color: "var(--text-secondary)",
              fontSize: 12,
              fontWeight: 600,
              cursor: recomputing ? "wait" : "pointer",
            }}
          >
            {recomputing ? "Replaying Pipeline…" : "⚙️ Recompute All"}
          </button>
        </div>
      </div>

      {/* Top KPI Strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: 14,
          marginBottom: 22,
        }}
      >
        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 16,
          }}
        >
          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            24h Clustered Events
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>
            {summary?.events_24h ?? 0}
            <span style={{ fontSize: 12, color: "#f59e0b", marginLeft: 8, fontWeight: 600 }}>
              {summary?.high_impact_events_24h ?? 0} high-rank
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
            {summary?.total_events_30d ?? 0} unique events tracked in last 30d
          </div>
        </div>

        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 16,
          }}
        >
          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            24h Net News Pressure
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              marginTop: 4,
              color:
                (summary?.net_pressure_24h ?? 0) >= 0.4
                  ? "#34d399"
                  : (summary?.net_pressure_24h ?? 0) <= -0.4
                    ? "#f87171"
                    : "var(--text-primary)",
            }}
          >
            {summary?.pressure_label ?? "Loading…"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
            Weighted score:{" "}
            {summary ? `${summary.net_pressure_24h >= 0 ? "+" : ""}${summary.net_pressure_24h.toFixed(2)}` : "—"}
          </div>
        </div>

        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 16,
          }}
        >
          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Strongest Learned Driver (Brent)
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, marginTop: 6, color: "var(--accent-cyan)" }}>
            {summary?.strongest_learned_driver?.event_type_label ?? "Gathering event history…"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
            {summary?.strongest_learned_driver?.peak_horizon
              ? `Peak ${HORIZON_LABELS[summary.strongest_learned_driver.peak_horizon] ?? summary.strongest_learned_driver.peak_horizon}: ${fmtPct(summary.strongest_learned_driver.peak_mean_ret_pct)} (${summary.strongest_learned_driver.peak_hit_rate_pct ?? "—"}% hit)`
              : "Click Fetch & Recompute to populate"}
          </div>
        </div>

        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 16,
          }}
        >
          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Scoring Engine
          </div>
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              marginTop: 6,
              color: summary?.llm_enabled ? "#a78bfa" : "#38bdf8",
            }}
          >
            {summary?.llm_enabled ? `Hybrid + LLM (${summary.llm_provider})` : "Local Rules + Empirical Lag"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
            {summary?.llm_enabled
              ? "Top clusters enriched with optional LLM"
              : "Zero-cost mode · Optional LLM disabled"}
          </div>
        </div>
      </div>

      {/* Latest AI Market Briefing (generated by GitHub Actions AI Analyst) */}
      <div
        style={{
          background: "linear-gradient(135deg, rgba(26, 35, 50, 0.96), rgba(17, 24, 39, 0.96))",
          border: "1px solid rgba(56, 189, 248, 0.28)",
          borderRadius: 12,
          padding: 18,
          marginBottom: 24,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
            marginBottom: briefing ? 10 : 4,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h2 style={{ fontSize: 16, fontWeight: 700 }}>🤖 AI Market Analyst Briefing</h2>
            {briefing && (
              <span
                style={{
                  padding: "3px 9px",
                  borderRadius: 999,
                  fontSize: 11,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  background:
                    briefing.market_bias === "bullish"
                      ? "rgba(16, 185, 129, 0.18)"
                      : briefing.market_bias === "bearish"
                        ? "rgba(239, 68, 68, 0.18)"
                        : "rgba(148, 163, 184, 0.18)",
                  color:
                    briefing.market_bias === "bullish"
                      ? "#34d399"
                      : briefing.market_bias === "bearish"
                        ? "#f87171"
                        : "#cbd5e1",
                }}
              >
                {briefing.market_bias}
                {briefing.confidence !== null ? ` · ${Math.round(briefing.confidence * 100)}% conf` : ""}
              </span>
            )}
          </div>
          {briefing && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {new Date(briefing.created_at).toLocaleString()}
              {briefing.model_used ? ` · ${briefing.model_used}` : ""}
            </span>
          )}
        </div>

        {briefing ? (
          <div>
            <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--text-primary)", marginBottom: 12 }}>
              {briefing.executive_summary}
            </p>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: 14,
              }}
            >
              {briefing.key_points.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", textTransform: "uppercase", marginBottom: 6 }}>
                    Key Points
                  </div>
                  <ul style={{ paddingLeft: 18, fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 5 }}>
                    {briefing.key_points.map((pt, idx) => (
                      <li key={idx}>{pt}</li>
                    ))}
                  </ul>
                </div>
              )}
              {briefing.watch_items.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "#fbbf24", textTransform: "uppercase", marginBottom: 6 }}>
                    What to Watch Next
                  </div>
                  <ul style={{ paddingLeft: 18, fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 5 }}>
                    {briefing.watch_items.map((w, idx) => (
                      <li key={idx}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        ) : (
          <p style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            No AI briefing stored yet. Run the <strong>AI Market Briefing Agent</strong> GitHub Action
            (scheduled twice daily at 07:00 &amp; 19:00 UTC, or trigger manually in GitHub Actions) to
            analyze <code>/api/agent-intel/context</code> and publish the executive summary &amp; key points here.
          </p>
        )}
      </div>

      {/* Section 1: Category x Time-Interval Impact Heatmap Matrix */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: 18,
          marginBottom: 24,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
            marginBottom: 14,
          }}
        >
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 700 }}>
              ⏱️ Learned Price-Impact Matrix ({symbol === "BZ=F" ? "Brent" : "WTI"}) — Which News Moves Oil &amp; When
            </h2>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Each cell shows mean return (%) and volatility-normalized z-score across horizons. Green = bullish move, Red = bearish move.
              Click any category row to filter the event feed below.
            </p>
          </div>
          {matrix?.as_of_date && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Walk-forward priors as of {matrix.as_of_date}
            </span>
          )}
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: "4px 4px", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Event Category</th>
                <th style={{ padding: "6px 8px", textAlign: "center" }}>Peak Window</th>
                <th style={{ padding: "6px 8px", textAlign: "center" }}>Behavior</th>
                {(matrix?.horizon_order ?? ["pre_24h", "pre_4h", "1h", "4h", "24h", "3d", "7d", "14d"]).map((h) => (
                  <th key={h} style={{ padding: "6px 8px", textAlign: "center", minWidth: 74 }}>
                    {HORIZON_LABELS[h] ?? h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(matrix?.rows ?? []).map((row) => {
                const isSelected = categoryFilter === row.event_type;
                return (
                  <tr
                    key={row.event_type}
                    onClick={() =>
                      setCategoryFilter(isSelected ? "all" : row.event_type)
                    }
                    style={{
                      cursor: "pointer",
                      background: isSelected ? "rgba(6, 182, 212, 0.12)" : "transparent",
                    }}
                  >
                    <td style={{ padding: "8px 10px", fontWeight: 600 }}>
                      <div>{row.event_type_label}</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {row.total_events} event{row.total_events === 1 ? "" : "s"}
                        {row.half_life_hours ? ` · t½ ~${Math.round(row.half_life_hours)}h` : ""}
                      </div>
                    </td>
                    <td style={{ padding: "8px 8px", textAlign: "center" }}>
                      {row.peak_horizon ? (
                        <span
                          style={{
                            padding: "3px 8px",
                            borderRadius: 999,
                            background: "rgba(6, 182, 212, 0.18)",
                            color: "#22d3ee",
                            fontWeight: 700,
                            fontSize: 11,
                          }}
                        >
                          {HORIZON_LABELS[row.peak_horizon] ?? row.peak_horizon}
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </td>
                    <td style={{ padding: "8px 8px", textAlign: "center", fontSize: 11, color: "var(--text-secondary)" }}>
                      {row.persistence_label}
                    </td>
                    {(matrix?.horizon_order ?? []).map((h) => {
                      const cell = row.horizons[h];
                      const isPeak = row.peak_horizon === h;
                      return (
                        <td
                          key={h}
                          title={
                            cell && cell.sample_count > 0
                              ? `n=${cell.sample_count}, mean=${fmtPct(cell.mean_ret_pct)}, z=${cell.mean_z ?? "—"}, hit=${cell.hit_rate_pct ?? "—"}%, t=${cell.t_stat ?? "—"}`
                              : "No matured observations yet"
                          }
                          style={{
                            padding: "7px 6px",
                            textAlign: "center",
                            borderRadius: 6,
                            background: cellBgForZ(cell),
                            border: isPeak ? "1px solid #22d3ee" : "1px solid rgba(255,255,255,0.04)",
                          }}
                        >
                          {cell && cell.sample_count > 0 ? (
                            <>
                              <div style={{ fontWeight: 700, color: "#f8fafc" }}>
                                {fmtPct(cell.mean_ret_pct)}
                              </div>
                              <div style={{ fontSize: 10, color: "rgba(241,245,249,0.75)" }}>
                                {cell.hit_rate_pct !== null ? `${Math.round(cell.hit_rate_pct)}%` : "—"} · n={cell.sample_count}
                              </div>
                            </>
                          ) : (
                            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>—</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 2: Ranked News Event Feed */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: 18,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
            marginBottom: 16,
          }}
        >
          <h2 style={{ fontSize: 16, fontWeight: 700 }}>
            🔥 Ranked Maritime &amp; Oil Events ({events.length})
          </h2>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              <option value="all">All Categories</option>
              {categoryOptions.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.label}
                </option>
              ))}
            </select>

            <select
              value={chokepointFilter}
              onChange={(e) => setChokepointFilter(e.target.value)}
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              {CHOKEPOINT_FILTERS.map((cp) => (
                <option key={cp} value={cp}>
                  {cp}
                </option>
              ))}
            </select>

            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value) as 7 | 14 | 30 | 90)}
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
              <option value={30}>Last 30d</option>
              <option value={90}>Last 90d</option>
            </select>

            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "rank" | "recent" | "learned")}
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              <option value="rank">Sort: Prior Rank</option>
              <option value="learned">Sort: Learned Impact</option>
              <option value="recent">Sort: Most Recent</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-muted)" }}>
            Loading ranked news events…
          </div>
        ) : events.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-muted)" }}>
            No matching news events yet. Click <strong>↻ Fetch &amp; Recompute</strong> above to ingest live free feeds.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {events.map((ev) => {
              const dir = directionBadge(ev.effective_direction);
              const isOpen = expandedId === ev.id;
              const activeImpact = symbol === "BZ=F" ? ev.brent_impact : ev.wti_impact;
              return (
                <div
                  key={ev.id}
                  style={{
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: 10,
                    padding: 14,
                  }}
                >
                  <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                    {/* Rank Badge */}
                    <div
                      style={{
                        minWidth: 50,
                        height: 50,
                        borderRadius: 10,
                        background: "rgba(15, 23, 42, 0.9)",
                        border: `2px solid ${rankColor(ev.prior_rank_score)}`,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <span style={{ fontSize: 16, fontWeight: 800, color: rankColor(ev.prior_rank_score) }}>
                        {Math.round(ev.prior_rank_score)}
                      </span>
                      <span style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
                        rank
                      </span>
                    </div>

                    {/* Main Content */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
                        <span
                          style={{
                            padding: "2px 8px",
                            borderRadius: 999,
                            background: dir.bg,
                            color: dir.color,
                            fontSize: 11,
                            fontWeight: 700,
                          }}
                        >
                          {dir.label}
                        </span>
                        <span
                          style={{
                            padding: "2px 8px",
                            borderRadius: 999,
                            background: "rgba(59, 130, 246, 0.16)",
                            color: "#93c5fd",
                            fontSize: 11,
                            fontWeight: 600,
                          }}
                        >
                          {ev.event_type_label}
                        </span>
                        {ev.chokepoints.map((cp) => (
                          <span
                            key={cp}
                            style={{
                              padding: "2px 8px",
                              borderRadius: 999,
                              background: "rgba(245, 158, 11, 0.15)",
                              color: "#fbbf24",
                              fontSize: 11,
                            }}
                          >
                            ⚓ {cp}
                          </span>
                        ))}
                        {ev.llm_enriched && (
                          <span
                            style={{
                              padding: "2px 7px",
                              borderRadius: 999,
                              background: "rgba(139, 92, 246, 0.2)",
                              color: "#c4b5fd",
                              fontSize: 10,
                              fontWeight: 600,
                            }}
                          >
                            ✨ LLM Verified
                          </span>
                        )}
                      </div>

                      <div
                        onClick={() => setExpandedId(isOpen ? null : ev.id)}
                        style={{
                          fontSize: 15,
                          fontWeight: 600,
                          color: "var(--text-primary)",
                          cursor: "pointer",
                          marginBottom: 6,
                        }}
                      >
                        {ev.headline}
                      </div>

                      <div
                        style={{
                          fontSize: 11,
                          color: "var(--text-muted)",
                          display: "flex",
                          gap: 12,
                          flexWrap: "wrap",
                          marginBottom: 10,
                        }}
                      >
                        <span>First seen: {new Date(ev.first_seen_at).toLocaleString()}</span>
                        <span>
                          {ev.distinct_sources} source{ev.distinct_sources === 1 ? "" : "s"} ({ev.article_count} article
                          {ev.article_count === 1 ? "" : "s"})
                        </span>
                        <span>Tier {ev.max_source_tier}</span>
                        {ev.learned_impact_score !== null && (
                          <span style={{ color: "var(--accent-cyan)" }}>
                            Learned score: {ev.learned_impact_score.toFixed(2)}σ
                          </span>
                        )}
                      </div>

                      {/* Similarity-Based Predicted Oil Impact Banner */}
                      {ev.prediction && (
                        <div
                          data-testid="event-prediction-banner"
                          style={{
                            marginBottom: 10,
                            padding: "8px 12px",
                            borderRadius: 8,
                            background:
                              ev.prediction.direction === "bullish"
                                ? "rgba(16, 185, 129, 0.10)"
                                : ev.prediction.direction === "bearish"
                                  ? "rgba(239, 68, 68, 0.10)"
                                  : "rgba(100, 116, 139, 0.12)",
                            border:
                              ev.prediction.direction === "bullish"
                                ? "1px solid rgba(16, 185, 129, 0.28)"
                                : ev.prediction.direction === "bearish"
                                  ? "1px solid rgba(239, 68, 68, 0.28)"
                                  : "1px solid rgba(148, 163, 184, 0.22)",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            gap: 12,
                            flexWrap: "wrap",
                          }}
                        >
                          <div style={{ fontSize: 12, color: "var(--text-primary)", flex: 1, minWidth: 220 }}>
                            <strong style={{ color: "#38bdf8" }}>🔮 Predicted Oil Impact:</strong>{" "}
                            {ev.prediction.narrative}
                          </div>
                          <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0, flexWrap: "wrap" }}>
                            <span
                              style={{
                                fontSize: 13,
                                fontWeight: 800,
                                color:
                                  ev.prediction.direction === "bullish"
                                    ? "#34d399"
                                    : ev.prediction.direction === "bearish"
                                      ? "#f87171"
                                      : "#cbd5e1",
                              }}
                            >
                              {symbol === "BZ=F" ? "Brent" : "WTI"}{" "}
                              {ev.prediction.expected_return_pct >= 0 ? "+" : ""}
                              {ev.prediction.expected_return_pct.toFixed(2)}% ({ev.prediction.peak_horizon})
                            </span>
                            <span
                              style={{
                                fontSize: 11,
                                padding: "2px 7px",
                                borderRadius: 999,
                                background: "rgba(15, 23, 42, 0.7)",
                                color: "var(--text-secondary)",
                                border: "1px solid var(--border)",
                              }}
                            >
                              {ev.prediction.confidence_pct.toFixed(0)}% conf · {ev.prediction.analog_count} analogs
                            </span>
                            <button
                              type="button"
                              onClick={() => setExpandedId(isOpen ? null : ev.id)}
                              style={{
                                fontSize: 11,
                                color: "#38bdf8",
                                background: "transparent",
                                border: "none",
                                cursor: "pointer",
                                padding: "2px 4px",
                                fontWeight: 600,
                              }}
                            >
                              {isOpen ? "Hide memory ▲" : "View analogs ▼"}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Realized Price Reaction Strip */}
                      <ReactionStrip
                        impact={activeImpact}
                        label={symbol === "BZ=F" ? "Brent:" : "WTI:"}
                      />

                      {/* Expandable Rationale, Matched Historical Analogs & Source Links */}
                      {isOpen && (
                        <div
                          style={{
                            marginTop: 12,
                            paddingTop: 10,
                            borderTop: "1px solid var(--border)",
                            fontSize: 12,
                            color: "var(--text-secondary)",
                          }}
                        >
                          {ev.prediction && ev.prediction.analogs.length > 0 && (
                            <div style={{ marginBottom: 12 }}>
                              <div
                                style={{
                                  fontSize: 11,
                                  fontWeight: 700,
                                  marginBottom: 6,
                                  color: "#38bdf8",
                                  textTransform: "uppercase",
                                  letterSpacing: 0.4,
                                }}
                              >
                                🧠 Historical Memory — Similar Past News &amp; Actual{" "}
                                {symbol === "BZ=F" ? "Brent" : "WTI"} Moves
                              </div>
                              <div style={{ overflowX: "auto" }}>
                                <table
                                  style={{
                                    width: "100%",
                                    borderCollapse: "collapse",
                                    fontSize: 11,
                                    background: "rgba(15, 23, 42, 0.65)",
                                    borderRadius: 6,
                                  }}
                                >
                                  <thead>
                                    <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-muted)" }}>
                                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Match</th>
                                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Past Event</th>
                                      <th style={{ textAlign: "right", padding: "6px 8px" }}>+4h</th>
                                      <th style={{ textAlign: "right", padding: "6px 8px" }}>+24h</th>
                                      <th style={{ textAlign: "right", padding: "6px 8px" }}>+3d</th>
                                      <th style={{ textAlign: "right", padding: "6px 8px" }}>+7d</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {ev.prediction.analogs.map((an) => (
                                      <tr
                                        key={an.event_id}
                                        style={{ borderBottom: "1px solid rgba(148, 163, 184, 0.1)" }}
                                      >
                                        <td style={{ padding: "6px 8px", fontWeight: 700, color: "#38bdf8" }}>
                                          {Math.round(an.similarity * 100)}%
                                        </td>
                                        <td style={{ padding: "6px 8px", color: "var(--text-primary)" }}>
                                          <div>{an.headline}</div>
                                          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                                            {new Date(an.first_seen_at).toLocaleDateString()} · {an.event_type_label}
                                          </div>
                                        </td>
                                        {[an.ret_4h_pct, an.ret_24h_pct, an.ret_3d_pct, an.ret_7d_pct].map(
                                          (val, i) => (
                                            <td
                                              key={i}
                                              style={{
                                                padding: "6px 8px",
                                                textAlign: "right",
                                                fontWeight: 600,
                                                color:
                                                  val === null
                                                    ? "var(--text-muted)"
                                                    : val > 0.1
                                                      ? "#34d399"
                                                      : val < -0.1
                                                        ? "#f87171"
                                                        : "#cbd5e1",
                                              }}
                                            >
                                              {val === null ? "—" : `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`}
                                            </td>
                                          )
                                        )}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}
                          {ev.llm_rationale && (
                            <p style={{ marginBottom: 8, color: "#ddd6fe" }}>
                              <strong>Analyst Rationale:</strong> {ev.llm_rationale}
                              {ev.llm_estimated_mbpd ? ` (~${ev.llm_estimated_mbpd} mb/d at risk)` : ""}
                            </p>
                          )}
                          {ev.summary && !ev.llm_rationale && (
                            <p style={{ marginBottom: 8 }}>{ev.summary}</p>
                          )}
                          {ev.articles.length > 0 && (
                            <div>
                              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--text-muted)" }}>
                                Linked Source Reports:
                              </div>
                              <ul style={{ paddingLeft: 16, display: "flex", flexDirection: "column", gap: 4 }}>
                                {ev.articles.map((a) => (
                                  <li key={a.id}>
                                    <a
                                      href={a.url}
                                      target="_blank"
                                      rel="noreferrer"
                                      style={{ color: "#38bdf8", textDecoration: "none" }}
                                    >
                                      [{a.source}] {a.title}
                                    </a>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
