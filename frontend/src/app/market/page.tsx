"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { getApiUrl } from "@/lib/config";
import type { MarketSnapshotResponse } from "@/lib/types";

// Dynamically import Recharts (prevents SSR hydration errors)
const ComposedChart = dynamic(() => import("recharts").then((m) => m.ComposedChart), { ssr: false });
const Line = dynamic(() => import("recharts").then((m) => m.Line), { ssr: false });
const Bar = dynamic(() => import("recharts").then((m) => m.Bar), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const Tooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const Legend = dynamic(() => import("recharts").then((m) => m.Legend), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });

type FeatureKey = Exclude<keyof MarketSnapshotResponse, "snapshot_date" | "brent_close_usd" | "wti_close_usd">;
type TargetMode = "level" | "pct_change";
type LagDays = 0 | 1 | 3 | 7;

type FeatureGroup = "fleet" | "strait" | "news";

const FEATURE_OPTIONS: { key: FeatureKey; label: string; color: string; group: FeatureGroup }[] = [
  { key: "total_active_vessels", label: "Total Active Vessels", color: "#06b6d4", group: "fleet" },
  { key: "vessels_in_transit", label: "Vessels In Transit", color: "#3b82f6", group: "fleet" },
  { key: "vessels_at_port", label: "Vessels At Port", color: "#10b981", group: "fleet" },
  { key: "avg_fleet_speed", label: "Average Fleet Speed", color: "#14b8a6", group: "fleet" },
  { key: "vessels_idle_gt_48h", label: "Vessels Idle >48h", color: "#f97316", group: "fleet" },
  { key: "dark_vessels_count", label: "Global Dark Fleet", color: "#ef4444", group: "fleet" },
  { key: "new_ais_gaps_24h", label: "New AIS Gaps", color: "#f43f5e", group: "fleet" },
  { key: "resolved_gaps_24h", label: "Resolved AIS Gaps", color: "#84cc16", group: "fleet" },
  { key: "avg_gap_duration_hours", label: "Avg Gap Duration (hrs)", color: "#ec4899", group: "fleet" },
  { key: "sts_events_24h", label: "STS Events", color: "#8b5cf6", group: "fleet" },
  { key: "sts_confirmed_24h", label: "Confirmed STS Events", color: "#a855f7", group: "fleet" },
  { key: "cargo_events_24h", label: "Cargo Events", color: "#f59e0b", group: "fleet" },
  { key: "estimated_volume_barrels_24h", label: "Estimated Daily Volume", color: "#eab308", group: "fleet" },
  { key: "chokepoint_transits_24h", label: "All Chokepoint Transits", color: "#6366f1", group: "strait" },
  { key: "other_straits_transits", label: "Other Straits Transits (excl. Hormuz & Bab)", color: "#22d3ee", group: "strait" },
  { key: "other_straits_traffic_share_pct", label: "Other Straits Traffic Share (%)", color: "#38bdf8", group: "strait" },
  { key: "cape_of_good_hope_transits", label: "Cape of Good Hope Transits", color: "#10b981", group: "strait" },
  { key: "strait_of_hormuz_transits", label: "Strait of Hormuz Transits", color: "#0ea5e9", group: "strait" },
  { key: "bab_el_mandeb_transits", label: "Bab el-Mandeb Transits", color: "#f43f5e", group: "strait" },
  { key: "suez_canal_transits", label: "Suez Canal Transits", color: "#a855f7", group: "strait" },
  { key: "strait_of_malacca_transits", label: "Strait of Malacca Transits", color: "#f97316", group: "strait" },
  { key: "panama_canal_transits", label: "Panama Canal Transits", color: "#eab308", group: "strait" },
  { key: "turkish_straits_transits", label: "Turkish Straits (Bosporus) Transits", color: "#ec4899", group: "strait" },
  { key: "danish_straits_transits", label: "Danish Straits Transits", color: "#84cc16", group: "strait" },
  { key: "other_straits_avg_transit_min", label: "Other Straits Avg Delay (min)", color: "#f59e0b", group: "strait" },
  { key: "cape_of_good_hope_avg_transit_min", label: "Cape of Good Hope Avg Delay (min)", color: "#34d399", group: "strait" },
  { key: "hormuz_bab_avg_transit_min", label: "Hormuz & Bab el-Mandeb Avg Delay (min)", color: "#fb7185", group: "strait" },
  { key: "avg_chokepoint_transit_min", label: "All Chokepoints Avg Delay (min)", color: "#818cf8", group: "strait" },
  { key: "portwatch_cape_tankers", label: "Cape of Good Hope Tankers (PortWatch)", color: "#2dd4bf", group: "strait" },
  { key: "portwatch_other_straits_tankers", label: "Other Straits Tankers (PortWatch)", color: "#60a5fa", group: "strait" },
  { key: "portwatch_hormuz_tankers", label: "Strait of Hormuz Tankers (PortWatch)", color: "#38bdf8", group: "strait" },
  { key: "portwatch_hormuz_capacity_tanker", label: "Hormuz Tanker Capacity (PortWatch)", color: "#0284c7", group: "strait" },
  { key: "portwatch_bab_el_mandeb_tankers", label: "Bab el-Mandeb Tankers (PortWatch)", color: "#fb7185", group: "strait" },
  { key: "portwatch_suez_tankers", label: "Suez Canal Tankers (PortWatch)", color: "#c084fc", group: "strait" },
  { key: "hormuz_crisis_pressure", label: "Hormuz Crisis Pressure Index (Straits.live)", color: "#ef4444", group: "strait" },
  { key: "hormuz_escalation_prob", label: "Hormuz Escalation Prob % (Straits.live)", color: "#f97316", group: "strait" },
  { key: "hormuz_stranded_vessels", label: "Persian Gulf Stranded Ships (Straits.live)", color: "#eab308", group: "strait" },
  { key: "hormuz_incidents_count", label: "Hormuz Maritime Incidents (Straits.live)", color: "#f43f5e", group: "strait" },
  { key: "gdelt_strait_risk_vol", label: "Hormuz & Red Sea News Risk Vol (GDELT)", color: "#f59e0b", group: "strait" },
  { key: "gdelt_strait_tone", label: "Hormuz & Red Sea News Tone (GDELT)", color: "#a855f7", group: "strait" },
  { key: "news_net_impact_1d", label: "News Net Impact (1d Decayed)", color: "#22c55e", group: "news" },
  { key: "news_net_impact_7d", label: "News Net Impact (7d Decayed)", color: "#10b981", group: "news" },
  { key: "news_supply_risk_score", label: "News Supply Risk Score", color: "#ef4444", group: "news" },
  { key: "news_sanctions_pressure_7d", label: "News Sanctions Pressure (7d)", color: "#f97316", group: "news" },
  { key: "news_opec_signal_7d", label: "News OPEC+ Signal (7d)", color: "#3b82f6", group: "news" },
  { key: "news_event_count_24h", label: "News Ranked Events (24h)", color: "#06b6d4", group: "news" },
  { key: "news_max_rank_24h", label: "News Max Event Rank (24h)", color: "#f59e0b", group: "news" },
  { key: "news_hormuz_x_transit_drop", label: "Hormuz News x Transit Drop (AIS Confirm)", color: "#f43f5e", group: "news" },
  { key: "news_sanctions_x_dark_fleet", label: "Sanctions News x Dark Fleet Gaps", color: "#a855f7", group: "news" },
];

