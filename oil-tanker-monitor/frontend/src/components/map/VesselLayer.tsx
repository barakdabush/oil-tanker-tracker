import React, { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap, CircleMarker, Polyline } from "react-leaflet";
import type { Vessel } from "@/lib/types";
import { getVesselColor } from "./utils";

export const VesselLayer = React.memo(function VesselLayer({
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
        marker = L.circleMarker([v.last_lat, v.last_lon], style).addTo(map);
        const tooltipContent = `<span style="color: #111; font-size: 12px; font-family: sans-serif">${v.name || `MMSI ${v.mmsi}`}</span>`;
        marker.bindTooltip(tooltipContent, { direction: "top", offset: [0, -8] });
        marker.on('click', () => handleVesselClick(v));
        markersRef.current.set(v.mmsi, marker);
      } else {
        marker.setLatLng([v.last_lat, v.last_lon]);
        marker.setStyle(style);
        marker.setRadius(style.radius);
      }
    });

    markersRef.current.forEach((marker, mmsi) => {
      if (!currentMmsis.has(mmsi)) {
        marker.remove();
        markersRef.current.delete(mmsi);
      }
    });
  }, [vessels, selectedVessel, map, handleVesselClick]);

  useEffect(() => {
    (window as any).__vesselMarkersMap = markersRef.current;
  }, []);

  return null;
}, (prevProps, nextProps) => {
  return prevProps.vessels === nextProps.vessels && prevProps.selectedVessel?.mmsi === nextProps.selectedVessel?.mmsi;
});

export const AnimatedTrail = React.memo(function AnimatedTrail({ trailLatLngs, totalPts, vesselMmsi }: { trailLatLngs: [number, number][], totalPts: number, vesselMmsi: number }) {
  if (trailLatLngs.length < 2) return null;

  return (
    <div key={`trail-container-${vesselMmsi}`}>
      <style>{`
        path.animated-dash-line-${vesselMmsi} {
          stroke-dasharray: 10, 15 !important;
          animation: dash-anim-${vesselMmsi} 1.5s linear infinite !important;
        }
        @keyframes dash-anim-${vesselMmsi} {
          0% { stroke-dashoffset: 25; }
          100% { stroke-dashoffset: 0; }
        }
      `}</style>

      <Polyline
        positions={trailLatLngs}
        pathOptions={{
          color: "#0284c7",
          weight: 3,
          opacity: 0.4,
        }}
      />
      
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
  return prevProps.vesselMmsi === nextProps.vesselMmsi && prevProps.trailLatLngs.length === nextProps.trailLatLngs.length;
});
