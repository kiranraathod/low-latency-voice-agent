import asyncio
from deepgram import AsyncDeepgramClient, LiveOptions
import os

async def test_deepgram():
    api_key = "8a48cddde445c46af522ba3e3cdbeb8b73e252b0"
    client = AsyncDeepgramClient(api_key=api_key)
    options = LiveOptions(
        model="nova-2",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        endpointing="300",
        interim_results=True,
        smart_format=True
    )
    
    try:
        async with client.listen.websocket.v("1").connect(options) as dg_connection:
            print("Successfully connected with LiveOptions!")
            await dg_connection.send_close_stream()
    except Exception as e:
        print(f"Failed to connect: {repr(e)}")

if __name__ == "__main__":
    asyncio.run(test_deepgram())
