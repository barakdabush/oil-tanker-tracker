from fastapi.testclient import TestClient
from app.main import app

def test_chokepoints_two_weeks():
    """Verify that the chokepoints endpoint correctly handles a 2-week (336h) range."""
    print("Testing /api/chokepoints?hours=336 ...")
    with TestClient(app) as client:
        response = client.get("/api/chokepoints", params={"hours": 336})
        assert response.status_code == 200, f"Chokepoints endpoint failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Chokepoints response is not a list"
        if len(data) > 0:
            assert "congestion_status" in data[0], "Missing congestion_status in chokepoints"
        print("✓ Chokepoints with hours=336 passed!")

if __name__ == "__main__":
    test_chokepoints_two_weeks()
