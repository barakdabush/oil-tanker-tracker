import React, { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap, CircleMarker, Polyline } from "react-leaflet";
import type { Vessel } from "@/lib/types";
import { getVesselColor, haversineKm, splitTrailSegments, TRAIL_BREAK_DISTANCE_KM } from "./utils";

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
  const handleVesselClickRef = useRef(handleVesselClick);
  useEffect(() => {
    handleVesselClickRef.current = handleVesselClick;
  }, [handleVesselClick]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      markersRef.current.forEach(marker => marker.remove());
      markersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!map.getPane("vesselPane")) {
      const pane = map.createPane("vesselPane");
      pane.style.zIndex = "660";
    }

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
        if (isHighlighted || isSelected) {
          const highlightColor = isHighlighted
            ? (highlightIdx === 0 ? "#3b82f6" : "#10b981")
            : getVesselColor(v);
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
        marker = L.circleMarker([v.last_lat, v.last_lon], { ...style, pane: "vesselPane" }).addTo(map);
        const tooltipContent = `<span style="color: #111; font-size: 12px; font-family: sans-serif">${v.name || `MMSI ${v.mmsi}`}</span>`;
        marker.bindTooltip(tooltipContent, { direction: "top", offset: [0, -8] });
        markersRef.current.set(v.mmsi, marker);
      } else {
        marker.setLatLng([v.last_lat, v.last_lon]);
        marker.setStyle(style);
        if (style.radius != null) marker.setRadius(style.radius);
      }

      marker.off("click");
      marker.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        handleVesselClickRef.current(v);
      });

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
  }, [vessels, selectedVessel, highlightedMmsis, map]);

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

export interface TrailPointItem {
  time: string;
  latitude: number;
  longitude: number;
  speed?: number | null;
}

const TRAIL_COLOR_BUCKETS = 48;

function getTrailAgeColor(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const hue = Math.round(clamped * 130); // 0 = red (oldest), 65 = yellow, 130 = green (newest)
  return `hsl(${hue}, 90%, 52%)`;
}

export const AnimatedTrail = React.memo(function AnimatedTrail({
  trailPoints,
  vesselMmsi,
}: {
  trailPoints: TrailPointItem[];
  vesselMmsi: number;
}) {
  if (trailPoints.length < 2) return null;

  const times = trailPoints.map((p) => {
    const ms = Date.parse(p.time);
    return Number.isFinite(ms) ? ms : 0;
  });
  const minTime = times[0];
  const maxTime = times[times.length - 1];
  const span = maxTime - minTime;

  const getRatio = (idx: number) => {
    if (span > 0) return Math.max(0, Math.min(1, (times[idx] - minTime) / span));
    return trailPoints.length > 1 ? idx / (trailPoints.length - 1) : 1;
  };

  // Group consecutive segments into color buckets (max ~48 Polylines) for smooth gradient + fast rendering.
  // Break the polyline across >1,500 km cross-region jumps (e.g. separate voyages months apart).
  const runs: { positions: [number, number][]; color: string }[] = [];
  let currentBucket = -1;

  for (let i = 0; i < trailPoints.length - 1; i++) {
    const p1: [number, number] = [trailPoints[i].latitude, trailPoints[i].longitude];
    const p2: [number, number] = [trailPoints[i + 1].latitude, trailPoints[i + 1].longitude];
    if (haversineKm(p1[0], p1[1], p2[0], p2[1]) > TRAIL_BREAK_DISTANCE_KM) {
      currentBucket = -1;
      continue;
    }

    const midRatio = (getRatio(i) + getRatio(i + 1)) / 2;
    const bucket = Math.min(TRAIL_COLOR_BUCKETS - 1, Math.floor(midRatio * TRAIL_COLOR_BUCKETS));

    if (bucket !== currentBucket || runs.length === 0) {
      currentBucket = bucket;
      runs.push({
        positions: [p1, p2],
        color: getTrailAgeColor((bucket + 0.5) / TRAIL_COLOR_BUCKETS),
      });
    } else {
      runs[runs.length - 1].positions.push(p2);
    }
  }

  const contiguousSegments = splitTrailSegments(trailPoints).filter((seg) => seg.length >= 2);
  const dotStep = Math.max(1, Math.floor(trailPoints.length / 30));

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

      {runs.map((run, idx) => (
        <Polyline
          key={`trail-seg-${vesselMmsi}-${idx}`}
          positions={run.positions}
          pathOptions={{
            color: run.color,
            weight: 3.5,
            opacity: 0.9,
          }}
        />
      ))}

      {trailPoints
        .map((pt, idx) => ({ pt, idx }))
        .filter(({ idx }) => idx % dotStep === 0 || idx === trailPoints.length - 1)
        .map(({ pt, idx }) => (
          <CircleMarker
            key={`base-dot-${vesselMmsi}-${idx}`}
            center={[pt.latitude, pt.longitude]}
            radius={2.5}
            pathOptions={{
              color: getTrailAgeColor(getRatio(idx)),
              fillColor: getTrailAgeColor(getRatio(idx)),
              fillOpacity: 0.85,
              weight: 0,
            }}
          />
        ))}

      {contiguousSegments.map((seg, segIdx) => (
        <Polyline
          key={`trail-dash-${vesselMmsi}-${segIdx}`}
          positions={seg.map((p) => [p.latitude, p.longitude] as [number, number])}
          pathOptions={{
            color: "#ffffff",
            weight: 2,
            opacity: 0.3,
            className: `animated-dash-line-${vesselMmsi}`,
          }}
        />
      ))}
    </div>
  );
}, (prevProps, nextProps) => {
  return prevProps.vesselMmsi === nextProps.vesselMmsi && prevProps.trailPoints === nextProps.trailPoints;
});
