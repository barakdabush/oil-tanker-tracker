"use client";

import React from "react";
import { CircleMarker, Popup } from "react-leaflet";
import type { SatelliteDetection } from "@/lib/types";

interface SatelliteLayerProps {
  detections: SatelliteDetection[];
}

function formatSourceLabel(source: string): string {
  if (source === "copernicus_s1") return "ESA Sentinel-1 SAR";
  if (source === "gfw_sar") return "Global Fishing Watch SAR";
  return source;
}

export function SatelliteLayer({ detections }: SatelliteLayerProps) {
  if (!detections || detections.length === 0) return null;

  return (
    <>
      {detections.map((det) => {
        const color = det.is_dark ? "#f43f5e" : "#818cf8";
        return (
          <React.Fragment key={`sat-${det.id}`}>
            {/* Outer radar halo for dark (AIS-off) vessels */}
            {det.is_dark && (
              <CircleMarker
                center={[det.latitude, det.longitude]}
                radius={11}
                interactive={false}
                pathOptions={{
                  color,
                  fillColor: color,
                  fillOpacity: 0.14,
                  weight: 1.5,
                  dashArray: "3 3",
                }}
              />
            )}
            <CircleMarker
              center={[det.latitude, det.longitude]}
              radius={det.is_dark ? 5.5 : 4.5}
              pathOptions={{
                color: "#ffffff",
                fillColor: color,
                fillOpacity: 0.9,
                weight: 1.5,
              }}
            >
              <Popup>
                <div style={{ color: "#111", minWidth: 210, fontSize: 12, lineHeight: 1.45 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <strong>🛰️ {formatSourceLabel(det.source)}</strong>
                    <span
                      style={{
                        padding: "2px 6px",
                        borderRadius: 4,
                        fontSize: 10,
                        fontWeight: 700,
                        background: det.is_dark ? "#ffe4e6" : "#e0e7ff",
                        color: det.is_dark ? "#be123c" : "#3730a3",
                      }}
                    >
                      {det.is_dark ? "DARK (NO AIS)" : "AIS MATCHED"}
                    </span>
                  </div>
                  <div>
                    <strong>Coords:</strong> {det.latitude.toFixed(4)}°N, {det.longitude.toFixed(4)}°E
                  </div>
                  <div>
                    <strong>Detected:</strong> {new Date(det.detected_at).toLocaleString()}
                  </div>
                  {det.chokepoint_name && (
                    <div>
                      <strong>Zone:</strong> {det.chokepoint_name}
                    </div>
                  )}
                  {det.estimated_length_m != null && (
                    <div>
                      <strong>Est. Hull Length:</strong> ~{Math.round(det.estimated_length_m)} m
                    </div>
                  )}
                  {det.radar_strength_db != null && (
                    <div>
                      <strong>Radar Backscatter:</strong> {det.radar_strength_db.toFixed(1)} dB
                    </div>
                  )}
                  {det.matched_mmsi && (
                    <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px solid #e5e7eb" }}>
                      <strong>Matched AIS:</strong> {det.matched_vessel_name || `MMSI ${det.matched_mmsi}`}
                      {det.match_distance_km != null && ` (${det.match_distance_km.toFixed(1)} km)`}
                    </div>
                  )}
                  {det.detection_count > 1 && (
                    <div>
                      <strong>Cell Detections:</strong> {det.detection_count}
                    </div>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          </React.Fragment>
        );
      })}
    </>
  );
}
