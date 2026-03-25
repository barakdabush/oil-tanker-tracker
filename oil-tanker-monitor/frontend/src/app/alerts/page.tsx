"use client";

import { useEffect, useState } from "react";
import type { AISGap, STSEvent, Chokepoint } from "@/lib/types";

function formatDuration(start: string, end?: string | null): string {
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const hours = Math.floor((e - s) / 3600000);
  if (hours < 1) return `${Math.floor((e - s) / 60000)}m`;
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function AlertsPage() {
  const [darkFleet, setDarkFleet] = useState<AISGap[]>([]);
  const [stsEvents, setStsEvents] = useState<STSEvent[]>([]);
  const [chokepoints, setChokepoints] = useState<Chokepoint[]>([]);

  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    Promise.all([
      fetch(`${API}/api/alerts/dark-fleet`).then((r) => r.json()).catch(() => null),
      fetch(`${API}/api/alerts/sts-events`).then((r) => r.json()).catch(() => null),
      fetch(`${API}/api/alerts/chokepoints`).then((r) => r.json()).catch(() => null),
    ]).then(([df, sts, cp]) => {
      if (df?.length > 0) setDarkFleet(df);
      if (sts?.length > 0) setStsEvents(sts);
      if (cp?.length > 0) setChokepoints(cp);
    });
  }, []);

  return (
    <>
      <div className="page-header">
        <h2>Alerts</h2>
        <p>Dark fleet monitoring, ship-to-ship transfers, and chokepoint congestion</p>
      </div>

      <div className="page-body">
        {/* Stats summary */}
        <div className="stats-grid animate-in" style={{ marginBottom: 24 }}>
          <div className="stat-card" style={{ "--card-accent": "var(--accent-red)" } as React.CSSProperties}>
            <div className="stat-card-label">Dark Vessels</div>
            <div className="stat-card-value">{darkFleet.filter((g) => g.status !== "resolved").length}</div>
            <div className="stat-card-icon">⚫</div>
          </div>
          <div className="stat-card" style={{ "--card-accent": "var(--accent-purple)" } as React.CSSProperties}>
            <div className="stat-card-label">STS Events</div>
            <div className="stat-card-value">{stsEvents.filter((e) => e.status !== "dismissed").length}</div>
            <div className="stat-card-icon">⚠️</div>
          </div>
          <div className="stat-card" style={{ "--card-accent": "var(--accent-amber)" } as React.CSSProperties}>
            <div className="stat-card-label">Congested</div>
            <div className="stat-card-value">{chokepoints.filter((c) => c.congestion_status === "congested").length}</div>
            <div className="stat-card-icon">🚧</div>
          </div>
        </div>

        {/* Dark Fleet Table */}
        <div className="panel animate-in" style={{ marginBottom: 24, animationDelay: "0.1s" }}>
          <div className="panel-header">
            <h3>⚫ Dark Fleet Monitor</h3>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>AIS gap tracking</span>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>MMSI</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Last Position</th>
                <th>Distance Jumped</th>
                <th>Spoofing</th>
              </tr>
            </thead>
            <tbody>
              {darkFleet.map((gap) => (
                <tr key={gap.id}>
                  <td style={{ fontFamily: "monospace", fontWeight: 600 }}>{gap.mmsi}</td>
                  <td>
                    <span className={`badge ${
                      gap.status === "extended_dark" ? "badge-dark" :
                      gap.status === "dark" ? "badge-congested" :
                      gap.status === "monitoring" ? "badge-loading" :
                      "badge-normal"
                    }`}>
                      {gap.status.replace("_", " ")}
                    </span>
                  </td>
                  <td>{formatDuration(gap.gap_start, gap.gap_end)}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                    {gap.last_known_lat?.toFixed(2)}°, {gap.last_known_lon?.toFixed(2)}°
                  </td>
                  <td>
                    {gap.distance_jumped_km ? (
                      <span style={{ color: gap.distance_jumped_km > 500 ? "var(--accent-red)" : "var(--text-secondary)" }}>
                        {gap.distance_jumped_km.toFixed(0)} km
                      </span>
                    ) : "—"}
                  </td>
                  <td>
                    {gap.spoofing_suspected ? (
                      <span style={{ color: "var(--accent-red)", fontWeight: 700 }}>⚠ YES</span>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Two columns: STS + Chokepoints */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {/* STS Events */}
          <div className="panel animate-in" style={{ animationDelay: "0.2s" }}>
            <div className="panel-header">
              <h3>⚠️ Ship-to-Ship Transfers</h3>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Vessels</th>
                  <th>Duration</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {stsEvents.map((evt) => (
                  <tr key={evt.id}>
                    <td>
                      <span className={`badge badge-${evt.status}`}>
                        {evt.status}
                      </span>
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                      {evt.vessel_a_mmsi}<br/>{evt.vessel_b_mmsi}
                    </td>
                    <td>{formatDuration(evt.start_time, evt.end_time)}</td>
                    <td>
                      {evt.confidence ? (
                        <span style={{
                          color: evt.confidence > 0.7 ? "var(--accent-red)" : "var(--accent-amber)",
                          fontWeight: 600,
                        }}>
                          {(evt.confidence * 100).toFixed(0)}%
                        </span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Chokepoints */}
          <div className="panel animate-in" style={{ animationDelay: "0.3s" }}>
            <div className="panel-header">
              <h3>🔶 Chokepoint Status</h3>
            </div>
            <div className="panel-body">
              {chokepoints.map((cp) => (
                <div
                  key={cp.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "12px 0",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                      {cp.name}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {cp.region} · {cp.avg_daily_oil_flow_mbd} mb/d
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                      {cp.current_vessel_count}
                    </div>
                    <span className={`badge badge-${cp.congestion_status}`}>
                      {cp.congestion_status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
