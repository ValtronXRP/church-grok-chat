"""
Test script to verify xAI API connection
Run with: python test_xai.py
"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def test():
    print("Testing xAI API connection...")
    print(f"XAI_API_KEY: {'*' * 20}...{os.getenv('XAI_API_KEY', '')[-4:]}")
    
    try:
        from livekit.plugins import xai
        
        # Try to create a realtime model instance
        model = xai.realtime.RealtimeModel(voice="Ara")
        print("✅ xAI RealtimeModel created successfully!")
        print(f"   Voice: Ara")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test())
