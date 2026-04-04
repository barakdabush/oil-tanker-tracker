"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, Polygon, Rectangle, Tooltip } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import type { Vessel, Port, Chokepoint } from "@/lib/types";
import { getApiUrl, getWsUrl } from "@/lib/config";

// Sub-components
import { ZoomControls, MapUpdater } from "./map/MapControls";
import { VesselLayer, AnimatedTrail } from "./map/VesselLayer";
import { ViewportFetcher } from "./map/ViewportFetcher";
import { VesselDetailPanel } from "./map/VesselDetailPanel";
import { SearchBar, MapControlsAndLegend } from "./map/MapUI";
import { getCongestionColor, geojsonToLeaflet, timeLabelMap, getVesselColor } from "./map/utils";

interface TrailPoint {
  time: string;
  latitude: number;
  longitude: number;
  speed?: number | null;
}

export default function MapView() {
  const [vessels, setVessels] = useState<Vessel[]>([]);
  const vesselsRef = useRef<Map<number, any>>(new Map());
  const allVesselsRef = useRef<Map<number, Vessel>>(new Map());

  const [ports, setPorts] = useState<Port[]>([]);
  const [dynamicChokepoints, setDynamicChokepoints] = useState<Chokepoint[]>([]);
  const [selectedVessel, setSelectedVessel] = useState<Vessel | null>(null);
  const [showPorts, setShowPorts] = useState(true);
  const [showChokepoints, setShowChokepoints] = useState(true);
  const [showTrails, setShowTrails] = useState(true);
  const [trailHours, setTrailHours] = useState(336);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Vessel[]>([]);
  const [vesselTrails, setVesselTrails] = useState<Record<number, TrailPoint[]>>({});
  const [loadingTrail, setLoadingTrail] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  
  const API = getApiUrl();
  const WS_API = getWsUrl();

  const selectedVesselRef = useRef(selectedVessel);
  useEffect(() => {
    selectedVesselRef.current = selectedVessel;
    if (selectedVessel) allVesselsRef.current.set(selectedVessel.mmsi, selectedVessel);
  }, [selectedVessel]);

  useEffect(() => {
    vessels.forEach(v => allVesselsRef.current.set(v.mmsi, v));
  }, [vessels]);

  const fetchTrail = useCallback(async (vessel: Vessel, h?: number) => {
    setLoadingTrail(true);
    try {
      const hours = h !== undefined ? h : trailHours;
      const res = await fetch(`${API}/api/vessels/${vessel.mmsi}/trail?hours=${hours}`);
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

  const mergeVessels = useCallback((incoming: Vessel[]) => {
    setVessels((prev) => {
      const map = new Map(prev.map((v) => [v.mmsi, v]));
      for (const v of incoming) {
        const existing = map.get(v.mmsi);
        if (!existing || new Date(v.last_seen || "") >= new Date(existing.last_seen || "")) {
          map.set(v.mmsi, v);
        }
      }
      return Array.from(map.values());
    });
  }, []);

  useEffect(() => {
    fetch(`${API}/api/ports?limit=500`).then((r) => r.json()).catch(() => null).then((data) => {
      if (data?.ports?.length > 0) setPorts(data.ports);
    });
  }, [API]);

  useEffect(() => {
    fetch(`${API}/api/chokepoints?hours=${trailHours}`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setDynamicChokepoints(data); })
      .catch(() => {});
  }, [API, trailHours]);

  useEffect(() => {
    const ws = new WebSocket(WS_API + "/api/ws/vessels");
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "POSITION_UPDATE") {
          const markersMap: Map<number, L.CircleMarker> | undefined = (window as any).__vesselMarkersMap;
          if (markersMap) {
            const marker = markersMap.get(data.mmsi);
            if (marker) {
              marker.setLatLng([data.lat, data.lon]);
              const existing = allVesselsRef.current.get(data.mmsi);
              if (existing) {
                const updated = { ...existing, last_seen: data.timestamp, last_speed: data.speed, last_lat: data.lat, last_lon: data.lon, last_course: data.course };
                allVesselsRef.current.set(data.mmsi, updated);
                const color = getVesselColor(updated);
                const isSelected = selectedVesselRef.current?.mmsi === data.mmsi;
                marker.setStyle({ color: isSelected ? "#ffffff" : color, fillColor: color, fillOpacity: 0.9, weight: isSelected ? 3 : 2 });
                marker.setRadius(isSelected ? 10 : 7);
              }
            }
          }
          vesselsRef.current.set(data.mmsi, data);
          setSelectedVessel(prev => (prev && prev.mmsi === data.mmsi) ? { ...prev, last_lat: data.lat, last_lon: data.lon, last_speed: data.speed, last_course: data.course, last_seen: data.timestamp } : prev);
          if (data.update_ui_trail && selectedVesselRef.current?.mmsi === data.mmsi) {
            setVesselTrails(prev => ({
              ...prev,
              [data.mmsi]: [...(prev[data.mmsi] || []), { time: data.timestamp, latitude: data.lat, longitude: data.lon, speed: data.speed }]
            }));
          }
        }
      } catch (_) {}
    };
    return () => ws.close();
  }, [WS_API]);

  const handleVesselClick = (v: Vessel) => {
    setSelectedVessel(v);
    fetchTrail(v, trailHours);
  };

  useEffect(() => {
    if (!searchQuery) { setSearchResults([]); setIsSearching(false); return; }
    setIsSearching(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/api/vessels?limit=10&search=${encodeURIComponent(searchQuery)}`);
        const data = await res.json();
        if (data?.vessels) setSearchResults(data.vessels);
      } catch (_) {} finally { setIsSearching(false); }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, API]);

  const currentTrail = selectedVessel ? (vesselTrails[selectedVessel.mmsi] || []) : [];
  const trailLatLngs: [number, number][] = currentTrail.filter(p => p.latitude && p.longitude).map(p => [p.latitude, p.longitude]);

  return (
    <div className="map-container">
      <SearchBar 
        searchQuery={searchQuery} 
        setSearchQuery={setSearchQuery} 
        searchResults={searchResults} 
        isSearching={isSearching}
        onSelectVessel={(v) => {
          setSelectedVessel(v);
          setVessels(prev => prev.find(e => e.mmsi === v.mmsi) ? prev : [...prev, v]);
          fetchTrail(v, trailHours);
          setSearchQuery("");
        }}
      />

      <MapContainer center={[25, 45]} zoom={3} style={{ width: "100%", height: "100%" }} zoomControl={false}>
        <ZoomControls />
        <MapUpdater selectedVessel={selectedVessel} />
        <ViewportFetcher apiBase={API} onVesselsLoaded={mergeVessels} />
        <TileLayer attribution='&copy; <a href="https://carto.com">CARTO</a>' url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />

        {showChokepoints && dynamicChokepoints.map((cp) => {
          const pos = geojsonToLeaflet(cp.boundary_geojson);
          if (pos.length === 0) return null;
          return (
            <Polygon key={`cp-${cp.id}`} positions={pos} pathOptions={{ color: getCongestionColor(cp.congestion_status), weight: 2, fillOpacity: 0.15, dashArray: "5 5" }}>
              <Tooltip permanent direction="center" className="chokepoint-tooltip">
                <div style={{ textAlign: "center", fontSize: 11 }}>
                  <strong>{cp.name}</strong><br />
                  <span style={{ color: getCongestionColor(cp.congestion_status) }}>{`${cp.current_vessel_count || 0} vessels / ${timeLabelMap[trailHours] || `${trailHours}h`}`}</span>
                </div>
              </Tooltip>
            </Polygon>
          );
        })}

        {[
          { name: "Strait of Hormuz",          bounds: [[27.5, 54.0], [22.0, 60.5]] },
          { name: "Strait of Malacca",         bounds: [[7.0, 98.0], [0.5, 105.5]] },
          { name: "Suez Canal",                bounds: [[32.0, 31.5], [27.0, 34.0]] },
          { name: "Bab el-Mandeb",             bounds: [[14.0, 41.0], [10.5, 46.5]] },
          { name: "Turkish Straits (Bosporus)",bounds: [[42.0, 27.5], [40.0, 30.5]] },
          { name: "Danish Straits",            bounds: [[57.5, 9.5], [54.0, 14.5]] },
          { name: "Cape of Good Hope",         bounds: [[-32.0, 16.5], [-37.0, 21.5]] },
          { name: "Panama Canal",              bounds: [[10.5, -80.5], [7.5, -77.5]] },
        ].map((zone) => (
          <Rectangle key={zone.name} bounds={zone.bounds as any} pathOptions={{ color: "#06b6d4", weight: 2, dashArray: "6 4", fillColor: "#06b6d4", fillOpacity: 0.08 }}>
            <Tooltip sticky><div style={{ fontSize: 12 }}><strong>📡 {zone.name}</strong><br /><span style={{ fontSize: 10, color: "#888" }}>AIS Monitoring Zone</span></div></Tooltip>
          </Rectangle>
        ))}

        {showPorts && ports.map((port) => (
          <CircleMarker key={`port-${port.id}`} center={[port.latitude, port.longitude]} radius={5} pathOptions={{ color: "#3b82f6", fillColor: "#3b82f6", fillOpacity: 0.6, weight: 1 }}>
            <Popup><div style={{ color: "#111", minWidth: 160 }}><strong>⚓ {port.name}</strong><br /><span style={{ fontSize: 12 }}>{port.country} · {port.region}</span><br /><span style={{ fontSize: 11, color: "#666" }}>{port.port_type}</span></div></Popup>
          </CircleMarker>
        ))}

        {showTrails && selectedVessel && trailLatLngs.length > 1 && <AnimatedTrail trailLatLngs={trailLatLngs} totalPts={trailLatLngs.length} vesselMmsi={selectedVessel.mmsi} />}
        <VesselLayer vessels={vessels} selectedVessel={selectedVessel} handleVesselClick={handleVesselClick} />
      </MapContainer>

      <MapControlsAndLegend showPorts={showPorts} setShowPorts={setShowPorts} showChokepoints={showChokepoints} setShowChokepoints={setShowChokepoints} showTrails={showTrails} setShowTrails={setShowTrails} selectedVessel={selectedVessel} trailLatLngsCount={trailLatLngs.length} />

      {selectedVessel && (
        <VesselDetailPanel 
          selectedVessel={selectedVessel} 
          onClose={() => setSelectedVessel(null)} 
          trailHours={trailHours} 
          setTrailHours={setTrailHours} 
          loadingTrail={loadingTrail} 
          trailPointsCount={trailLatLngs.length}
          onFetchTrail={fetchTrail}
        />
      )}
    </div>
  );
}
