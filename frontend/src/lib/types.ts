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
  last_source?: string | null;
  operational_state?: string | null;
  state_since?: string | null;
  current_port_id?: number | null;
  cargo_status?: string | null;
  is_sanctioned?: boolean;
  sanctions_program?: string | null;
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
  gate_line_geojson?: any;
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
  detection_method?: "polygon" | "gate_crossing" | "polygon_segment" | string | null;
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

export interface PositionSourceStat {
  source: string;
  count: number;
  percentage: number;
}

// ─── Global Oil Market / ML Feature Types ───────────────────────────────────

export interface MarketSnapshotResponse {
  snapshot_date: string;
  total_active_vessels: number;
  vessels_in_transit: number;
  vessels_at_port: number;
  avg_fleet_speed: number;
  vessels_idle_gt_48h: number;
  dark_vessels_count: number;
  new_ais_gaps_24h: number;
  resolved_gaps_24h: number;
  avg_gap_duration_hours: number;
  sts_events_24h: number;
  sts_confirmed_24h: number;
  chokepoint_transits_24h: number;
  strait_of_hormuz_transits: number;
  cape_of_good_hope_transits?: number | null;
  bab_el_mandeb_transits?: number | null;
  suez_canal_transits?: number | null;
  strait_of_malacca_transits?: number | null;
  panama_canal_transits?: number | null;
  turkish_straits_transits?: number | null;
  danish_straits_transits?: number | null;
  other_straits_transits?: number | null;
  other_straits_traffic_share_pct?: number | null;
  avg_chokepoint_transit_min?: number | null;
  cape_of_good_hope_avg_transit_min?: number | null;
  other_straits_avg_transit_min?: number | null;
  hormuz_bab_avg_transit_min?: number | null;
  portwatch_cape_tankers?: number | null;
  portwatch_other_straits_tankers?: number | null;
  portwatch_hormuz_tankers?: number | null;
  portwatch_hormuz_capacity_tanker?: number | null;
  portwatch_bab_el_mandeb_tankers?: number | null;
  portwatch_suez_tankers?: number | null;
  hormuz_crisis_pressure?: number | null;
  hormuz_escalation_prob?: number | null;
  hormuz_stranded_vessels?: number | null;
  hormuz_incidents_count?: number | null;
  gdelt_strait_risk_vol?: number | null;
  gdelt_strait_tone?: number | null;
  cargo_events_24h: number;
  estimated_volume_barrels_24h: number;
  anchored_waiting_count?: number | null;
  avg_port_wait_hours?: number | null;
  port_congestion_pct?: number | null;
  floating_storage_count?: number | null;
  in_transit_laden_count?: number | null;
  in_transit_ballast_count?: number | null;
  news_event_count_24h?: number | null;
  news_max_rank_24h?: number | null;
  news_net_impact_1d?: number | null;
  news_net_impact_7d?: number | null;
  news_supply_risk_score?: number | null;
  news_sanctions_pressure_7d?: number | null;
  news_opec_signal_7d?: number | null;
  news_hormuz_x_transit_drop?: number | null;
  news_sanctions_x_dark_fleet?: number | null;
  brent_close_usd: number | null;
  wti_close_usd: number | null;
}

export interface PortCongestionItem {
  port_id: number;
  name: string;
  country: string | null;
  region: string | null;
  port_type: string | null;
  berthed_count: number;
  anchored_waiting_count: number;
  avg_wait_hours: number | null;
  utilization_pct: number | null;
}

// ─── News & Event Intelligence Types ────────────────────────────────────────

export interface NewsArticleItem {
  id: number;
  source: string;
  source_tier: number;
  title: string;
  url: string;
  published_at: string;
  gdelt_tone: number | null;
}

export interface EventPriceReaction {
  symbol: string;
  price_t0: number | null;
  ret_pre_24h: number | null;
  ret_pre_4h: number | null;
  ret_1h: number | null;
  ret_4h: number | null;
  ret_24h: number | null;
  ret_3d: number | null;
  ret_7d: number | null;
  ret_14d: number | null;
  z_1h: number | null;
  z_4h: number | null;
  z_24h: number | null;
  z_3d: number | null;
  z_7d: number | null;
  z_14d: number | null;
  max_computed_horizon: string | null;
}

export interface NewsEventResponse {
  id: number;
  headline: string;
  summary: string | null;
  event_type: string;
  event_type_label: string;
  chokepoints: string[];
  entities: string[];
  primary_region: string;
  first_seen_at: string;
  market_aligned_at: string;
  article_count: number;
  distinct_sources: number;
  max_source_tier: number;
  novelty_score: number;
  effective_direction: number;
  effective_severity: number;
  prior_rank_score: number;
  learned_impact_score: number | null;
  llm_enriched: boolean;
  llm_confidence: number | null;
  llm_estimated_mbpd: number | null;
  llm_rationale: string | null;
  brent_impact: EventPriceReaction | null;
  wti_impact: EventPriceReaction | null;
  articles: NewsArticleItem[];
}

export interface HorizonStatCell {
  horizon: string;
  sample_count: number;
  mean_ret_pct: number | null;
  median_ret_pct: number | null;
  mean_z: number | null;
  hit_rate_pct: number | null;
  t_stat: number | null;
  shrunk_weight: number;
}

export interface ImpactMatrixRow {
  event_type: string;
  event_type_label: string;
  total_events: number;
  peak_horizon: string | null;
  peak_mean_ret_pct: number | null;
  peak_hit_rate_pct: number | null;
  peak_t_stat: number | null;
  persistence_label: string;
  half_life_hours: number | null;
  horizons: Record<string, HorizonStatCell>;
}

export interface ImpactMatrixResponse {
  symbol: string;
  as_of_date: string | null;
  horizon_order: string[];
  rows: ImpactMatrixRow[];
}

export interface NewsSummaryResponse {
  events_24h: number;
  high_impact_events_24h: number;
  total_events_30d: number;
  net_pressure_24h: number;
  pressure_label: string;
  top_event_24h: NewsEventResponse | null;
  strongest_learned_driver: ImpactMatrixRow | null;
  llm_enabled: boolean;
  llm_provider: string | null;
  scoring_mode: string;
}

export interface AIMarketBriefingResponse {
  id: number;
  window_days: number;
  model_used: string | null;
  market_bias: "bullish" | "bearish" | "neutral" | "mixed";
  confidence: number | null;
  executive_summary: string;
  key_points: string[];
  watch_items: string[];
  full_markdown: string | null;
  created_at: string;
}



