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


import logging
import pytest


@pytest.mark.asyncio
async def test_portwatch_separates_maintenance_from_rate_limit(monkeypatch, caplog):
    from app.services import portwatch_fetcher

    calls = []

    def _make_client(payload):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                calls.append(params["where"])
                return _Resp()

        return _Client

    fetcher = portwatch_fetcher.PortWatchFetcher()

    # Upstream maintenance (Token Required) -> INFO, stops after 1st chokepoint
    monkeypatch.setattr(portwatch_fetcher.httpx, "AsyncClient", _make_client({"error": {"code": 499, "message": "Token Required"}}))
    with caplog.at_level(logging.INFO, logger="app.services.portwatch_fetcher"):
        res = await fetcher.fetch_and_store()
    assert res == 0
    assert len(calls) == 1
    assert any(r.levelno == logging.INFO and "maintenance" in r.message.lower() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    calls.clear()
    caplog.clear()

    # Rate limit (Too many requests) -> ERROR, stops after 1st chokepoint
    monkeypatch.setattr(portwatch_fetcher.httpx, "AsyncClient", _make_client({"error": {"code": 429, "message": "Unable to perform query. Too many requests."}}))
    with caplog.at_level(logging.INFO, logger="app.services.portwatch_fetcher"):
        res = await fetcher.fetch_and_store()
    assert res == 0
    assert len(calls) == 1
    assert any(r.levelno == logging.ERROR and "rate-limited" in r.message.lower() for r in caplog.records)

