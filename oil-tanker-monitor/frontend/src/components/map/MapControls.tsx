import React, { useEffect } from "react";
import { useMap } from "react-leaflet";
import type { Vessel } from "@/lib/types";

/** Keyboard navigation: arrows pan, +/- zoom. Auto-focuses the map container. */
export function KeyboardNavigation() {
  const map = useMap();

  useEffect(() => {
    const container = map.getContainer();
    container.tabIndex = 0;
    container.style.outline = "none";
    container.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't capture keys when typing in an input/search
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;

      const step = 150;
      switch (e.key) {
        case "ArrowUp":    map.panBy([0, -step]); e.preventDefault(); break;
        case "ArrowDown":  map.panBy([0, step]);  e.preventDefault(); break;
        case "ArrowLeft":  map.panBy([-step, 0]); e.preventDefault(); break;
        case "ArrowRight": map.panBy([step, 0]);  e.preventDefault(); break;
        case "+": case "=": map.zoomIn();  e.preventDefault(); break;
        case "-": case "_": map.zoomOut(); e.preventDefault(); break;
      }
    };

    container.addEventListener("keydown", handleKeyDown);
    return () => container.removeEventListener("keydown", handleKeyDown);
  }, [map]);

  return null;
}

/** Visual navigation widget: D-pad for panning + zoom buttons */
export function NavigationControls() {
  const map = useMap();
  const step = 200;

  const btnBase: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(15, 23, 42, 0.85)",
    border: "1px solid rgba(255,255,255,0.12)",
    color: "#e2e8f0",
    cursor: "pointer",
    backdropFilter: "blur(8px)",
    userSelect: "none",
    transition: "background 0.15s, transform 0.1s",
    fontSize: 16,
    padding: 0,
  };

  const arrowBtn: React.CSSProperties = { ...btnBase, width: 32, height: 32 };
  const zoomBtn: React.CSSProperties = { ...btnBase, width: 36, height: 36, fontSize: 20, fontWeight: 300 };

  const hover = (e: React.MouseEvent) => {
    (e.currentTarget as HTMLElement).style.background = "rgba(6,182,212,0.3)";
    (e.currentTarget as HTMLElement).style.transform = "scale(1.1)";
  };
  const unhover = (e: React.MouseEvent) => {
    (e.currentTarget as HTMLElement).style.background = "rgba(15,23,42,0.85)";
    (e.currentTarget as HTMLElement).style.transform = "scale(1)";
  };

  return (
    <div style={{
      position: "absolute", bottom: 32, right: 16, zIndex: 1000,
      display: "flex", flexDirection: "column", alignItems: "center", gap: 8,
    }}>
      {/* D-pad */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "32px 32px 32px",
        gridTemplateRows: "32px 32px 32px",
        gap: 2,
        borderRadius: 12,
        overflow: "hidden",
        boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
      }}>
        {/* Row 1 */}
        <div />
        <button title="Pan up" style={{ ...arrowBtn, borderRadius: "8px 8px 0 0" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.panBy([0, -step])}>▲</button>
        <div />

        {/* Row 2 */}
        <button title="Pan left" style={{ ...arrowBtn, borderRadius: "8px 0 0 8px" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.panBy([-step, 0])}>◀</button>
        <button title="Reset view" style={{ ...arrowBtn, fontSize: 10, fontWeight: 600, letterSpacing: 0.5 }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.flyTo([25, 45], 3, { duration: 1 })}>⌂</button>
        <button title="Pan right" style={{ ...arrowBtn, borderRadius: "0 8px 8px 0" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.panBy([step, 0])}>▶</button>

        {/* Row 3 */}
        <div />
        <button title="Pan down" style={{ ...arrowBtn, borderRadius: "0 0 8px 8px" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.panBy([0, step])}>▼</button>
        <div />
      </div>

      {/* Zoom buttons */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 2,
        borderRadius: 10, overflow: "hidden",
        boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
      }}>
        <button title="Zoom in (+)" style={{ ...zoomBtn, borderRadius: "10px 10px 0 0" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.zoomIn()}>+</button>
        <button title="Zoom out (−)" style={{ ...zoomBtn, borderRadius: "0 0 10px 10px" }}
          onMouseEnter={hover} onMouseLeave={unhover}
          onClick={() => map.zoomOut()}>−</button>
      </div>
    </div>
  );
}

export function MapUpdater({ selectedVessel }: { selectedVessel: Vessel | null }) {
  const map = useMap();
  useEffect(() => {
    if (selectedVessel && selectedVessel.last_lat != null && selectedVessel.last_lon != null) {
      map.flyTo([selectedVessel.last_lat, selectedVessel.last_lon], 10, { duration: 1.5 });
    }
  }, [selectedVessel, map]);
  return null;
}
