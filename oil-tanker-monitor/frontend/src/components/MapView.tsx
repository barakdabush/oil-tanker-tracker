"use client";

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import React, { useEffect, useState, useCallback, useRef } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Popup,
  Polygon,
  Rectangle,
  Polyline,
  Tooltip,
  useMap,
} from "react-leaflet";

import type { Vessel, Port, Chokepoint, STSEvent } from "@/lib/types";

/** Custom zoom controls rendered inside the MapContainer so useMap() works */
function ZoomControls() {
  const map = useMap();
  const btnStyle: React.CSSProperties = {
    width: 36,
    height: 36,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(15, 23, 42, 0.85)",
    border: "1px solid rgba(255,255,255,0.12)",
    color: "#e2e8f0",
    fontSize: 20,
    fontWeight: 300,
    cursor: "pointer",
    backdropFilter: "blur(8px)",
    userSelect: "none",
    transition: "background 0.15s",
  };
  return (
    <div
      style={{
        position: "absolute",
        bottom: 32,
        right: 16,
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        borderRadius: 10,
        overflow: "hidden",
        boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
      }}
    >
      <button
        title="Zoom in"
        style={{ ...btnStyle, borderRadius: "10px 10px 0 0", borderBottom: "none" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(6,182,212,0.25)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(15,23,42,0.85)")}
        onClick={() => map.zoomIn()}
      >
        +
      </button>
      <button
        title="Zoom out"
        style={{ ...btnStyle, borderRadius: "0 0 10px 10px" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(6,182,212,0.25)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(15,23,42,0.85)")}
        onClick={() => map.zoomOut()}
      >
        −
      </button>
    </div>
  );
}

/** Component to update map view when a vessel is selected */
function MapUpdater({ selectedVessel }: { selectedVessel: Vessel | null }) {
  const map = useMap();
  useEffect(() => {
    if (selectedVessel && selectedVessel.last_lat != null && selectedVessel.last_lon != null) {
      // Fly to the vessel with a smooth zoom animation
      map.flyTo([selectedVessel.last_lat, selectedVessel.last_lon], 10, { duration: 1.5 });
    }
  }, [selectedVessel, map]);
  return null;
}

/** Convert GeoJSON {type: "Polygon", coordinates: [[[lon, lat], ...]]} to Leaflet [[lat, lon], ...] */
function geojsonToLeaflet(geojson: any): [number, number][] {
  if (!geojson || geojson.type !== "Polygon" || !geojson.coordinates?.length) return [];
  return geojson.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]]);
}

/** Viewport-aware vessel fetcher — lives inside MapContainer to access useMap() */
function ViewportFetcher({
  apiBase,
  onVesselsLoaded,
}: {
  apiBase: string;
  onVesselsLoaded: (vessels: Vessel[]) => void;
}) {
  const map = useMap();
  const lastBoundsRef = useRef<L.LatLngBounds | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchViewport = useCallback(async () => {
    const bounds = map.getBounds();

    // Skip if bounds haven't changed significantly (< 15% shift) to avoid redundant calls
    if (lastBoundsRef.current) {
      const prev = lastBoundsRef.current;
      const latSpan = Math.abs(bounds.getNorth() - bounds.getSouth());
      const lonSpan = Math.abs(bounds.getEast() - bounds.getWest());
      const latDiff = Math.abs(bounds.getCenter().lat - prev.getCenter().lat);
      const lonDiff = Math.abs(bounds.getCenter().lng - prev.getCenter().lng);
      if (latDiff < latSpan * 0.15 && lonDiff < lonSpan * 0.15) return;
    }

    lastBoundsRef.current = bounds;

    const params = new URLSearchParams({
      lat_min: bounds.getSouth().toFixed(5),
      lon_min: bounds.getWest().toFixed(5),
      lat_max: bounds.getNorth().toFixed(5),
      lon_max: bounds.getEast().toFixed(5),
      limit: "1000",
    });

    try {
      const res = await fetch(`${apiBase}/api/vessels?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data?.vessels?.length) onVesselsLoaded(data.vessels);
    } catch (_) {}
  }, [map, apiBase, onVesselsLoaded]);

  useEffect(() => {
    const handler = () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(fetchViewport, 600);
    };
    map.on("moveend", handler);
    // Fetch immediately for the initial viewport
    fetchViewport();
    return () => {
      map.off("moveend", handler);
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [map, fetchViewport]);

  return null;
}

interface TrailPoint {
  time: string;
  latitude: number;
  longitude: number;
  speed?: number | null;
}

function getVesselColor(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;
  if (hoursSinceLastSeen > 6) return "#1f2937";
  if ((v.last_speed || 0) <= 1) return "#f59e0b";
  if ((v.draft || 0) > 12) return "#ef4444";
  return "#10b981";
}

function getVesselStatus(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;
  if (hoursSinceLastSeen > 6) return "DARK";
  if ((v.last_speed || 0) <= 1) return "At Port";
  if ((v.draft || 0) > 12) return "Loaded";
  return "Ballast";
}

function getCongestionColor(status: string): string {
  if (status === "congested") return "#ef4444";
  if (status === "low") return "#f59e0b";
  return "#10b981";
}

/** Interpolate trail color from deep blue (old) → bright cyan (recent) */
function trailColor(idx: number, total: number): string {
  const t = total <= 1 ? 1 : idx / (total - 1);
  const r = Math.round(0 + t * 6);       // 0 -> 6
  const g = Math.round(100 + t * 82);    // 100 -> 182
  const b = Math.round(255);             // 255
  return `rgb(${r},${g},${b})`;
}

const VesselLayer = React.memo(function VesselLayer({
  vessels,
  selectedVessel,
  handleVesselClick,
}: {
  vessels: Vessel[];
  selectedVessel: Vessel | null;
  handleVesselClick: (v: Vessel) => void;
}) {
  const map = useMap();
  const markersRef = useRef<Map<number, L.CircleMarker>>(new Map());

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      markersRef.current.forEach(marker => marker.remove());
      markersRef.current.clear();
    };
  }, []);

  // Update markers imperatively when vessels array changes (initial load/search)
  // or when selectedVessel changes (to update styling)
  useEffect(() => {
    const currentMmsis = new Set<number>();

    vessels.forEach(v => {
      if (v.last_lat == null || v.last_lon == null) return;
      currentMmsis.add(v.mmsi);

      let marker = markersRef.current.get(v.mmsi);
      const isSelected = selectedVessel?.mmsi === v.mmsi;
      
      const style = {
        color: isSelected ? "#ffffff" : getVesselColor(v),
        fillColor: getVesselColor(v),
        fillOpacity: 0.9,
        weight: isSelected ? 3 : 2,
        radius: isSelected ? 10 : 7,
      };

      if (!marker) {
        // Create new marker
        marker = L.circleMarker([v.last_lat, v.last_lon], style).addTo(map);
        
        // Add tooltip
        const tooltipContent = `<span style="color: #111; font-size: 12px; font-family: sans-serif">${v.name || `MMSI ${v.mmsi}`}</span>`;
        marker.bindTooltip(tooltipContent, { direction: "top", offset: [0, -8] });
        
        // Add click handler
        marker.on('click', () => handleVesselClick(v));
        
        markersRef.current.set(v.mmsi, marker);
      } else {
        // Update existing marker
        marker.setLatLng([v.last_lat, v.last_lon]);
        marker.setStyle(style);
        marker.setRadius(style.radius);
      }
    });

    // Remove markers for vessels no longer in the array
    markersRef.current.forEach((marker, mmsi) => {
      if (!currentMmsis.has(mmsi)) {
        marker.remove();
        markersRef.current.delete(mmsi);
      }
    });
  }, [vessels, selectedVessel, map, handleVesselClick]);

  // Expose the markers map to the parent component via global window object
  // for high-perf bypass of React tree
  useEffect(() => {
    (window as any).__vesselMarkersMap = markersRef.current;
  }, []);

  return null;
}, (prevProps, nextProps) => {
  // We only care if the vessels base array reference changes (like initial load)
  // We DO NOT want to re-render when the WebSocket updates the vessels state.
  // We will handle WS updates by writing directly to the Leaflet markers.
  return prevProps.vessels === nextProps.vessels && prevProps.selectedVessel?.mmsi === nextProps.selectedVessel?.mmsi;
});

const AnimatedTrail = React.memo(function AnimatedTrail({ trailLatLngs, totalPts, vesselMmsi }: { trailLatLngs: [number, number][], totalPts: number, vesselMmsi: number }) {
  console.log(`[DEBUG] Rendering AnimatedTrail for vessel ${vesselMmsi} at ${new Date().toISOString()}`);

  if (trailLatLngs.length < 2) return null;

  return (
    <div key={`trail-container-${vesselMmsi}`}>
      <style>{`
        path.animated-dash-line-${vesselMmsi} {
          stroke-dasharray: 10, 15 !important;
          animation: dash-anim-${vesselMmsi} 1.5s linear infinite !important;
        }
        @keyframes dash-anim-${vesselMmsi} {
          0% {
            stroke-dashoffset: 25;
          }
          100% {
            stroke-dashoffset: 0;
          }
        }
      `}</style>

      {/* 1. Static background trail */}
      <Polyline
        positions={trailLatLngs}
        pathOptions={{
          color: "#0284c7", // Solid distinct blue for the base
          weight: 3,
          opacity: 0.4,
        }}
      />
      
      {/* Target markers at intervals */}
      {trailLatLngs
        .filter((_, i) => i % Math.max(1, Math.floor(totalPts / 30)) === 0)
        .map((pt, i) => (
          <CircleMarker
            key={`base-dot-${vesselMmsi}-${i}`}
            center={pt}
            radius={2}
            pathOptions={{ color: "#38bdf8", fillColor: "#38bdf8", fillOpacity: 0.6, weight: 0 }}
          />
        ))}

      {/* 2. Moving dashes on top of the base line */}
      <Polyline
        positions={trailLatLngs}
        pathOptions={{
          color: "#ffffff", 
          weight: 3,
          opacity: 0.9,
          className: `animated-dash-line-${vesselMmsi}`
        }}
      />
    </div>
  );
}, (prevProps, nextProps) => {
  // Only re-render if the vessel changes or the length of the trail changes
  return prevProps.vesselMmsi === nextProps.vesselMmsi && prevProps.trailLatLngs.length === nextProps.trailLatLngs.length;
});

export default function MapView() {
  const [vessels, setVessels] = useState<Vessel[]>([]);
  const vesselsRef = useRef<Map<number, any>>(new Map()); // Store live updates quietly
  
  // Track all vessels for quick lookup of static data like draft without closure traps
  const allVesselsRef = useRef<Map<number, Vessel>>(new Map());
  useEffect(() => {
    vessels.forEach(v => allVesselsRef.current.set(v.mmsi, v));
  }, [vessels]);

  const [ports, setPorts] = useState<Port[]>([]);
  const [dynamicChokepoints, setDynamicChokepoints] = useState<Chokepoint[]>([]);
  const [selectedVessel, setSelectedVessel] = useState<Vessel | null>(null);
  const [showPorts, setShowPorts] = useState(true);
  const [showChokepoints, setShowChokepoints] = useState(true);
  const [showTrails, setShowTrails] = useState(true);
  const [trailHours, setTrailHours] = useState(24);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Vessel[]>([]);
  const [vesselTrails, setVesselTrails] = useState<Record<number, TrailPoint[]>>({});
  const [loadingTrail, setLoadingTrail] = useState(false);
  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const WS_API = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8001";
  
  // Keep track of the selected vessel in a ref for the WebSocket handler
  const selectedVesselRef = useRef(selectedVessel);
  useEffect(() => {
    selectedVesselRef.current = selectedVessel;
  }, [selectedVessel]);

  // No longer needed: we use local appends now
  // const fetchTrailRef = useRef<((v: Vessel, h?: number) => Promise<void>) | null>(null);
  
  /** Fetch trail when a vessel is selected */
  const fetchTrail = useCallback(async (vessel: Vessel, overrideHours?: number) => {
    setLoadingTrail(true);
    try {
      const hoursToFetch = overrideHours !== undefined ? overrideHours : trailHours;
      const res = await fetch(`${API}/api/vessels/${vessel.mmsi}/trail?hours=${hoursToFetch}`);
      if (res.ok) {
        const data = await res.json();
        const pts: TrailPoint[] = (data.positions || []).map((p: any) => ({
          time: p.time,
          latitude: p.latitude,
          longitude: p.longitude,
          speed: p.speed,
        }));
        setVesselTrails((prev) => ({ ...prev, [vessel.mmsi]: pts }));
      }
    } catch (_) {}
    setLoadingTrail(false);
  }, [API, trailHours]);

  // useEffect(() => {
  //   fetchTrailRef.current = fetchTrail;
  // }, [fetchTrail]);

  // Merge incoming viewport vessels into existing state (MMSI deduplicated)
  const mergeVessels = useCallback((incoming: Vessel[]) => {
    setVessels((prev) => {
      const map = new Map(prev.map((v) => [v.mmsi, v]));
      for (const v of incoming) {
        // Only overwrite if this is newer data or vessel not yet seen
        const existing = map.get(v.mmsi);
        if (!existing || new Date(v.last_seen || "") >= new Date(existing.last_seen || "")) {
          map.set(v.mmsi, v);
        }
      }
      return Array.from(map.values());
    });
  }, []);

  useEffect(() => {
    // Only load ports once on mount — vessels are loaded by ViewportFetcher
    fetch(`${API}/api/ports?limit=500`).then((r) => r.json()).catch(() => null).then((portData) => {
      if (portData?.ports?.length > 0) setPorts(portData.ports);
    });
  }, [API]);

  // Fetch dynamic chokepoint statuses whenever the time window changes
  useEffect(() => {
    fetch(`${API}/api/chokepoints?hours=${trailHours}`)
      .then(r => r.json())
      .then(data => {
        if (data && Array.isArray(data)) setDynamicChokepoints(data);
      })
      .catch(() => {});
  }, [API, trailHours]);

  // Real-time WebSocket updates - optimized to update Leaflet directly without React renders
  useEffect(() => {
    const wsUrl = WS_API + "/api/ws/vessels";
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "POSITION_UPDATE") {
          
          // 1. UPDATE LEAFLET DIRECTLY
          // Find the map container element tracking markers
          const mapEl = document.querySelector('.leaflet-container');
          if (mapEl) {
            // Retrieve the Leaflet map instance and its attached _vesselMarkers map
            // Note: In React-Leaflet v4, getting the instance from DOM is tricky
            // We set it on a global window object for this specific high-perf case
            const markersMap: Map<number, L.CircleMarker> | undefined = (window as any).__vesselMarkersMap;
            
            if (markersMap) {
              const marker = markersMap.get(data.mmsi);
              if (marker) {
                // Instantly move the marker without triggering ANY React renders
                marker.setLatLng([data.lat, data.lon]);
                
                // Recalculate color dynamically to revive "dark" ships
                const existingVessel = allVesselsRef.current.get(data.mmsi);
                if (existingVessel) {
                  const updatedVessel = {
                    ...existingVessel,
                    last_seen: data.timestamp,
                    last_speed: data.speed,
                    last_lat: data.lat,
                    last_lon: data.lon,
                    last_course: data.course
                  };
                  allVesselsRef.current.set(data.mmsi, updatedVessel); // cache it
                  
                  const color = getVesselColor(updatedVessel);
                  const isSelected = selectedVesselRef.current?.mmsi === data.mmsi;
                  
                  marker.setStyle({
                    color: isSelected ? "#ffffff" : color,
                    fillColor: color,
                    fillOpacity: 0.9,
                    weight: isSelected ? 3 : 2,
                  });
                  marker.setRadius(isSelected ? 10 : 7);
                }
              }
            }
          }

          // 2. SILENT STATE UPDATE
          // We still need to update the vessels state so if the user clicks a ship later
          // or searches for it, we have the latest coordinates. 
          // However, we DO NOT want to trigger a re-render of the Map component.
          // By updating a ref, we keep tracking data without rendering.
          vesselsRef.current.set(data.mmsi, {
             mmsi: data.mmsi,
             name: data.name,
             lat: data.lat,
             lon: data.lon,
             speed: data.speed,
             course: data.course,
             timestamp: data.timestamp
          });

          // Optional: If the currently selected vessel moves, update its state 
          // to update the info panel, but only that panel.
          setSelectedVessel(prev => {
             if (prev && prev.mmsi === data.mmsi) {
                return {
                  ...prev,
                  last_lat: data.lat,
                  last_lon: data.lon,
                  last_speed: data.speed,
                  last_course: data.course,
                  last_seen: data.timestamp
                };
             }
             return prev;
          });
          
          // 3. AUTO-REFRESH ROUTE TRAIL (INCREMENTAL)
          // Instead of refetching the whole history (which is slow and spams the API),
          // we locally append the new point if the backend signals it's a "trail point" (e.g. every 1 min)
          const currentVessel = selectedVesselRef.current;
          if (data.update_ui_trail && currentVessel && data.mmsi === currentVessel.mmsi) {
            setVesselTrails(prev => {
              const currentTrail = prev[data.mmsi] || [];
              // Prevent duplicates if the message arrives twice
              const lastPt = currentTrail[currentTrail.length - 1];
              if (lastPt && lastPt.time === data.timestamp) return prev;

              return {
                ...prev,
                [data.mmsi]: [...currentTrail, {
                  time: data.timestamp,
                  latitude: data.lat,
                  longitude: data.lon,
                  speed: data.speed,
                }]
              };
            });
          }
        }
      } catch (error) {
        // Ignore parsing errors
      }
    };

    return () => {
      ws.close();
    };
  }, [WS_API]);

  const handleVesselClick = (v: Vessel) => {
    setSelectedVessel(v);
    fetchTrail(v, trailHours);
  };

  const currentTrail = selectedVessel ? (vesselTrails[selectedVessel.mmsi] || []) : [];
  const trailLatLngs: [number, number][] = currentTrail
    .filter((p) => p.latitude && p.longitude)
    .map((p) => [p.latitude, p.longitude]);

  // Fetch dynamic search results through the backend API
  useEffect(() => {
    if (!searchQuery) {
      setSearchResults([]);
      return;
    }
    const delayTimer = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/api/vessels?limit=10&search=${encodeURIComponent(searchQuery)}`);
        const data = await res.json();
        if (data?.vessels) {
          setSearchResults(data.vessels);
        }
      } catch (err) {}
    }, 300);
    return () => clearTimeout(delayTimer);
  }, [searchQuery, API]);

  return (
    <div className="map-container">
      {/* Search Bar */}
      <div
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          gap: 4,
          width: 280,
        }}
      >
        <div style={{ position: "relative" }}>
          <input
            type="text"
            placeholder="Search Ship by Name or MMSI..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              padding: "10px 36px 10px 14px",
              background: "rgba(15, 23, 42, 0.85)",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 8,
              color: "#fff",
              fontSize: 13,
              backdropFilter: "blur(8px)",
              outline: "none",
            }}
          />
          {searchQuery ? (
            <button
              onClick={() => setSearchQuery("")}
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                background: "transparent",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
                fontSize: 16,
              }}
            >
              ✕
            </button>
          ) : (
            <span
              style={{
                position: "absolute",
                right: 12,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--text-muted)",
                fontSize: 14,
                pointerEvents: "none",
              }}
            >
              🔍
            </span>
          )}
        </div>
        
        {searchQuery && searchResults.length > 0 && (
          <div style={{
            background: "rgba(15, 23, 42, 0.95)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 8,
            overflow: "hidden",
            boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
            backdropFilter: "blur(12px)",
          }}>
            {searchResults.map(v => (
              <div
                key={v.mmsi}
                onClick={() => {
                  setSelectedVessel(v);
                  // Ensure this ship is in the main render array so the map dot actually draws
                  setVessels((prev) => {
                    const exists = prev.find(existing => existing.mmsi === v.mmsi);
                    return exists ? prev : [...prev, v];
                  });
                  fetchTrail(v, trailHours);
                  setSearchQuery("");
                }}
                style={{
                  padding: "10px 14px",
                  cursor: "pointer",
                  borderBottom: "1px solid rgba(255,255,255,0.05)",
                  display: "flex",
                  flexDirection: "column",
                  transition: "background 0.2s"
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "rgba(6,182,212,0.15)"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
              >
                <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>
                  {v.name || `MMSI ${v.mmsi}`}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  MMSI: {v.mmsi}
                </div>
              </div>
            ))}
          </div>
        )}
        {searchQuery && searchResults.length === 0 && (
          <div style={{
            background: "rgba(15, 23, 42, 0.95)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 8,
            padding: "12px",
            color: "var(--text-muted)",
            fontSize: 12,
            textAlign: "center",
            backdropFilter: "blur(12px)",
          }}>
            No vessels found matching "{searchQuery}"
          </div>
        )}
      </div>

      <MapContainer
        center={[25, 45]}
        zoom={3}
        style={{ width: "100%", height: "100%" }}
        zoomControl={false}
      >
        <ZoomControls />
        <MapUpdater selectedVessel={selectedVessel} />
        <ViewportFetcher apiBase={API} onVesselsLoaded={mergeVessels} />
        <TileLayer
          attribution='&copy; <a href="https://carto.com">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        />

        {/* Chokepoint polygons */}
        {showChokepoints &&
          dynamicChokepoints.map((cp) => {
            const status = cp.congestion_status;
            const count = cp.current_vessel_count || 0;
            const flowText = `${count} vessels / ${trailHours === 168 ? '1w' : trailHours === 336 ? '2w' : trailHours + 'h'}`;
            const positions = cp.boundary_geojson ? geojsonToLeaflet(cp.boundary_geojson) : [];

            if (positions.length === 0) return null;

            return (
              <Polygon
                key={`cp-${cp.id}`}
                positions={positions}
                pathOptions={{
                  color: getCongestionColor(status),
                  weight: 2,
                  fillOpacity: 0.15,
                  dashArray: "5 5",
                }}
              >
                <Tooltip permanent direction="center" className="chokepoint-tooltip">
                  <div style={{ textAlign: "center", fontSize: 11 }}>
                    <strong>{cp.name}</strong>
                    <br />
                    <span style={{ color: getCongestionColor(status) }}>{flowText}</span>
                  </div>
                </Tooltip>
              </Polygon>
            );
          })}

        {/* ── AIS Monitoring Zones ──────────────────────────── */}
        {/* These rectangles match the bounding boxes subscribed in ais_ingestion.py */}
        {[
          { name: "Strait of Hormuz",          bounds: [[27.5, 54.0], [22.0, 60.5]] as [[number,number],[number,number]] },
          { name: "Strait of Malacca",         bounds: [[7.0, 98.0], [0.5, 105.5]] as [[number,number],[number,number]] },
          { name: "Suez Canal",                bounds: [[32.0, 31.5], [27.0, 34.0]] as [[number,number],[number,number]] },
          { name: "Bab el-Mandeb",             bounds: [[14.0, 41.0], [10.5, 46.5]] as [[number,number],[number,number]] },
          { name: "Turkish Straits (Bosporus)",bounds: [[42.0, 27.5], [40.0, 30.5]] as [[number,number],[number,number]] },
          { name: "Danish Straits",            bounds: [[57.5, 9.5], [54.0, 14.5]] as [[number,number],[number,number]] },
          { name: "Cape of Good Hope",         bounds: [[-32.0, 16.5], [-37.0, 21.5]] as [[number,number],[number,number]] },
          { name: "Panama Canal",              bounds: [[10.5, -80.5], [7.5, -77.5]] as [[number,number],[number,number]] },
        ].map((zone) => (
          <Rectangle
            key={zone.name}
            bounds={zone.bounds}
            pathOptions={{
              color: "#06b6d4",
              weight: 2,
              dashArray: "6 4",
              fillColor: "#06b6d4",
              fillOpacity: 0.08,
            }}
          >
            <Tooltip sticky>
              <div style={{ fontSize: 12 }}>
                <strong>📡 {zone.name}</strong>
                <br />
                <span style={{ fontSize: 10, color: "#888" }}>AIS Monitoring Zone</span>
              </div>
            </Tooltip>
          </Rectangle>
        ))}

        {/* Port markers */}
        {showPorts &&
          ports.map((port) => (
            <CircleMarker
              key={`port-${port.id}`}
              center={[port.latitude, port.longitude]}
              radius={5}
              pathOptions={{
                color: "#3b82f6",
                fillColor: "#3b82f6",
                fillOpacity: 0.6,
                weight: 1,
              }}
            >
              <Popup>
                <div style={{ color: "#111", minWidth: 160 }}>
                  <strong>⚓ {port.name}</strong>
                  <br />
                  <span style={{ fontSize: 12 }}>
                    {port.country} · {port.region}
                  </span>
                  <br />
                  <span style={{ fontSize: 11, color: "#666" }}>{port.port_type}</span>
                </div>
              </Popup>
            </CircleMarker>
          ))}

        {/* ── Route trail for selected vessel ─────────────── */}
        {showTrails && selectedVessel && trailLatLngs.length > 1 && (
          <AnimatedTrail trailLatLngs={trailLatLngs} totalPts={trailLatLngs.length} vesselMmsi={selectedVessel.mmsi} />
        )}

        {/* Vessel markers */}
        <VesselLayer
          vessels={vessels}
          selectedVessel={selectedVessel}
          handleVesselClick={handleVesselClick}
        />
      </MapContainer>

      {/* Legend */}
      <div className="map-legend">
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>
          Vessel Status
        </div>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: "#ef4444" }}></div>
          Loaded
        </div>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: "#10b981" }}></div>
          Ballast / In Transit
        </div>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: "#f59e0b" }}></div>
          At Port
        </div>
        <div className="legend-item">
          <div className="legend-dot" style={{ background: "#1f2937", border: "1px solid #6b7280" }}></div>
          Dark (AIS off)
        </div>
        <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <div className="legend-dot" style={{ background: "#3b82f6" }}></div>
          Oil Port / Terminal
        </div>
        {selectedVessel && trailLatLngs.length > 0 && (
          <div className="legend-item" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <div style={{ width: 16, height: 3, background: "linear-gradient(to right, #00ffff, #ffffff)", borderRadius: 2, marginRight: 6 }}></div>
            Route ({trailLatLngs.length} pts)
          </div>
        )}
      </div>

      {/* Toggle controls */}
      <div
        style={{
          position: "absolute",
          top: 16,
          left: 16,
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <button
          onClick={() => setShowPorts(!showPorts)}
          style={{
            padding: "8px 14px",
            background: showPorts ? "var(--accent-blue)" : "var(--bg-card)",
            color: showPorts ? "#fff" : "var(--text-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            backdropFilter: "blur(8px)",
          }}
        >
          ⚓ Ports
        </button>
        <button
          onClick={() => setShowChokepoints(!showChokepoints)}
          style={{
            padding: "8px 14px",
            background: showChokepoints ? "var(--accent-blue)" : "var(--bg-card)",
            color: showChokepoints ? "#fff" : "var(--text-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            backdropFilter: "blur(8px)",
          }}
        >
          🔶 Chokepoints
        </button>
        <button
          onClick={() => setShowTrails(!showTrails)}
          style={{
            padding: "8px 14px",
            background: showTrails ? "#06b6d4" : "var(--bg-card)",
            color: showTrails ? "#fff" : "var(--text-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            backdropFilter: "blur(8px)",
          }}
        >
          🛤️ Route Trail
        </button>
      </div>

      {/* Vessel detail panel */}
      {selectedVessel && (
        <div className="map-overlay-panel animate-slide">
          <div
            className="panel-header"
            style={{ background: "var(--bg-secondary)" }}
          >
            <h3>🚢 {selectedVessel.name || `MMSI ${selectedVessel.mmsi}`}</h3>
            <button
              onClick={() => { setSelectedVessel(null); }}
              style={{
                background: "none",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
                fontSize: 18,
              }}
            >
              ✕
            </button>
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
                  <span
                    className={`badge ${
                      getVesselStatus(selectedVessel) === "DARK"
                        ? "badge-dark"
                        : getVesselStatus(selectedVessel) === "At Port"
                        ? "badge-loading"
                        : getVesselStatus(selectedVessel) === "Loaded"
                        ? "badge-unloading"
                        : "badge-normal"
                    }`}
                  >
                    {getVesselStatus(selectedVessel)}
                  </span>
                </div>
              </div>
              <div>
                <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Speed</div>
                <div style={{ fontWeight: 600 }}>
                  {selectedVessel.last_speed?.toFixed(1) || "—"} kn
                </div>
              </div>
              <div>
                <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Draft</div>
                <div style={{ fontWeight: 600 }}>
                  {selectedVessel.draft?.toFixed(1) || "—"} m
                </div>
              </div>
              <div>
                <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Length × Beam</div>
                <div style={{ fontWeight: 600 }}>
                  {selectedVessel.length || "—"} × {selectedVessel.beam || "—"} m
                </div>
              </div>
              <div>
                <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Destination</div>
                <div style={{ fontWeight: 600 }}>{selectedVessel.destination || "—"}</div>
              </div>
            </div>

            {/* Route info */}
            <div
              style={{
                marginTop: 16,
                padding: "12px 14px",
                background: "var(--bg-secondary)",
                borderRadius: 8,
                fontSize: 12,
              }}
            >
              <div style={{ color: "var(--text-muted)", marginBottom: 4 }}>Last Position</div>
              <div style={{ fontFamily: "monospace", color: "var(--accent-cyan)" }}>
                {selectedVessel.last_lat?.toFixed(4)}°, {selectedVessel.last_lon?.toFixed(4)}°
              </div>
              <div style={{ color: "var(--text-muted)", marginTop: 6, fontSize: 11 }}>
                Course: {selectedVessel.last_course?.toFixed(0) || "—"}° · DWT{" "}
                {selectedVessel.dwt ? `${(selectedVessel.dwt / 1000).toFixed(0)}K` : "—"} t
              </div>
            </div>

            {/* Trail info */}
            <div
              style={{
                marginTop: 10,
                padding: "12px 14px",
                background: "rgba(6, 182, 212, 0.08)",
                borderRadius: 8,
                border: "1px solid rgba(6, 182, 212, 0.2)",
                fontSize: 12,
              }}
            >
              <div style={{ color: "var(--accent-cyan)", fontWeight: 600, marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>🛤️ Route Trail</span>
                {loadingTrail && <span style={{ fontSize: 10, opacity: 0.7 }}>Loading...</span>}
                {!loadingTrail && <span style={{ fontSize: 10, opacity: 0.7 }}>{trailLatLngs.length} points</span>}
              </div>

              {/* Time window selector */}
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {[6, 12, 24, 48, 72, 168, 336].map((h) => (
                  <button
                    key={h}
                    onClick={() => {
                      setTrailHours(h);
                      fetchTrail(selectedVessel, h);
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
                    {h === 168 ? '1w' : h === 336 ? '2w' : `${h}h`}
                  </button>
                ))}
              </div>

              {trailLatLngs.length === 0 && !loadingTrail && (
                <div style={{ color: "var(--text-muted)", marginTop: 8, fontSize: 11 }}>
                  No position history available for this window.
                </div>
              )}
            </div>

          </div>
        </div>
      )}
    </div>
  );
}
