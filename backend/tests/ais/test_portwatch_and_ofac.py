"""Unit tests for IMF PortWatch and US Treasury OFAC SDN parsers."""

from datetime import date

from app.services.portwatch_fetcher import _parse_arcgis_date, _parse_portwatch_feature
from app.services.ofac_fetcher import parse_ofac_sdn_csv


def test_parse_portwatch_feature_epoch_ms():
    attrs = {
        "portid": "chokepoint6",
        "portname": "Strait of Hormuz",
        "date": 1794096000000,  # 2026-11-08T00:00:00Z
        "n_total": 3,
        "n_tanker": 1,
        "n_cargo": 2,
        "n_container": 0,
        "capacity_tanker": 120000.0,
        "capacity": 210000.0,
    }
    row = _parse_portwatch_feature(attrs)
    assert row is not None
    assert row["portid"] == "chokepoint6"
    assert row["portname"] == "Strait of Hormuz"
    assert row["date"] == date(2026, 11, 8)
    assert row["n_total"] == 3
    assert row["n_tanker"] == 1
    assert row["capacity_tanker"] == 120000.0


def test_parse_arcgis_date_iso_string():
    assert _parse_arcgis_date("2026-11-08T00:00:00Z") == date(2026, 11, 8)
    assert _parse_arcgis_date(None) is None


def test_parse_ofac_sdn_csv_extracts_vessels_and_identifiers():
    sample_csv = (
        '12345,"SILVER CLOUD","vessel","IRAN","-0-","EPBB","Crude Oil Tanker","99094","56068","Iran","NITC","Linked To: NATIONAL IRANIAN TANKER COMPANY; Vessel Registration Identification IMO 9569683; MMSI 422012345."\n'
        '12346,"ACME CORP","entity","SDGT","-0-","-0-","-0-","-0-","-0-","-0-","-0-","Non-vessel entity."\n'
    )
    vessels = parse_ofac_sdn_csv(sample_csv)
    assert len(vessels) == 1
    v = vessels[0]
    assert v["uid"] == 12345
    assert v["name"] == "SILVER CLOUD"
    assert v["program"] == "IRAN"
    assert v["imo"] == 9569683
    assert v["mmsi"] == 422012345
    assert v["flag"] == "Iran"
    assert v["vessel_type"] == "Crude Oil Tanker"
