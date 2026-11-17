"""OFAC SDN sanctioned vessels model."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, text

from app.database import Base


class OfacSanctionedVessel(Base):
    __tablename__ = "ofac_sanctioned_vessels"

    uid = Column(Integer, primary_key=True)  # OFAC ent_num
    imo = Column(BigInteger, nullable=True, index=True)
    mmsi = Column(BigInteger, nullable=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    sdn_type = Column(String(32), nullable=True)
    program = Column(String(128), nullable=True)
    flag = Column(String(64), nullable=True)
    vessel_type = Column(String(64), nullable=True)
    remarks = Column(String(1000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
