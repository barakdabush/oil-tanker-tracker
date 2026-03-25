// ─── Vessel Types ───────────────────────────────────────────────────────────

export interface Vessel {
  mmsi: number;
  name: string | null;
  imo: number | null;
  ship_type: number | null;
  flag: string | null;
  callsign: string | null;
  length: number | null;
  beam: number | null;
  draft: number | null;
  dwt: number | null;
  destination: string | null;
  eta: string | null;
  last_seen: string | null;
  last_lat: number | null;
  last_lon: number | null;
  last_speed: number | null;
  last_course: number | null;
}

export interface VesselListResponse {
  vessels: Vessel[];
  total: number;
}

export interface Position {
  time: string;
  mmsi: number;
  latitude: number;
  longitude: number;
  speed: number | null;
  course: number | null;
  heading: number | null;
  draft: number | null;
}

export interface TrailResponse {
  mmsi: number;
  positions: Position[];
}

// ─── Port Types ─────────────────────────────────────────────────────────────

export interface Port {
  id: number;
  name: string;
  country: string | null;
  region: string | null;
  port_type: string | null;
  latitude: number;
  longitude: number;
  oil_capacity_barrels: number | null;
}

export interface PortListResponse {
  ports: Port[];
  total: number;
}

// ─── Cargo Event Types ──────────────────────────────────────────────────────

export interface CargoEvent {
  id: string;
  mmsi: number;
  port_id: number | null;
  port_name: string | null;
  event_type: "loading" | "unloading" | "unknown";
  arrival_time: string | null;
  departure_time: string | null;
  draft_arrival: number | null;
  draft_departure: number | null;
  draft_change: number | null;
  estimated_volume_barrels: number | null;
  confidence: number | null;
  created_at: string | null;
}

// ─── AIS Gap (Dark Fleet) Types ─────────────────────────────────────────────

export interface AISGap {
  id: string;
  mmsi: number;
  gap_start: string;
  gap_end: string | null;
  last_known_lat: number | null;
  last_known_lon: number | null;
  reappear_lat: number | null;
  reappear_lon: number | null;
  distance_jumped_km: number | null;
  status: "monitoring" | "dark" | "extended_dark" | "resolved";
  spoofing_suspected: boolean;
  near_port_on_reappear: string | null;
}

// ─── STS Event Types ────────────────────────────────────────────────────────

export interface STSEvent {
  id: string;
  vessel_a_mmsi: number;
  vessel_b_mmsi: number;
  start_time: string;
  end_time: string | null;
  lat: number | null;
  lon: number | null;
  distance_from_port_km: number | null;
  vessel_a_draft_change: number | null;
  vessel_b_draft_change: number | null;
  estimated_volume_barrels: number | null;
  status: "detected" | "ongoing" | "confirmed" | "dismissed";
  confidence: number | null;
}

// ─── Chokepoint Types ───────────────────────────────────────────────────────

export interface Chokepoint {
  id: number;
  name: string;
  region: string | null;
  avg_daily_oil_flow_mbd: number | null;
  congestion_threshold: number | null;
  current_vessel_count: number;
  congestion_status: "normal" | "congested" | "low";
  boundary_geojson?: any;
}

export interface ChokepointTransit {
  id: string;
  chokepoint_id: number;
  mmsi: number;
  entry_time: string;
  exit_time: string | null;
  direction: string | null;
  transit_duration_min: number | null;
  draft_on_entry: number | null;
}

// ─── Analytics Types ────────────────────────────────────────────────────────

export interface FlowDataPoint {
  source_region: string;
  destination_region: string;
  volume_barrels: number;
  vessel_count: number;
}

export interface VolumeOverTime {
  date: string;
  volume_barrels: number;
  event_count: number;
}

export interface TopRoute {
  origin_port: string;
  destination_port: string;
  voyage_count: number;
  total_volume_barrels: number;
}

export interface FleetStatus {
  total_vessels: number;
  in_transit: number;
  at_port: number;
  currently_dark: number;
  loaded: number;
  ballast: number;
}

export interface DashboardStats {
  fleet: FleetStatus;
  active_alerts: number;
  ongoing_sts_events: number;
  congested_chokepoints: number;
  recent_cargo_events: CargoEvent[];
  daily_volume_barrels: number;
}

// ─── Ingestion Analytics Types ──────────────────────────────────────────────

export interface IngestionDataPoint {
  timestamp: string;
  vessel_positions: number;
  chokepoint_transits: number;
  sts_events: number;
  ais_gaps: number;
  cargo_events: number;
}

