import React, { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap, CircleMarker, Polyline } from "react-leaflet";
import type { Vessel } from "@/lib/types";
import { getVesselColor } from "./utils";

export const VesselLayer = React.memo(function VesselLayer({
  vessels,
  selectedVessel,
  highlightedMmsis,
  handleVesselClick,
}: {
  vessels: Vessel[];
  selectedVessel: Vessel | null;
  highlightedMmsis?: number[] | null;
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
    const hasHighlight = Boolean(highlightedMmsis && highlightedMmsis.length > 0);

    vessels.forEach(v => {
      if (v.last_lat == null || v.last_lon == null) return;
      currentMmsis.add(v.mmsi);

      let marker = markersRef.current.get(v.mmsi);
      const isSelected = selectedVessel?.mmsi === v.mmsi;
      const highlightIdx = hasHighlight ? highlightedMmsis!.indexOf(v.mmsi) : -1;
      const isHighlighted = highlightIdx !== -1;

      const hoursSinceLastSeen = v.last_seen
        ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
        : 999;

      let style: L.CircleMarkerOptions;
      if (hasHighlight) {
        if (isHighlighted) {
          const highlightColor = highlightIdx === 0 ? "#3b82f6" : "#10b981";
          style = {
            color: "#ffffff",
            fillColor: highlightColor,
            opacity: 1,
            fillOpacity: 1,
            weight: 4,
            radius: 11,
          };
        } else {
          style = {
            color: "#475569",
            fillColor: "#334155",
            opacity: 0.25,
            fillOpacity: 0.15,
            weight: 1,
            radius: 4,
          };
        }
      } else {
        style = {
          color: isSelected ? "#ffffff" : getVesselColor(v),
          fillColor: getVesselColor(v),
          opacity: 1,
          fillOpacity: hoursSinceLastSeen > 24 ? 0.3 : (hoursSinceLastSeen > 6 ? 0.5 : 0.9),
          weight: isSelected ? 3 : 2,
          radius: isSelected ? 10 : 7,
        };
      }

      if (!marker) {
        marker = L.circleMarker([v.last_lat, v.last_lon], style).addTo(map);
        const tooltipContent = `<span style="color: #111; font-size: 12px; font-family: sans-serif">${v.name || `MMSI ${v.mmsi}`}</span>`;
        marker.bindTooltip(tooltipContent, { direction: "top", offset: [0, -8] });
        marker.on('click', () => handleVesselClick(v));
        markersRef.current.set(v.mmsi, marker);
      } else {
        marker.setLatLng([v.last_lat, v.last_lon]);
        marker.setStyle(style);
        if (style.radius != null) marker.setRadius(style.radius);
      }

      if (isHighlighted || isSelected) {
        marker.bringToFront();
      }
    });

    markersRef.current.forEach((marker, mmsi) => {
      if (!currentMmsis.has(mmsi)) {
        marker.remove();
        markersRef.current.delete(mmsi);
      }
    });
  }, [vessels, selectedVessel, highlightedMmsis, map, handleVesselClick]);

  useEffect(() => {
    (window as any).__vesselMarkersMap = markersRef.current;
  }, []);

  return null;
}, (prevProps, nextProps) => {
  return (
    prevProps.vessels === nextProps.vessels &&
    prevProps.selectedVessel?.mmsi === nextProps.selectedVessel?.mmsi &&
    prevProps.highlightedMmsis === nextProps.highlightedMmsis
  );
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
