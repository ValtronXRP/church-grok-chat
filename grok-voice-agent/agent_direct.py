import asyncio
import logging
import os
import json
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai

SERMONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sermons_static.json')
SERMON_API_URL = os.environ.get('SERMON_API_URL', 'https://web-production-b652a.up.railway.app')
sermons_data = []

def load_sermons():
    global sermons_data
    try:
        with open(SERMONS_FILE, 'r') as f:
            sermons_data = json.load(f)
        logger.info(f"Loaded {len(sermons_data)} static sermon segments (fallback)")
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

async def search_sermons_api(query, n_results=6):
    """Search ChromaDB API (132K+ segments)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SERMON_API_URL}/api/sermon/search",
                json={"query": query, "n_results": n_results},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    logger.info(f"ChromaDB found {len(results)} segments for: {query}")
                    return results
    except Exception as e:
        logger.warning(f"ChromaDB API error: {e}")
    return None

async def search_illustrations_api(query, n_results=3):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SERMON_API_URL}/api/illustration/search",
                json={"query": query, "n_results": n_results},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    logger.info(f"Found {len(results)} illustrations for: {query}")
                    return results
    except Exception as e:
        logger.warning(f"Illustration API error: {e}")
    return []

def search_sermons_local(query, n_results=5):
    """Fallback: search static JSON (583 segments)"""
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

async def search_sermons(query, n_results=6):
    """Search sermons - try ChromaDB API first, fallback to static"""
    results = await search_sermons_api(query, n_results)
    if results:
        return results
    logger.info(f"Falling back to local search for: {query}")
    return search_sermons_local(query, n_results)

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

ABSOLUTE FACTS - MEMORIZE THESE (NEVER GUESS OR MAKE UP NAMES):
Pastor Bob Kopeny's wife is BECKY. Her name is BECKY KOPENY. NOT Anne, NOT any other name. BECKY.

Pastor Bob has THREE sons:
1. JESSE Kopeny - oldest son, born July 24, 1984
   - Married (wife's name not specified)
   - Has 4 children: Julia (daughter), Lily (daughter), Jonah (son), Jeffrey (son)
2. VALOR Kopeny - middle son, born December 2, 1985
   - Married to STACY
   - Has 1 son: LUCA (born June 1, 2022 - turned 3 in June 2025)
3. CHRISTIAN Kopeny - youngest son, born May 16, 1989
   - Married to HAYLEY
   - Has 1 daughter: CORA (born December 2024 - turned 1 in December 2025)

GRANDCHILDREN SUMMARY:
- Jesse's kids: Julia, Lily, Jonah, Jeffrey
- Valor & Stacy's son: Luca (age 3)
- Christian & Hayley's daughter: Cora (age 1)

If asked about Pastor Bob's family - USE ONLY THESE NAMES. Do not guess or invent names.

CRITICAL - NO HALLUCINATIONS:
- ONLY share stories or examples that are DIRECTLY in the sermon text provided
- NEVER make up or invent stories not in the actual sermon text
- NEVER invent or guess Pastor Bob's theological position on ANY topic — only state positions explicitly in the provided transcript data
- If no specific content is found, say "I don't have a specific teaching from Pastor Bob on that exact topic" and give a general biblical answer WITHOUT attributing it to Pastor Bob
- You may summarize transcript content naturally, but the substance must come from actual data

KEY RULES:
- APB stands for "Ask Pastor Bob"
- Keep responses conversational for voice
- Say "Pastor Bob teaches..." ONLY when you have actual sermon data to back it up
- Otherwise say "The Bible teaches..." without attributing to Pastor Bob

STYLE:
- Warm and welcoming
- Reference videos naturally when available
"""

class APBAssistant(Agent):
    def __init__(self):
        super().__init__(instructions=PASTOR_BOB_INSTRUCTIONS)

