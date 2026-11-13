-- Seed Test Database
INSERT INTO vessels (mmsi, name, ship_type, last_lat, last_lon, created_at, updated_at) 
VALUES (999999999, 'SEEDED_TEST_VESSEL', 80, 24.5, 54.5, NOW(), NOW())
ON CONFLICT (mmsi) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO vessel_positions (time, mmsi, latitude, longitude)
VALUES 
    (NOW() - INTERVAL '1 hour', 999999999, 24.0, 54.0),
    (NOW() - INTERVAL '30 minutes', 999999999, 24.25, 54.25),
    (NOW(), 999999999, 24.5, 54.5)
ON CONFLICT (time, mmsi) DO NOTHING;
