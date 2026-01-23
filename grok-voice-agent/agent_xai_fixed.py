import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.plugins import xai
from livekit.api import AccessToken, VideoGrants

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
XAI_API_KEY = os.getenv("XAI_API_KEY")
ROOM_NAME = "apb-voice-room"

# Initialize xAI API key
xai.api_key = XAI_API_KEY

# Pastor Bob's context and instructions
PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a friendly voice assistant that helps people learn about the Bible and faith based on Pastor Bob Kopeny's teachings.

IMPORTANT CONTEXT:
- APB stands for "Ask Pastor Bob"
- Pastor Bob Kopeny is the specific pastor whose teachings you represent
- You should ONLY reference Pastor Bob Kopeny's perspectives and teachings
- Do NOT reference or discuss other pastors named Bob unless the user specifically asks about a different pastor
- If you don't know what Pastor Bob would say about something, be honest and say "I'm not sure what Pastor Bob's specific teaching on that is, but I can share what the Bible says..."

YOUR ROLE:
- Answer questions about the Bible, faith, and Christian living
- Share relevant illustrations and stories in a warm, pastoral way
- Be encouraging, compassionate, and helpful
- Keep responses conversational and brief since this is voice chat
- If asked about topics outside faith/Bible, gently redirect or answer briefly

SPEAKING STYLE:
- Warm and welcoming, like a friendly pastor
- Use simple, clear language
- Be encouraging and supportive
- Keep answers focused and not too long
- It's okay to ask clarifying questions

BOUNDARIES:
- Only share teachings that align with Biblical Christianity
- Don't make up quotes or stories - if you're not sure, say so
- Don't pretend to be Pastor Bob himself - you are an assistant that helps share his teachings
- Be respectful of all questions, even difficult ones
"""

class APBAssistant(Agent):
    def __init__(self):
        super().__init__(instructions=PASTOR_BOB_INSTRUCTIONS)

async def run_session():
    """Run one complete session, return when user disconnects"""
    
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("apb-agent") \
        .with_grants(VideoGrants(
            room=ROOM_NAME,
            room_join=True,
            can_publish=True,
            can_subscribe=True,
        ))
    
    room = rtc.Room()
    user_disconnected = asyncio.Event()
    current_user = None

    @room.on("participant_connected")
    def on_connect(p):
        nonlocal current_user
        if not p.identity.startswith("agent") and not p.identity.startswith("apb"):
            logger.info(f"User joined: {p.identity}")
            current_user = p

    @room.on("participant_disconnected") 
    def on_disconnect(p):
        if not p.identity.startswith("agent"):
            logger.info(f"User left: {p.identity}")
            user_disconnected.set()

    @room.on("track_subscribed")
    def on_track(track, pub, p):
        logger.info(f"Track: {track.kind} from {p.identity}")

    try:
        await room.connect(LIVEKIT_URL, token.to_jwt())
        logger.info(f"Connected to room: {room.name}")
        
        # Check if user is already in the room
        for participant in room.remote_participants.values():
            if not participant.identity.startswith("agent") and not participant.identity.startswith("apb"):
                logger.info(f"User already in room: {participant.identity}")
                current_user = participant
                break
        
        if current_user is None:
            logger.info("Waiting for user...")
            # Wait for user with periodic status updates
            wait_count = 0
            while current_user is None:
                await asyncio.sleep(1.0)
                wait_count += 1
                if wait_count % 5 == 0:
                    logger.info(f"Still waiting... ({wait_count}s, {len(room.remote_participants)} remote participants)")

        logger.info(f"Starting session with {current_user.identity}")

        try:
            logger.info("Creating xAI realtime model with explicit configuration...")
            
            # Try to use the xAI realtime model with base_url override
            # Based on the xAI Voice documentation, the API should be compatible with OpenAI spec
            import httpx
            
            class XAIRealtimeModel:
                """Custom wrapper for xAI Voice API"""
                def __init__(self):
                    self.api_key = XAI_API_KEY
                    self.voice = "Ara"
                    self.model = "grok-2-1212"  # Try the available Grok model
                    
            # For now, let's try using the standard xAI plugin but with environment variable override
            os.environ['XAI_BASE_URL'] = 'https://api.x.ai/v1'
            os.environ['XAI_MODEL'] = 'grok-2-1212'
            
            # Try to create the session with the xAI realtime model
            session = AgentSession(
                llm=xai.realtime.RealtimeModel(
                    voice="Ara",
                    api_key=XAI_API_KEY
                )
            )
            logger.info("✅ xAI session created")

            logger.info("Starting agent session...")
            await session.start(room=room, agent=APBAssistant())
            logger.info("✅ Agent session started")
            
            # Send greeting after a delay
            await asyncio.sleep(1.0)
            
            logger.info("Generating greeting...")
            await session.generate_reply(
                instructions="Say exactly: 'Welcome to Ask Pastor Bob! How can I help you today?'"
            )
            logger.info("✅ Greeting sent")
            
            logger.info("LISTENING - speak now!")
        except Exception as e:
            logger.error(f"Session error: {e}")
            logger.error(f"Error type: {type(e)}")
            import traceback
            traceback.print_exc()
            raise

        # Wait until user disconnects
        await user_disconnected.wait()

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await room.disconnect()
        logger.info("Room disconnected")

async def main():
    logger.info("=" * 50)
    logger.info("APB - Ask Pastor Bob Voice Agent (xAI Fixed)")
    logger.info("=" * 50)

    while True:
        await run_session()
        logger.info("Session ended. Restarting in 2 seconds...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())