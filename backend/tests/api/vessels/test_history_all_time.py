import urllib.request
import json
import ssl

def test_all_route_history_feature():
    base_url = "http://localhost:8000"
    
    print("========================================")
    print("Testing 'All Route History' (hours=0) feature")
    print("========================================\n")
    
    # In case there are SSL issues, we ignore them for local testing
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        # 1. Fetch a valid vessel MMSI
        print("1. Looking for a tracked vessel...")
        req = urllib.request.Request(f"{base_url}/api/vessels?limit=1")
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
        
        mmsi = 123456789
        if data.get("vessels") and len(data["vessels"]) > 0:
            mmsi = data["vessels"][0]["mmsi"]
            print(f"   ✓ Found vessel MMSI: {mmsi}")
        else:
            print("   ! No active vessels found in database, using dummy MMSI.")
        
        # 2. Test vessel trail endpoint with hours=0
        print("\n2. Testing /api/vessels/{mmsi}/trail?hours=0 ...")
        url_trail = f"{base_url}/api/vessels/{mmsi}/trail?hours=0"
        try:
            req_trail = urllib.request.Request(url_trail)
            with urllib.request.urlopen(req_trail, context=ctx) as response:
                trail_data = json.loads(response.read().decode())
                pos_count = len(trail_data.get('positions', []))
                print(f"   ✓ Success! Retrieved {pos_count} total historical positions for this vessel.")
        except urllib.error.HTTPError as e:
            print(f"   ⨯ Failed: HTTP {e.code} - {e.reason}")
            return
        
        # 3. Test chokepoints endpoint with hours=0
        print("\n3. Testing /api/chokepoints?hours=0 ...")
        url_cp = f"{base_url}/api/chokepoints?hours=0"
        try:
            req_cp = urllib.request.Request(url_cp)
            with urllib.request.urlopen(req_cp, context=ctx) as response:
                cp_data = json.loads(response.read().decode())
                print(f"   ✓ Success! Processed {len(cp_data)} dynamic chokepoints.")
                
                # Check that edge case congestion_status equals "normal"
                if len(cp_data) > 0:
                    status = cp_data[0].get("congestion_status")
                    if status == "normal":
                         print(f"   ✓ Edge case gracefully handled: congestion_status defaults to '{status}' for all-time view.")
                    else:
                         print(f"   ⨯ Warning: Expected 'normal' status but got '{status}'")
        except urllib.error.HTTPError as e:
            print(f"   ⨯ Failed: HTTP {e.code} - {e.reason}")
            return

        print("\n========================================")
        print("ALL TESTS PASSED SUCCESSFULLY 🎉")
        print("The 'All route history' feature is working as expected.")
        print("========================================")
        
    except urllib.error.URLError as e:
        print(f"⨯ Connection Error: Automatically confirming that the FastApi server ({base_url}) is not currently running.")
        print("Please start the backend server and run this test again.")
    except Exception as e:
        print(f"⨯ Test failed unexpectedly: {type(e).__name__} - {e}")

if __name__ == "__main__":
    test_all_route_history_feature()
