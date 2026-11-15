import React from "react";
import type { STSEvent } from "@/lib/types";

function formatDuration(start: string, end: string | null): string {
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const mins = Math.max(0, Math.round((e - s) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem > 0 ? `${hrs}h ${rem}m` : `${hrs}h`;
}

function confidenceColor(c: number): string {
  if (c >= 0.7) return "#22c55e";
  if (c >= 0.4) return "#f59e0b";
  return "#94a3b8";
}

interface STSDetailPanelProps {
  event: STSEvent;
  loading: boolean;
  onClose: () => void;
}

export function STSDetailPanel({ event, loading, onClose }: STSDetailPanelProps) {
  const isOngoing = event.status === "ongoing" || event.status === "detected";

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        className="panel-header"
        style={{
          background: "var(--bg-card)",
          padding: "10px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <span style={{ fontSize: 16 }}>⚓</span>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#fbbf24" }}>
            Ship-to-Ship Transfer
          </h3>
          <span
            style={{
              fontSize: 10,
              padding: "2px 8px",
              borderRadius: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              background: isOngoing ? "rgba(251,191,36,0.18)" : "rgba(34,197,94,0.18)",
              color: isOngoing ? "#fbbf24" : "#4ade80",
            }}
          >
            {event.status}
          </span>
          {loading && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading routes...</span>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close STS details"
          style={{
            background: "none",
            border: "none",
            color: "var(--text-muted)",
            cursor: "pointer",
            fontSize: 18,
            lineHeight: 1,
          }}
        >
          ✕
        </button>
      </div>

      <div
        style={{
          padding: "12px 16px",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "10px 16px",
          fontSize: 12,
          overflowY: "auto",
        }}
      >
        <div>
          <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Duration</div>
          <div style={{ fontWeight: 700, color: "#fbbf24" }}>
            {formatDuration(event.start_time, event.end_time)}
            {!event.end_time && " (ongoing)"}
          </div>
        </div>

        <div>
          <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Vessel A</div>
          <div style={{ fontFamily: "monospace", fontWeight: 600, color: "#60a5fa" }}>
            {event.vessel_a_mmsi}
          </div>
        </div>

        <div>
          <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Vessel B</div>
          <div style={{ fontFamily: "monospace", fontWeight: 600, color: "#34d399" }}>
            {event.vessel_b_mmsi}
          </div>
        </div>

        <div>
          <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Started</div>
          <div style={{ fontWeight: 500 }}>{new Date(event.start_time).toLocaleString()}</div>
        </div>

        {event.end_time && (
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Ended</div>
            <div style={{ fontWeight: 500 }}>{new Date(event.end_time).toLocaleString()}</div>
          </div>
        )}

        <div>
          <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Location</div>
          <div style={{ fontFamily: "monospace", fontWeight: 500 }}>
            {event.lat?.toFixed(4)}°, {event.lon?.toFixed(4)}°
          </div>
        </div>

        {event.distance_from_port_km != null && (
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>From Port</div>
            <div style={{ fontWeight: 500 }}>{event.distance_from_port_km.toFixed(1)} km</div>
          </div>
        )}

        {event.confidence != null && (
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Confidence</div>
            <div style={{ fontWeight: 700, color: confidenceColor(event.confidence) }}>
              {(event.confidence * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
