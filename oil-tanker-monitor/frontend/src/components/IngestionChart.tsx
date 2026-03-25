"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import type { IngestionDataPoint } from "@/lib/types";
import { getIngestionData } from "@/lib/api";

// Dynamically import Recharts (no SSR)
const AreaChart = dynamic(() => import("recharts").then((m) => m.AreaChart), { ssr: false });
const Area = dynamic(() => import("recharts").then((m) => m.Area), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const RechartsTooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });
const Legend = dynamic(() => import("recharts").then((m) => m.Legend), { ssr: false });

const TIME_RANGES = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "7d", hours: 168 },
];

const TABLE_COLORS: Record<string, { stroke: string; fill: string; label: string }> = {
  vessel_positions:    { stroke: "#06b6d4", fill: "#06b6d4", label: "Vessel Positions" },
  chokepoint_transits: { stroke: "#10b981", fill: "#10b981", label: "Chokepoint Transits" },
  sts_events:          { stroke: "#f59e0b", fill: "#f59e0b", label: "STS Events" },
  ais_gaps:            { stroke: "#ef4444", fill: "#ef4444", label: "AIS Gaps" },
  cargo_events:        { stroke: "#8b5cf6", fill: "#8b5cf6", label: "Cargo Events" },
};

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

function formatDateAxis(ts: string, hours: number): string {
  try {
    const d = new Date(ts);
    if (hours <= 24) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) +
      " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

export default function IngestionChart() {
  const [data, setData] = useState<IngestionDataPoint[]>([]);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getIngestionData(hours);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
      setData([]);
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    fetchData();
    // Auto-refresh every 60s
    const interval = setInterval(fetchData, 60_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const totalRows = data.reduce(
    (sum, d) =>
      sum + d.vessel_positions + d.chokepoint_transits + d.sts_events + d.ais_gaps + d.cargo_events,
    0
  );

  return (
    <div className="panel animate-in" id="ingestion-chart" style={{ marginBottom: 24 }}>
      <div className="panel-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h3>📡 Data Ingestion Monitor</h3>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            WebSocket pipeline health — {totalRows.toLocaleString()} rows ingested
          </span>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {TIME_RANGES.map((r) => (
            <button
              key={r.hours}
              onClick={() => setHours(r.hours)}
              style={{
                padding: "4px 12px",
                fontSize: 12,
                fontWeight: 600,
                border: "1px solid",
                borderColor: hours === r.hours ? "var(--accent-cyan)" : "var(--border)",
                borderRadius: 6,
                background: hours === r.hours ? "rgba(6,182,212,0.15)" : "transparent",
                color: hours === r.hours ? "var(--accent-cyan)" : "var(--text-muted)",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              {r.label}
            </button>
          ))}
          <button
            onClick={fetchData}
            title="Refresh"
            style={{
              padding: "4px 10px",
              fontSize: 14,
              border: "1px solid var(--border)",
              borderRadius: 6,
              background: "transparent",
              color: "var(--text-muted)",
              cursor: "pointer",
              transition: "all 0.2s",
            }}
          >
            ↻
          </button>
        </div>
      </div>

      <div className="panel-body">
        {loading && data.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>
            Loading ingestion data…
          </div>
        ) : error ? (
          <div style={{ textAlign: "center", padding: 40, color: "#ef4444" }}>
            ⚠️ {error}
          </div>
        ) : data.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>
            No data ingested in the last {hours}h — check WebSocket connection
          </div>
        ) : (
          <div className="chart-container">
            {mounted && (
              <ResponsiveContainer width="100%" height={350}>
                <AreaChart data={data}>
                  <defs>
                    {Object.entries(TABLE_COLORS).map(([key, { fill }]) => (
                      <linearGradient key={key} id={`gradient-${key}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={fill} stopOpacity={0.25} />
                        <stop offset="95%" stopColor={fill} stopOpacity={0} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a3548" />
                  <XAxis
                    dataKey="timestamp"
                    tick={{ fill: "#64748b", fontSize: 10 }}
                    tickFormatter={(v) => formatDateAxis(v, hours)}
                    stroke="#2a3548"
                    interval="preserveStartEnd"
                    minTickGap={50}
                  />
                  <YAxis
                    tick={{ fill: "#64748b", fontSize: 11 }}
                    stroke="#2a3548"
                    allowDecimals={false}
                  />
                  <RechartsTooltip
                    contentStyle={{
                      background: "#1a2332",
                      border: "1px solid #2a3548",
                      borderRadius: 8,
                      color: "#f1f5f9",
                      fontSize: 12,
                    }}
                    labelFormatter={(v) => formatTime(v as string)}
                  />
                  {Object.entries(TABLE_COLORS).map(([key, { stroke, label }]) => (
                    <Area
                      key={key}
                      type="monotone"
                      dataKey={key}
                      name={label}
                      stroke={stroke}
                      strokeWidth={2}
                      fill={`url(#gradient-${key})`}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        )}

        {/* Summary cards */}
        {data.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 16 }}>
            {Object.entries(TABLE_COLORS).map(([key, { stroke, label }]) => {
              const total = data.reduce((sum, d) => sum + (d[key as keyof IngestionDataPoint] as number || 0), 0);
              return (
                <div
                  key={key}
                  style={{
                    padding: "10px 12px",
                    borderRadius: 8,
                    border: `1px solid ${stroke}33`,
                    background: `${stroke}0a`,
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: 18, fontWeight: 700, color: stroke }}>
                    {total.toLocaleString()}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                    {label}
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
