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
from livekit.plugins import xai
from livekit.api import AccessToken, VideoGrants

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
XAI_API_KEY = os.getenv("XAI_API_KEY")
ROOM_NAME = "apb-voice-room"

# Initialize xAI API key
xai.api_key = XAI_API_KEY

# Pastor Bob's context and instructions with shared context support
PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a friendly voice assistant that helps people learn about the Bible and faith based on Pastor Bob Kopeny's teachings.

IMPORTANT: You maintain conversation context across both voice and text interactions. When responding, consider the full conversation history that includes both text messages and voice interactions.

CONTEXT:
- APB stands for "Ask Pastor Bob"
- Pastor Bob Kopeny is the specific pastor whose teachings you represent
- You should ONLY reference Pastor Bob Kopeny's perspectives and teachings
- Maintain awareness of previous questions and answers in the conversation

YOUR ROLE:
- Answer questions about the Bible, faith, and Christian living
- Share relevant illustrations and stories in a warm, pastoral way
- Be encouraging, compassionate, and helpful
- Keep responses conversational and brief since this is voice chat
- Reference previous parts of the conversation when relevant

SPEAKING STYLE:
- Warm and welcoming, like a friendly pastor
- Use simple, clear language
- Be encouraging and supportive
- Keep answers focused and not too long
- Remember what was discussed earlier in the conversation
"""

class APBAssistant(Agent):
    def __init__(self, conversation_history=None):
        # Include conversation history in instructions if available
        instructions = PASTOR_BOB_INSTRUCTIONS
        if conversation_history:
            context_str = "\n\nPrevious conversation context:\n"
            for msg in conversation_history[-10:]:  # Last 10 messages
                role = "User" if msg.get("role") == "user" else "You"
                context_str += f"{role}: {msg.get('content', '')}\n"
            instructions += context_str
        
        super().__init__(instructions=instructions)
        self.conversation_history = conversation_history or []

async def run_session():
    """Run one complete session with integrated text/voice support"""
    
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("apb-agent") \
        .with_grants(VideoGrants(
            room=ROOM_NAME,
            room_join=True,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    
    room = rtc.Room()
    user_disconnected = asyncio.Event()
    current_user = None
    session = None
    conversation_history = []

    @room.on("participant_connected")
    def on_connect(p):
        nonlocal current_user
        if not p.identity.startswith("agent") and not p.identity.startswith("apb"):
            logger.info(f"User joined: {p.identity}")
            current_user = p

    @room.on("participant_disconnected") 
    def on_disconnect(p):
        if not p.identity.startswith("agent") and not p.identity.startswith("apb"):
            logger.info(f"User left: {p.identity}")
            user_disconnected.set()

    @room.on("track_subscribed")
    def on_track(track, pub, p):
        logger.info(f"Track: {track.kind} from {p.identity}")

    @room.on("data_received")
    def on_data_received(data: bytes, participant: rtc.Participant, kind: rtc.DataPacketKind):
        """Handle data messages from the web client"""
        if participant == room.local_participant:
            return  # Ignore our own messages
        
        try:
            message = json.loads(data.decode())
            logger.info(f"Received data message: {message.get('type')}")
            
            if message.get('type') == 'speak' and session:
                # Text message that should be spoken
                text = message.get('text', '')
                context = message.get('context', [])
                
                # Update conversation history
                nonlocal conversation_history
                conversation_history = context
                
                logger.info(f"Speaking text response: {text[:50]}...")
                
                # Generate voice response for the text in a task
                async def speak_text():
                    try:
                        # Use generate_reply to speak the text
                        await session.generate_reply(
                            instructions=f"Say exactly: '{text}'"
                        )
                        logger.info("✅ Text response spoken")
                    except Exception as e:
                        logger.error(f"Error speaking text: {e}")
                
                asyncio.create_task(speak_text())
                    
            elif message.get('type') == 'context_update':
                # Update conversation context
                conversation_history = message.get('context', [])
                logger.info(f"Updated context with {len(conversation_history)} messages")
                
        except Exception as e:
            logger.error(f"Error processing data message: {e}")

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
            logger.info("Creating xAI realtime model with context awareness...")
            session = AgentSession(
                llm=xai.realtime.RealtimeModel(
                    voice="Ara",
                    turn_detection={"type": "server_vad", "threshold": 0.5, "silence_duration_ms": 800}
                ),
            )
            logger.info("✅ xAI session created")

            logger.info("Starting agent session with context...")
            await session.start(room=room, agent=APBAssistant(conversation_history))
            logger.info("✅ Agent session started")
            
            # Custom greeting
            logger.info("Generating greeting...")
            await session.generate_reply(
                instructions="Say exactly: 'Welcome to Ask Pastor Bob! I can help you with questions about faith and the Bible. Feel free to speak or type your questions.'"
            )
            logger.info("✅ Greeting sent")
            
            # Send initial context sync to client
            await room.local_participant.publish_data(
                json.dumps({
                    "type": "agent_ready",
                    "message": "Voice agent connected and ready"
                }).encode(),
                reliable=True
            )
            
            logger.info("LISTENING - Ready for voice and text!")

        except Exception as e:
            logger.error(f"Session error: {e}")
            raise

        # Keep session alive and handle messages
        while not user_disconnected.is_set():
            await asyncio.sleep(1)
            
            # Periodically update context if needed
            if len(conversation_history) > 0 and session:
                # Session maintains context automatically through xAI
                pass

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await room.disconnect()
        logger.info("Room disconnected")

async def main():
    logger.info("=" * 50)
    logger.info("APB - Integrated Voice & Text Agent")
    logger.info("=" * 50)

    while True:
        await run_session()
        logger.info("Session ended. Restarting in 2 seconds...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())