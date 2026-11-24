"use client";

import React, { useMemo } from "react";
import { Marker, Pane, Popup } from "react-leaflet";
import L from "leaflet";
import type { SatelliteDetection } from "@/lib/types";

interface SatelliteLayerProps {
  detections: SatelliteDetection[];
}

export const SAR_DARK_COLOR = "#22d3ee";    // Electric cyan (distinct from all vessel states)
export const SAR_MATCHED_COLOR = "#a3e635"; // Bright lime (distinct from all vessel states)

function formatSourceLabel(source: string): string {
  if (source === "copernicus_s1") return "ESA Sentinel-1 SAR";
  if (source === "gfw_sar") return "Global Fishing Watch SAR";
  return source;
}

function createSarIcon(isDark: boolean): L.DivIcon {
  if (isDark) {
    // High-contrast electric cyan radar crosshair + diamond so dark SAR targets pop on dark basemap
    return L.divIcon({
      className: "sar-marker-icon",
      iconSize: [24, 24],
      iconAnchor: [12, 12],
      popupAnchor: [0, -12],
      html: `
        <div style="position:relative;width:24px;height:24px;display:flex;align-items:center;justify-content:center;">
          <div style="position:absolute;inset:0;border-radius:50%;border:2px dashed ${SAR_DARK_COLOR};background:rgba(34,211,238,0.18);box-shadow:0 0 8px rgba(34,211,238,0.65);"></div>
          <div style="width:10px;height:10px;background:${SAR_DARK_COLOR};border:2px solid #0f172a;transform:rotate(45deg);box-shadow:0 0 4px #22d3ee;"></div>
        </div>
      `,
    });
  }

  // Bright lime diamond for AIS-matched SAR targets
  return L.divIcon({
    className: "sar-marker-icon",
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    popupAnchor: [0, -9],
    html: `
      <div style="position:relative;width:18px;height:18px;display:flex;align-items:center;justify-content:center;">
        <div style="width:10px;height:10px;background:${SAR_MATCHED_COLOR};border:2px solid #0f172a;transform:rotate(45deg);box-shadow:0 0 6px rgba(163,230,53,0.75);"></div>
      </div>
    `,
  });
}

export function SatelliteLayer({ detections }: SatelliteLayerProps) {
  const darkIcon = useMemo(() => createSarIcon(true), []);
  const matchedIcon = useMemo(() => createSarIcon(false), []);

  if (!detections || detections.length === 0) return null;

  return (
    <Pane name="satellite-sar-pane" style={{ zIndex: 650 }}>
      {detections.map((det) => (
        <Marker
          key={`sat-${det.id}`}
          position={[det.latitude, det.longitude]}
          icon={det.is_dark ? darkIcon : matchedIcon}
          zIndexOffset={det.is_dark ? 1000 : 500}
        >
          <Popup>
            <div style={{ color: "#111", minWidth: 215, fontSize: 12, lineHeight: 1.45 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <strong>🛰️ {formatSourceLabel(det.source)}</strong>
                <span
                  style={{
                    padding: "2px 6px",
                    borderRadius: 4,
                    fontSize: 10,
                    fontWeight: 700,
                    background: det.is_dark ? "#cffafe" : "#ecfccb",
                    color: det.is_dark ? "#155e75" : "#3f6212",
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
        </Marker>
      ))}
    </Pane>
  );
}
