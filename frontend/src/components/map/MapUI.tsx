import React from "react";
import type { Vessel } from "@/lib/types";

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

export function MapControlsAndLegend({ showPorts, setShowPorts, showChokepoints, setShowChokepoints, showTrails, setShowTrails, selectedVessel, trailLatLngsCount }: Pick<MapUIProps, 'showPorts' | 'setShowPorts' | 'showChokepoints' | 'setShowChokepoints' | 'showTrails' | 'setShowTrails' | 'selectedVessel' | 'trailLatLngsCount'>) {
  return (
    <>
      {/* Legend */}
      <div className="map-legend">
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>Vessel Status</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#ef4444" }}></div>Loaded</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#10b981" }}></div>Ballast / In Transit</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#f59e0b" }}></div>At Port</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#1f2937", border: "1px solid #6b7280" }}></div>Dark (AIS off)</div>
        <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}><div className="legend-dot" style={{ background: "#3b82f6" }}></div>Oil Port / Terminal</div>
        {selectedVessel && trailLatLngsCount > 0 && (
          <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <div style={{ width: 16, height: 3, background: "linear-gradient(to right, #00ffff, #ffffff)", borderRadius: 2, marginRight: 6 }}></div>
            Route ({trailLatLngsCount} pts)
          </div>
        )}
      </div>

      {/* Toggle buttons */}
      <div style={{ position: "absolute", top: 16, left: 16, zIndex: 1000, display: "flex", flexDirection: "column", gap: 8 }}>
        <button onClick={() => setShowPorts(!showPorts)} style={{ padding: "8px 14px", background: showPorts ? "var(--accent-blue)" : "var(--bg-card)", color: showPorts ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>⚓ Ports</button>
        <button onClick={() => setShowChokepoints(!showChokepoints)} style={{ padding: "8px 14px", background: showChokepoints ? "var(--accent-blue)" : "var(--bg-card)", color: showChokepoints ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>🔶 Chokepoints</button>
        <button onClick={() => setShowTrails(!showTrails)} style={{ padding: "8px 14px", background: showTrails ? "#06b6d4" : "var(--bg-card)", color: showTrails ? "#fff" : "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(8px)" }}>🛤️ Route Trail</button>
      </div>
    </>
  );
}
