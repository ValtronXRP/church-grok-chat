#!/usr/bin/env python
"""Manually request agent for a room"""

import asyncio
import os
import sys
from dotenv import load_dotenv
import aiohttp
import base64

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def request_agent(room_name):
    """Request agent for a specific room"""
    
    http_url = LIVEKIT_URL.replace('wss://', 'https://')
    
    # Use the LiveKit SDK to create a proper token for API access
    from livekit import api
    
    # Agent dispatch endpoint
    dispatch_url = f"{http_url}/twirp/livekit.AgentDispatchService/CreateDispatch"
    
    # Create an API token
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET).to_jwt()
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "room": room_name,
        "agent_name": "grok-voice-assistant",
        "metadata": ""
    }
    
    print(f"Requesting agent for room: {room_name}")
    print(f"URL: {dispatch_url}")
    print(f"Agent: grok-voice-assistant")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(dispatch_url, json=payload, headers=headers) as response:
                text = await response.text()
                print(f"Response status: {response.status}")
                print(f"Response: {text}")
                
                if response.status == 200:
                    print("✅ Agent dispatch requested successfully!")
                else:
                    print(f"❌ Failed to dispatch agent")
                    
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    room_name = sys.argv[1] if len(sys.argv) > 1 else "test-room-1768759134141"
    asyncio.run(request_agent(room_name))