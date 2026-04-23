import React, { useMemo } from "react";
import { Marker, Popup, Tooltip, useMap, Polyline } from "react-leaflet";
import L from "leaflet";
import type { STSEvent, TrailResponse } from "@/lib/types";

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function formatDuration(start: string, end: string | null): string {
  const endDate = end ? new Date(end) : new Date();
  const mins = Math.round((endDate.getTime() - new Date(start).getTime()) / 60000);
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 24) return `${h}h ${m}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function confidenceColor(c: number): string {
  if (c >= 0.85) return "#ef4444";
  if (c >= 0.6) return "#f59e0b";
  return "#22c55e";
}

/* ── Custom animated marker icon ─────────────────────────────────────── */

function createSTSIcon(isOngoing: boolean, confidence: number | null): L.DivIcon {
  const ring = isOngoing
    ? `<span class="sts-ring sts-ring--pulse"></span>
       <span class="sts-ring sts-ring--pulse" style="animation-delay:.6s"></span>`
    : `<span class="sts-ring"></span>`;

  const confBadge =
    confidence != null
      ? `<span class="sts-conf" style="background:${confidenceColor(confidence)}">${Math.round(confidence * 100)}%</span>`
      : "";

  return L.divIcon({
    className: "sts-marker-wrapper",
    iconSize: [40, 40],
    iconAnchor: [20, 20],
    popupAnchor: [0, -22],
    tooltipAnchor: [0, -22],
    html: `
      <div class="sts-marker ${isOngoing ? "sts-marker--ongoing" : "sts-marker--resolved"}">
        ${ring}
        <span class="sts-core">⚓</span>
        ${confBadge}
      </div>`,
  });
}

/* ── Styles ─────────────────────────────────────────────────────────── */

const STS_STYLES = `
.sts-marker-wrapper {
  background: none !important;
  border: none !important;
}
.sts-marker {
  position: relative;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.sts-core {
  position: relative;
  z-index: 2;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 13px;
  line-height: 1;
  box-shadow: 0 0 12px rgba(251,191,36,0.5), 0 2px 8px rgba(0,0,0,0.3);
  transition: transform .2s;
}
.sts-marker--ongoing .sts-core { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #78350f; }
.sts-marker--resolved .sts-core { background: linear-gradient(135deg, #94a3b8, #64748b); color: #1e293b; }
.sts-marker:hover .sts-core { transform: scale(1.2); }
.sts-ring {
  position: absolute; top: 50%; left: 50%; width: 36px; height: 36px; margin: -18px 0 0 -18px;
  border-radius: 50%; border: 2px solid rgba(251,191,36,0.4); z-index: 1;
}
.sts-ring--pulse { animation: sts-ring-expand 2.5s ease-out infinite; }
@keyframes sts-ring-expand {
  0%   { width: 26px; height: 26px; margin: -13px 0 0 -13px; opacity: 1; border-color: rgba(251,191,36,0.8); }
  100% { width: 60px; height: 60px; margin: -30px 0 0 -30px; opacity: 0; border-color: rgba(251,191,36,0); }
}
.sts-conf {
  position: absolute; top: -4px; right: -6px; z-index: 3; font-size: 9px; font-weight: 700;
  padding: 1px 4px; border-radius: 6px; color: #fff; line-height: 1.3; box-shadow: 0 1px 4px rgba(0,0,0,0.3);
}
.sts-popup .leaflet-popup-content-wrapper {
  background: rgba(15, 23, 42, 0.92); backdrop-filter: blur(16px);
  border: 1px solid rgba(251,191,36,0.25); border-radius: 14px; color: #e2e8f0; padding: 0;
}
.sts-popup .leaflet-popup-content { margin: 0; width: auto !important; }
.sts-popup-inner { min-width: 240px; font-size: 12px; font-family: 'Inter', system-ui, sans-serif; }
.sts-popup-header { display: flex; align-items: center; gap: 8px; padding: 12px 14px 10px; border-bottom: 1px solid rgba(255,255,255,0.08); }
.sts-popup-header-icon { width: 32px; height: 32px; border-radius: 8px; background: linear-gradient(135deg, #fbbf24, #d97706); display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.sts-popup-header h4 { margin: 0; font-size: 13px; font-weight: 700; color: #fbbf24; }
.sts-popup-header .sts-status-pill { font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 600; text-transform: uppercase; }
.sts-status-pill--active { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.3); }
.sts-status-pill--resolved { background: rgba(148,163,184,0.15); color: #94a3b8; border: 1px solid rgba(148,163,184,0.3); }
.sts-popup-grid { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; padding: 10px 14px 12px; }
.sts-popup-grid .label { color: #94a3b8; font-size: 11px; white-space: nowrap; }
.sts-popup-grid .value { color: #e2e8f0; font-weight: 500; }
.sts-popup-grid .value--mono { font-family: monospace; letter-spacing: .5px; }
.sts-investigate-btn {
  width: 100%; margin-top: 10px; padding: 10px; background: rgba(59, 130, 246, 0.2);
  border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 8px; color: #60a5fa;
  font-weight: 600; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px;
}
.sts-investigate-btn:hover { background: rgba(59, 130, 246, 0.3); border-color: #60a5fa; transform: translateY(-1px); }
.sts-investigate-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.sts-clear-btn {
  position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%);
  z-index: 1000; background: #ef4444; color: white; padding: 8px 16px; border-radius: 20px;
  font-weight: 600; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.3); cursor: pointer;
  display: flex; align-items: center; gap: 8px;
}
.sts-popup-duration { grid-column: 1 / -1; margin: 4px 0; padding: 6px 10px; border-radius: 8px; background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.15); display: flex; align-items: center; gap: 8px; }
.sts-popup-duration .dur-value { color: #fbbf24; font-weight: 700; font-size: 13px; }
`;

/* ── Component ───────────────────────────────────────────────────────── */

export const STSLayer = React.memo(function STSLayer({
  events,
}: {
  events: STSEvent[];
}) {
  const map = useMap();
  const [investigation, setInvestigation] = React.useState<{
    id: string;
    vesselA: TrailResponse;
    vesselB: TrailResponse;
  } | null>(null);
  const [loading, setLoading] = React.useState<string | null>(null);

  const fetchTrail = async (mmsi: number, start: string, end: string | null) => {
    const API_URL = typeof window !== 'undefined' ? (window.location.origin.includes('localhost') ? 'http://localhost:8000/api' : '/api') : '/api';
    const endParam = end ? `&end_time=${encodeURIComponent(end)}` : `&end_time=${encodeURIComponent(new Date().toISOString())}`;
    const res = await fetch(`${API_URL}/vessels/${mmsi}/trail?start_time=${encodeURIComponent(start)}${endParam}`);
    if (!res.ok) throw new Error("Failed to fetch trail");
    return res.json();
  };

  const handleInvestigate = async (event: STSEvent) => {
    setLoading(event.id);
    try {
      const [vA, vB] = await Promise.all([
        fetchTrail(event.vessel_a_mmsi, event.start_time, event.end_time),
        fetchTrail(event.vessel_b_mmsi, event.start_time, event.end_time)
      ]);
      setInvestigation({ id: event.id, vesselA: vA, vesselB: vB });
      
      // Zoom to fit the trails
      const allPoints = [
        ...vA.positions.map((p: any) => [p.latitude, p.longitude] as [number, number]),
        ...vB.positions.map((p: any) => [p.latitude, p.longitude] as [number, number])
      ];
      if (allPoints.length > 0) {
        map.fitBounds(allPoints, { padding: [50, 50] });
      }
    } catch (err) {
      console.error("Investigation failed", err);
      alert("Failed to load vessel history for this period.");
    } finally {
      setLoading(null);
    }
  };

  // Memoize icons per event
  const icons = useMemo(() => {
    const iconMap = new Map();
    events.forEach(e => {
      iconMap.set(e.id, createSTSIcon(
        e.status === "ongoing" || e.status === "detected",
        e.confidence
      ));
    });
    return iconMap;
  }, [events]);

  return (
    <>
      <style>{STS_STYLES}</style>

      {events.map((event) => {
        if (event.lat == null || event.lon == null) return null;

        const isOngoing = event.status === "ongoing" || event.status === "detected";
        const icon = icons.get(event.id);
        if (!icon) return null;

        return (
          <Marker
            key={`sts-${event.id}`}
            position={[event.lat, event.lon]}
            icon={icon}
            eventHandlers={{
              click: () => {
                map.flyTo([event.lat!, event.lon!], 15, { duration: 1.2 });
              },
            }}
          >
            <Tooltip direction="top" offset={[0, -22]}>
              <div style={{ fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
                <span>⚠️</span>
                <span>STS Transfer {isOngoing ? "(Active)" : ""}</span>
              </div>
            </Tooltip>

            <Popup className="sts-popup" closeButton={false}>
              <div className="sts-popup-inner">
                <div className="sts-popup-header">
                  <div className="sts-popup-header-icon">⚓</div>
                  <div><h4>Ship-to-Ship Transfer</h4></div>
                  <span className={`sts-status-pill ${isOngoing ? "sts-status-pill--active" : "sts-status-pill--resolved"}`}>
                    {event.status}
                  </span>
                </div>

                <div style={{ padding: "8px 14px 0" }}>
                  <div className="sts-popup-duration">
                    <span style={{ fontSize: 14 }}>⏱️</span>
                    <div>
                      <div style={{ color: "#94a3b8", fontSize: 10 }}>Duration</div>
                      <div className="dur-value">
                        {formatDuration(event.start_time, event.end_time)}
                        {!event.end_time && " (ongoing)"}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="sts-popup-grid">
                  <span className="label">Vessel A</span>
                  <span className="value value--mono">{event.vessel_a_mmsi}</span>

                  <span className="label">Vessel B</span>
                  <span className="value value--mono">{event.vessel_b_mmsi}</span>

                  <span className="label">Started</span>
                  <span className="value">{new Date(event.start_time).toLocaleString()}</span>

                  {event.end_time && (
                    <>
                      <span className="label">Ended</span>
                      <span className="value">{new Date(event.end_time).toLocaleString()}</span>
                    </>
                  )}

                  <span className="label">Location</span>
                  <span className="value value--mono">
                    {event.lat?.toFixed(4)}°, {event.lon?.toFixed(4)}°
                  </span>

                  {event.distance_from_port_km != null && (
                    <>
                      <span className="label">From port</span>
                      <span className="value">{event.distance_from_port_km.toFixed(1)} km</span>
                    </>
                  )}

                  {event.confidence != null && (
                    <>
                      <span className="label">Confidence</span>
                      <span className="value" style={{ color: confidenceColor(event.confidence), fontWeight: 700 }}>
                        {(event.confidence * 100).toFixed(0)}%
                      </span>
                    </>
                  )}
                </div>

                <div style={{ padding: "0 14px 14px" }}>
                  <button 
                    className="sts-investigate-btn"
                    onClick={() => handleInvestigate(event)}
                    disabled={loading === event.id}
                  >
                    {loading === event.id ? "⌛ Fetching..." : "🔍 Show Meeting Trails"}
                  </button>
                </div>
              </div>
            </Popup>
          </Marker>
        );
      })}

      {investigation && (
        <>
          <button className="sts-clear-btn" onClick={() => setInvestigation(null)}>
            ✕ Clear Investigation Trails
          </button>
          
          <Polyline 
            positions={investigation.vesselA.positions.map((p: any) => [p.latitude, p.longitude])}
            pathOptions={{ color: "#3b82f6", weight: 4, opacity: 0.8, dashArray: "10, 10" }}
          />
          <Polyline 
            positions={investigation.vesselB.positions.map((p: any) => [p.latitude, p.longitude])}
            pathOptions={{ color: "#10b981", weight: 4, opacity: 0.8, dashArray: "10, 10" }}
          />
          
          {/* Start/End Markers for Investigation */}
          {investigation.vesselA.positions[0] && (
            <Tooltip position={[investigation.vesselA.positions[0].latitude, investigation.vesselA.positions[0].longitude]} permanent direction="right">
              <span style={{ color: "#3b82f6" }}>A Entry</span>
            </Tooltip>
          )}
          {investigation.vesselB.positions[0] && (
            <Tooltip position={[investigation.vesselB.positions[0].latitude, investigation.vesselB.positions[0].longitude]} permanent direction="left">
              <span style={{ color: "#10b981" }}>B Entry</span>
            </Tooltip>
          )}
        </>
      )}
    </>
  );
});
