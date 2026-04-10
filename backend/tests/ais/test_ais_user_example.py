import asyncio
import websockets
import json
from datetime import datetime, timezone

async def connect_ais_stream():
    # Use the same URL as the app
    url = "wss://stream.aisstream.io/v0/stream"
    api_key = "a84d09908db610311f1cd0c3f3a53e2b18589b91"

    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url) as websocket:
            print("Connected! Sending subscription...")
            subscribe_message = {
                "APIKey": api_key,
                "BoundingBoxes": [[[-90, -180], [90, 180]]],
                "FilterMessageTypes": ["PositionReport"]
            }

            await websocket.send(json.dumps(subscribe_message))
            print("Subscription sent. Waiting for messages...")

            # Receive just 3 messages then stop
            count = 0
            async for message_json in websocket:
                message = json.loads(message_json)
                print(f"Received message type: {message.get('MessageType')}")
                count += 1
                if count >= 3:
                    break
            print("Test successful!")
    except Exception as e:
        print(f"Test failed: {type(e).__name__} - {e}")

if __name__ == "__main__":
    asyncio.run(connect_ais_stream())
