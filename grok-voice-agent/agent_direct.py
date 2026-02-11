import asyncio
import logging
import os
import json
import aiohttp
import re
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents.llm import ChatContext
from livekit.plugins import openai

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://localhost:5050')

PINNED_STORY_CLIPS = {
    "becky_story": {
        "keywords": ["becky", "wife", "how did bob meet", "how did pastor bob meet", "how they met",
                      "bob and becky", "bob meet becky", "married", "engagement", "how bob met",
                      "love story", "bob's wife", "pastor bob's wife"],
        "clips": [
            {
                "title": "How To Press On (03/26/2017)",
                "url": "https://www.youtube.com/watch?v=sGIJP13TxPQ",
                "timestamped_url": "https://www.youtube.com/watch?v=sGIJP13TxPQ&t=2382s",
                "start_time": "39:42",
                "video_id": "sGIJP13TxPQ",
                "text": "Pastor Bob shares the full story of how he met Becky - from meeting her briefly at church, to God putting her name in his mind at the intersection of Chapman and Kramer while driving to seminary, to the Lord revealing she had gotten engaged the night before, to God telling him to propose three weeks after their first date."
            }
        ]
    },
    "testimony": {
        "keywords": ["testimony", "how was bob saved", "when was bob saved", "how did bob get saved",
                      "bob's testimony", "bob come to christ", "bob receive christ",
                      "fred", "jeff maples", "gene schaeffer", "jr high camp", "8th grade"],
        "clips": [
            {
                "title": "Be Faithful - 2 Timothy 1",
                "url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "timestamped_url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "start_time": "",
                "video_id": "72R6uNs2ka4",
                "text": "Pastor Bob shares his testimony - Jeff Maples and Gene Schaeffer shared Christ with him at a Jr. High church camp when he was 13."
            }
        ]
    }
}

def detect_personal_story(query):
    q = query.lower()
    matches = []
    for story_key, story in PINNED_STORY_CLIPS.items():
        for kw in story["keywords"]:
            if kw in q:
                matches.append(story_key)
                break
    return matches

def filter_results(results):
    filtered = []
    for r in results:
        title = (r.get('title') or '').lower()
        text = (r.get('text') or '').lower()
        if title in ['unknown sermon', 'unknown', '']:
            continue
        if any(ind in title for ind in ['worship song', 'hymn', 'music video', 'singing', 'choir']):
            continue
        if len(text) < 50:
            continue
        filtered.append(r)
    return filtered

async def do_sermon_search(query, n_results=5):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search/fast",
                json={"query": query, "n_results": n_results},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    sermons = data.get('results', [])
                    logger.info(f"Fast search: {len(sermons)} sermons ({data.get('timing_ms', 0)}ms)")
                    return sermons
    except Exception as e:
        logger.warning(f"Fast search error: {e}")
    return []


class FixedXAIRealtimeModel(openai.realtime.RealtimeModel):
    def __init__(self, voice="Aria", api_key=None, **kwargs):
        api_key = api_key or os.environ.get("XAI_API_KEY")
        super().__init__(
            base_url="wss://api.x.ai/v1/realtime",
            model="",
            voice=voice,
            api_key=api_key,
            modalities=["audio", "text"],
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": False,
                "interrupt_response": True,
            },
            **kwargs
        )

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== YOUR #1 JOB ===
When sermon segments are provided in the conversation, you MUST read them and answer the user's question using that content. Say "Pastor Bob teaches..." and share what he says. The segments are his REAL words from REAL sermons. Always give a substantive answer.

=== BANNED PHRASES (NEVER SAY THESE) ===
- "I don't have a specific teaching"
- "I'd need to check"
- "I don't have that in my materials"
- "The sermon segments provided don't directly address"
- Any variation of hedging or saying you lack information

=== WHAT TO DO ===
- Sermon segments provided? USE THEM. Say "Pastor Bob teaches..." and share what he says.
- Related but not exact? USE whatever is relevant AND supplement with solid biblical teaching.
- No segments? Give a solid biblical answer. Do NOT mention the absence of segments.
- The user does NOT know about segments. Just answer warmly.

