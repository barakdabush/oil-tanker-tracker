import asyncio
import websockets

async def test_echo():
    url = "wss://echo.websocket.org"
    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("Connected!")
            await ws.send("Hello")
            res = await ws.recv()
            print(f"Received: {res}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_echo())
