import React, { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { Marker, Polyline, useMap } from "react-leaflet";
import { formatDistanceKm, formatDistanceNm, haversineKm } from "./utils";

export interface MeasurePoint {
  id: string;
  lat: number;
  lng: number;
}

interface MeasureSegment {
  index: number;
  from: MeasurePoint;
  to: MeasurePoint;
  distanceKm: number;
  midpoint: [number, number];
}

const DRAG_THRESHOLD_PX = 8;
const GESTURE_COOLDOWN_MS = 250;

function computeSegments(points: MeasurePoint[]): MeasureSegment[] {
  const segments: MeasureSegment[] = [];
  for (let i = 1; i < points.length; i++) {
    const from = points[i - 1];
    const to = points[i];
    segments.push({
      index: i,
      from,
      to,
      distanceKm: haversineKm(from.lat, from.lng, to.lat, to.lng),
      midpoint: [(from.lat + to.lat) / 2, (from.lng + to.lng) / 2],
    });
  }
  return segments;
}

function createPointIcon(index: number, total: number): L.DivIcon {
  const isStart = index === 0;
  const isEnd = index === total - 1 && total > 1;
  const bg = isStart ? "#10b981" : isEnd ? "#f59e0b" : "#06b6d4";

  return L.divIcon({
    className: "measure-point-marker",
    html: `<div style="
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: ${bg};
      border: 2px solid #ffffff;
      color: #0f172a;
      font-size: 11px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.55);
      pointer-events: none;
    ">${index + 1}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function createSegmentLabelIcon(distanceKm: number): L.DivIcon {
  const nm = formatDistanceNm(distanceKm);
  const km = formatDistanceKm(distanceKm);

  return L.divIcon({
    className: "measure-segment-label",
    html: `<div style="
      display: inline-flex;
      flex-direction: column;
      align-items: center;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid rgba(34, 211, 238, 0.65);
      border-radius: 6px;
      padding: 2px 7px;
      color: #f8fafc;
      font-size: 11px;
      font-weight: 700;
      line-height: 1.2;
      white-space: nowrap;
      box-shadow: 0 3px 10px rgba(0, 0, 0, 0.5);
      pointer-events: none;
      transform: translate(-50%, -50%);
    "><span>${nm}</span><span style="font-size: 9px; font-weight: 500; color: #94a3b8;">${km}</span></div>`,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
}

export function MeasureLayer({
  enabled,
  points,
  onAddPoint,
}: {
  enabled: boolean;
  points: MeasurePoint[];
  onAddPoint: (lat: number, lng: number) => void;
}) {
  const map = useMap();
  const onAddPointRef = useRef(onAddPoint);
  const enabledRef = useRef(enabled);

  useEffect(() => {
    onAddPointRef.current = onAddPoint;
  }, [onAddPoint]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  // Pointer & gesture tracking to strictly distinguish taps/clicks from drags and pinch-zoom
  const activePointersRef = useRef<Set<number>>(new Set());
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const movedBeyondThresholdRef = useRef(false);
  const multiTouchRef = useRef(false);
  const isMapGesturingRef = useRef(false);
  const lastGestureEndRef = useRef(0);

  useEffect(() => {
    if (!map.getPane("measurePane")) {
      const pane = map.createPane("measurePane");
      pane.style.zIndex = "670";
    }
  }, [map]);

  useEffect(() => {
    const container = map.getContainer();
    if (enabled) {
      container.classList.add("measure-mode-active");
    } else {
      container.classList.remove("measure-mode-active");
    }
    return () => {
      container.classList.remove("measure-mode-active");
    };
  }, [map, enabled]);

  useEffect(() => {
    const container = map.getContainer();

    const handlePointerDown = (e: PointerEvent) => {
      activePointersRef.current.add(e.pointerId);
      if (activePointersRef.current.size > 1) {
        multiTouchRef.current = true;
        movedBeyondThresholdRef.current = true;
        return;
      }
      pointerDownRef.current = { x: e.clientX, y: e.clientY };
      movedBeyondThresholdRef.current = false;
      multiTouchRef.current = false;
    };

    const handlePointerMove = (e: PointerEvent) => {
      if (!pointerDownRef.current || movedBeyondThresholdRef.current) return;
      const dist = Math.hypot(
        e.clientX - pointerDownRef.current.x,
        e.clientY - pointerDownRef.current.y
      );
      if (dist > DRAG_THRESHOLD_PX) {
        movedBeyondThresholdRef.current = true;
      }
    };

    const handlePointerUpOrCancel = (e: PointerEvent) => {
      activePointersRef.current.delete(e.pointerId);
      if (activePointersRef.current.size === 0 && multiTouchRef.current) {
        lastGestureEndRef.current = Date.now();
        multiTouchRef.current = false;
      }
    };

    const handleGestureStart = () => {
      isMapGesturingRef.current = true;
      movedBeyondThresholdRef.current = true;
    };

    const handleGestureEnd = () => {
      isMapGesturingRef.current = false;
      lastGestureEndRef.current = Date.now();
    };

    const handleMapClick = (e: L.LeafletMouseEvent) => {
      if (!enabledRef.current) return;
      if (isMapGesturingRef.current) return;
      if (Date.now() - lastGestureEndRef.current < GESTURE_COOLDOWN_MS) return;
      if (multiTouchRef.current || movedBeyondThresholdRef.current) return;

      const orig = e.originalEvent as MouseEvent | PointerEvent | undefined;
      if (orig) {
        const target = orig.target as HTMLElement | null;
        if (
          target?.closest(
            ".measure-hud, .map-left-controls, .map-search-bar, .map-legend, .leaflet-control, button, input, a"
          )
        ) {
          return;
        }
        if (
          pointerDownRef.current &&
          typeof orig.clientX === "number" &&
          typeof orig.clientY === "number"
        ) {
          const dist = Math.hypot(
            orig.clientX - pointerDownRef.current.x,
            orig.clientY - pointerDownRef.current.y
          );
          if (dist > DRAG_THRESHOLD_PX) return;
        }
      }

      pointerDownRef.current = null;
      onAddPointRef.current(e.latlng.lat, e.latlng.lng);
    };

    container.addEventListener("pointerdown", handlePointerDown, { passive: true });
    container.addEventListener("pointermove", handlePointerMove, { passive: true });
    container.addEventListener("pointerup", handlePointerUpOrCancel, { passive: true });
    container.addEventListener("pointercancel", handlePointerUpOrCancel, { passive: true });

    map.on("dragstart", handleGestureStart);
    map.on("zoomstart", handleGestureStart);
    map.on("dragend", handleGestureEnd);
    map.on("zoomend", handleGestureEnd);
    map.on("click", handleMapClick);

    return () => {
      container.removeEventListener("pointerdown", handlePointerDown);
      container.removeEventListener("pointermove", handlePointerMove);
      container.removeEventListener("pointerup", handlePointerUpOrCancel);
      container.removeEventListener("pointercancel", handlePointerUpOrCancel);

      map.off("dragstart", handleGestureStart);
      map.off("zoomstart", handleGestureStart);
      map.off("dragend", handleGestureEnd);
      map.off("zoomend", handleGestureEnd);
      map.off("click", handleMapClick);
    };
  }, [map]);

  const segments = useMemo(() => computeSegments(points), [points]);
  const latLngs = useMemo<[number, number][]>(
    () => points.map((p) => [p.lat, p.lng]),
    [points]
  );

  if (points.length === 0) return null;

  return (
    <>
      {latLngs.length >= 2 && (
        <Polyline
          positions={latLngs}
          interactive={false}
          pane="measurePane"
          pathOptions={{
            color: "#22d3ee",
            weight: 3,
            opacity: 0.95,
            dashArray: "8 6",
          }}
        />
      )}

      {segments.map((seg) => (
        <Marker
          key={`seg-${seg.from.id}-${seg.to.id}`}
          position={seg.midpoint}
          icon={createSegmentLabelIcon(seg.distanceKm)}
          interactive={false}
          pane="measurePane"
        />
      ))}

      {points.map((pt, idx) => (
        <Marker
          key={pt.id}
          position={[pt.lat, pt.lng]}
          icon={createPointIcon(idx, points.length)}
          interactive={false}
          pane="measurePane"
        />
      ))}
    </>
  );
}

export function MeasureHUD({
  enabled,
  points,
  onToggleEnabled,
  onUndo,
  onClear,
}: {
  enabled: boolean;
  points: MeasurePoint[];
  onToggleEnabled: () => void;
  onUndo: () => void;
  onClear: () => void;
}) {
  const [showLegs, setShowLegs] = useState(false);

  const segments = useMemo(() => computeSegments(points), [points]);
  const totalKm = useMemo(
    () => segments.reduce((sum, s) => sum + s.distanceKm, 0),
    [segments]
  );

  if (!enabled && points.length === 0) return null;

  const stopEvent = (e: React.SyntheticEvent) => {
    e.stopPropagation();
  };

  return (
    <div
      className="measure-hud"
      data-testid="measure-hud"
      onClick={stopEvent}
      onPointerDown={stopEvent}
    >
      <div className="measure-hud-main">
        <div className="measure-hud-info">
          <div className="measure-hud-status">
            <span
              className="measure-hud-dot"
              style={{
                background: enabled ? "#22d3ee" : "#94a3b8",
                boxShadow: enabled ? "0 0 8px #22d3ee" : "none",
              }}
            />
            <span style={{ fontWeight: 600, fontSize: 12, color: "#f1f5f9" }}>
              {enabled ? "Measure Active" : "Measured Trail"}
            </span>
            {points.length > 0 && (
              <span style={{ fontSize: 11, color: "#94a3b8" }}>
                · {points.length} {points.length === 1 ? "pt" : "pts"}
              </span>
            )}
          </div>

          {points.length === 0 ? (
            <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
              Tap or click map to place points · Drag to pan
            </div>
          ) : points.length === 1 ? (
            <div style={{ fontSize: 12, color: "#cbd5e1", marginTop: 2 }}>
              Tap another point to measure distance
            </div>
          ) : (
            <div className="measure-hud-total" data-testid="measure-total">
              <span className="measure-total-nm">{formatDistanceNm(totalKm)}</span>
              <span className="measure-total-km">({formatDistanceKm(totalKm)})</span>
            </div>
          )}
        </div>

        <div className="measure-hud-actions">
          {segments.length > 0 && (
            <button
              type="button"
              onClick={() => setShowLegs((v) => !v)}
              className="measure-btn measure-btn-secondary"
              title="Show distance per leg"
              data-testid="measure-legs-btn"
            >
              {showLegs ? "Legs ▲" : `Legs (${segments.length}) ▾`}
            </button>
          )}
          <button
            type="button"
            onClick={onUndo}
            disabled={points.length === 0}
            className="measure-btn measure-btn-secondary"
            title="Remove last point"
            data-testid="measure-undo-btn"
          >
            ↩ Undo
          </button>
          <button
            type="button"
            onClick={onClear}
            disabled={points.length === 0}
            className="measure-btn measure-btn-danger"
            title="Remove current trail"
            data-testid="measure-clear-btn"
          >
            ✕ Clear
          </button>
          <button
            type="button"
            onClick={onToggleEnabled}
            className={`measure-btn ${enabled ? "measure-btn-primary" : "measure-btn-secondary"}`}
            title={enabled ? "Stop placing points" : "Resume placing points"}
            data-testid="measure-done-btn"
          >
            {enabled ? "✓ Done" : "Resume"}
          </button>
        </div>
      </div>

      {showLegs && segments.length > 0 && (
        <div className="measure-legs-list" data-testid="measure-legs-list">
          {segments.map((seg) => (
            <div key={`leg-${seg.index}`} className="measure-leg-row">
              <span style={{ color: "#94a3b8" }}>
                Leg {seg.index} ({seg.index} → {seg.index + 1})
              </span>
              <span style={{ fontWeight: 600, color: "#f8fafc", fontFamily: "monospace" }}>
                {formatDistanceNm(seg.distanceKm)}{" "}
                <span style={{ color: "#94a3b8", fontWeight: 400 }}>
                  ({formatDistanceKm(seg.distanceKm)})
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
