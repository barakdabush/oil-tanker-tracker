"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { DashboardStats, NewsEventResponse } from "@/lib/types";
import { getApiUrl } from "@/lib/config";
import BottomLineCard from "@/components/BottomLineCard";

// Mock data for when backend is unavailable
const ZERO_STATS: DashboardStats = {
  fleet: {
    total_vessels: 0,
    in_transit: 0,
    at_port: 0,
    currently_dark: 0,
    loaded: 0,
    ballast: 0,
  },
  active_alerts: 0,
  ongoing_sts_events: 0,
  congested_chokepoints: 0,
  recent_cargo_events: [],
  daily_volume_barrels: 0,
};

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
  return String(n);
}

function formatBarrels(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M bbl";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K bbl";
  return n.toFixed(0) + " bbl";
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats>(ZERO_STATS);
  const [topNews, setTopNews] = useState<NewsEventResponse[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const API = getApiUrl();
    Promise.allSettled([
      fetch(`${API}/api/analytics/dashboard`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/api/news/events?days=3&sort_by=rank&limit=5&symbol=BZ%3DF`, { cache: "no-store" }).then((r) =>
        r.ok ? r.json() : []
      ),
    ])
      .then(([dashRes, newsRes]) => {
        if (dashRes.status === "fulfilled" && dashRes.value) setStats(dashRes.value);
        if (newsRes.status === "fulfilled" && Array.isArray(newsRes.value)) setTopNews(newsRes.value);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  const fleet = stats.fleet;

  return (
    <>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>Global oil tanker fleet overview — real-time monitoring &amp; oil price outlook</p>
      </div>

      <div className="page-body">
        {/* Hero Bottom-Line Oil Price Predictor */}
        <BottomLineCard compact />

        {/* Stats Cards */}
        <div className="stats-grid animate-in" style={{ marginBottom: 24 }}>
          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-blue)" } as React.CSSProperties}
          >
            <div className="stat-card-label">Total Fleet</div>
            <div className="stat-card-value">{formatNumber(fleet.total_vessels)}</div>
            <div className="stat-card-icon">🚢</div>
            <div className="stat-card-sub">Tracked tankers worldwide</div>
          </div>

          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-cyan)" } as React.CSSProperties}
          >
            <div className="stat-card-label">In Transit</div>
            <div className="stat-card-value">{formatNumber(fleet.in_transit)}</div>
            <div className="stat-card-icon">🌊</div>
            <div className="stat-card-sub">Vessels underway &gt; 3 knots</div>
          </div>

          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-emerald)" } as React.CSSProperties}
          >
            <div className="stat-card-label">At Port</div>
            <div className="stat-card-value">{formatNumber(fleet.at_port)}</div>
            <div className="stat-card-icon">⚓</div>
            <div className="stat-card-sub">Loading or unloading</div>
          </div>

          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-red)" } as React.CSSProperties}
          >
            <div className="stat-card-label">Dark Fleet</div>
            <div className="stat-card-value">{fleet.currently_dark}</div>
            <div className="stat-card-icon">⚫</div>
            <div className="stat-card-sub">AIS transponder off</div>
          </div>

          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-amber)" } as React.CSSProperties}
          >
            <div className="stat-card-label">Daily Volume</div>
            <div className="stat-card-value">{formatBarrels(stats.daily_volume_barrels)}</div>
            <div className="stat-card-icon">🛢️</div>
            <div className="stat-card-sub">Estimated barrels moved</div>
          </div>

          <div
            className="stat-card"
            style={{ "--card-accent": "var(--accent-purple)" } as React.CSSProperties}
          >
            <div className="stat-card-label">Active Alerts</div>
            <div className="stat-card-value">{stats.active_alerts}</div>
            <div className="stat-card-icon">🚨</div>
            <div className="stat-card-sub">
              {stats.ongoing_sts_events} STS · {fleet.currently_dark} dark
            </div>
          </div>
        </div>

        {/* Two-column layout: Cargo Events + Chokepoints */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {/* Recent Cargo Events */}
          <div className="panel animate-in" style={{ animationDelay: "0.1s" }}>
            <div className="panel-header">
              <h3>🛢️ Recent Cargo Events</h3>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Last 24h</span>
            </div>
            {stats.recent_cargo_events.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">📦</div>
                <div className="empty-state-text">
                  No cargo events detected yet. Events will appear when tankers load or unload at
                  ports.
                </div>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Port</th>
                    <th>Volume</th>
                    <th>Confidence</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_cargo_events.map((evt) => (
                    <tr key={evt.id}>
                      <td>
                        <span
                          className={`badge ${
                            evt.event_type === "loading" ? "badge-loading" : "badge-unloading"
                          }`}
                        >
                          {evt.event_type === "loading" ? "▲" : "▼"} {evt.event_type}
                        </span>
                      </td>
                      <td>{evt.port_name || `Port #${evt.port_id}`}</td>
                      <td style={{ fontWeight: 600 }}>
                        {evt.estimated_volume_barrels
                          ? formatBarrels(evt.estimated_volume_barrels)
                          : "—"}
                      </td>
                      <td>
                        {evt.confidence ? (
                          <span
                            style={{
                              color:
                                evt.confidence > 0.7
                                  ? "var(--accent-emerald)"
                                  : "var(--accent-amber)",
                            }}
                          >
                            {(evt.confidence * 100).toFixed(0)}%
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td style={{ color: "var(--text-muted)" }}>
                        {timeAgo(evt.departure_time)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Alert Summary */}
          <div className="panel animate-in" style={{ animationDelay: "0.2s" }}>
            <div className="panel-header">
              <h3>🚨 Alert Summary</h3>
            </div>
            <div className="panel-body">
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "14px 16px",
                    background: "rgba(239, 68, 68, 0.08)",
                    borderRadius: 8,
                    border: "1px solid rgba(239, 68, 68, 0.15)",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-red)" }}>
                      ⚫ Dark Fleet Vessels
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      AIS signal lost &gt; 6 hours
                    </div>
                  </div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--accent-red)" }}>
                    {fleet.currently_dark}
                  </div>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "14px 16px",
                    background: "rgba(139, 92, 246, 0.08)",
                    borderRadius: 8,
                    border: "1px solid rgba(139, 92, 246, 0.15)",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-purple)" }}>
                      ⚠️ Ship-to-Ship Transfers
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      Ongoing mid-ocean encounters
                    </div>
                  </div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--accent-purple)" }}>
                    {stats.ongoing_sts_events}
                  </div>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "14px 16px",
                    background: "rgba(245, 158, 11, 0.08)",
                    borderRadius: 8,
                    border: "1px solid rgba(245, 158, 11, 0.15)",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-amber)" }}>
                      🚧 Congested Chokepoints
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      Above traffic threshold
                    </div>
                  </div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--accent-amber)" }}>
                    {stats.congested_chokepoints}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Top Market-Moving News & Predicted Oil Impact */}
        <div className="panel animate-in" style={{ marginTop: 16, animationDelay: "0.25s" }}>
          <div className="panel-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3>📰 Top Market-Moving News &amp; Predicted Oil Impact</h3>
            <Link href="/news" style={{ fontSize: 12, color: "#38bdf8", textDecoration: "none" }}>
              Open News Intelligence →
            </Link>
          </div>
          {topNews.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📰</div>
              <div className="empty-state-text">
                No recent ranked news events yet. Visit News Intelligence to fetch live feeds.
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 16px" }}>
              {topNews.map((ev) => {
                const pred = ev.prediction;
                const moveColor =
                  pred && pred.expected_return_pct >= 0.25
                    ? "#34d399"
                    : pred && pred.expected_return_pct <= -0.25
                      ? "#f87171"
                      : "#94a3b8";
                return (
                  <div
                    key={ev.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 12,
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "rgba(15, 23, 42, 0.55)",
                      border: "1px solid var(--border)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 220 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 3 }}>
                        {ev.headline}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {ev.event_type_label} · {timeAgo(ev.first_seen_at)}
                        {pred ? ` · ${pred.narrative}` : ""}
                      </div>
                    </div>
                    {pred && (
                      <div
                        style={{
                          padding: "5px 10px",
                          borderRadius: 8,
                          background: "rgba(2, 6, 23, 0.65)",
                          border: `1px solid ${moveColor}44`,
                          textAlign: "right",
                          minWidth: 120,
                        }}
                      >
                        <div style={{ fontSize: 13, fontWeight: 800, color: moveColor }}>
                          Brent {pred.expected_return_pct >= 0 ? "+" : ""}
                          {pred.expected_return_pct.toFixed(2)}%
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                          next {pred.peak_horizon} · {pred.confidence_pct.toFixed(0)}% conf
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
