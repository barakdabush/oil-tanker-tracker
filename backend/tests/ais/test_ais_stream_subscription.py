import asyncio
import websockets
import json
import ssl
import certifi

async def test_ais_handshake():
    url = "wss://stream.aisstream.io/v0/stream"
    api_key = "a84d09908db610311f1cd0c3f3a53e2b18589b91"
    
    print(f"--- AIS Handshake Test ---")
    print(f"Target: {url}")
    
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        print("Initiating WebSocket connection...")
        async with websockets.connect(
            url, 
            ssl=ssl_context, 
            extra_headers=headers,
            open_timeout=30
        ) as ws:
            print("Connected! Sending subscription message...")
            subscribe_msg = {
                "APIKey": api_key,
                "BoundingBoxes": [[[90, -180], [-90, 180]]],
                "FilterMessageTypes": ["PositionReport"]
            }
            await ws.send(json.dumps(subscribe_msg))
            print("Subscription sent. Waiting for first message (timeout 20s)...")
            
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=20)
                print(f"SUCCESS: Received message: {str(msg)[:200]}...")
            except asyncio.TimeoutError:
                print("FAILED: Connection established but no data received (timeout).")
                
    except Exception as e:
        print(f"FAILED: Connection error: {type(e).__name__}")
        print(f"Details: {e}")

if __name__ == "__main__":
    asyncio.run(test_ais_handshake())
