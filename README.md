# 🛢️ Oil Tanker Monitor

A **real-time maritime intelligence platform** that tracks oil tanker movements across the world's most critical chokepoints, detects anomalous behavior, and correlates vessel activity with live global oil prices.

> Built with Python (AsyncIO + FastAPI), Next.js, TimescaleDB, and Docker.

---

## 📸 Overview

Oil accounts for roughly 60% of global maritime trade. Disruptions at key chokepoints — the Strait of Hormuz, Suez Canal, or Strait of Malacca — can shift oil prices globally within hours. This platform provides a live intelligence view of exactly those chokepoints, tracking tankers in real time and automatically detecting suspicious patterns.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🗺️ **Live Map** | Real-time tanker positions streamed via WebSocket onto an interactive map |
| 🚢 **AIS Ingestion** | Connects to [aisstream.io](https://aisstream.io) and consumes live AIS position & static data filtered to oil tanker ship types (80–89) |
| 🌊 **Chokepoint Monitoring** | Tracks vessel density and activity across 8 strategic chokepoints (Hormuz, Suez, Malacca, Bosphorus, Bab el-Mandeb, Danish Straits, Cape of Good Hope, Panama Canal) |
| 🕵️ **Dark Fleet Detection** | Identifies vessels exhibiting AIS-off behavior or other evasion patterns |
| 🔄 **Ship-to-Ship (STS) Detection** | Detects potential ship-to-ship transfers (a common sanctions evasion technique) |
| 📦 **Cargo Status Detection** | Infers whether a tanker is laden or ballast based on draft and speed data |
| 💹 **Oil Price Tracking** | Fetches live Brent & WTI oil prices from the EIA API and correlates with vessel activity |
| 🧠 **Analytics Engine** | Builds global oil feature snapshots for trend analysis and historical correlation |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL DATA SOURCES                              │
│                                                                             │
│   aisstream.io (WebSocket)              EIA API (HTTP REST)                 │
│   Live AIS position & static data       Brent / WTI Oil Prices             │
└──────────────┬───────────────────────────────────┬──────────────────────────┘
               │                                   │
               ▼                                   ▼
┌──────────────────────────┐       ┌───────────────────────────────────────┐
│   WebSocket Container    │       │          Scheduler Container           │
│   (Port 8001)            │       │          (worker.py)                   │
│                          │       │                                         │
│  websocket_app.py        │       │  ┌─────────────────────────────────┐  │
│  ┌────────────────────┐  │       │  │  CargoDetector      (periodic)  │  │
│  │  AIS Ingestion     │  │       │  │  DarkFleetDetector  (periodic)  │  │
│  │  (asyncio tasks)   │  │       │  │  STSDetector        (periodic)  │  │
│  │                    │  │       │  │  ChokepointMonitor  (periodic)  │  │
│  │  Smart delta logic │  │       │  │  GlobalOilFeature   (periodic)  │  │
│  │  - Only save if    │  │       │  │  OilPriceFetcher    (periodic)  │  │
│  │    moved >10km or  │  │       │  └─────────────────────────────────┘  │
│  │    >30min elapsed  │  │       └──────────────────┬────────────────────┘
│  └────────┬───────────┘  │                          │
│           │              │                          │
│  ┌────────▼───────────┐  │                          │
│  │   ws_manager.py    │  │                          │
│  │   (ConnectionMgr)  │  │                          │
│  │   Broadcasts to    │  │                          │
│  │   all clients      │  │                          │
│  └────────────────────┘  │                          │
└──────────────────────────┘                          │
               │                                      │
               │  WebSocket                           │  SQL (asyncpg)
               │  (POSITION_UPDATE events)            │
               ▼                                      ▼
┌──────────────────────────┐       ┌───────────────────────────────────────┐
│   Frontend Container     │       │         TimescaleDB Container          │
│   (Port 3000)            │       │         (PostgreSQL 16)                │
│                          │       │                                         │
│  Next.js + TypeScript    │       │  Tables:                               │
│  React + Mapbox/Leaflet  │       │  - vessels          (ship registry)    │
│                          │       │  - vessel_positions (time-series)      │
│  STSLayer.tsx            │       │  - oil_market_snapshots               │
│  (live map rendering)    │       │  - analytics / anomalies              │
└──────────────────────────┘       └───────────────────────────────────────┘
               ▲
               │  HTTP REST (JSON)
               │
┌──────────────────────────┐
│   Backend Container      │
│   (Port 8000)            │
│                          │
│  FastAPI (app/main.py)   │
│  REST API endpoints      │
│  for vessels, analytics, │
│  prices, chokepoints     │
└──────────────────────────┘
```

---

## 🐳 Services (Docker Compose)

| Container | Port | Role |
|---|---|---|
| `db` | — | TimescaleDB (Postgres 16) — persistent vessel & analytics storage |
| `migrate` | — | One-shot Alembic migration runner — runs on startup, exits when done |
| `backend` | `8000` | FastAPI REST API — serves vessel data, analytics, oil prices to the frontend |
| `scheduler` | — | Background worker — runs all anomaly detection & analytics jobs periodically |
| `websocket` | `8001` | FastAPI WebSocket server — ingests AIS stream and broadcasts live position updates |
| `frontend` | `3000` | Next.js app — interactive map UI |
| `adminer` | `8080` | (Dev only) Database administration UI |

---

## 🔁 Data Flow

```
1. AIS INGESTION (websocket container)
   aisstream.io ──WebSocket──► ais_ingestion.py
                               │
                               ├─► Filter: Only tanker ship types (80-89)
                               ├─► Smart delta: only save if moved >10km or >30min
                               ├─► Batch accumulator (flush every 2s to DB)
                               └─► Broadcast POSITION_UPDATE to all connected browser clients

2. ANOMALY DETECTION (scheduler container)
   worker.py runs all detectors in parallel asyncio tasks:
   ├─► CargoDetector       → laden/ballast status from draft + speed
   ├─► DarkFleetDetector   → AIS silence / spoofing patterns
   ├─► STSDetector         → ship-to-ship proximity events
   ├─► ChokepointMonitor   → vessel count per strategic chokepoint
   ├─► GlobalOilFeature    → aggregate market feature snapshots
   └─► OilPriceFetcher     → Brent/WTI price from EIA API → DB

3. REST API (backend container)
   Frontend ──HTTP──► FastAPI
                      └─► Query TimescaleDB → return JSON to map UI

4. LIVE UI (frontend container)
   Browser ──WebSocket──► Position updates (delta only, per ship)
   Browser ──HTTP REST──► Initial snapshot + analytics data
```

---

## 🛠️ Tech Stack & Design Decisions

### Why **Python AsyncIO** for the WebSocket server?
Instead of a thread-per-connection model (which would require gigabytes of RAM at scale), the async event loop uses the OS `epoll` system call to monitor thousands of socket File Descriptors simultaneously on a **single thread**. This allows the WebSocket server to handle many concurrent browser connections without memory exhaustion.

### Why **TimescaleDB** instead of plain Postgres?
Vessel position data is a **time-series** workload — millions of rows ordered by timestamp with frequent range queries like "give me all positions in the last 6 hours." TimescaleDB's automatic hypertables and time-based chunk indexing make these queries orders of magnitude faster than standard relational tables.

### Why a **separate Scheduler container** instead of running jobs inside the main API?
The anomaly detection jobs (dark fleet, STS detection) are CPU-heavy and involve complex geospatial queries that can take seconds. Running them in the same asyncio event loop as the FastAPI server would **block the event loop** and cause WebSocket disconnects and API timeouts. Isolating them in a separate process (`worker.py`) gives each its own Python GIL and prevents interference.

### Why **delta updates** instead of broadcasting the full vessel list?
The system only broadcasts a `POSITION_UPDATE` event when a specific ship's position changes, containing just the MMSI, coordinates, and timestamp. Broadcasting the full state of all tracked tankers on every AIS message would be extremely wasteful in bandwidth and cause the frontend map to re-render unnecessarily.

### Why **batch writes** to the database?
AIS data arrives at very high frequency. Writing each position to the database immediately would saturate the connection pool and generate excessive I/O. Instead, positions are accumulated in an in-memory batch and flushed to the DB every 2 seconds as a single bulk upsert. This reduces DB round-trips by orders of magnitude.

---

## 🚀 Getting Started

### Prerequisites
- Docker & Docker Compose
- An [aisstream.io](https://aisstream.io) API key (free tier available)
- An [EIA](https://www.eia.gov/opendata/) API key (free)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/oil-tanker-tracker.git
cd oil-tanker-tracker

# 2. Create environment file
cp .env.example .env
# Edit .env and set:
#   AIS_API_KEY=your_aisstream_key
#   EIA_API_KEY=your_eia_key

# 3. Start all services
docker compose up --build

# 4. Open the app
# Map UI:      http://localhost:3000
# REST API:    http://localhost:8000/docs
# WebSocket:   ws://localhost:8001/api/ws/vessels
# DB Admin:    http://localhost:8080
```

### Production Deployment

```bash
# Set your server IP in .env
SERVER_IP=YOUR_VPS_IP

# Use the production compose file (uses pre-built images from GHCR)
docker compose -f docker-compose.prod.yml up -d
```

A GitHub Actions workflow (`.github/workflows/deploy.yml`) handles automatic building and pushing of Docker images to GitHub Container Registry on every push to `main`.

---

## 🌍 Monitored Chokepoints

| Chokepoint | Significance |
|---|---|
| Strait of Hormuz | ~20% of global oil trade passes through here |
| Strait of Malacca | Primary route between Middle East and Asia-Pacific |
| Suez Canal | Connects Red Sea to Mediterranean, shortcut for Europe-Asia trade |
| Bab el-Mandeb | Southern Red Sea gateway; disruptions force Cape of Good Hope rerouting |
| Turkish Straits (Bosphorus) | Only exit from the Black Sea for Russian/Caspian oil |
| Danish Straits | Baltic Sea exits for North Sea and Scandinavian oil |
| Cape of Good Hope | Alternative route when Suez is disrupted |
| Panama Canal | Pacific–Atlantic connection for US Gulf oil exports |

---

## 📁 Project Structure

```
t3/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI route handlers
│   │   ├── models/        # SQLAlchemy ORM models
│   │   ├── schemas/       # Pydantic schemas
│   │   ├── services/
│   │   │   ├── ais_ingestion.py        # Live AIS WebSocket consumer
│   │   │   ├── cargo_detector.py       # Laden/ballast inference
│   │   │   ├── dark_fleet_detector.py  # AIS evasion detection
│   │   │   ├── sts_detector.py         # Ship-to-ship transfer detection
│   │   │   ├── chokepoint_monitor.py   # Strategic chokepoint tracking
│   │   │   ├── global_oil_feature_builder.py  # Market analytics
│   │   │   └── oil_price_fetcher.py    # EIA price ingestion
│   │   ├── types/         # TypedDicts for AIS message structures
│   │   ├── utils/         # Geo helpers (haversine, etc.)
│   │   ├── database.py    # AsyncPG SQLAlchemy engine
│   │   ├── main.py        # FastAPI REST app
│   │   └── ws_manager.py  # WebSocket connection manager
│   ├── migrations/        # Alembic DB migrations
│   ├── worker.py          # Background scheduler entrypoint
│   └── websocket_app.py   # WebSocket server entrypoint
├── frontend/
│   └── src/
│       └── components/
│           └── map/       # Live map components (STSLayer, etc.)
├── data/                  # DB seed SQL scripts
├── docker-compose.yml         # Development compose
├── docker-compose.prod.yml    # Production compose
└── .github/workflows/         # CI/CD pipeline
```

---

## 📄 License

MIT
