"use client";

import { useCallback, useEffect, useState } from "react";
import { getApiUrl } from "@/lib/config";

interface AISSourceStats {
  connected?: boolean;
  messages_received?: number;
  last_message_at?: string | null;
}

interface ServiceHealth {
  name: string;
  label: string;
  status: "healthy" | "degraded" | "down" | "unknown";
  errors_1h: number;
  warnings_1h: number;
  last_log_at: string | null;
  ais_sources?: Record<string, AISSourceStats>;
}

interface PipelineFreshness {
  name: string;
  source: string;
  latest_at: string | null;
  fetched_at?: string | null;
  count: number;
  count_label: string;
  status: "healthy" | "stale" | "empty";
}

interface HealthResponse {
  checked_at: string;
  services: ServiceHealth[];
  pipelines: PipelineFreshness[];
}

interface SystemLogEntry {
  id: number;
  created_at: string | null;
  service: string;
  level: string;
  logger_name: string;
  message: string;
}

function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const mins = Math.floor(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "healthy":
      return "badge-normal";
    case "degraded":
    case "stale":
      return "badge-congested";
    case "down":
    case "empty":
      return "badge-dark";
    default:
      return "badge-loading";
  }
}

function levelBadgeStyle(level: string): React.CSSProperties {
  switch (level) {
    case "ERROR":
    case "CRITICAL":
      return { background: "rgba(239, 68, 68, 0.16)", color: "#f87171", border: "1px solid rgba(239, 68, 68, 0.35)" };
    case "WARNING":
      return { background: "rgba(245, 158, 11, 0.16)", color: "#fbbf24", border: "1px solid rgba(245, 158, 11, 0.35)" };
    default:
      return { background: "rgba(6, 182, 212, 0.14)", color: "#22d3ee", border: "1px solid rgba(6, 182, 212, 0.3)" };
  }
}

function serviceAccent(status: string): string {
  switch (status) {
    case "healthy":
      return "var(--accent-green, #10b981)";
    case "degraded":
      return "var(--accent-amber, #f59e0b)";
    case "down":
      return "var(--accent-red, #ef4444)";
    default:
      return "var(--accent-cyan, #06b6d4)";
  }
}

