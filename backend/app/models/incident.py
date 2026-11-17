"""Incident model: deduplicated production errors / data anomalies tracked as GitHub issues."""

from sqlalchemy import Column, DateTime, Integer, String, Text, text

from app.database import Base


class Incident(Base):
    __tablename__ = "incidents"

    fingerprint = Column(String(64), primary_key=True)
    kind = Column(String(32), nullable=False)  # log_error | pipeline_stale | pipeline_empty | service_down
    service = Column(String(32), nullable=False)
    title = Column(String(256), nullable=False)
    sample_message = Column(Text, nullable=False)
    logger_name = Column(String(128), nullable=True)

    first_seen = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_seen = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    occurrences = Column(Integer, nullable=False, default=0)
    occurrences_at_notify = Column(Integer, nullable=False, default=0)

    # open (tracked, no issue yet) | issued (GitHub issue exists) | muted
    status = Column(String(16), nullable=False, default="open")
    github_issue_number = Column(Integer, nullable=True)
    issue_created_at = Column(DateTime(timezone=True), nullable=True)
    last_notified_at = Column(DateTime(timezone=True), nullable=True)
