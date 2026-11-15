import React, { useMemo } from "react";
import { Marker, Popup, Tooltip, useMap, Polyline } from "react-leaflet";
import L from "leaflet";
import type { STSEvent, TrailResponse, Vessel } from "@/lib/types";
import { getApiUrl } from "@/lib/config";

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
.sts-mobile-sheet {
  position: absolute;
  bottom: 12px;
  left: 12px;
  right: 124px;
  max-height: 38vh;
  overflow-y: auto;
  z-index: 1001;
  background: rgba(15, 23, 42, 0.96);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(251, 191, 36, 0.35);
  border-radius: 12px;
  color: #f8fafc;
  box-shadow: 0 12px 32px rgba(0,0,0,0.65);
  font-size: 12px;
  font-family: 'Inter', system-ui, sans-serif;
}
.sts-mobile-sheet .sts-popup-header { padding: 8px 12px; }
.sts-mobile-sheet .sts-popup-grid { padding: 8px 12px; gap: 4px 10px; }
.sts-popup-duration { grid-column: 1 / -1; margin: 4px 0; padding: 6px 10px; border-radius: 8px; background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.15); display: flex; align-items: center; gap: 8px; }
.sts-popup-duration .dur-value { color: #fbbf24; font-weight: 700; font-size: 13px; }
`;

/* ── Component ───────────────────────────────────────────────────────── */

export const STSLayer = React.memo(function STSLayer({
  events,
  selectedStsEvent,
  onSelectSts,
  onLoadingChange,
  onHighlightVessels,
  onEnsureVessels,
}: {
  events: STSEvent[];
  selectedStsEvent?: STSEvent | null;
  onSelectSts?: (event: STSEvent | null) => void;
  onLoadingChange?: (loading: boolean) => void;
  onHighlightVessels?: (mmsis: number[] | null) => void;
  onEnsureVessels?: (vessels: Vessel[]) => void;
}) {
  const map = useMap();
  const [investigation, setInvestigation] = React.useState<{
    id: string;
    vesselA: TrailResponse;
    vesselB: TrailResponse;
    windowStartMs: number;
    windowEndMs: number;
  } | null>(null);
  const latestRequestRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!selectedStsEvent) {
      latestRequestRef.current = null;
      setInvestigation(null);
      onHighlightVessels?.(null);
      onLoadingChange?.(false);
    }
  }, [selectedStsEvent, onHighlightVessels, onLoadingChange]);

  const fetchVessel = async (mmsi: number): Promise<Vessel | null> => {
    try {
      const res = await fetch(`${getApiUrl()}/api/vessels/${mmsi}`);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  };

  const fetchFullTrail = async (mmsi: number): Promise<TrailResponse> => {
    const API_URL = getApiUrl();
    const res = await fetch(`${API_URL}/api/vessels/${mmsi}/trail?hours=0`);
    if (!res.ok) throw new Error("Failed to fetch trail");
    return res.json();
  };

  const handleInvestigate = async (event: STSEvent) => {
    latestRequestRef.current = event.id;
    onSelectSts?.(event);
    onLoadingChange?.(true);
    onHighlightVessels?.([event.vessel_a_mmsi, event.vessel_b_mmsi]);

    if (event.lat != null && event.lon != null) {
      map.flyTo([event.lat, event.lon], Math.max(map.getZoom(), 12), { duration: 0.5 });
    }

    const windowStartMs = new Date(event.start_time).getTime() - 6 * 3600_000;
    const windowEndMs = event.end_time
      ? new Date(event.end_time).getTime() + 1 * 3600_000
      : Date.now();

    const vesselsPromise = Promise.all([
      fetchVessel(event.vessel_a_mmsi),
      fetchVessel(event.vessel_b_mmsi),
    ]).then(([vesselAInfo, vesselBInfo]) => {
      if (latestRequestRef.current !== event.id) return [];
      const loadedVessels = [vesselAInfo, vesselBInfo].filter((v): v is Vessel => v !== null);
      if (loadedVessels.length > 0) {
        onEnsureVessels?.(loadedVessels);
        const vesselCoords: [number, number][] = loadedVessels
          .filter((v) => v.last_lat != null && v.last_lon != null)
          .map((v) => [v.last_lat!, v.last_lon!]);
        if (event.lat != null && event.lon != null) {
          vesselCoords.push([event.lat, event.lon]);
        }
        if (vesselCoords.length > 1) {
          map.fitBounds(vesselCoords, { padding: [80, 80], maxZoom: 13 });
        }
      }
      return loadedVessels;
    });

    try {
      const [vA, vB, loadedVessels] = await Promise.all([
        fetchFullTrail(event.vessel_a_mmsi),
        fetchFullTrail(event.vessel_b_mmsi),
        vesselsPromise,
      ]);
      if (latestRequestRef.current !== event.id) return;

      setInvestigation({ id: event.id, vesselA: vA, vesselB: vB, windowStartMs, windowEndMs });

      const inEncounterWindow = (p: any) => {
        if (!p.latitude || !p.longitude) return false;
        const ms = Date.parse(p.time);
        return Number.isFinite(ms) && ms >= windowStartMs && ms <= windowEndMs;
      };

      const focusPoints: [number, number][] = [
        ...vA.positions.filter(inEncounterWindow).map((p: any) => [p.latitude, p.longitude] as [number, number]),
        ...vB.positions.filter(inEncounterWindow).map((p: any) => [p.latitude, p.longitude] as [number, number]),
        ...loadedVessels
          .filter((v) => v.last_lat != null && v.last_lon != null)
          .map((v) => [v.last_lat!, v.last_lon!] as [number, number]),
      ];
      if (event.lat != null && event.lon != null) {
        focusPoints.push([event.lat, event.lon]);
      }
      if (focusPoints.length > 1) {
        map.fitBounds(focusPoints, { padding: [60, 60], maxZoom: 14 });
      }
    } catch (err) {
      console.error("Investigation failed", err);
    } finally {
      if (latestRequestRef.current === event.id) {
        onLoadingChange?.(false);
      }
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
                handleInvestigate(event);
              },
            }}
          >
            <Tooltip direction="top" offset={[0, -22]}>
              <div style={{ fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
                <span>⚠️</span>
                <span>STS Transfer {isOngoing ? "(Active)" : ""}</span>
              </div>
            </Tooltip>
          </Marker>
        );
      })}

      {investigation && (() => {
        const isValid = (p: any) => Boolean(p.latitude && p.longitude);
        const inWindow = (p: any) => {
          if (!isValid(p)) return false;
          const ms = Date.parse(p.time);
          return Number.isFinite(ms) && ms >= investigation.windowStartMs && ms <= investigation.windowEndMs;
        };

        const fullA = investigation.vesselA.positions.filter(isValid).map((p: any) => [p.latitude, p.longitude] as [number, number]);
        const fullB = investigation.vesselB.positions.filter(isValid).map((p: any) => [p.latitude, p.longitude] as [number, number]);
        const encA = investigation.vesselA.positions.filter(inWindow).map((p: any) => [p.latitude, p.longitude] as [number, number]);
        const encB = investigation.vesselB.positions.filter(inWindow).map((p: any) => [p.latitude, p.longitude] as [number, number]);

        const entryA = encA[0] ?? fullA[0];
        const entryB = encB[0] ?? fullB[0];

        return (
          <>
            {fullA.length > 1 && (
              <Polyline
                positions={fullA}
                pathOptions={{ color: "#3b82f6", weight: 2.5, opacity: 0.35, dashArray: "6, 8" }}
              />
            )}
            {fullB.length > 1 && (
              <Polyline
                positions={fullB}
                pathOptions={{ color: "#10b981", weight: 2.5, opacity: 0.35, dashArray: "6, 8" }}
              />
            )}

            {encA.length > 1 && (
              <Polyline
                positions={encA}
                pathOptions={{ color: "#3b82f6", weight: 4.5, opacity: 0.95 }}
              />
            )}
            {encB.length > 1 && (
              <Polyline
                positions={encB}
                pathOptions={{ color: "#10b981", weight: 4.5, opacity: 0.95 }}
              />
            )}

            {entryA && (
              <Tooltip position={entryA} permanent direction="right">
                <span style={{ color: "#3b82f6" }}>A Entry</span>
              </Tooltip>
            )}
            {entryB && (
              <Tooltip position={entryB} permanent direction="left">
                <span style={{ color: "#10b981" }}>B Entry</span>
              </Tooltip>
            )}
          </>
        );
      })()}
    </>
  );
});