=== VERIFIED FACTS ABOUT PASTOR BOB ===
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp
- Pastors Calvary Chapel East Anaheim

=== RULES ===
1. NEVER invent stories or teachings.
2. Be warm, helpful, and conversational.
3. NEVER mention clips, sidebar, or videos in your verbal response.
4. Bible book names: Say "First John" NOT "one John". Always spell out First, Second, Third.
"""


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
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}
    search_lock = asyncio.Lock()

    session = AgentSession(llm=FixedXAIRealtimeModel(voice="Aria"))
    apb_agent = Agent(instructions=PASTOR_BOB_INSTRUCTIONS)

    def on_data_received(data_packet):
        try:
            raw_data = data_packet.data if hasattr(data_packet, 'data') else data_packet
            message = json.loads(raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
            msg_type = message.get('type')
            logger.info(f"Data received: {msg_type}")

            if msg_type == 'user_query' and message.get('text'):
                user_text = message['text'].strip()
                if user_text and len(user_text) > 2:
                    logger.info(f"GOT USER QUERY VIA DATA: {user_text[:80]}")
                    asyncio.create_task(search_and_respond(user_text))
            elif msg_type == 'silent_connection':
                text_to_speak = message.get('textToSpeak', '')
                if text_to_speak:
                    logger.info(f"Got text to speak: {text_to_speak[:80]}")
                    asyncio.create_task(session.generate_reply(instructions=f"Say exactly: '{text_to_speak[:500]}'"))
        except Exception as e:
            logger.error(f"Data parse error: {e}")

    ctx.room.on("data_received", on_data_received)

    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")

    async def search_and_respond(user_text):
        async with search_lock:
            logger.info(f"Searching sermons for: '{user_text[:80]}'")

            sermons_raw = await do_sermon_search(user_text, 5)
            sermons = filter_results(sermons_raw)

            story_matches = detect_personal_story(user_text)
            if story_matches:
                pinned = []
                pinned_vids = set()
                for sk in story_matches:
                    story = PINNED_STORY_CLIPS.get(sk)
                    if story:
                        for clip in story["clips"]:
                            pinned.append(clip)
                            pinned_vids.add(clip["video_id"])
                sermons = [r for r in sermons if r.get("video_id") not in pinned_vids]
                sermons = pinned + sermons

            for r in sermons[:3]:
                await send_data_message(ctx.room, "sermon_reference", {
                    "title": r.get('title', 'Sermon'),
                    "url": r.get('timestamped_url', r.get('url', '')),
                    "timestamp": r.get('start_time', ''),
                    "text": r.get('text', '')[:200]
                })

            sermon_context = ""
            if sermons:
                sermon_context = f'\n\nThe user asked: "{user_text}"\n\nHere is Pastor Bob\'s ACTUAL sermon content on this topic. You MUST use this to answer:\n\n'
                for i, r in enumerate(sermons[:5]):
                    sermon_context += f'[Segment {i+1}] "{r.get("title", "Sermon")}":\n'
                    sermon_context += f'"{r.get("text", "")[:800]}"\n\n'
                sermon_context += "Answer using the sermon content above. Say 'Pastor Bob teaches...' and share what he actually says.\n"

            reply_input = user_text
            if sermon_context:
                reply_input = sermon_context + "\nUser's question: " + user_text

            logger.info(f"Generating reply with {len(sermons)} sermon segments ({len(reply_input)} chars)")
            await session.generate_reply(user_input=reply_input)
            logger.info("generate_reply called with user_input")

    @session.on("conversation_item_added")
    def on_conversation_item(event):
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
                                text += (c.text or '')
                            elif hasattr(c, 'transcript'):
                                text += (c.transcript or '')
                    elif isinstance(content, str):
                        text = content
                if not text and hasattr(item, 'text'):
                    text = item.text or ''
                text = text.strip()

                if text and text != last_sent_message["text"]:
                    last_sent_message["text"] = text
                    logger.info(f"AGENT SAID: {text[:100]}...")
                    asyncio.create_task(send_data_message(ctx.room, "agent_transcript", {"text": text}))
        except Exception as e:
            logger.error(f"Error in conversation_item_added: {e}")

    await session.start(room=ctx.room, agent=apb_agent)
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
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
