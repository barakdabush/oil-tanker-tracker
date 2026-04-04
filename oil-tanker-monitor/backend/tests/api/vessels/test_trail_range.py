from fastapi.testclient import TestClient
from app.main import app

def test_vessels_trail_two_weeks():
    """Verify that the vessels trail endpoint correctly handles a 2-week (336h) range."""
    print("Testing /api/vessels/{mmsi}/trail?hours=336 ...")
    with TestClient(app) as client:
        response = client.get("/api/vessels/123000000/trail", params={"hours": 336})
        assert response.status_code == 200, f"Vessels trail endpoint failed: {response.text}"
        data = response.json()
        assert "positions" in data, "No positions field in response"
        print("✓ Vessels trail with hours=336 passed!")

if __name__ == "__main__":
    test_vessels_trail_two_weeks()
