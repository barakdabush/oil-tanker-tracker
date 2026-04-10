# 🧪 Backend Tests

This directory contains integration and subsystem tests for the Oil Tanker Monitor backend, organized by service and route.

## 📂 Folder Structure

### `tests/api/`
Tests grouped by their API route prefix:
- **`vessels/`**: 
  - `test_history_all_time.py`: Full integration test hitting the live `/api/vessels/{mmsi}/trail?hours=0` endpoint.
  - `test_trail_range.py`: Subsystem test for the 2-week history range on vessel trails.
- **`chokepoints/`**: 
  - `test_congestion_range.py`: Subsystem test for the 2-week history range on chokepoint data.
- **`ports/`**: (Placeholder for future port-specific tests)
- **`alerts/`**: (Placeholder for future alert-specific tests)
- **`analytics/`**: (Placeholder for future analytics-specific tests)

### `tests/ais/`
- **`test_ais_stream_connection.py`**: Direct connection test to the external `aisstream.io` WebSocket.
- **`test_ais_stream_subscription.py`**: Logic test for subscribing to ship types and bounding boxes.
- **`test_ais_user_example.py`**: Reference implementation for AIS streaming handled in isolation.

---

## 🚀 Running Tests

### 1. Preparation (Test Environment)
Most integration and full-stack tests require the test environment to be up:
```bash
# From the root directory:
docker compose -f docker-compose.test.yml up -d
```

### 2. Run All Tests
From the `backend/` directory:
```bash
DATABASE_URL=postgresql+asyncpg://admin:secret@localhost:5433/oil_monitor_test ./venv/bin/pytest
```

### 3. Run Specific Service Tests
```bash
# Run only Vessel API tests
./venv/bin/pytest tests/api/vessels/

# Run only AIS stream tests (requires internet)
./venv/bin/pytest tests/ais/
```

## 🛠️ Data & Configuration
- **Seeding**: The test database is automatically seeded by the SQL files in the root `/data` folder (`01_seed_init.sql` and `02_seed_test.sql`).
- **Config**: Shared configuration is stored in `pytest.ini` in the backend root.
