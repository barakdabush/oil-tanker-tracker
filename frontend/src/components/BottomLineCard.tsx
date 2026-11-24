"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getApiUrl } from "@/lib/config";
import type { BottomLineForecastResponse, ForecastDriver, ForecastPillar } from "@/lib/types";

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
  const [horizon, setHorizon] = useState<"24h" | "3d" | "7d">("3d");
  const [forecast, setForecast] = useState<BottomLineForecastResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    } catch {
      setError("Oil price predictor unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadForecast(symbol);
  }, [symbol, loadForecast]);

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
          Computing Bottom-Line Oil Price Forecast…
        </div>
      </div>
    );
  }

  if (error || !forecast) {
    return null;
  }

  const activeH = forecast.horizons[horizon] ?? forecast.horizons["3d"];
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
            🎯 Bottom Line Oil Forecast
          </span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            News Analogs ({forecast.analog_memory_size}) + Chokepoints + Dark Fleet + Walk-Forward ML
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
            {(["24h", "3d", "7d"] as const).map((h) => (
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
                  background: horizon === h ? "rgba(56, 189, 248, 0.25)" : "transparent",
                  color: horizon === h ? "#e0f2fe" : "var(--text-secondary)",
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
        {/* Left: Verdict & Headline */}
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
              {colors.icon} {activeH.verdict.toUpperCase()}
            </span>
            <span style={{ fontSize: 22, fontWeight: 800, color: colors.text }}>
              {activeH.predicted_return_pct >= 0 ? "+" : ""}
              {activeH.predicted_return_pct.toFixed(2)}%
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>in next {horizon}</span>
          </div>
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
                  {activeH.backtest_hit_rate_pct.toFixed(1)}%{" "}
                  <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-muted)" }}>
                    directional hit rate
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  MAE {activeH.backtest_mae_pct?.toFixed(2)}% over {activeH.backtest_samples} OOS days ·
                  ML blend {Math.round(activeH.ml_weight * 100)}%
                  {activeH.high_conviction_hit_rate_pct != null && activeH.high_conviction_samples
                    ? ` · High-conv ${activeH.high_conviction_hit_rate_pct.toFixed(1)}% (${activeH.high_conviction_samples})`
                    : ""}
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
    </div>
  );
}
