"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings loaded from environment variables."""

    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@localhost:5432/oil_monitor"
    DATABASE_URL_SYNC: str = "postgresql://admin:secret@localhost:5432/oil_monitor"
    AIS_API_KEY: str = "your_aisstream_api_key_here"
    EIA_API_KEY: str = "your_eia_api_key_here"

    # AIS ingestion settings
    AIS_WS_URL: str = "wss://stream.aisstream.io/v0/stream"
    OPENWATERS_ENABLED: bool = True
    OPENWATERS_WS_URL: str = "wss://ais.openwaters.io/v1/stream"
    OPENWATERS_API_KEY: str = ""
    AIS_TANKER_TYPES: list[int] = list(range(80, 90))  # AIS ship types 80-89

    # Detection engine settings
    PORT_PROXIMITY_KM: float = 5.0
    STATIONARY_SPEED_KNOTS: float = 1.0
    DEPARTURE_SPEED_KNOTS: float = 3.0
    DRAFT_CHANGE_THRESHOLD_M: float = 0.5
    DARK_FLEET_SUSPICIOUS_MIN: int = 240
    DARK_FLEET_DARK_HOURS: int = 6
    DARK_FLEET_EXTENDED_HOURS: int = 24
    STS_PROXIMITY_M: float = 250.0
    STS_MIN_DURATION_MIN: int = 30
    STS_PORT_DISTANCE_KM: float = 20.0

    # Tanker block coefficient for volume estimation
    BLOCK_COEFFICIENT: float = 0.85
    SEAWATER_DENSITY: float = 1.025  # tonnes/m³

    # Auto-incident pipeline (errors/anomalies -> GitHub issues)
    AUTO_INCIDENTS_ENABLED: bool = True
    GITHUB_INCIDENT_TOKEN: str = ""  # fine-grained PAT, this repo only, Issues: read & write
    GITHUB_REPO: str = "barakdabush/oil-tanker-tracker"
    INCIDENT_MIN_OCCURRENCES: int = 3  # log errors needed before opening an issue
    INCIDENT_MIN_ANOMALY_RUNS: int = 2  # consecutive detector runs an anomaly must persist
    INCIDENT_MAX_ISSUES_PER_DAY: int = 5
    INCIDENT_COMMENT_INTERVAL_HOURS: int = 6
    DASHBOARD_URL: str = ""  # e.g. http://<server-ip>:3000, linked from issues

    # News & Event Intelligence pipeline (free sources by default; LLM is strictly optional)
    NEWS_FETCH_ENABLED: bool = True
    NEWS_FETCH_INTERVAL_MINUTES: int = 15
    NEWS_LLM_ENABLED: bool = False
    NEWS_LLM_PROVIDER: str = "openai"  # "openai" | "gemini"
    NEWS_LLM_API_KEY: str = ""
    NEWS_LLM_MODEL: str = ""  # defaults to gpt-4o-mini (openai) or gemini-2.0-flash (gemini)
    NEWS_LLM_BASE_URL: str = ""  # optional custom OpenAI-compatible endpoint
    NEWS_LLM_MIN_RANK: float = 45.0
    NEWS_LLM_MAX_EVENTS_PER_DAY: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