export default function SystemPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [serviceFilter, setServiceFilter] = useState("all");
  const [levelFilter, setLevelFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchAll = useCallback(async () => {
    const API = getApiUrl();
    const params = new URLSearchParams({ limit: "150" });
    if (serviceFilter !== "all") params.set("service", serviceFilter);
    if (levelFilter !== "ALL") params.set("level", levelFilter);
    if (searchQuery.trim()) params.set("search", searchQuery.trim());

    try {
      const [hRes, lRes] = await Promise.all([
        fetch(`${API}/api/system/health`),
        fetch(`${API}/api/system/logs?${params.toString()}`),
      ]);

      if (!hRes.ok) throw new Error(`Health API returned ${hRes.status}`);
      if (!lRes.ok) throw new Error(`Logs API returned ${lRes.status}`);

      const hData: HealthResponse = await hRes.json();
      const lData = await lRes.json();
      setHealth(hData);
      setLogs(lData.logs || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch system status");
    } finally {
      setLoading(false);
    }
  }, [serviceFilter, levelFilter, searchQuery]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(fetchAll, 10000);
    return () => clearInterval(timer);
  }, [autoRefresh, fetchAll]);

  return (
    <>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h2>System Health & Logs</h2>
          <p>
            Container status, data pipeline freshness, and centralized worker logs
            {health?.checked_at ? ` · Checked ${formatTimeAgo(health.checked_at)}` : ""}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-secondary)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh (10s)
          </label>
          <button
            type="button"
            onClick={fetchAll}
            style={{
              padding: "6px 14px",
              borderRadius: 8,
              border: "1px solid var(--border-color)",
              background: "var(--bg-card)",
              color: "var(--text-primary)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      <div className="page-body">
        {error && (
          <div
            className="panel"
            style={{
              marginBottom: 20,
              padding: "12px 16px",
              borderColor: "rgba(239, 68, 68, 0.4)",
              color: "#f87171",
              fontSize: 13,
            }}
          >
            Failed to load system status: {error}
          </div>
        )}

        {/* Service Cards */}
        <div className="stats-grid animate-in" style={{ marginBottom: 24 }}>
          {(health?.services ?? []).map((svc) => (
            <div
              key={svc.name}
              className="stat-card"
              style={{ "--card-accent": serviceAccent(svc.status) } as React.CSSProperties}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div className="stat-card-label" style={{ marginBottom: 0 }}>{svc.label}</div>
                <span className={`badge ${statusBadgeClass(svc.status)}`}>{svc.status}</span>
              </div>

              <div style={{ display: "flex", gap: 16, fontSize: 13, marginTop: 6 }}>
                <span>
                  Errors (1h):{" "}
                  <strong style={{ color: svc.errors_1h > 0 ? "#f87171" : "var(--text-primary)" }}>
                    {svc.errors_1h}
                  </strong>
                </span>
                <span>
                  Warnings:{" "}
                  <strong style={{ color: svc.warnings_1h > 0 ? "#fbbf24" : "var(--text-primary)" }}>
                    {svc.warnings_1h}
                  </strong>
                </span>
              </div>

              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                Last activity: {formatTimeAgo(svc.last_log_at)}
              </div>

              {svc.ais_sources && Object.keys(svc.ais_sources).length > 0 && (
                <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border-color)", fontSize: 11, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 4 }}>
                  {Object.entries(svc.ais_sources).map(([srcName, st]) => (
                    <div key={srcName} style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>
                        {st.connected ? "🟢" : "🔴"} {srcName}
                      </span>
                      <span style={{ fontFamily: "monospace" }}>
                        {(st.messages_received ?? 0).toLocaleString()} msgs
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Data Pipeline Freshness */}
        <div className="panel animate-in" style={{ marginBottom: 24, animationDelay: "0.05s" }}>
          <div className="panel-header">
            <h3>📡 Data Pipeline Freshness</h3>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Latest ingested records & upstream sync status
            </span>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Pipeline</th>
                <th>Source</th>
                <th>Status</th>
                <th>Latest Data</th>
                <th>Last Sync</th>
                <th>Records</th>
              </tr>
            </thead>
            <tbody>
              {(health?.pipelines ?? []).map((p) => (
                <tr key={p.name}>
                  <td style={{ fontWeight: 600 }}>{p.name}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{p.source}</td>
                  <td>
                    <span className={`badge ${statusBadgeClass(p.status)}`}>{p.status}</span>
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                    {p.latest_at ? formatTimestamp(p.latest_at) : "—"}
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {formatTimeAgo(p.fetched_at ?? p.latest_at)}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                    {p.count.toLocaleString()} <span style={{ color: "var(--text-muted)" }}>{p.count_label}</span>
                  </td>
                </tr>
              ))}
              {!loading && (!health || health.pipelines.length === 0) && (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>
                    No pipeline metrics available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Centralized Logs Viewer */}
        <div className="panel animate-in" style={{ animationDelay: "0.1s" }}>
          <div className="panel-header" style={{ flexWrap: "wrap", gap: 12 }}>
            <h3>📜 Container & Worker Logs</h3>

            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <select
                value={serviceFilter}
                onChange={(e) => setServiceFilter(e.target.value)}
                aria-label="Filter by service"
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-secondary, #0f172a)",
                  color: "var(--text-primary)",
                  fontSize: 12,
                }}
              >
                <option value="all">All Services</option>
                <option value="scheduler">scheduler</option>
                <option value="websocket">websocket</option>
                <option value="backend">backend</option>
              </select>

              <select
                value={levelFilter}
                onChange={(e) => setLevelFilter(e.target.value)}
                aria-label="Filter by level"
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-secondary, #0f172a)",
                  color: "var(--text-primary)",
                  fontSize: 12,
                }}
              >
                <option value="ALL">All Levels</option>
                <option value="ERROR">ERROR</option>
                <option value="WARNING">WARNING</option>
                <option value="INFO">INFO</option>
              </select>

              <input
                type="text"
                placeholder="Search logs..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-secondary, #0f172a)",
                  color: "var(--text-primary)",
                  fontSize: 12,
                  minWidth: 180,
                }}
              />
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 155 }}>Time</th>
                  <th style={{ width: 95 }}>Service</th>
                  <th style={{ width: 85 }}>Level</th>
                  <th style={{ width: 220 }}>Logger</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((entry) => (
                  <tr key={entry.id}>
                    <td style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                      {formatTimestamp(entry.created_at)}
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 600 }}>
                      {entry.service}
                    </td>
                    <td>
                      <span
                        className="badge"
                        style={{ ...levelBadgeStyle(entry.level), fontSize: 10, padding: "2px 7px" }}
                      >
                        {entry.level}
                      </span>
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-secondary)" }}>
                      {entry.logger_name}
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {entry.message}
                    </td>
                  </tr>
                ))}
                {!loading && logs.length === 0 && (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>
                      No logs matching the current filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
