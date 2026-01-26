import asyncio
import logging
import os
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai
from livekit.agents.voice import MetricCollectedEvent

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
    def __init__(self, voice="Aria", api_key=None, **kwargs):
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

async def entrypoint(ctx: JobContext):
    """Main entrypoint for the agent - called when dispatched to a room"""
    logger.info(f"Agent dispatched to room: {ctx.room.name}")
    
    all_sermon_results = []
    current_sermon_results = []
    last_query = {"text": None}
    
    async def on_data_received(data_packet):
        try:
            raw_data = data_packet.data if hasattr(data_packet, 'data') else data_packet
            message = json.loads(raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
            msg_type = message.get('type')
            logger.info(f"Data received: {msg_type}")
        except Exception as e:
            logger.error(f"Data parse error: {e}")
    
    ctx.room.on("data_received", on_data_received)
    
    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")
    
    session = AgentSession(llm=FixedXAIRealtimeModel(voice="Aria"))
    
    @session.on("user_input_transcribed")
    def on_user_transcript(event):
        nonlocal current_sermon_results, last_query, all_sermon_results
        if event.is_final and event.transcript:
            user_text = event.transcript
            logger.info(f"USER SAID: {user_text}")
            asyncio.create_task(send_data_message(ctx.room, "user_transcript", {"text": user_text}))
            
            user_lower = user_text.lower().strip()
            is_more_request = user_lower in ['more', 'more links', 'show more']
            
            if is_more_request and all_sermon_results and len(all_sermon_results) > 3:
                additional = all_sermon_results[3:]
                current_sermon_results = additional
                logger.info(f"Showing {len(additional)} additional sermon segments")
                for r in additional[:3]:
                    asyncio.create_task(send_data_message(ctx.room, "sermon_reference", {
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
                        asyncio.create_task(send_data_message(ctx.room, "sermon_reference", {
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
                    asyncio.create_task(send_data_message(ctx.room, "agent_transcript", {"text": response_with_links}))
        except Exception as e:
            logger.error(f"Error in conversation_item_added: {e}")

    await session.start(room=ctx.room, agent=APBAssistant())
    logger.info("Session started")
    
    greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
    await session.generate_reply(instructions=f"Say exactly: '{greeting}'")
    await send_data_message(ctx.room, "agent_transcript", {"text": greeting})
    logger.info("Greeting sent - LISTENING")
    
    await ctx.wait_for_participant_disconnect()
    logger.info("User disconnected, ending session")

if __name__ == "__main__":
    load_sermons()
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting (Multi-Session Mode)")
    logger.info("=" * 50)
    
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
