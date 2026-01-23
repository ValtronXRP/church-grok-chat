import os
import asyncio
from dotenv import load_dotenv
from livekit.plugins import xai

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")
print(f"XAI_API_KEY set: {bool(XAI_API_KEY)}")
print(f"XAI_API_KEY starts with: {XAI_API_KEY[:10] if XAI_API_KEY else 'None'}")

# Set the API key
xai.api_key = XAI_API_KEY

async def test():
    try:
        print("Creating RealtimeModel...")
        model = xai.realtime.RealtimeModel(
            voice="Ara",
            api_key=XAI_API_KEY  # Also pass it explicitly
        )
        print(f"Model created: {model}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