async def send_data_message(room, message_type, data):
    try:
        payload = {k: v for k, v in data.items() if k != "type"}
        payload["type"] = message_type
        message = json.dumps(payload)
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
    last_sent_message = {"text": None}
    
    def on_data_received(data_packet):
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
    
    def filter_sermon_results(results):
        """Filter out songs, unknown titles, and non-sermon content"""
        filtered = []
        for r in results:
            title = (r.get('title') or '').lower()
            text = (r.get('text') or '').lower()
            
            # Skip unknown/untitled
            if title in ['unknown sermon', 'unknown', '']:
                continue
            
            # Skip songs/music
            song_indicators = ['worship song', 'hymn', 'music video', 'singing', 'choir']
            if any(ind in title for ind in song_indicators):
                continue
            
            # Skip very short text
            if len(text) < 50:
                continue
            
            # Skip repeated worship phrases (likely lyrics)
            import re
            worship_count = len(re.findall(r'\b(la la|hallelujah|glory glory|praise him)\b', text, re.I))
            if worship_count > 2:
                continue
            
            filtered.append(r)
        return filtered
    
    async def handle_user_query(user_text):
        nonlocal current_sermon_results, last_query, all_sermon_results
        user_lower = user_text.lower().strip()
        is_more_request = user_lower in ['more', 'more links', 'show more', 'more clips']
        
        sermon_context = ""
        
        if is_more_request and all_sermon_results and len(all_sermon_results) > 3:
            additional = all_sermon_results[3:6]
            if additional:
                current_sermon_results = additional
                logger.info(f"Showing {len(additional)} additional sermon segments")
                for r in additional:
                    await send_data_message(ctx.room, "sermon_reference", {
                        "title": r.get('title', 'Sermon'),
                        "url": r.get('timestamped_url', r.get('url', '')),
                        "timestamp": r.get('start_time', ''),
                        "text": r.get('text', '')[:200]
                    })
            else:
                logger.info("No more sermon segments available")
        else:
            results = await search_sermons(user_text, 10)
            filtered_results = filter_sermon_results(results)
            all_sermon_results = filtered_results
            current_sermon_results = filtered_results[:3]
            last_query["text"] = user_text
            
            if filtered_results:
                logger.info(f"Found {len(results)} segments, {len(filtered_results)} after filtering, showing first 3")
                for r in filtered_results[:3]:
                    await send_data_message(ctx.room, "sermon_reference", {
                        "title": r.get('title', 'Sermon'),
                        "url": r.get('timestamped_url', r.get('url', '')),
                        "timestamp": r.get('start_time', ''),
                        "text": r.get('text', '')[:200]
                    })
                
                sermon_context = "\n\nRELEVANT SERMON CONTENT FROM PASTOR BOB:\n"
                for i, r in enumerate(filtered_results[:3]):
                    sermon_context += f"\n[Clip {i+1}] {r.get('title', 'Sermon')} at {r.get('start_time', '')}:\n"
                    sermon_context += f'"{r.get("text", "")[:400]}"\n'
                sermon_context += "\nUse these actual quotes to answer. Say 'Pastor Bob teaches...' when referencing this content."

            illustrations = await search_illustrations_api(user_text, 3)
            if illustrations:
                for ill in illustrations:
                    await send_data_message(ctx.room, "illustration", {
                        "title": ill.get('illustration', ill.get('title', 'Illustration')),
                        "text": ill.get('text', '')[:300],
                        "url": ill.get('video_url', ''),
                        "illustration_type": ill.get('type', ''),
                        "tone": ill.get('tone', '')
                    })
        
        if sermon_context:
            try:
                await session.generate_reply(instructions=sermon_context)
                logger.info("Injected sermon context into LLM response")
            except Exception as e:
                logger.warning(f"Could not inject sermon context: {e}")
    
    @session.on("user_input_transcribed")
    def on_user_transcript(event):
        if event.is_final and event.transcript:
            user_text = event.transcript
            logger.info(f"USER SAID: {user_text}")
            asyncio.create_task(send_data_message(ctx.room, "user_transcript", {"text": user_text}))
            asyncio.create_task(handle_user_query(user_text))
    
    @session.on("conversation_item_added")
    def on_conversation_item(event):
        nonlocal current_sermon_results, last_sent_message
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
                
                if text and text != last_sent_message["text"]:
                    last_sent_message["text"] = text
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
    logger.info("Greeting sent - LISTENING")
    
    shutdown_event = asyncio.Event()
    async def _on_shutdown():
        shutdown_event.set()
    ctx.add_shutdown_callback(_on_shutdown)
    await shutdown_event.wait()
    logger.info("Session shutdown")

if __name__ == "__main__":
    load_sermons()
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting (Multi-Session Mode)")
    logger.info("=" * 50)
    
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
