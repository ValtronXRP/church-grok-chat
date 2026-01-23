import asyncio
import logging
import os
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.plugins import xai, openai
from livekit.api import AccessToken, VideoGrants

class FixedXAIRealtimeModel(openai.realtime.RealtimeModel):
    """xAI Realtime Model"""
    def __init__(self, voice="Ara", api_key=None, **kwargs):
        api_key = api_key or os.environ.get("XAI_API_KEY")
        super().__init__(
            base_url="wss://api.x.ai/v1/realtime",
            model="",
            voice=voice,
            api_key=api_key,
            modalities=["audio"],
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 200,
                "create_response": True,
                "interrupt_response": True,
            },
            **kwargs
        )

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
XAI_API_KEY = os.getenv("XAI_API_KEY")
ROOM_NAME = "apb-voice-room"

xai.api_key = XAI_API_KEY

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

async def send_data_message(room, message_type, data):
    """Send a data message to all participants in the room"""
    try:
        message = json.dumps({"type": message_type, **data})
        await room.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type} message to room")
    except Exception as e:
        logger.error(f"Failed to send data message: {e}")

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
    silent_connection = False
    pending_text_to_speak = None
    session = None

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

    @room.on("data_received")
    def on_data(data, participant):
        nonlocal silent_connection, pending_text_to_speak
        try:
            message = json.loads(data.decode('utf-8'))
            logger.info(f"Received data message: {message.get('type')}")
            
            if message.get('type') == 'silent_connection':
                silent_connection = True
                pending_text_to_speak = message.get('textToSpeak')
                logger.info(f"Silent connection - text to speak: {pending_text_to_speak[:50] if pending_text_to_speak else 'None'}...")
        except Exception as e:
            logger.error(f"Error parsing data message: {e}")

    try:
        await room.connect(LIVEKIT_URL, token.to_jwt())
        logger.info(f"Connected to room: {room.name}")
        
        for participant in room.remote_participants.values():
            if not participant.identity.startswith("agent") and not participant.identity.startswith("apb"):
                logger.info(f"User already in room: {participant.identity}")
                current_user = participant
                break
        
        if current_user is None:
            logger.info("Waiting for user...")
            wait_count = 0
            while current_user is None:
                await asyncio.sleep(1.0)
                wait_count += 1
                if wait_count % 5 == 0:
                    logger.info(f"Still waiting... ({wait_count}s, {len(room.remote_participants)} remote participants)")

        logger.info(f"Starting session with {current_user.identity}")

        try:
            logger.info("Creating xAI realtime model...")
            session = AgentSession(
                llm=FixedXAIRealtimeModel(voice="Ara")
            )
            logger.info("xAI session created")

            logger.info("Starting agent session...")
            await session.start(room=room, agent=APBAssistant())
            logger.info("Agent session started")
            
            await asyncio.sleep(0.5)
            
            if silent_connection and pending_text_to_speak:
                logger.info("Speaking text response...")
                await session.generate_reply(
                    instructions=f"Read this response naturally: {pending_text_to_speak}"
                )
                await send_data_message(room, "agent_transcript", {"text": pending_text_to_speak})
                await asyncio.sleep(2)
                await send_data_message(room, "speech_complete", {})
                logger.info("Text-to-speech complete")
            elif not silent_connection:
                logger.info("Generating greeting...")
                greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
                await session.generate_reply(
                    instructions=f"Say exactly: '{greeting}'"
                )
                await send_data_message(room, "agent_transcript", {"text": greeting})
                logger.info("Greeting sent")
            
            logger.info("LISTENING - speak now!")
        except Exception as e:
            logger.error(f"Session error: {e}")
            raise

        await user_disconnected.wait()

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await room.disconnect()
        logger.info("Room disconnected")

async def main():
    logger.info("=" * 50)
    logger.info("APB - Ask Pastor Bob Voice Agent")
    logger.info("=" * 50)

    while True:
        await run_session()
        logger.info("Session ended. Restarting in 2 seconds...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
