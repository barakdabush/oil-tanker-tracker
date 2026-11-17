"use client";

import { useEffect, useState } from "react";
import { getApiUrl } from "@/lib/config";

interface SanctionedVesselRow {
  mmsi: number;
  imo: number | null;
  name: string | null;
  flag: string | null;
  last_speed: number | null;
  last_seen: string | null;
  program: string;
}

interface SanctionsResponse {
  total_ofac_vessels: number;
  matched_tracked_count: number;
  vessels: SanctionedVesselRow[];
}

export default function SanctionedVesselsCard() {
  const [data, setData] = useState<SanctionsResponse | null>(null);

  useEffect(() => {
    fetch(`${getApiUrl()}/api/analytics/sanctions?limit=20`)
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => setData(res))
      .catch(() => setData(null));
  }, []);

  return (
    <div className="panel animate-in" style={{ marginBottom: 24 }}>
      <div className="panel-header" style={{ flexWrap: "wrap", gap: 8 }}>
        <h3>🚨 US Treasury OFAC SDN · Sanctioned Tankers</h3>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {data
            ? `${data.matched_tracked_count} tracked match(es) · ${data.total_ofac_vessels.toLocaleString()} OFAC vessels indexed`
            : "Loading OFAC SDN cross-reference…"}
        </span>
      </div>
      <div className="panel-body">
        {!data ? (
          <div style={{ textAlign: "center", padding: 24, color: "var(--text-muted)", fontSize: 13 }}>
            Loading…
          </div>
        ) : data.vessels.length === 0 ? (
          <div style={{ textAlign: "center", padding: 24, color: "var(--text-muted)", fontSize: 13 }}>
            {data.total_ofac_vessels === 0
              ? "Awaiting initial OFAC SDN sync (runs automatically in background worker)."
              : "No currently tracked tankers match the OFAC SDN list."}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Vessel</th>
                  <th>IMO</th>
                  <th>MMSI</th>
                  <th>Flag</th>
                  <th>Program</th>
                  <th>Speed</th>
                </tr>
              </thead>
              <tbody>
                {data.vessels.map((v) => (
                  <tr key={v.mmsi}>
                    <td style={{ fontWeight: 600 }}>{v.name || `MMSI ${v.mmsi}`}</td>
                    <td style={{ fontFamily: "monospace" }}>{v.imo || "—"}</td>
                    <td style={{ fontFamily: "monospace" }}>{v.mmsi}</td>
                    <td>{v.flag || "—"}</td>
                    <td>
                      <span
                        style={{
                          background: "rgba(239, 68, 68, 0.16)",
                          color: "#f87171",
                          border: "1px solid rgba(239, 68, 68, 0.4)",
                          borderRadius: 9999,
                          padding: "2px 8px",
                          fontSize: 11,
                          fontWeight: 600,
                        }}
                      >
                        {v.program}
                      </span>
                    </td>
                    <td>{v.last_speed != null ? `${v.last_speed.toFixed(1)} kn` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
