"use client";

import { useEffect, useState } from "react";
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

export default function MarketIntelligencePage() {
  const [data, setData] = useState<MarketSnapshotResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedFeature, setSelectedFeature] = useState<keyof MarketSnapshotResponse>("total_active_vessels");

  useEffect(() => {
    const API = getApiUrl();
    fetch(`${API}/api/global-oil-features/snapshots?days=90`)
      .then((r) => r.json())
      .then((json: MarketSnapshotResponse[]) => {
        // Reverse correctly so the oldest data is on the left of the chart
        const sortedData = json.sort(
          (a, b) => new Date(a.snapshot_date).getTime() - new Date(b.snapshot_date).getTime()
        );
        setData(sortedData);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load market features", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="page-body"><div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>Loading Market Intelligence data...</div></div>;
  }

  const latest = data.length > 0 ? data[data.length - 1] : null;

  // Format the dataset so recharts handles null numbers (like missing weekend oil prices) gracefully
  const chartData = data.map((d) => ({
    ...d,
    brent_close_usd: d.brent_close_usd ?? undefined,
    wti_close_usd: d.wti_close_usd ?? undefined,
  }));

  const featureOptions: { key: keyof MarketSnapshotResponse; label: string; color: string }[] = [
    { key: "total_active_vessels", label: "Total Active Vessels", color: "#06b6d4" },
    { key: "vessels_in_transit", label: "Vessels In Transit", color: "#3b82f6" },
    { key: "vessels_at_port", label: "Vessels At Port", color: "#10b981" },
    { key: "dark_vessels_count", label: "Global Dark Fleet", color: "#ef4444" },
    { key: "sts_events_24h", label: "STS Events", color: "#8b5cf6" },
    { key: "cargo_events_24h", label: "Cargo Events", color: "#f59e0b" },
    { key: "new_ais_gaps_24h", label: "New AIS Gaps", color: "#f43f5e" },
    { key: "avg_fleet_speed", label: "Average Fleet Speed", color: "#14b8a6" },
    { key: "estimated_volume_barrels_24h", label: "Estimated Daily Volume", color: "#eab308" },
    { key: "chokepoint_transits_24h", label: "Chokepoint Transits", color: "#6366f1" },
  ];

  const activeFeatureConfig = featureOptions.find((o) => o.key === selectedFeature) || featureOptions[0];

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
              <div className="stat-card-value">{latest.brent_close_usd ? `$${latest.brent_close_usd.toFixed(2)}` : "—"}</div>
              <div className="stat-card-icon">📈</div>
              <div className="stat-card-sub">Current Global Benchmark</div>
            </div>

            <div className="stat-card" style={{ "--card-accent": "var(--accent-cyan)" } as React.CSSProperties}>
              <div className="stat-card-label">WTI Crude</div>
              <div className="stat-card-value">{latest.wti_close_usd ? `$${latest.wti_close_usd.toFixed(2)}` : "—"}</div>
              <div className="stat-card-icon">🛢️</div>
              <div className="stat-card-sub">US Benchmark</div>
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
          <div className="panel-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <h3>🔍 ML Feature Correlations</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Last 90 Days</span>
            </div>
            
            <select 
              value={selectedFeature} 
              onChange={(e) => setSelectedFeature(e.target.value as keyof MarketSnapshotResponse)}
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
              {featureOptions.map(opt => (
                <option key={opt.key} value={opt.key}>{opt.label}</option>
              ))}
            </select>
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

        {/* Data Grid table */}
        <div className="panel animate-in" style={{ animationDelay: "0.2s" }}>
          <div className="panel-header">
            <h3>🗄️ Raw ML Feature Table</h3>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Exportable Pipeline Ready</span>
          </div>
          
          <div style={{ overflowX: "auto" }}>
            <table className="data-table" style={{ minWidth: 1200 }}>
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
                </tr>
              </thead>
              <tbody>
                {[...data].reverse().map((row) => (
                  <tr key={row.snapshot_date}>
                    <td style={{ color: "var(--text-secondary)", fontWeight: 500 }}>{row.snapshot_date}</td>
                    <td style={{ fontWeight: 600, color: "var(--accent-amber)" }}>{row.brent_close_usd ? `$${row.brent_close_usd}` : "—"}</td>
                    <td style={{ fontWeight: 600, color: "var(--accent-purple)" }}>{row.wti_close_usd ? `$${row.wti_close_usd}` : "—"}</td>
                    <td>{row.total_active_vessels}</td>
                    <td>{row.vessels_in_transit}</td>
                    <td style={{ color: "var(--accent-red)", fontWeight: 600 }}>{row.dark_vessels_count}</td>
                    <td>{row.sts_events_24h}</td>
                    <td>{row.avg_fleet_speed.toFixed(1)} kn</td>
                    <td style={{ color: "var(--accent-emerald)" }}>{row.cargo_events_24h}</td>
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
