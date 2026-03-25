"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { VolumeOverTime, TopRoute, FleetStatus } from "@/lib/types";
import IngestionChart from "@/components/IngestionChart";

// Dynamically import Recharts components (they don't support SSR well)
const AreaChart = dynamic(
  () => import("recharts").then((m) => m.AreaChart),
  { ssr: false }
);
const Area = dynamic(
  () => import("recharts").then((m) => m.Area),
  { ssr: false }
);
const XAxis = dynamic(
  () => import("recharts").then((m) => m.XAxis),
  { ssr: false }
);
const YAxis = dynamic(
  () => import("recharts").then((m) => m.YAxis),
  { ssr: false }
);
const CartesianGrid = dynamic(
  () => import("recharts").then((m) => m.CartesianGrid),
  { ssr: false }
);
const RechartsTooltip = dynamic(
  () => import("recharts").then((m) => m.Tooltip),
  { ssr: false }
);
const ResponsiveContainer = dynamic(
  () => import("recharts").then((m) => m.ResponsiveContainer),
  { ssr: false }
);
const PieChart = dynamic(
  () => import("recharts").then((m) => m.PieChart),
  { ssr: false }
);
const Pie = dynamic(
  () => import("recharts").then((m) => m.Pie),
  { ssr: false }
);
const Cell = dynamic(
  () => import("recharts").then((m) => m.Cell),
  { ssr: false }
);

const PIE_COLORS = ["#06b6d4", "#10b981", "#f59e0b", "#ef4444"];

function formatBarrels(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
  return String(n);
}

function NoDataMessage({ message }: { message?: string }) {
  return (
    <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)", fontSize: 14 }}>
      {message || "No data available"}
    </div>
  );
}

export default function AnalyticsPage() {
  const [volume, setVolume] = useState<VolumeOverTime[] | null>(null);
  const [routes, setRoutes] = useState<TopRoute[] | null>(null);
  const [fleet, setFleet] = useState<FleetStatus | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    Promise.all([
      fetch(`${API}/api/analytics/volume?days=30`).then((r) => r.json()).catch(() => null),
      fetch(`${API}/api/analytics/top-routes?limit=10`).then((r) => r.json()).catch(() => null),
      fetch(`${API}/api/analytics/fleet-status`).then((r) => r.json()).catch(() => null),
    ]).then(([volData, routeData, fleetData]) => {
      setVolume(Array.isArray(volData) && volData.length > 0 ? volData : []);
      setRoutes(Array.isArray(routeData) && routeData.length > 0 ? routeData : []);
      setFleet(fleetData?.total_vessels != null ? fleetData : null);
    });
  }, []);

  const pieData = fleet
    ? [
        { name: "In Transit", value: fleet.in_transit },
        { name: "At Port", value: fleet.at_port },
        { name: "Loaded", value: fleet.loaded },
        { name: "Dark", value: fleet.currently_dark },
      ]
    : [];

  return (
    <>
      <div className="page-header">
        <h2>Analytics</h2>
        <p>Oil supply chain intelligence — volume trends, routes, and fleet analysis</p>
      </div>

      <div className="page-body">
        {/* Data Ingestion Monitor */}
        <IngestionChart />

        {/* Volume Chart */}
        <div className="panel animate-in" style={{ marginBottom: 24 }}>
          <div className="panel-header">
            <h3>📊 Oil Volume Over Time</h3>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Last 30 days</span>
          </div>
          <div className="panel-body">
            {volume === null ? (
              <NoDataMessage message="Loading…" />
            ) : volume.length === 0 ? (
              <NoDataMessage />
            ) : (
              <div className="chart-container">
                {mounted && (
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={volume}>
                      <defs>
                        <linearGradient id="volumeGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#06b6d4" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#2a3548" />
                      <XAxis
                        dataKey="date"
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={(v) => v.slice(5)}
                        stroke="#2a3548"
                      />
                      <YAxis
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={(v) => formatBarrels(v)}
                        stroke="#2a3548"
                      />
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
                        dataKey="volume_barrels"
                        stroke="#06b6d4"
                        strokeWidth={2}
                        fill="url(#volumeGradient)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Two column: Routes + Fleet */}
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
          {/* Top Routes */}
          <div className="panel animate-in" style={{ animationDelay: "0.1s" }}>
            <div className="panel-header">
              <h3>🛤️ Top Tanker Routes</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {routes ? `${routes.length} routes` : ""}
              </span>
            </div>
            {routes === null ? (
              <NoDataMessage message="Loading…" />
            ) : routes.length === 0 ? (
              <NoDataMessage />
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Origin → Destination</th>
                    <th>Voyages</th>
                    <th>Total Volume</th>
                  </tr>
                </thead>
                <tbody>
                  {routes.map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 700, color: "var(--accent-cyan)" }}>{i + 1}</td>
                      <td>
                        <span style={{ color: "var(--text-primary)" }}>{r.origin_port}</span>
                        <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>→</span>
                        <span style={{ color: "var(--text-primary)" }}>{r.destination_port}</span>
                      </td>
                      <td style={{ fontWeight: 600 }}>{r.voyage_count}</td>
                      <td style={{ fontWeight: 600, color: "var(--accent-emerald)" }}>
                        {formatBarrels(r.total_volume_barrels)} bbl
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Fleet Status */}
          <div className="panel animate-in" style={{ animationDelay: "0.2s" }}>
            <div className="panel-header">
              <h3>🚢 Fleet Breakdown</h3>
            </div>
            {!fleet ? (
              <NoDataMessage />
            ) : (
              <div className="panel-body" style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                {mounted && (
                  <ResponsiveContainer width="100%" height={200}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={80}
                        paddingAngle={3}
                        dataKey="value"
                      >
                        {pieData.map((_, index) => (
                          <Cell key={index} fill={PIE_COLORS[index % PIE_COLORS.length]} />
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
                      />
                    </PieChart>
                  </ResponsiveContainer>
                )}
                <div style={{ width: "100%", marginTop: 8 }}>
                  {pieData.map((d, i) => (
                    <div
                      key={d.name}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "8px 0",
                        borderBottom: i < pieData.length - 1 ? "1px solid var(--border)" : "none",
                        fontSize: 13,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: 3,
                            background: PIE_COLORS[i],
                          }}
                        />
                        <span style={{ color: "var(--text-secondary)" }}>{d.name}</span>
                      </div>
                      <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{d.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
