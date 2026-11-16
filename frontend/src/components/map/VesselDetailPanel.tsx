import React from "react";
import type { Vessel } from "@/lib/types";
import { getVesselStatus, timeLabelMap } from "./utils";

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

interface TrailPoint {
  latitude: number;
  longitude: number;
}

interface VesselDetailPanelProps {
  selectedVessel: Vessel;
  onClose: () => void;
  trailHours: number;
  setTrailHours: (h: number) => void;
  loadingTrail: boolean;
  trailPointsCount: number;
  onFetchTrail: (v: Vessel, h: number) => void;
}

export function VesselDetailPanel({
  selectedVessel,
  onClose,
  trailHours,
  setTrailHours,
  loadingTrail,
  trailPointsCount,
  onFetchTrail
}: VesselDetailPanelProps) {
  const status = getVesselStatus(selectedVessel);
  
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
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            🚢 {selectedVessel.name || `MMSI ${selectedVessel.mmsi}`}
          </h3>
          <span className={`badge ${
            status === "EXTENDED DARK" ? "badge-dark" :
            status === "DARK" ? "badge-dark" :
            status === "At Port" ? "badge-loading" :
            status === "Loaded" ? "badge-unloading" : "badge-normal"
          }`}>
            {status}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close vessel details"
          style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18, lineHeight: 1 }}
        >
          ✕
        </button>
      </div>

      <div style={{ padding: "12px 16px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: "8px 14px", fontSize: 12 }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>MMSI</div>
            <div style={{ fontWeight: 600, fontFamily: "monospace" }}>{selectedVessel.mmsi}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>IMO</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.imo || "—"}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Flag</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.flag || "—"}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Speed / Course</div>
            <div style={{ fontWeight: 600 }}>
              {selectedVessel.last_speed?.toFixed(1) || "—"} kn · {selectedVessel.last_course?.toFixed(0) || "—"}°
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Draft</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.draft?.toFixed(1) || "—"} m</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Length × Beam</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.length || "—"} × {selectedVessel.beam || "—"} m</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>DWT</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.dwt ? `${(selectedVessel.dwt / 1000).toFixed(0)}K t` : "—"}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Destination</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.destination || "—"}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Last Position</div>
            <div style={{ fontFamily: "monospace", color: "var(--accent-cyan)", fontWeight: 600 }}>
              {selectedVessel.last_lat?.toFixed(4)}°, {selectedVessel.last_lon?.toFixed(4)}°
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Last Seen</div>
            <div style={{ fontWeight: 600 }}>
              {selectedVessel.last_seen ? `${timeAgo(selectedVessel.last_seen)}` : "—"}
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>AIS Source</div>
            <div style={{ fontWeight: 600, fontFamily: "monospace", fontSize: 11, color: selectedVessel.last_source?.startsWith("openwaters") ? "var(--accent-emerald)" : "var(--text-primary)" }}>
              {selectedVessel.last_source || "—"}
            </div>
          </div>
        </div>

        <div style={{ padding: "8px 12px", background: "rgba(6, 182, 212, 0.08)", borderRadius: 8, border: "1px solid rgba(6, 182, 212, 0.2)", fontSize: 12, display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <div style={{ color: "var(--accent-cyan)", fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            <span>🛤️ Route Trail</span>
            <span style={{ fontSize: 11, opacity: 0.75, fontWeight: 400 }}>
              {loadingTrail ? "Loading..." : `${trailPointsCount} points`}
            </span>
          </div>

          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {[6, 12, 24, 48, 72, 168, 336, 0].map((h) => (
              <button
                key={h}
                data-testid={`trail-btn-${h}`}
                onClick={() => {
                  setTrailHours(h);
                  onFetchTrail(selectedVessel, h);
                }}
                style={{
                  padding: "3px 8px",
                  fontSize: 10,
                  fontWeight: 600,
                  border: "1px solid rgba(6,182,212,0.3)",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: trailHours === h ? "rgba(6,182,212,0.3)" : "transparent",
                  color: trailHours === h ? "#00ffff" : "var(--text-muted)",
                }}
              >
                {timeLabelMap[h] || `${h}h`}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
