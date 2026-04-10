"use client";

import dynamic from "next/dynamic";

// Leaflet must be rendered client-side only (no SSR)
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

export default function MapPage() {
  return (
    <div style={{ width: "100%", height: "100vh" }}>
      <MapView />
    </div>
  );
}
