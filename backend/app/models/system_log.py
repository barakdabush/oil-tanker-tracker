"""Centralized system log model for container & worker observability."""

from sqlalchemy import BigInteger, Column, DateTime, Index, String, Text, text

from app.database import Base


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    service = Column(String(32), nullable=False, index=True)
    level = Column(String(16), nullable=False, index=True)
    logger_name = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_system_logs_created_at", "created_at"),
        Index("idx_system_logs_service_level", "service", "level"),
    )
