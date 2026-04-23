from typing import TypedDict

class AISPositionReport(TypedDict):
    """AIS Position Report (MessageID 1, 2, 3).

    Sent every 2-10 seconds depending on speed/turn rate.
    """
    MessageID: int                    # 1, 2, or 3
    RepeatIndicator: int              # 0-3, times message has been repeated
    UserID: int                       # MMSI number
    Valid: bool                       # Whether the message is valid
    NavigationalStatus: int           # 0=Under way using engine, 1=At anchor, 2=Not under command,
                                      # 3=Restricted manoeuvrability, 4=Constrained by draught,
                                      # 5=Moored, 6=Aground, 7=Engaged in fishing, 8=Under way sailing,
                                      # 9-14=Reserved, 15=Not defined
    RateOfTurn: int                   # 0-127 (127=not available), degrees/min
    Sog: float                        # Speed over ground in knots (0-102.2, 102.3=not available)
    PositionAccuracy: bool            # True=high (DGPS), False=low (GNSS)
    Longitude: float                  # Degrees (-180 to 180, 181=not available)
    Latitude: float                   # Degrees (-90 to 90, 91=not available)
    Cog: float                        # Course over ground in degrees (0-359.9, 360=not available)
    TrueHeading: int                  # Degrees (0-359, 511=not available)
    Timestamp: int                    # UTC second (0-59, 60=not available, 61=manual, 62=dead reckoning, 63=inoperative)
    SpecialManoeuvreIndicator: int    # 0=not available, 1=not engaged, 2=engaged
    Spare: int                        # Not used
    Raim: bool                        # RAIM (Receiver Autonomous Integrity Monitoring) flag
    CommunicationState: int           # SOTDMA/ITDMA communication state


class AISShipDimension(TypedDict):
    """Ship dimension reference point for antenna position."""
    A: int  # Distance from bow to reference point (meters)
    B: int  # Distance from reference point to stern (meters)  → Length = A + B
    C: int  # Distance from port side to reference point (meters)
    D: int  # Distance from reference point to starboard (meters) → Beam = C + D


class AISEta(TypedDict):
    """Estimated Time of Arrival."""
    Month: int   # 1-12, 0=not available
    Day: int     # 1-31, 0=not available
    Hour: int    # 0-23, 24=not available
    Minute: int  # 0-59, 60=not available


class AISShipStaticData(TypedDict):
    """AIS Static and Voyage Related Data (MessageID 5).

    Sent every 6 minutes or on request.
    """
    MessageID: int                    # Always 5
    RepeatIndicator: int              # 0-3
    UserID: int                       # MMSI number
    Valid: bool                       # Whether the message is valid
    AisVersion: int                   # 0-3, AIS version indicator
    ImoNumber: int                    # IMO ship identification number (1-999999999)
    CallSign: str                     # 7 chars, right-padded with spaces
    Name: str                         # 20 chars, right-padded with spaces
    Type: int                         # Ship type (80-89 = Tanker)
                                      # 80=Tanker, 81=Tanker carrying DG/HS/MP
                                      # 82=Tanker carrying DG, 83=Tanker carrying DG/HS
                                      # 84=Tanker carrying DG/HS/MP, 85-89=Tanker (reserved)
    Dimension: AISShipDimension       # Ship dimensions
    FixType: int                      # Position fix type (0=undefined, 1=GPS, 2=GLONASS, etc.)
    Eta: AISEta                       # Estimated time of arrival
    MaximumStaticDraught: float       # Draft in meters (with 1 decimal)
    Destination: str                  # 20 chars, right-padded with spaces
    Dte: bool                         # Data terminal equipment ready
    Spare: bool                       # Not used


class AISMetaData(TypedDict):
    """Metadata added by aisstream.io to every message."""
    MMSI: int                         # MMSI number
    MMSI_String: int                  # MMSI as string (but comes as int)
    ShipName: str                     # 20 chars, right-padded with spaces
    latitude: float                   # Current latitude
    longitude: float                  # Current longitude
    time_utc: str                     # Timestamp, e.g. "2026-04-23 18:42:33.749439468 +0000 UTC"


class AISMessage(TypedDict):
    """Top-level AIS message envelope from aisstream.io."""
    MessageType: str                  # "PositionReport" or "ShipStaticData"
    Message: dict                     # {"PositionReport": {...}} or {"ShipStaticData": {...}}
    MetaData: AISMetaData             # Metadata about the message
