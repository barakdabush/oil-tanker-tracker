"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings loaded from environment variables."""

    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@localhost:5432/oil_monitor"
    DATABASE_URL_SYNC: str = "postgresql://admin:secret@localhost:5432/oil_monitor"
    AIS_API_KEY: str = "your_aisstream_api_key_here"

    # AIS ingestion settings
    AIS_WS_URL: str = "wss://stream.aisstream.io/v0/stream"
    AIS_TANKER_TYPES: list[int] = list(range(80, 90))  # AIS ship types 80-89

    # Detection engine settings
    PORT_PROXIMITY_KM: float = 5.0
    STATIONARY_SPEED_KNOTS: float = 1.0
    DEPARTURE_SPEED_KNOTS: float = 3.0
    DRAFT_CHANGE_THRESHOLD_M: float = 0.5
    DARK_FLEET_SUSPICIOUS_MIN: int = 240
    DARK_FLEET_DARK_HOURS: int = 6
    DARK_FLEET_EXTENDED_HOURS: int = 24
    STS_PROXIMITY_M: float = 500.0
    STS_MIN_DURATION_MIN: int = 30
    STS_PORT_DISTANCE_KM: float = 20.0

    # Tanker block coefficient for volume estimation
    BLOCK_COEFFICIENT: float = 0.85
    SEAWATER_DENSITY: float = 1.025  # tonnes/m³

    class Config:
        env_file = ".env"


settings = Settings()
