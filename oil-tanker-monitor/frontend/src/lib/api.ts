import type {
  VesselListResponse,
  Vessel,
  TrailResponse,
  CargoEvent,
  PortListResponse,
  Port,
  AISGap,
  STSEvent,
  Chokepoint,
  ChokepointTransit,
  DashboardStats,
  FlowDataPoint,
  VolumeOverTime,
  TopRoute,
  FleetStatus,
  IngestionDataPoint,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

// ─── Vessels ────────────────────────────────────────────────────────────────

export const getVessels = (params?: {
  limit?: number;
  offset?: number;
  search?: string;
  status?: string;
}) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.offset) q.set("offset", String(params.offset));
  if (params?.search) q.set("search", params.search);
  if (params?.status) q.set("status", params.status);
  return fetchJSON<VesselListResponse>(`/api/vessels?${q}`);
};

export const getVessel = (mmsi: number) =>
  fetchJSON<Vessel>(`/api/vessels/${mmsi}`);

export const getVesselTrail = (mmsi: number, hours = 24) =>
  fetchJSON<TrailResponse>(`/api/vessels/${mmsi}/trail?hours=${hours}`);

export const getVesselCargoEvents = (mmsi: number) =>
  fetchJSON<CargoEvent[]>(`/api/vessels/${mmsi}/cargo-events`);

// ─── Ports ──────────────────────────────────────────────────────────────────

export const getPorts = (params?: { limit?: number; region?: string; search?: string }) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.region) q.set("region", params.region);
  if (params?.search) q.set("search", params.search);
  return fetchJSON<PortListResponse>(`/api/ports?${q}`);
};

export const getPort = (id: number) =>
  fetchJSON<Port>(`/api/ports/${id}`);

export const getPortActivity = (id: number, days = 7) =>
  fetchJSON<CargoEvent[]>(`/api/ports/${id}/activity?days=${days}`);

// ─── Alerts ─────────────────────────────────────────────────────────────────

export const getDarkFleet = (status?: string) => {
  const q = status ? `?status=${status}` : "";
  return fetchJSON<AISGap[]>(`/api/alerts/dark-fleet${q}`);
};

export const getSTSEvents = (status?: string) => {
  const q = status ? `?status=${status}` : "";
  return fetchJSON<STSEvent[]>(`/api/alerts/sts-events${q}`);
};

export const getChokepoints = () =>
  fetchJSON<Chokepoint[]>(`/api/alerts/chokepoints`);

export const getChokepointTransits = (cpId: number, hours = 24) =>
  fetchJSON<ChokepointTransit[]>(`/api/alerts/chokepoints/${cpId}/transits?hours=${hours}`);

// ─── Analytics ──────────────────────────────────────────────────────────────

export const getDashboard = () =>
  fetchJSON<DashboardStats>(`/api/analytics/dashboard`);

export const getOilFlow = (days = 30) =>
  fetchJSON<FlowDataPoint[]>(`/api/analytics/flow?days=${days}`);

export const getVolumeOverTime = (days = 30) =>
  fetchJSON<VolumeOverTime[]>(`/api/analytics/volume?days=${days}`);

export const getTopRoutes = (limit = 20) =>
  fetchJSON<TopRoute[]>(`/api/analytics/top-routes?limit=${limit}`);

export const getFleetStatus = () =>
  fetchJSON<FleetStatus>(`/api/analytics/fleet-status`);

export const getIngestionData = (hours = 24) =>
  fetchJSON<IngestionDataPoint[]>(`/api/analytics/ingestion?hours=${hours}`);

