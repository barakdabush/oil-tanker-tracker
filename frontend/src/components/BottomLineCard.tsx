"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getApiUrl } from "@/lib/config";
import type {
  BottomLineForecastResponse,
  FeatureImportanceResponse,
  ForecastDriver,
  ForecastPillar,
} from "@/lib/types";

interface BottomLineCardProps {
  compact?: boolean;
}

function directionColors(dir: string): { text: string; bg: string; border: string; icon: string } {
  if (dir === "bullish") {
    return {
      text: "#34d399",
      bg: "rgba(16, 185, 129, 0.14)",
      border: "rgba(16, 185, 129, 0.35)",
      icon: "▲",
    };
  }
  if (dir === "bearish") {
    return {
      text: "#f87171",
      bg: "rgba(239, 68, 68, 0.14)",
      border: "rgba(239, 68, 68, 0.35)",
      icon: "▼",
    };
  }
  return {
    text: "#94a3b8",
    bg: "rgba(148, 163, 184, 0.14)",
    border: "rgba(148, 163, 184, 0.3)",
    icon: "●",
  };
}

function driverIcon(sourceType: string): string {
  switch (sourceType) {
    case "news":
      return "📰";
    case "chokepoint":
      return "⚓";
    case "dark_fleet":
      return "⚫";
    default:
      return "📈";
  }
}

