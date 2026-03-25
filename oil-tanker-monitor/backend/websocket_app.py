import asyncio
import logging
import sys
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Ensure the backend directory is in the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import engine
from app.services.ais_ingestion import start_ais_ingestion, start_flush_task
from app.ws_manager import manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("websocket_app")

async def safe_ais_ingestion():
    """Wrapper to ensure ingestion keeps running even if it hits errors."""
    while True:
        try:
            await start_ais_ingestion()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"AIS ingestion crashed: {e}. Restarting in 5s...")
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WebSocket and AIS ingestion service...")
    
    # Start background AIS ingestion so it can broadcast to manager
    ingestion_task = asyncio.create_task(safe_ais_ingestion())
    flush_task = asyncio.create_task(start_flush_task())
    
    yield
    
    logger.info("Shutting down WebSocket and AIS ingestion service...")
    ingestion_task.cancel()
    flush_task.cancel()
    try:
        await asyncio.gather(ingestion_task, flush_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    
    await engine.dispose()
    logger.info("Shutdown complete")

app = FastAPI(
    title="Oil Tanker Monitor WebSocket Service",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "websocket"}


@app.websocket("/api/ws/vessels")
async def websocket_vessels(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Just keep the connection alive, ignore incoming messages from client
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
