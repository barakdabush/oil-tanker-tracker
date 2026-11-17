"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { getApiUrl } from "@/lib/config";

const AreaChart = dynamic(() => import("recharts").then((m) => m.AreaChart), { ssr: false });
const Area = dynamic(() => import("recharts").then((m) => m.Area), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const RechartsTooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });

interface PortWatchPoint {
  date: string;
  n_total: number;
  n_tanker: number;
  n_cargo: number;
  n_container: number;
  capacity_tanker: number | null;
}

interface PortWatchResponse {
  portname: string;
  portid: string | null;
  latest_date: string | null;
  latest_total: number;
  latest_tanker: number;
  seven_day_avg_total: number;
  seven_day_avg_tanker: number;
  baseline_avg_total: number;
  throughput_pct: number | null;
  series: PortWatchPoint[];
}

const CHOKEPOINT_OPTIONS = [
  "Strait of Hormuz",
  "Suez Canal",
  "Bab el-Mandeb",
  "Malacca Strait",
  "Panama Canal",
  "Cape of Good Hope",
  "Bosporus",
];

export default function PortWatchChart() {
  const [selected, setSelected] = useState("Strait of Hormuz");
  const [data, setData] = useState<PortWatchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetch(`${getApiUrl()}/api/chokepoints/portwatch?portname=${encodeURIComponent(selected)}&days=90`)
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        if (active) setData(res);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected]);

  return (
    <div className="panel animate-in" style={{ marginBottom: 24 }}>
      <div className="panel-header" style={{ flexWrap: "wrap", gap: 10 }}>
        <div>
          <h3 style={{ margin: 0 }}>🛰️ IMF PortWatch · Chokepoint Daily Transits (Satellite AIS)</h3>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {data?.latest_date
              ? `Latest: ${data.latest_date} · ${data.latest_total} ships (${data.latest_tanker} tankers) · 7d avg ${data.seven_day_avg_total}/day${
                  data.throughput_pct != null ? ` · ${data.throughput_pct}% of 1y avg` : ""
                }`
              : "Official IMF PortWatch satellite-AIS daily transit calls (~7-9 day publication lag)"}
          </div>
        </div>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Select chokepoint"
          style={{
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 12,
          }}
        >
          {CHOKEPOINT_OPTIONS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className="panel-body">
        {loading ? (
          <div style={{ height: 260, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div className="loading-spinner" />
          </div>
        ) : !data || data.series.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)", fontSize: 13 }}>
            Awaiting IMF PortWatch sync (runs automatically in background worker).
          </div>
        ) : (
          mounted && (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={data.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.12)" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#94a3b8", fontSize: 11 }}
                  tickFormatter={(v) => String(v).slice(5)}
                />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} width={36} />
                <RechartsTooltip
                  contentStyle={{
                    background: "#1a2332",
                    border: "1px solid #2a3548",
                    borderRadius: 8,
                    color: "#f1f5f9",
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="n_total"
                  name="Total Ships"
                  stroke="#06b6d4"
                  fill="rgba(6, 182, 212, 0.16)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="n_tanker"
                  name="Tankers"
                  stroke="#f59e0b"
                  fill="rgba(245, 158, 11, 0.22)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          )
        )}
      </div>
    </div>
  );
}