export default function BottomLineCard({ compact = false }: BottomLineCardProps) {
  const [symbol, setSymbol] = useState<"BZ=F" | "CL=F">("BZ=F");
  const [horizon, setHorizon] = useState<string>("5d");
  const [forecast, setForecast] = useState<BottomLineForecastResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showImportance, setShowImportance] = useState(false);
  const [showAllFeatures, setShowAllFeatures] = useState(false);
  const [importance, setImportance] = useState<FeatureImportanceResponse | null>(null);
  const [impLoading, setImpLoading] = useState(false);

  const loadForecast = useCallback(async (sym: "BZ=F" | "CL=F") => {
    const API = getApiUrl();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API}/api/forecast/bottom-line?symbol=${encodeURIComponent(sym)}`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: BottomLineForecastResponse = await res.json();
      setForecast(json);
      if (json.primary_horizon && json.horizons[json.primary_horizon]) {
        setHorizon((prev) => (json.horizons[prev] ? prev : json.primary_horizon));
      }
    } catch {
      setError("Oil price predictor unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadForecast(symbol);
  }, [symbol, loadForecast]);

  useEffect(() => {
    if (compact || !showImportance) return;
    let cancelled = false;
    const API = getApiUrl();
    setImpLoading(true);
    fetch(
      `${API}/api/forecast/feature-importance?symbol=${encodeURIComponent(symbol)}&horizon=${encodeURIComponent(horizon)}&include_all_candidates=true`,
      { cache: "no-store" }
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data: FeatureImportanceResponse | null) => {
        if (!cancelled && data) setImportance(data);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setImpLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [compact, showImportance, symbol, horizon]);

  if (loading && !forecast) {
    return (
      <div
        className="panel animate-in"
        style={{
          marginBottom: 20,
          padding: 20,
          border: "1px solid var(--border)",
          background: "linear-gradient(135deg, rgba(15,23,42,0.95), rgba(30,41,59,0.75))",
        }}
      >
        <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
          Computing Multi-Day Oil Price Wave Forecast…
        </div>
      </div>
    );
  }

  if (error || !forecast) {
    return null;
  }

  const availableHorizons = Object.keys(forecast.horizons);
  const activeH =
    forecast.horizons[horizon] ??
    forecast.horizons[forecast.primary_horizon] ??
    forecast.horizons[availableHorizons[0]];
  const colors = directionColors(activeH.direction);

  return (
    <div
      className="panel animate-in"
      data-testid="bottom-line-card"
      style={{
        marginBottom: 22,
        padding: compact ? "18px 20px" : "22px 24px",
        border: `1px solid ${colors.border}`,
        background:
          "linear-gradient(135deg, rgba(15, 23, 42, 0.96) 0%, rgba(17, 24, 39, 0.92) 60%, rgba(30, 41, 59, 0.85) 100%)",
        boxShadow: "0 10px 30px rgba(0, 0, 0, 0.28)",
      }}
    >
      {/* Top Header Row */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          marginBottom: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: 0.8,
              textTransform: "uppercase",
              padding: "3px 9px",
              borderRadius: 999,
              background: "rgba(56, 189, 248, 0.14)",
              color: "#38bdf8",
              border: "1px solid rgba(56, 189, 248, 0.3)",
            }}
          >
            🌊 Oil Price Wave Forecast
          </span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            5y PortWatch Ships + News Analogs ({forecast.analog_memory_size}) + Crack Spreads + Wave GBT
          </span>
        </div>

        {/* Symbol & Horizon Toggles */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div
            style={{
              display: "flex",
              background: "rgba(15, 23, 42, 0.8)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {(["BZ=F", "CL=F"] as const).map((sym) => (
              <button
                key={sym}
                type="button"
                onClick={() => setSymbol(sym)}
                style={{
                  padding: "5px 10px",
                  fontSize: 12,
                  fontWeight: 600,
                  border: "none",
                  cursor: "pointer",
                  background: symbol === sym ? "var(--accent-blue)" : "transparent",
                  color: symbol === sym ? "#fff" : "var(--text-secondary)",
                }}
              >
                {sym === "BZ=F" ? "Brent" : "WTI"}
              </button>
            ))}
          </div>

          <div
            style={{
              display: "flex",
              background: "rgba(15, 23, 42, 0.8)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {availableHorizons.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h)}
                style={{
                  padding: "5px 10px",
                  fontSize: 12,
                  fontWeight: 600,
                  border: "none",
                  cursor: "pointer",
                  background: activeH.horizon === h ? "rgba(56, 189, 248, 0.25)" : "transparent",
                  color: activeH.horizon === h ? "#e0f2fe" : "var(--text-secondary)",
                }}
              >
                {h}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Hero Verdict + Price Target Strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: compact ? "1.3fr 1fr" : "1.2fr 1fr 1fr",
          gap: 16,
          alignItems: "center",
          padding: "14px 16px",
          borderRadius: 10,
          background: "rgba(2, 6, 23, 0.55)",
          border: "1px solid rgba(148, 163, 184, 0.14)",
          marginBottom: 16,
        }}
      >
        {/* Left: Verdict, Wave Probabilities & Headline */}
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
            <span
              style={{
                padding: "4px 12px",
                borderRadius: 999,
                background: colors.bg,
                color: colors.text,
                border: `1px solid ${colors.border}`,
                fontSize: 13,
                fontWeight: 800,
              }}
            >
              {colors.icon} {(activeH.wave_state || activeH.verdict).toUpperCase()}
            </span>
            <span style={{ fontSize: 22, fontWeight: 800, color: colors.text }}>
              {activeH.predicted_return_pct >= 0 ? "+" : ""}
              {activeH.predicted_return_pct.toFixed(2)}%
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>over next {activeH.horizon}</span>
          </div>
          {activeH.up_wave_prob_pct != null && activeH.down_wave_prob_pct != null && (
            <div style={{ marginBottom: 8 }}>
              <div
                style={{
                  display: "flex",
                  height: 6,
                  borderRadius: 999,
                  overflow: "hidden",
                  background: "rgba(148, 163, 184, 0.18)",
                  marginBottom: 4,
                }}
              >
                <div style={{ width: `${activeH.up_wave_prob_pct}%`, background: "#34d399" }} />
                <div style={{ width: `${activeH.range_prob_pct ?? 0}%`, background: "#64748b" }} />
                <div style={{ width: `${activeH.down_wave_prob_pct}%`, background: "#f87171" }} />
              </div>
              <div style={{ display: "flex", gap: 10, fontSize: 11, color: "var(--text-muted)", flexWrap: "wrap" }}>
                <span style={{ color: "#34d399" }}>▲ Up Wave {activeH.up_wave_prob_pct.toFixed(0)}%</span>
                <span>● Range {(activeH.range_prob_pct ?? 0).toFixed(0)}%</span>
                <span style={{ color: "#f87171" }}>▼ Down Wave {activeH.down_wave_prob_pct.toFixed(0)}%</span>
              </div>
            </div>
          )}
          <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.45 }}>
            {forecast.headline_summary}
          </div>
        </div>

        {/* Middle: Spot -> Predicted Target */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            {forecast.symbol_label} Target ({horizon})
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 15, color: "var(--text-secondary)", fontWeight: 600 }}>
              {forecast.current_price_usd != null ? `$${forecast.current_price_usd.toFixed(2)}` : "—"}
            </span>
            <span style={{ color: "var(--text-muted)" }}>→</span>
            <span style={{ fontSize: 22, fontWeight: 800, color: "#f8fafc" }}>
              {activeH.predicted_price_usd != null ? `$${activeH.predicted_price_usd.toFixed(2)}` : "—"}
            </span>
          </div>
          {activeH.price_low_usd != null && activeH.price_high_usd != null && (
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              1σ range: ${activeH.price_low_usd.toFixed(2)} – ${activeH.price_high_usd.toFixed(2)} ·
              Confidence: <strong style={{ color: "#e2e8f0" }}>{activeH.confidence_pct.toFixed(0)}%</strong>
            </div>
          )}
        </div>

        {/* Right: Walk-Forward Backtest & Model Blend (full view) */}
        {!compact && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
              Walk-Forward Verification
            </div>
            {activeH.backtest_samples > 0 && activeH.backtest_hit_rate_pct != null ? (
              <>
                <div style={{ fontSize: 18, fontWeight: 700, color: "#38bdf8" }}>
                  {(activeH.indep_hit_rate_pct ?? activeH.backtest_hit_rate_pct).toFixed(1)}%{" "}
                  <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-muted)" }}>
                    {activeH.indep_hit_rate_pct != null ? "independent wave hit rate" : "directional hit rate"}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {activeH.indep_samples ? (
                    <>
                      <strong style={{ color: "#e2e8f0" }}>n={activeH.indep_samples}</strong> non-overlapping ·{" "}
                      <span
                        style={{
                          color:
                            activeH.indep_p_value != null && activeH.indep_p_value <= 0.05
                              ? "#34d399"
                              : activeH.indep_p_value != null && activeH.indep_p_value <= 0.15
                              ? "#38bdf8"
                              : "#fbbf24",
                          fontWeight: 700,
                        }}
                      >
                        p={activeH.indep_p_value != null ? activeH.indep_p_value.toFixed(3) : "—"}
                      </span>
                      {" · "}
                    </>
                  ) : null}
                  Rolling {activeH.backtest_hit_rate_pct.toFixed(1)}% ({activeH.backtest_samples}d) · MAE{" "}
                  {activeH.backtest_mae_pct?.toFixed(2)}% · ML blend {Math.round(activeH.ml_weight * 100)}%
                </div>
                {activeH.model_name && (
                  <div style={{ fontSize: 10, color: "#94a3b8" }}>Model: {activeH.model_name}</div>
                )}
              </>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Building out-of-sample history (currently using transparent 4-pillar + analog blend).
              </div>
            )}
          </div>
        )}
      </div>

      {/* 4-Pillar Score Bars */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          gap: 10,
          marginBottom: compact ? 12 : 16,
        }}
      >
        {forecast.pillars.map((p: ForecastPillar) => {
          const pc = directionColors(p.direction);
          const barWidth = Math.min(100, Math.abs(p.score));
          return (
            <div
              key={p.key}
              style={{
                padding: "10px 12px",
                borderRadius: 8,
                background: "rgba(15, 23, 42, 0.7)",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                  {p.label}
                </span>
                <span style={{ fontSize: 12, fontWeight: 700, color: pc.text }}>
                  {p.score >= 0 ? "+" : ""}
                  {p.score.toFixed(0)}
                </span>
              </div>
              <div
                style={{
                  height: 5,
                  borderRadius: 999,
                  background: "rgba(148, 163, 184, 0.16)",
                  overflow: "hidden",
                  marginBottom: 6,
                }}
              >
                <div
                  style={{
                    width: `${barWidth}%`,
                    height: "100%",
                    background: pc.text,
                    borderRadius: 999,
                  }}
                />
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.35 }}>
                {compact ? p.metrics[0] || p.summary : p.summary}
              </div>
            </div>
          );
        })}
      </div>

      {/* Top Drivers ("Why?") */}
      {forecast.top_drivers.length > 0 && (
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>
              Key Drivers Behind This Call
            </span>
            {compact && (
              <Link href="/market" style={{ fontSize: 12, color: "#38bdf8", textDecoration: "none" }}>
                Full Forecast &amp; Backtest →
              </Link>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {forecast.top_drivers.slice(0, compact ? 3 : 5).map((d: ForecastDriver, idx: number) => {
              const dc = directionColors(d.impact_direction);
              return (
                <div
                  key={`${d.source_type}-${idx}`}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 11px",
                    borderRadius: 8,
                    background: "rgba(15, 23, 42, 0.55)",
                    border: "1px solid rgba(148, 163, 184, 0.12)",
                    fontSize: 12,
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      {driverIcon(d.source_type)} {d.title}
                    </div>
                    {!compact && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                        {d.detail}
                      </div>
                    )}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                    {d.expected_move_pct != null && (
                      <span
                        style={{
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: dc.bg,
                          color: dc.text,
                          fontSize: 11,
                          fontWeight: 700,
                        }}
                      >
                        {d.expected_move_pct >= 0 ? "+" : ""}
                        {d.expected_move_pct.toFixed(2)}%
                        {d.horizon ? ` (${d.horizon})` : ""}
                      </span>
                    )}
                    {d.expected_move_pct == null && (
                      <span
                        style={{
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: dc.bg,
                          color: dc.text,
                          fontSize: 11,
                          fontWeight: 700,
                          textTransform: "capitalize",
                        }}
                      >
                        {dc.icon} {d.impact_direction}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Collapsible Measured Out-of-Sample Feature Importance (Full /market view) */}
      {!compact && (
        <div
          style={{
            marginTop: 16,
            paddingTop: 14,
            borderTop: "1px solid rgba(148, 163, 184, 0.14)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => setShowImportance((v) => !v)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                background: "rgba(56, 189, 248, 0.1)",
                border: "1px solid rgba(56, 189, 248, 0.28)",
                color: "#7dd3fc",
                borderRadius: 8,
                padding: "6px 12px",
                fontSize: 12,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              <span>🔬 Top Model Features (Measured OOS Importance)</span>
              <span style={{ fontSize: 10 }}>{showImportance ? "▲ Hide" : "▼ Inspect"}</span>
            </button>
            {showImportance && importance && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 11, color: "var(--text-muted)", flexWrap: "wrap" }}>
                <span>
                  OOS Test: <strong style={{ color: "#e2e8f0" }}>{importance.test_days}d</strong> (Train {importance.train_days}d)
                </span>
                {importance.baseline_oos_accuracy_pct != null && (
                  <span>
                    Baseline Acc: <strong style={{ color: "#38bdf8" }}>{importance.baseline_oos_accuracy_pct.toFixed(1)}%</strong>
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => setShowAllFeatures((v) => !v)}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--border)",
                    color: "var(--text-secondary)",
                    borderRadius: 6,
                    padding: "2px 8px",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {showAllFeatures ? "Top 15" : `All ${importance.features.length}`}
                </button>
              </div>
            )}
          </div>

          {showImportance && (
            <div style={{ marginTop: 12 }}>
              {impLoading && !importance ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Measuring out-of-sample permutation importance across all candidate features…
                </div>
              ) : importance && importance.features.length > 0 ? (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid rgba(148,163,184,0.18)", color: "var(--text-muted)", textAlign: "left" }}>
                        <th style={{ padding: "6px 8px", fontWeight: 600 }}>#</th>
                        <th style={{ padding: "6px 8px", fontWeight: 600 }}>Feature</th>
                        <th style={{ padding: "6px 8px", fontWeight: 600 }}>Group</th>
                        <th style={{ padding: "6px 8px", fontWeight: 600 }}>Model</th>
                        <th style={{ padding: "6px 8px", fontWeight: 600, textAlign: "right" }}>OOS Accuracy Lift</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(showAllFeatures ? importance.features : importance.features.slice(0, 15)).map((f, i) => {
                        const liftColor =
                          f.importance_pct >= 0.25
                            ? "#34d399"
                            : f.importance_pct <= -0.25
                            ? "#f87171"
                            : "#94a3b8";
                        return (
                          <tr
                            key={f.feature}
                            style={{ borderBottom: "1px solid rgba(148,163,184,0.08)" }}
                          >
                            <td style={{ padding: "6px 8px", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
                              {i + 1}
                            </td>
                            <td style={{ padding: "6px 8px", color: "var(--text-primary)", fontWeight: 600 }}>
                              {f.label}
                            </td>
                            <td style={{ padding: "6px 8px" }}>
                              <span
                                style={{
                                  fontSize: 10,
                                  padding: "2px 7px",
                                  borderRadius: 999,
                                  background: "rgba(148,163,184,0.14)",
                                  color: "#cbd5e1",
                                }}
                              >
                                {f.group}
                              </span>
                            </td>
                            <td style={{ padding: "6px 8px" }}>
                              <span
                                style={{
                                  fontSize: 10,
                                  padding: "2px 7px",
                                  borderRadius: 999,
                                  background: f.in_active_model ? "rgba(56,189,248,0.14)" : "rgba(148,163,184,0.1)",
                                  color: f.in_active_model ? "#7dd3fc" : "#94a3b8",
                                }}
                              >
                                {f.in_active_model ? "Active" : "Candidate"}
                              </span>
                            </td>
                            <td
                              style={{
                                padding: "6px 8px",
                                textAlign: "right",
                                fontWeight: 700,
                                color: liftColor,
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {f.importance_pct >= 0 ? "+" : ""}
                              {f.importance_pct.toFixed(2)}%{" "}
                              <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
                                (±{f.std_pct.toFixed(2)})
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Not enough historical trading days to compute out-of-sample feature importance.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
