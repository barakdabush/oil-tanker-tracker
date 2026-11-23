/* Map utility functions and constants */
import type { Vessel } from "@/lib/types";

export const timeLabelMap: Record<number, string> = {
  0: "All",
  168: "1w",
  336: "2w",
};

export const STATE_COLORS: Record<string, string> = {
  // Moving / In Transit -> Green
  in_transit_laden: "#10b981",
  in_transit_ballast: "#10b981",
  // Stationary / Waiting (Port, Anchored, Idle/Drifting) -> Amber
  at_port: "#f59e0b",
  anchored_waiting: "#f59e0b",
  idle_drifting: "#f59e0b",
  // Special Offshore Ops (STS / Floating Storage) -> Purple
  sts_transfer: "#a855f7",
  floating_storage: "#a855f7",
  // Dark / AIS Off -> Red
  dark: "#ef4444",
};

export const STATE_LABELS: Record<string, string> = {
  dark: "Dark (AIS off)",
  sts_transfer: "STS Transfer",
  at_port: "At Port (Berthed)",
  anchored_waiting: "Anchored (Waiting)",
  floating_storage: "Floating Storage",
  in_transit_laden: "In Transit (Laden)",
  in_transit_ballast: "In Transit (Ballast)",
  idle_drifting: "Idle / Drifting",
};

export function getVesselColor(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;

  if (hoursSinceLastSeen > 6) return STATE_COLORS.dark;
  if (v.operational_state && v.operational_state !== "dark" && STATE_COLORS[v.operational_state]) {
    return STATE_COLORS[v.operational_state];
  }
  if ((v.last_speed || 0) <= 1) return STATE_COLORS.at_port;
  if ((v.draft || 0) > 12) return STATE_COLORS.in_transit_laden;
  return STATE_COLORS.in_transit_ballast;
}

export function getVesselStatus(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;

  if (hoursSinceLastSeen > 24) return "EXTENDED DARK";
  if (hoursSinceLastSeen > 6) return "DARK";
  if (v.operational_state && v.operational_state !== "dark" && STATE_LABELS[v.operational_state]) {
    return STATE_LABELS[v.operational_state];
  }
  if ((v.last_speed || 0) <= 1) return "At Port";
  if ((v.draft || 0) > 12) return "Loaded";
  return "Ballast";
}

export function getCongestionColor(status: string): string {
  if (status === "congested") return "#ef4444";
  if (status === "low") return "#f59e0b";
  return "#10b981";
}

export function geojsonToLeaflet(geojson: any): [number, number][] {
  if (!geojson || geojson.type !== "Polygon" || !geojson.coordinates?.length) return [];
  return geojson.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]]);
}

export function geojsonLineToLeaflet(geojson: any): [number, number][] {
  if (!geojson || geojson.type !== "LineString" || !geojson.coordinates?.length) return [];
  return geojson.coordinates.map((coord: [number, number]) => [coord[1], coord[0]]);
}

export function trailColor(idx: number, total: number): string {
  const t = total <= 1 ? 1 : idx / (total - 1);
  const r = Math.round(0 + t * 6);       // 0 -> 6
  const g = Math.round(100 + t * 82);    // 100 -> 182
  const b = Math.round(255);             // 255
  return `rgb(${r},${g},${b})`;
}

export const TRAIL_BREAK_DISTANCE_KM = 1500;

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
}

/** Split a chronological trail into contiguous segments whenever consecutive points exceed maxGapKm. */
export function splitTrailSegments<T extends { latitude: number; longitude: number }>(
  points: T[],
  maxGapKm: number = TRAIL_BREAK_DISTANCE_KM
): T[][] {
  if (points.length === 0) return [];
  const segments: T[][] = [[points[0]]];
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    if (haversineKm(prev.latitude, prev.longitude, curr.latitude, curr.longitude) > maxGapKm) {
      segments.push([curr]);
    } else {
      segments[segments.length - 1].push(curr);
    }
  }
  return segments;
}
