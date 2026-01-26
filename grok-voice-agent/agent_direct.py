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

def search_sermons(query, n_results=5):
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

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a friendly voice assistant based on Pastor Bob Kopeny's teachings.

PASTOR BOB'S FAMILY (VERIFIED FACTS - always use these):
- Wife: Becky Kopeny
- Oldest son: Jesse Kopeny (born July 24, 1984)
- Middle son: Valor Kopeny (born December 2, 1985)
- Youngest son: Christian Kopeny (born May 16, 1989)

CRITICAL - NO HALLUCINATIONS:
- ONLY share stories, illustrations, or examples that are DIRECTLY quoted in the sermon segments provided to you
- NEVER make up, invent, or embellish stories that are not in the actual sermon text
- If no specific illustration is found in the data, simply say "Pastor Bob teaches about this topic" without inventing details
- Do NOT assume what a video is about - only describe what is ACTUALLY in the transcript provided

KEY RULES:
- APB stands for "Ask Pastor Bob"
- Pastor Bob Kopeny is the pastor whose teachings you represent
- When given sermon segments, reference the YouTube links naturally
- Quote illustrations ONLY if they appear word-for-word in the provided text
- Keep responses conversational for voice
- If you don't have specific sermon content, give a general biblical answer and say you'll look for more resources

STYLE:
- Warm and welcoming
- Reference videos: "Here are some related videos from Pastor Bob..."
- Only quote stories that are ACTUALLY in the sermon text provided
"""

class APBAssistant(Agent):
    def __init__(self):
        super().__init__(instructions=PASTOR_BOB_INSTRUCTIONS)

async def send_data_message(room, message_type, data):
    try:
        message = json.dumps({"type": message_type, **data})
        await room.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type}")
    except Exception as e:
        logger.error(f"Failed to send data: {e}")

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
    session = None
    
    silent_connection = asyncio.Event()
    pending_text = {"text": None}
    data_received = asyncio.Event()
    current_sermon_results = []
    last_query = {"text": None}
    all_sermon_results = []

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
    def on_data(data_packet):
        try:
            raw_data = data_packet.data if hasattr(data_packet, 'data') else data_packet
            message = json.loads(raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
            msg_type = message.get('type')
            logger.info(f"Data received: {msg_type}")
            
            if msg_type == 'silent_connection':
                silent_connection.set()
                pending_text["text"] = message.get('textToSpeak')
                if pending_text["text"]:
                    logger.info(f"Text to speak: {pending_text['text'][:50]}...")
                data_received.set()
        except Exception as e:
            logger.error(f"Data parse error: {e}")

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
                if wait_count % 10 == 0:
                    logger.info(f"Still waiting... ({wait_count}s)")

        logger.info(f"User connected: {current_user.identity}")

        logger.info("Creating xAI session...")
        session = AgentSession(llm=FixedXAIRealtimeModel(voice="Ara"))
        
        @session.on("user_input_transcribed")
        def on_user_transcript(event):
            nonlocal current_sermon_results, last_query, all_sermon_results
            if event.is_final and event.transcript:
                user_text = event.transcript
                logger.info(f"USER SAID: {user_text}")
                asyncio.create_task(send_data_message(room, "user_transcript", {"text": user_text}))
                
                user_lower = user_text.lower().strip()
                is_more_request = user_lower in ['more', 'more links', 'show more']
                
                if is_more_request and all_sermon_results and len(all_sermon_results) > 3:
                    additional = all_sermon_results[3:]
                    current_sermon_results = additional
                    logger.info(f"Showing {len(additional)} additional sermon segments")
                    for r in additional[:3]:
                        asyncio.create_task(send_data_message(room, "sermon_reference", {
                            "title": r['title'],
                            "url": r['timestamped_url'],
                            "timestamp": r['start_time'],
                            "text": r['text'][:200]
                        }))
                else:
                    results = search_sermons(user_text, 6)
                    all_sermon_results = results
                    current_sermon_results = results[:3]
                    last_query["text"] = user_text
                    if results:
                        logger.info(f"Found {len(results)} sermon segments, showing first 3")
                        for r in results[:3]:
                            asyncio.create_task(send_data_message(room, "sermon_reference", {
                                "title": r['title'],
                                "url": r['timestamped_url'],
                                "timestamp": r['start_time'],
                                "text": r['text'][:200]
                            }))
        
        @session.on("conversation_item_added")
        def on_conversation_item(event):
            nonlocal current_sermon_results
            try:
                item = event.item
                role = getattr(item, 'role', None)
                logger.info(f"CONVERSATION ITEM: role={role}")
                
                if role == 'assistant':
                    text = ""
                    content = getattr(item, 'content', None)
                    if content:
                        if isinstance(content, list):
                            for c in content:
                                if isinstance(c, str):
                                    text += c
                                elif hasattr(c, 'text'):
                                    text += c.text
                                elif hasattr(c, 'transcript'):
                                    text += c.transcript
                        elif isinstance(content, str):
                            text = content
                    
                    if text:
                        logger.info(f"AGENT SAID: {text[:100]}...")
                        response_with_links = text
                        if current_sermon_results:
                            response_with_links += "\n\nRelated sermon videos:\n"
                            for r in current_sermon_results:
                                response_with_links += f"- {r['title']} ({r['start_time']}): {r['timestamped_url']}\n"
                        asyncio.create_task(send_data_message(room, "agent_transcript", {"text": response_with_links}))
            except Exception as e:
                logger.error(f"Error in conversation_item_added: {e}")

        await session.start(room=room, agent=APBAssistant())
        logger.info("Session started")
        
        logger.info("Waiting 0.5s for data message...")
        try:
            await asyncio.wait_for(data_received.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            logger.info("No data message - normal voice connection")
        
        if silent_connection.is_set() and pending_text["text"]:
            text = pending_text["text"]
            logger.info(f"Speaking text response ({len(text)} chars)...")
            
            await session.generate_reply(
                instructions=f"You are APB answering a user's question. Read this response out loud exactly as written, as if YOU are giving this answer to the user. Do not comment on it or add anything - just speak it naturally as your response: {text}"
            )
            await send_data_message(room, "agent_transcript", {"text": text})
            
            await asyncio.sleep(2)
            await send_data_message(room, "speech_complete", {})
            logger.info("Text-to-speech done")
        else:
            logger.info("Sending greeting...")
            greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
            await session.generate_reply(instructions=f"Say exactly: '{greeting}'")
            await send_data_message(room, "agent_transcript", {"text": greeting})
            logger.info("Greeting sent - LISTENING")
        
        await user_disconnected.wait()

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await room.disconnect()
        logger.info("Disconnected")

async def main():
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting")
    logger.info("=" * 50)
    
    load_sermons()

    while True:
        await run_session()
        logger.info("Session ended. Restarting...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
