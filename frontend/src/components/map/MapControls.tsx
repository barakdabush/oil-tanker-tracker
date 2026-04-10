import React, { useEffect } from "react";
import { useMap } from "react-leaflet";
import type { Vessel } from "@/lib/types";

export function ZoomControls() {
  const map = useMap();
  const btnStyle: React.CSSProperties = {
    width: 36,
    height: 36,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(15, 23, 42, 0.85)",
    border: "1px solid rgba(255,255,255,0.12)",
    color: "#e2e8f0",
    fontSize: 20,
    fontWeight: 300,
    cursor: "pointer",
    backdropFilter: "blur(8px)",
    userSelect: "none",
    transition: "background 0.15s",
  };
  return (
    <div
      style={{
        position: "absolute",
        bottom: 32,
        right: 16,
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        borderRadius: 10,
        overflow: "hidden",
        boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
      }}
    >
      <button
        title="Zoom in"
        style={{ ...btnStyle, borderRadius: "10px 10px 0 0", borderBottom: "none" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(6,182,212,0.25)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(15,23,42,0.85)")}
        onClick={() => map.zoomIn()}
      >
        +
      </button>
      <button
        title="Zoom out"
        style={{ ...btnStyle, borderRadius: "0 0 10px 10px" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(6,182,212,0.25)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(15,23,42,0.85)")}
        onClick={() => map.zoomOut()}
      >
        −
      </button>
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
