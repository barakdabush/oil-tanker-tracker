import React from "react";
import type { Vessel } from "@/lib/types";
import { getVesselStatus, timeLabelMap } from "./utils";

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
    <div className="map-overlay-panel animate-slide">
      <div className="panel-header" style={{ background: "var(--bg-secondary)" }}>
        <h3>🚢 {selectedVessel.name || `MMSI ${selectedVessel.mmsi}`}</h3>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18 }}>✕</button>
      </div>
      <div className="panel-body">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 13 }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>MMSI</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.mmsi}</div>
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
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Status</div>
            <div>
              <span className={`badge ${
                status === "DARK" ? "badge-dark" : 
                status === "At Port" ? "badge-loading" : 
                status === "Loaded" ? "badge-unloading" : "badge-normal"
              }`}>
                {status}
              </span>
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Speed</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.last_speed?.toFixed(1) || "—"} kn</div>
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
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Destination</div>
            <div style={{ fontWeight: 600 }}>{selectedVessel.destination || "—"}</div>
          </div>
        </div>

        <div style={{ marginTop: 16, padding: "12px 14px", background: "var(--bg-secondary)", borderRadius: 8, fontSize: 12 }}>
          <div style={{ color: "var(--text-muted)", marginBottom: 4 }}>Last Position</div>
          <div style={{ fontFamily: "monospace", color: "var(--accent-cyan)" }}>
            {selectedVessel.last_lat?.toFixed(4)}°, {selectedVessel.last_lon?.toFixed(4)}°
          </div>
          <div style={{ color: "var(--text-muted)", marginTop: 6, fontSize: 11 }}>
            Course: {selectedVessel.last_course?.toFixed(0) || "—"}° · DWT{" "}
            {selectedVessel.dwt ? `${(selectedVessel.dwt / 1000).toFixed(0)}K` : "—"} t
          </div>
        </div>

        <div style={{ marginTop: 10, padding: "12px 14px", background: "rgba(6, 182, 212, 0.08)", borderRadius: 8, border: "1px solid rgba(6, 182, 212, 0.2)", fontSize: 12 }}>
          <div style={{ color: "var(--accent-cyan)", fontWeight: 600, marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>🛤️ Route Trail</span>
            <span style={{ fontSize: 10, opacity: 0.7 }}>
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
          {trailPointsCount === 0 && !loadingTrail && (
            <div style={{ color: "var(--text-muted)", marginTop: 8, fontSize: 11 }}>
              No position history available for this window.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
