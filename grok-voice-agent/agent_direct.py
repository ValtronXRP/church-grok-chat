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

SERMONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sermons_static.json')
sermons_data = []

def load_sermons():
    global sermons_data
    try:
        with open(SERMONS_FILE, 'r') as f:
            sermons_data = json.load(f)
        logger.info(f"Loaded {len(sermons_data)} sermon segments")
    except Exception as e:
        logger.error(f"Failed to load sermons: {e}")
        sermons_data = []

def time_to_seconds(time_str):
    if not time_str:
        return 0
    parts = time_str.split(':')
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0

def search_sermons(query, n_results=3):
    if not query or not sermons_data:
        return []
    
    query_lower = query.lower()
    query_words = query_lower.split()
    
    scored = []
    for sermon in sermons_data:
        text_lower = sermon.get('text', '').lower()
        word_matches = sum(1 for word in query_words if len(word) > 3 and word in text_lower)
        topics = sermon.get('topics', [])
        topic_score = 0.3 if any(t.lower() in query_lower for t in topics) else 0
        score = (word_matches / len(query_words) if query_words else 0) + topic_score
        
        if score > 0.2:
            scored.append((score, sermon))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    results = []
    for score, sermon in scored[:n_results]:
        start_seconds = time_to_seconds(sermon.get('start_time', '0'))
        timestamped_url = f"{sermon.get('url', '')}&t={start_seconds}s"
        results.append({
            'text': sermon.get('text', ''),
            'title': sermon.get('title', 'Unknown Sermon'),
            'video_id': sermon.get('video_id', ''),
            'start_time': sermon.get('start_time', ''),
            'url': sermon.get('url', ''),
            'timestamped_url': timestamped_url,
            'relevance_score': score
        })
    
    return results

def has_illustration(text):
    markers = ['remember when', 'story', 'once ', 'example', 'illustration', 
               'let me tell you', 'imagine', 'picture this', 'there was a']
    return any(m in text.lower() for m in markers)

def format_sermon_context(results):
    if not results:
        return ""
    
    context = "\n\nRELEVANT SERMON SEGMENTS FROM PASTOR BOB:\n"
    for i, r in enumerate(results, 1):
        context += f"\n[Segment {i}] From '{r['title']}' at {r['start_time']}:\n"
        context += f"Watch: {r['timestamped_url']}\n"
        if has_illustration(r['text']):
            context += f"ILLUSTRATION: \"{r['text'][:500]}...\"\n"
        else:
            context += f"Teaching: {r['text'][:300]}...\n"
    
    context += "\nINSTRUCTIONS: Include relevant YouTube links and quote any illustrations directly in your response.\n"
    return context

class FixedXAIRealtimeModel(openai.realtime.RealtimeModel):
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
- When given sermon segments, ALWAYS mention the YouTube links so users can watch
- If there's an illustration or story in the segments, quote it directly
- Keep voice responses conversational but include the video references

YOUR ROLE:
- Answer questions about the Bible, faith, and Christian living
- Share relevant illustrations and stories from Pastor Bob's sermons
- Always reference the YouTube videos when available
- Be encouraging, compassionate, and helpful

SPEAKING STYLE:
- Warm and welcoming, like a friendly pastor
- When you have video segments, say things like "Pastor Bob teaches about this in his sermon, you can watch it at..." 
- Quote illustrations directly: "Pastor Bob shares this story..."
- Keep answers focused but include the resources
"""

class APBAssistant(Agent):
    def __init__(self, extra_context=""):
        full_instructions = PASTOR_BOB_INSTRUCTIONS
        if extra_context:
            full_instructions += extra_context
        super().__init__(instructions=full_instructions)

async def send_data_message(room, message_type, data):
    try:
        message = json.dumps({"type": message_type, **data})
        await room.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type} message to room")
    except Exception as e:
        logger.error(f"Failed to send data message: {e}")

async def run_session():
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
    current_sermon_results = []

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
            
            @session.on("user_input_transcribed")
            async def on_user_speech(text):
                nonlocal current_sermon_results
                if text and len(text) > 5:
                    logger.info(f"User said: {text}")
                    await send_data_message(room, "user_transcript", {"text": text})
                    
                    results = search_sermons(text, 3)
                    current_sermon_results = results
                    if results:
                        logger.info(f"Found {len(results)} sermon segments for: {text}")
                        for r in results:
                            await send_data_message(room, "sermon_reference", {
                                "title": r['title'],
                                "url": r['timestamped_url'],
                                "timestamp": r['start_time'],
                                "text": r['text'][:200]
                            })
            
            @session.on("agent_response")
            async def on_agent_response(text):
                if text:
                    logger.info(f"Agent response: {text[:100]}...")
                    response_with_links = text
                    if current_sermon_results:
                        response_with_links += "\n\nRelated sermon videos:\n"
                        for r in current_sermon_results:
                            response_with_links += f"- {r['title']} ({r['start_time']}): {r['timestamped_url']}\n"
                    await send_data_message(room, "agent_transcript", {"text": response_with_links})
            
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
    
    load_sermons()

    while True:
        await run_session()
        logger.info("Session ended. Restarting in 2 seconds...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
