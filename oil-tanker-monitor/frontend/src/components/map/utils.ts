/* Map utility functions and constants */
import type { Vessel } from "@/lib/types";

export const timeLabelMap: Record<number, string> = {
  0: "All",
  168: "1w",
  336: "2w",
};

export function getVesselColor(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;
  if (hoursSinceLastSeen > 6) return "#1f2937";
  if ((v.last_speed || 0) <= 1) return "#f59e0b";
  if ((v.draft || 0) > 12) return "#ef4444";
  return "#10b981";
}

export function getVesselStatus(v: Vessel): string {
  const hoursSinceLastSeen = v.last_seen
    ? (Date.now() - new Date(v.last_seen).getTime()) / 3600000
    : 999;
  if (hoursSinceLastSeen > 6) return "DARK";
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

export function trailColor(idx: number, total: number): string {
  const t = total <= 1 ? 1 : idx / (total - 1);
  const r = Math.round(0 + t * 6);       // 0 -> 6
  const g = Math.round(100 + t * 82);    // 100 -> 182
  const b = Math.round(255);             // 255
  return `rgb(${r},${g},${b})`;
}
