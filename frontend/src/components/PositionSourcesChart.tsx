"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import type { PositionSourceStat } from "@/lib/types";
import { getPositionSources } from "@/lib/api";

const PieChart = dynamic(() => import("recharts").then((m) => m.PieChart), { ssr: false });
const Pie = dynamic(() => import("recharts").then((m) => m.Pie), { ssr: false });
const Cell = dynamic(() => import("recharts").then((m) => m.Cell), { ssr: false });
const RechartsTooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });

const TIME_RANGES = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "7d", hours: 168 },
];

const SOURCE_PALETTE = [
  "#06b6d4",
  "#10b981",
  "#3b82f6",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#64748b",
];

function getSourceColor(source: string, idx: number): string {
  if (source === "aisstream") return "#06b6d4";
  if (source.startsWith("openwaters")) return "#10b981";
  if (source === "unknown") return "#64748b";
  return SOURCE_PALETTE[idx % SOURCE_PALETTE.length];
}

export default function PositionSourcesChart() {
  const [data, setData] = useState<PositionSourceStat[]>([]);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getPositionSources(hours);
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
    const interval = setInterval(fetchData, 60_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const totalPositions = data.reduce((sum, d) => sum + d.count, 0);
  const chartData = data.map((d, idx) => ({
    ...d,
    fill: getSourceColor(d.source, idx),
  }));

  return (
    <div className="panel animate-in" id="position-sources-chart" style={{ marginBottom: 24 }}>
      <div className="panel-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h3>🛰️ Position Split by AIS Source</h3>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {totalPositions.toLocaleString()} positions saved across {data.length} {data.length === 1 ? "source" : "sources"}
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
            Loading source breakdown…
          </div>
        ) : error ? (
          <div style={{ textAlign: "center", padding: 40, color: "#ef4444" }}>
            ⚠️ {error}
          </div>
        ) : data.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>
            No positions recorded in the last {hours}h
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 24, alignItems: "center" }}>
            <div style={{ width: "100%", height: 240, outline: "none" }}>
              <style>{`#position-sources-chart svg:focus, #position-sources-chart .recharts-wrapper:focus { outline: none; }`}</style>
              {mounted && (
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie
                      data={chartData}
                      dataKey="count"
                      nameKey="source"
                      cx="50%"
                      cy="50%"
                      innerRadius={58}
                      outerRadius={92}
                      paddingAngle={3}
                      stroke="none"
                    >
                      {chartData.map((entry) => (
                        <Cell key={entry.source} fill={entry.fill} />
                      ))}
                    </Pie>
                    <RechartsTooltip
                      contentStyle={{
                        background: "#1a2332",
                        border: "1px solid #2a3548",
                        borderRadius: 8,
                        color: "#f1f5f9",
                        fontSize: 12,
                      }}
                      formatter={(value: unknown, name: unknown) => [
                        `${Number(value).toLocaleString()} positions`,
                        String(name),
                      ]}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
              {chartData.map((item) => {
                const color = item.fill;
                return (
                  <div key={item.source} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, fontSize: 13 }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "monospace", fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                        <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0, display: "inline-block" }} />
                        {item.source}
                      </span>
                      <span style={{ color: "var(--text-secondary)", fontSize: 12, whiteSpace: "nowrap" }}>
                        <strong>{item.count.toLocaleString()}</strong> ({item.percentage}%)
                      </span>
                    </div>
                    <div style={{ height: 6, borderRadius: 4, background: "rgba(148,163,184,0.12)", overflow: "hidden" }}>
                      <div style={{ width: `${Math.max(2, item.percentage)}%`, height: "100%", background: color, borderRadius: 4 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