function addDaysIso(isoDate: string, days: number): string {
  if (days === 0) return isoDate;
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

function pearsonR(pairs: [number, number][]): number | null {
  const n = pairs.length;
  if (n < 3) return null;
  let sumX = 0;
  let sumY = 0;
  for (const [x, y] of pairs) {
    sumX += x;
    sumY += y;
  }
  const meanX = sumX / n;
  const meanY = sumY / n;
  let num = 0;
  let denX = 0;
  let denY = 0;
  for (const [x, y] of pairs) {
    const dx = x - meanX;
    const dy = y - meanY;
    num += dx * dy;
    denX += dx * dx;
    denY += dy * dy;
  }
  if (denX <= 0 || denY <= 0) return null;
  const r = num / Math.sqrt(denX * denY);
  return Math.max(-1, Math.min(1, r));
}

function formatR(r: number | null): string {
  if (r === null) return "—";
  return `${r >= 0 ? "+" : ""}${r.toFixed(2)}`;
}

export default function MarketIntelligencePage() {
  const [data, setData] = useState<MarketSnapshotResponse[]>([]);
  const [portCongestion, setPortCongestion] = useState<import("@/lib/types").PortCongestionItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [recalculating, setRecalculating] = useState<boolean>(false);
  const [selectedFeature, setSelectedFeature] = useState<FeatureKey>("total_active_vessels");
  const [targetMode, setTargetMode] = useState<TargetMode>("level");
  const [lagDays, setLagDays] = useState<LagDays>(0);
  const [featureGroupFilter, setFeatureGroupFilter] = useState<"all" | FeatureGroup>("all");
  const [windowDays, setWindowDays] = useState<90 | 180 | 365 | 730>(90);

  const fetchSnapshots = useCallback(async () => {
    const API = getApiUrl();
    try {
      const [snapRes, portRes] = await Promise.allSettled([
        fetch(`${API}/api/global-oil-features/snapshots?days=${windowDays}`),
        fetch(`${API}/api/ports/congestion?limit=15`),
      ]);
      if (snapRes.status === "fulfilled" && snapRes.value.ok) {
        const json: MarketSnapshotResponse[] = await snapRes.value.json();
        const sortedData = json.sort(
          (a, b) => new Date(a.snapshot_date).getTime() - new Date(b.snapshot_date).getTime()
        );
        setData(sortedData);
      }
      if (portRes.status === "fulfilled" && portRes.value.ok) {
        setPortCongestion(await portRes.value.json());
      }
    } catch (err) {
      console.error("Failed to load market features", err);
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => {
    fetchSnapshots();
  }, [fetchSnapshots]);

  const handleRecalculate = async () => {
    setRecalculating(true);
    const API = getApiUrl();
    try {
      const res = await fetch(`${API}/api/global-oil-features/recalculate?days=${windowDays}`, {
        method: "POST",
      });
      if (res.ok) {
        await fetchSnapshots();
      }
    } catch (err) {
      console.error("Failed to recalculate market features", err);
    } finally {
      setRecalculating(false);
    }
  };

  if (loading) {
    return <div className="page-body"><div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>Loading Market Intelligence data...</div></div>;
  }

  const latest = [...data].reverse().find((d) => d.total_active_vessels != null) ?? null;
  const latestBrent = [...data].reverse().find((d) => d.brent_close_usd != null) ?? null;
  const latestWti = [...data].reverse().find((d) => d.wti_close_usd != null) ?? null;

  // Format the dataset so recharts handles null numbers (like missing weekend oil prices) gracefully
  const chartData = data.map((d) => ({
    ...d,
    brent_close_usd: d.brent_close_usd ?? undefined,
    wti_close_usd: d.wti_close_usd ?? undefined,
  }));

  // Precompute target price/return lookup maps by date
  const brentTargetByDate = new Map<string, number>();
  const wtiTargetByDate = new Map<string, number>();
  let prevBrent: number | null = null;
  let prevWti: number | null = null;
  for (const row of data) {
    if (row.brent_close_usd != null && Number.isFinite(row.brent_close_usd)) {
      if (targetMode === "level") {
        brentTargetByDate.set(row.snapshot_date, row.brent_close_usd);
      } else if (prevBrent != null && prevBrent !== 0) {
        brentTargetByDate.set(row.snapshot_date, ((row.brent_close_usd - prevBrent) / prevBrent) * 100);
      }
      prevBrent = row.brent_close_usd;
    }
    if (row.wti_close_usd != null && Number.isFinite(row.wti_close_usd)) {
      if (targetMode === "level") {
        wtiTargetByDate.set(row.snapshot_date, row.wti_close_usd);
      } else if (prevWti != null && prevWti !== 0) {
        wtiTargetByDate.set(row.snapshot_date, ((row.wti_close_usd - prevWti) / prevWti) * 100);
      }
      prevWti = row.wti_close_usd;
    }
  }

  const correlationRows = FEATURE_OPTIONS.map((opt) => {
    const brentPairs: [number, number][] = [];
    const wtiPairs: [number, number][] = [];
    for (const row of data) {
      const featureVal = row[opt.key];
      if (typeof featureVal !== "number" || !Number.isFinite(featureVal)) continue;
      const targetDate = addDaysIso(row.snapshot_date, lagDays);
      const brentVal = brentTargetByDate.get(targetDate);
      if (brentVal != null && Number.isFinite(brentVal)) {
        brentPairs.push([featureVal, brentVal]);
      }
      const wtiVal = wtiTargetByDate.get(targetDate);
      if (wtiVal != null && Number.isFinite(wtiVal)) {
        wtiPairs.push([featureVal, wtiVal]);
      }
    }
    const rBrent = pearsonR(brentPairs);
    const rWti = pearsonR(wtiPairs);
    return {
      ...opt,
      nBrent: brentPairs.length,
      rBrent,
      rWti,
    };
  }).sort((a, b) => {
    const aSufficient = a.nBrent >= 10 && a.rBrent != null ? 1 : 0;
    const bSufficient = b.nBrent >= 10 && b.rBrent != null ? 1 : 0;
    if (aSufficient !== bSufficient) return bSufficient - aSufficient;
    const absA = a.rBrent != null ? Math.abs(a.rBrent) : -1;
    const absB = b.rBrent != null ? Math.abs(b.rBrent) : -1;
    return absB - absA;
  });

  const activeFeatureConfig = FEATURE_OPTIONS.find((o) => o.key === selectedFeature) || FEATURE_OPTIONS[0];

  return (
    <>
      <div className="page-header">
        <h2>Market Intelligence</h2>
        <p>Analyze global macro-fleet ML features mapped directly against crude oil pricing trends.</p>
      </div>

      <div className="page-body">
        {/* KPI Cards */}
        {latest && (
          <div className="stats-grid animate-in" style={{ marginBottom: 24, gridTemplateColumns: "repeat(4, 1fr)" }}>
            <div className="stat-card" style={{ "--card-accent": "var(--accent-amber)" } as React.CSSProperties}>
              <div className="stat-card-label">Brent Crude</div>
              <div className="stat-card-value">{latestBrent?.brent_close_usd != null ? `$${latestBrent.brent_close_usd.toFixed(2)}` : "—"}</div>
              <div className="stat-card-icon">📈</div>
              <div className="stat-card-sub">{latestBrent ? `Futures close · ${latestBrent.snapshot_date}` : "Current Global Benchmark"}</div>
            </div>

            <div className="stat-card" style={{ "--card-accent": "var(--accent-cyan)" } as React.CSSProperties}>
              <div className="stat-card-label">WTI Crude</div>
              <div className="stat-card-value">{latestWti?.wti_close_usd != null ? `$${latestWti.wti_close_usd.toFixed(2)}` : "—"}</div>
              <div className="stat-card-icon">🛢️</div>
              <div className="stat-card-sub">{latestWti ? `Futures close · ${latestWti.snapshot_date}` : "US Benchmark"}</div>
            </div>

            <div className="stat-card" style={{ "--card-accent": "var(--accent-blue)" } as React.CSSProperties}>
              <div className="stat-card-label">Global Active Fleet</div>
              <div className="stat-card-value">{latest.total_active_vessels}</div>
              <div className="stat-card-icon">🚢</div>
              <div className="stat-card-sub">Active tracked vessels today</div>
            </div>

            <div className="stat-card" style={{ "--card-accent": "var(--accent-red)" } as React.CSSProperties}>
              <div className="stat-card-label">Global Dark Fleet</div>
              <div className="stat-card-value">{latest.dark_vessels_count}</div>
              <div className="stat-card-icon">⚫</div>
              <div className="stat-card-sub">Vessels hiding locations</div>
            </div>
          </div>
        )}

        {/* The Dual-Axis ML Correlation Chart */}
        <div className="panel animate-in" style={{ animationDelay: "0.1s", marginBottom: 24 }}>
          <div className="panel-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div>
              <h3>🔍 ML Feature Correlations</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Last {windowDays === 365 ? "1 Year" : windowDays === 730 ? "2 Years" : `${windowDays} Days`}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div
                title="History window for charts and correlations"
                style={{ display: "inline-flex", background: "rgba(15, 23, 42, 0.7)", border: "1px solid var(--border)", borderRadius: 6, padding: 2 }}
              >
                {([
                  { days: 90, label: "90d" },
                  { days: 180, label: "180d" },
                  { days: 365, label: "1y" },
                  { days: 730, label: "2y" },
                ] as const).map((opt) => (
                  <button
                    key={opt.days}
                    type="button"
                    onClick={() => setWindowDays(opt.days)}
                    style={{
                      padding: "5px 9px",
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 4,
                      border: "none",
                      cursor: "pointer",
                      background: windowDays === opt.days ? "rgba(6, 182, 212, 0.2)" : "transparent",
                      color: windowDays === opt.days ? "var(--accent-cyan)" : "var(--text-muted)",
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              <button
                onClick={handleRecalculate}
                disabled={recalculating}
                title="Recalculate historical market features from vessel data"
                style={{
                  background: recalculating ? "rgba(6, 182, 212, 0.12)" : "rgba(6, 182, 212, 0.2)",
                  border: "1px solid rgba(6, 182, 212, 0.45)",
                  color: "#22d3ee",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: 600,
                  cursor: recalculating ? "not-allowed" : "pointer",
                  opacity: recalculating ? 0.7 : 1,
                }}
              >
                {recalculating ? "⏳ Recalculating..." : "🔄 Recalculate"}
              </button>

              <select 
                value={selectedFeature} 
                onChange={(e) => setSelectedFeature(e.target.value as FeatureKey)}
                style={{
                  background: "rgba(30, 41, 59, 0.8)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  fontSize: "14px",
                  outline: "none",
                  cursor: "pointer"
                }}
              >
                <optgroup label="Strait Traffic & Delays">
                  {FEATURE_OPTIONS.filter((opt) => opt.group === "strait").map((opt) => (
                    <option key={opt.key} value={opt.key}>{opt.label}</option>
                  ))}
                </optgroup>
                <optgroup label="Fleet, Dark Fleet & Cargo">
                  {FEATURE_OPTIONS.filter((opt) => opt.group === "fleet").map((opt) => (
                    <option key={opt.key} value={opt.key}>{opt.label}</option>
                  ))}
                </optgroup>
              </select>
            </div>
          </div>
          
          <div className="panel-body">
            {chartData.length === 0 ? (
              <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)", fontSize: 14 }}>No data available</div>
            ) : (
              <div className="chart-container" style={{ width: "100%", height: 380 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chartData} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a3548" vertical={false} />
                    <XAxis 
                      dataKey="snapshot_date" 
                         tick={{ fill: "#64748b", fontSize: 11 }} 
                      stroke="#2a3548" 
                      tickFormatter={(v) => v.slice(5)} // mm-dd
                    />
                    
                    {/* Left Axis: Oil Pricing (Yellow) */}
                     <YAxis 
                      yAxisId="left"
                      domain={['auto', 'auto']}
                      tick={{ fill: "var(--accent-amber)", fontSize: 11 }} 
                      stroke="#2a3548"
                      tickFormatter={(v) => `$${v}`}
                    />
                    
                     {/* Right Axis: Feature Metrics (Dynamic Color) */}
                     <YAxis 
                      yAxisId="right" 
                      orientation="right" 
                      domain={['auto', 'auto']}
                      tick={{ fill: activeFeatureConfig.color, fontSize: 11 }} 
                      stroke="#2a3548" 
                      tickFormatter={(v) => v >= 1000000 ? (v/1000000).toFixed(1) + "M" : v}
                    />

                    <Tooltip 
                      contentStyle={{
                        background: "#1a2332",
                        border: "1px solid #2a3548",
                        borderRadius: 8,
                        color: "#f1f5f9",
                        fontSize: 13,
                        fontWeight: 500
                      }}
                      itemStyle={{ padding: "2px 0" }}
                      labelStyle={{ color: "var(--accent-cyan)", marginBottom: "4px" }}
                    />
                    <Legend wrapperStyle={{ fontSize: '13px', paddingTop: '10px' }} />

                    {/* Plot the Selected Feature on the Right Axis as a Bar */}
                    <Bar 
                      yAxisId="right" 
                      dataKey={selectedFeature} 
                      name={activeFeatureConfig.label} 
                      fill={activeFeatureConfig.color} 
                      radius={[4, 4, 0, 0]} 
                      barSize={20}
                    />
                    
                     {/* Plot Brent Crude on the Left Axis as a Line */}
                    <Line 
                      yAxisId="left" 
                      type="monotone" 
                      dataKey="brent_close_usd" 
                      name="Brent Crude Price" 
                      stroke="var(--accent-amber)" 
                      strokeWidth={3}
                      dot={false}
                      activeDot={{ r: 6 }}
                      connectNulls={true} 
                    />
                     {/* Extra: WTI Crude faintly */}
                     <Line 
                      yAxisId="left" 
                      type="monotone" 
                      dataKey="wti_close_usd" 
                      name="WTI Price" 
                      stroke="var(--accent-purple)" 
                      strokeWidth={2}
                      strokeDasharray="4 4"
                      dot={false}
                      connectNulls={true} 
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>

        {/* Feature Correlation Ranking Panel */}
        <div className="panel animate-in" style={{ animationDelay: "0.15s", marginBottom: 24 }}>
          <div
            className="panel-header"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}
          >
            <div>
              <h3>📊 Feature Correlation Ranking</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Pearson r ({targetMode === "level" ? "Price Level" : "Daily % Change"}, lag {lagDays === 0 ? "0d" : `+${lagDays}d`}) · Click a row to plot above
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              {/* Feature Group Filter */}
              <div style={{ display: "inline-flex", background: "rgba(15, 23, 42, 0.7)", border: "1px solid var(--border)", borderRadius: 6, padding: 2 }}>
                {(
                  [
                    { id: "all", label: "All Features" },
                    { id: "strait", label: "Strait Traffic & Delays" },
                    { id: "news", label: "News Intel" },
                  ] as const
                ).map((grp) => (
                  <button
                    key={grp.id}
                    type="button"
                    onClick={() => setFeatureGroupFilter(grp.id)}
                    style={{
                      padding: "5px 10px",
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 4,
                      border: "none",
                      cursor: "pointer",
                      background: featureGroupFilter === grp.id ? "rgba(16, 185, 129, 0.2)" : "transparent",
                      color: featureGroupFilter === grp.id ? "var(--accent-emerald)" : "var(--text-muted)",
                    }}
                  >
                    {grp.label}
                  </button>
                ))}
              </div>

              {/* Target Mode Toggle */}
              <div style={{ display: "inline-flex", background: "rgba(15, 23, 42, 0.7)", border: "1px solid var(--border)", borderRadius: 6, padding: 2 }}>
                {(
                  [
                    { id: "level", label: "Price Level ($)" },
                    { id: "pct_change", label: "Daily Change (%)" },
                  ] as const
                ).map((mode) => (
                  <button
                    key={mode.id}
                    type="button"
                    onClick={() => setTargetMode(mode.id)}
                    style={{
                      padding: "5px 10px",
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 4,
                      border: "none",
                      cursor: "pointer",
                      background: targetMode === mode.id ? "rgba(6, 182, 212, 0.2)" : "transparent",
                      color: targetMode === mode.id ? "var(--accent-cyan)" : "var(--text-muted)",
                    }}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>

              {/* Lead/Lag Selector */}
              <div
                title="Feature on day t vs oil price on day t + lag"
                style={{ display: "inline-flex", background: "rgba(15, 23, 42, 0.7)", border: "1px solid var(--border)", borderRadius: 6, padding: 2 }}
              >
                {([0, 1, 3, 7] as const).map((lag) => (
                  <button
                    key={lag}
                    type="button"
                    onClick={() => setLagDays(lag)}
                    style={{
                      padding: "5px 9px",
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 4,
                      border: "none",
                      cursor: "pointer",
                      background: lagDays === lag ? "rgba(245, 158, 11, 0.2)" : "transparent",
                      color: lagDays === lag ? "var(--accent-amber)" : "var(--text-muted)",
                    }}
                  >
                    {lag === 0 ? "0d" : `+${lag}d`}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th style={{ width: 90 }}>Samples</th>
                  <th style={{ width: 110 }}>r vs Brent</th>
                  <th style={{ minWidth: 180 }}>Strength (Brent)</th>
                  <th style={{ width: 110 }}>r vs WTI</th>
                </tr>
              </thead>
              <tbody>
                {correlationRows
                  .filter((row) => featureGroupFilter === "all" || row.group === featureGroupFilter)
                  .map((row) => {
                  const isSelected = row.key === selectedFeature;
                  const rColor =
                    row.rBrent == null
                      ? "var(--text-muted)"
                      : Math.abs(row.rBrent) < 0.15
                      ? "var(--text-secondary)"
                      : row.rBrent > 0
                      ? "var(--accent-emerald)"
                      : "var(--accent-red)";
                  const wtiColor =
                    row.rWti == null
                      ? "var(--text-muted)"
                      : Math.abs(row.rWti) < 0.15
                      ? "var(--text-secondary)"
                      : row.rWti > 0
                      ? "var(--accent-emerald)"
                      : "var(--accent-red)";
                  const barPct = row.rBrent != null ? Math.round(Math.abs(row.rBrent) * 100) : 0;

                  const isLowSample = row.nBrent < 10;

                  return (
                    <tr
                      key={row.key}
                      onClick={() => setSelectedFeature(row.key)}
                      style={{
                        cursor: "pointer",
                        background: isSelected ? "rgba(6, 182, 212, 0.08)" : undefined,
                        opacity: isLowSample && !isSelected ? 0.55 : 1,
                      }}
                    >
                      <td style={{ fontWeight: isSelected ? 600 : 500, color: "var(--text-primary)" }}>
                        <span
                          style={{
                            display: "inline-block",
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            background: row.color,
                            marginRight: 8,
                          }}
                        />
                        {row.label}
                      </td>
                      <td style={{ color: isLowSample ? "var(--accent-amber)" : "var(--text-muted)", fontSize: 12 }}>
                        {row.rBrent != null
                          ? `n=${row.nBrent}${isLowSample ? " (low)" : ""}`
                          : row.nBrent < 3
                          ? `n=${row.nBrent}`
                          : "const"}
                      </td>
                      <td style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums", color: rColor }}>
                        {formatR(row.rBrent)}
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div
                            style={{
                              flex: 1,
                              height: 6,
                              borderRadius: 999,
                              background: "rgba(148, 163, 184, 0.15)",
                              overflow: "hidden",
                            }}
                          >
                            {row.rBrent != null && (
                              <div
                                style={{
                                  width: `${barPct}%`,
                                  height: "100%",
                                  borderRadius: 999,
                                  background: row.rBrent >= 0 ? "var(--accent-emerald)" : "var(--accent-red)",
                                  transition: "width 0.2s ease",
                                }}
                              />
                            )}
                          </div>
                        </div>
                      </td>
                      <td style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums", color: wtiColor }}>
                        {formatR(row.rWti)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Port Fullness & Anchorage Congestion */}
        <div className="panel animate-in" style={{ animationDelay: "0.18s", marginBottom: 24 }}>
          <div className="panel-header">
            <div>
              <h3>⚓ Port Fullness &amp; Anchorage Congestion</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Live tankers berthed vs. waiting at anchor outside major terminals
              </span>
            </div>
          </div>
          {portCongestion.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>
              Port state classifier warming up — live port counts will appear after the first 5-minute cycle.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Port / Terminal</th>
                    <th>Country</th>
                    <th>Berthed</th>
                    <th>Anchored (Waiting)</th>
                    <th>Avg Wait</th>
                    <th>Fullness (vs 90d Peak)</th>
                  </tr>
                </thead>
                <tbody>
                  {portCongestion.map((p) => (
                    <tr key={p.port_id}>
                      <td style={{ fontWeight: 600 }}>{p.name}</td>
                      <td style={{ color: "var(--text-muted)" }}>{p.country ?? "—"}</td>
                      <td style={{ color: "var(--accent-amber)", fontWeight: 600 }}>{p.berthed_count}</td>
                      <td style={{ color: p.anchored_waiting_count > 0 ? "#f97316" : "var(--text-muted)", fontWeight: 600 }}>
                        {p.anchored_waiting_count}
                      </td>
                      <td>{p.avg_wait_hours != null ? `${p.avg_wait_hours.toFixed(1)}h` : "—"}</td>
                      <td>{p.utilization_pct != null ? `${p.utilization_pct.toFixed(0)}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Data Grid table */}
        <div className="panel animate-in" style={{ animationDelay: "0.2s" }}>
          <div className="panel-header">
            <h3>🗄️ Raw ML Feature Table</h3>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Exportable Pipeline Ready</span>
          </div>
          
          <div style={{ overflowX: "auto" }}>
            <table className="data-table" style={{ minWidth: 1350 }}>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Brent ($)</th>
                  <th>WTI ($)</th>
                  <th>Active Fleet</th>
                  <th>In Transit</th>
                  <th>Dark Fleet</th>
                  <th>STS Events</th>
                  <th>Avg Speed</th>
                  <th>Cargo Events</th>
                  <th>Other Straits</th>
                  <th>Cape Transits</th>
                  <th>Other Delay</th>
                  <th>Cape Tankers (PW)</th>
                </tr>
              </thead>
              <tbody>
                {[...data].reverse().map((row) => (
                  <tr key={row.snapshot_date}>
                    <td style={{ color: "var(--text-secondary)", fontWeight: 500 }}>{row.snapshot_date}</td>
                    <td style={{ fontWeight: 600, color: "var(--accent-amber)" }}>{row.brent_close_usd ? `$${row.brent_close_usd}` : "—"}</td>
                    <td style={{ fontWeight: 600, color: "var(--accent-purple)" }}>{row.wti_close_usd ? `$${row.wti_close_usd}` : "—"}</td>
                    <td>{row.total_active_vessels ?? "—"}</td>
                    <td>{row.vessels_in_transit ?? "—"}</td>
                    <td style={{ color: "var(--accent-red)", fontWeight: 600 }}>{row.dark_vessels_count ?? "—"}</td>
                    <td>{row.sts_events_24h ?? "—"}</td>
                    <td>{row.avg_fleet_speed != null ? `${row.avg_fleet_speed.toFixed(1)} kn` : "—"}</td>
                    <td style={{ color: "var(--accent-emerald)" }}>{row.cargo_events_24h ?? "—"}</td>
                    <td style={{ color: "var(--accent-cyan)", fontWeight: 600 }}>{row.other_straits_transits ?? "—"}</td>
                    <td>{row.cape_of_good_hope_transits ?? "—"}</td>
                    <td>{row.other_straits_avg_transit_min != null ? `${row.other_straits_avg_transit_min.toFixed(0)}m` : "—"}</td>
                    <td>{row.portwatch_cape_tankers ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
