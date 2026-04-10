import { useEffect, useCallback, useRef } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";
import type { Vessel } from "@/lib/types";

export function ViewportFetcher({
  apiBase,
  onVesselsLoaded,
}: {
  apiBase: string;
  onVesselsLoaded: (vessels: Vessel[]) => void;
}) {
  const map = useMap();
  const lastBoundsRef = useRef<L.LatLngBounds | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchViewport = useCallback(async () => {
    const bounds = map.getBounds();

    if (lastBoundsRef.current) {
      const prev = lastBoundsRef.current;
      const latSpan = Math.abs(bounds.getNorth() - bounds.getSouth());
      const lonSpan = Math.abs(bounds.getEast() - bounds.getWest());
      const latDiff = Math.abs(bounds.getCenter().lat - prev.getCenter().lat);
      const lonDiff = Math.abs(bounds.getCenter().lng - prev.getCenter().lng);
      if (latDiff < latSpan * 0.15 && lonDiff < lonSpan * 0.15) return;
    }

    lastBoundsRef.current = bounds;

    const params = new URLSearchParams({
      lat_min: bounds.getSouth().toFixed(5),
      lon_min: bounds.getWest().toFixed(5),
      lat_max: bounds.getNorth().toFixed(5),
      lon_max: bounds.getEast().toFixed(5),
      limit: "1000",
    });

    try {
      const res = await fetch(`${apiBase}/api/vessels?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data?.vessels?.length) onVesselsLoaded(data.vessels);
    } catch (_) {}
  }, [map, apiBase, onVesselsLoaded]);

  useEffect(() => {
    const handler = () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(fetchViewport, 600);
    };
    map.on("moveend", handler);
    fetchViewport();
    return () => {
      map.off("moveend", handler);
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [map, fetchViewport]);

  return null;
}
