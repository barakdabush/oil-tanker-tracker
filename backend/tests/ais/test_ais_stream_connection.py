import asyncio
import websockets
import json
import os

async def test_connection():
    url = "wss://stream.aisstream.io/v0/stream"
    api_key = "a84d09908db610311f1cd0c3f3a53e2b18589b91" # Masked in logs later if needed
    
    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("Connected! Sending subscription...")
            msg = {
                "APIKey": api_key,
                "BoundingBoxes": [[[90, -180], [-90, 180]]]
            }
            await ws.send(json.dumps(msg))
            print("Message sent. Waiting for response...")
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            print(f"Received response: {response[:100]}...")
    except Exception as e:
        print(f"Connection failed: {type(e).__name__} - {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
