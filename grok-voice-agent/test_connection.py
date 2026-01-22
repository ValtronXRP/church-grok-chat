#!/usr/bin/env python
"""Test if agent can join a room directly"""

import asyncio
import os
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")  
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def test_room():
    print(f"Testing connection to: {LIVEKIT_URL}")
    
    # Create a room client
    room_client = api.RoomServiceClient(
        LIVEKIT_URL.replace('wss://', 'https://'),
        LIVEKIT_API_KEY,
        LIVEKIT_API_SECRET
    )
    
    # List rooms
    try:
        rooms = await room_client.list_rooms()
        print(f"\nActive rooms: {len(rooms)}")
        for room in rooms:
            print(f"  - {room.name} (participants: {room.num_participants})")
            
            # List participants in each room
            participants = await room_client.list_participants(room.name)
            for p in participants:
                print(f"    • {p.identity} ({p.state})")
    except Exception as e:
        print(f"Error listing rooms: {e}")
    
    await room_client.aclose()

if __name__ == "__main__":
    asyncio.run(test_room())