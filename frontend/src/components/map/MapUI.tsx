import React from "react";
import type { Vessel } from "@/lib/types";

import { SAR_DARK_COLOR, SAR_MATCHED_COLOR } from "./SatelliteLayer";

interface MapUIProps {
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  searchResults: Vessel[];
  isSearching: boolean;
  onSelectVessel: (v: Vessel) => void;
  showPorts: boolean;
  setShowPorts: (s: boolean) => void;
  showChokepoints: boolean;
  setShowChokepoints: (s: boolean) => void;
  showTrails: boolean;
  setShowTrails: (s: boolean) => void;
  showSatellite: boolean;
  setShowSatellite: (s: boolean) => void;
  vesselStateCounts?: Record<string, number>;
  portsCount?: number;
  satelliteCount?: number;
  satelliteDarkCount?: number;
  satelliteMatchedCount?: number;
  selectedVessel: Vessel | null;
  trailLatLngsCount: number;
}

export function SearchBar({ searchQuery, setSearchQuery, searchResults, isSearching, onSelectVessel }: Pick<MapUIProps, 'searchQuery' | 'setSearchQuery' | 'searchResults' | 'isSearching' | 'onSelectVessel'>) {
  return (
    <div style={{ position: "absolute", top: 16, right: 16, zIndex: 1000, display: "flex", flexDirection: "column", gap: 4, width: 280 }}>
      <div style={{ position: "relative" }}>
        <input
          type="text"
          placeholder="Search Ship by Name or MMSI..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ width: "100%", padding: "10px 36px 10px 14px", background: "rgba(15, 23, 42, 0.85)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, color: "#fff", fontSize: 13, backdropFilter: "blur(8px)", outline: "none" }}
        />
        {searchQuery ? (
          <button onClick={() => setSearchQuery("")} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16 }}>✕</button>
        ) : (
          <span style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", fontSize: 14, pointerEvents: "none" }}>🔍</span>
        )}
      </div>
      
      {searchQuery && searchResults.length > 0 && (
        <div style={{ background: "rgba(15, 23, 42, 0.95)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, overflow: "hidden", boxShadow: "0 4px 20px rgba(0,0,0,0.5)", backdropFilter: "blur(12px)" }}>
          {searchResults.map(v => (
            <div
              key={v.mmsi}
              data-testid={`search-result-${v.mmsi}`}
              onClick={() => onSelectVessel(v)}
              style={{ padding: "10px 14px", cursor: "pointer", borderBottom: "1px solid rgba(255,255,255,0.05)", display: "flex", flexDirection: "column", transition: "background 0.2s" }}
              onMouseEnter={(e) => e.currentTarget.style.background = "rgba(6,182,212,0.15)"}
              onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>{v.name || `MMSI ${v.mmsi}`}</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>MMSI: {v.mmsi}</div>
            </div>
          ))}
        </div>
      )}
      {searchQuery && !isSearching && searchResults.length === 0 && (
        <div style={{ background: "rgba(15, 23, 42, 0.95)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, padding: "12px", color: "var(--text-muted)", fontSize: 12, textAlign: "center", backdropFilter: "blur(12px)" }}>
          No vessels found matching "{searchQuery}"
        </div>
      )}
    </div>
  );
}

const VESSEL_LEGEND_GROUPS: Array<{ keys: string[]; label: string; color: string }> = [
  { keys: ["in_transit_laden", "in_transit_ballast"], label: "In Transit (Moving)", color: "#10b981" },
  { keys: ["at_port", "anchored_waiting", "idle_drifting"], label: "Stationary (Port / Anchored / Idle)", color: "#f59e0b" },
  { keys: ["sts_transfer", "floating_storage"], label: "STS / Floating Storage", color: "#a855f7" },
  { keys: ["dark"], label: "Dark (AIS off > 6h)", color: "#ef4444" },
];

export function MapControlsAndLegend({
  showPorts,
  setShowPorts,
  showChokepoints,
  setShowChokepoints,
  showTrails,
  setShowTrails,
  showSatellite,
  setShowSatellite,
  vesselStateCounts = {},
  portsCount = 0,
  satelliteCount = 0,
  satelliteDarkCount = 0,
  satelliteMatchedCount = 0,
  selectedVessel,
  trailLatLngsCount,
}: Pick<
  MapUIProps,
  | "showPorts"
  | "setShowPorts"
  | "showChokepoints"
  | "setShowChokepoints"
  | "showTrails"
  | "setShowTrails"
  | "showSatellite"
  | "setShowSatellite"
  | "vesselStateCounts"
  | "portsCount"
  | "satelliteCount"
  | "satelliteDarkCount"
  | "satelliteMatchedCount"
  | "selectedVessel"
  | "trailLatLngsCount"
>) {
  const totalVessels = Object.values(vesselStateCounts).reduce((a, b) => a + b, 0);

  return (
    <>
      {/* Legend */}
      <div className="map-legend">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>
          <span>Vessel State</span>
          <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>{totalVessels}</span>
        </div>
        {VESSEL_LEGEND_GROUPS.map((group) => {
          const count = group.keys.reduce((sum, k) => sum + (vesselStateCounts[k] || 0), 0);
          return (
            <div key={group.label} className="legend-item" style={{ justifyContent: "space-between", gap: 10 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="legend-dot" style={{ background: group.color }} />
                {group.label}
              </span>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>
                {count}
              </span>
            </div>
          );
        })}
        {showSatellite && (
          <>
            <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8, justifyContent: "space-between", gap: 10 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 9, height: 9, background: SAR_DARK_COLOR, border: "1.5px solid #0f172a", transform: "rotate(45deg)", display: "inline-block", boxShadow: `0 0 5px ${SAR_DARK_COLOR}`, flexShrink: 0 }} />
                SAR Dark (No AIS)
              </span>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: SAR_DARK_COLOR, fontWeight: 600 }}>
                {satelliteDarkCount}
              </span>
            </div>
            <div className="legend-item" style={{ justifyContent: "space-between", gap: 10 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 9, height: 9, background: SAR_MATCHED_COLOR, border: "1.5px solid #0f172a", transform: "rotate(45deg)", display: "inline-block", flexShrink: 0 }} />
                SAR AIS-Matched
              </span>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: SAR_MATCHED_COLOR, fontWeight: 600 }}>
                {satelliteMatchedCount}
              </span>
            </div>
          </>
        )}
        <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8, justifyContent: "space-between", gap: 10 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="legend-dot" style={{ background: "#3b82f6" }} />
            Oil Port / Terminal
          </span>
          <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>{portsCount}</span>
        </div>
        {showChokepoints && (
          <div className="legend-item">
            <div style={{ width: 18, height: 0, borderTop: "2.5px dashed #facc15", marginRight: 6 }}></div>
            Chokepoint Gate Line
          </div>
        )}
        {selectedVessel && trailLatLngsCount > 0 && (
          <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <div style={{ width: 22, height: 4, background: "linear-gradient(to right, hsl(0, 90%, 52%), hsl(65, 90%, 52%), hsl(130, 90%, 52%))", borderRadius: 2, marginRight: 6 }}></div>
            Route (Old → Now, {trailLatLngsCount} pts)
          </div>
        )}
      </div>

      {/* Toggle buttons */}
      <div style={{ position: "absolute", top: 16, left: 16, zIndex: 1000, display: "flex", flexDirection: "column", gap: 8 }}>
        <button onClick={() => setShowPorts(!showPorts)} style={{ padding: "8px 14px", background: showPorts ? "var(--accent-blue)" : "var(--bg-card)", color: showPorts ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>⚓ Ports</button>
        <button onClick={() => setShowChokepoints(!showChokepoints)} style={{ padding: "8px 14px", background: showChokepoints ? "var(--accent-blue)" : "var(--bg-card)", color: showChokepoints ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>🔶 Chokepoints</button>
        <button onClick={() => setShowTrails(!showTrails)} style={{ padding: "8px 14px", background: showTrails ? "#06b6d4" : "var(--bg-card)", color: showTrails ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>🛤️ Route Trail</button>
        <button
          onClick={() => setShowSatellite(!showSatellite)}
          title="Sentinel-1 & Global Fishing Watch SAR satellite detections"
          style={{
            padding: "8px 14px",
            background: showSatellite ? "#0891b2" : "var(--bg-card)",
            color: showSatellite ? "#fff" : "var(--text-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            backdropFilter: "blur(8px)",
          }}
        >
          🛰️ Satellite SAR{showSatellite && satelliteCount > 0 ? ` (${satelliteDarkCount} dark / ${satelliteCount})` : ""}
        </button>
      </div>
    </>
  );
}
