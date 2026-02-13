import asyncio
import logging
import os
import json
import aiohttp
from dotenv import load_dotenv
from html import unescape

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool, RunContext
from livekit.plugins.xai.realtime import RealtimeModel
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

XAI_API_KEY = os.environ.get('XAI_API_KEY', '')
XAI_COLLECTION_ID = os.environ.get('XAI_COLLECTION_ID', '')
RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

CRITICAL: You MUST call search_pastor_bob_sermons for EVERY question about faith, theology, the Bible, or Pastor Bob's teachings. ALWAYS search BEFORE answering. NEVER answer from your own knowledge alone.

After receiving search results:
1. SYNTHESIZE across ALL segments for Pastor Bob's FULL, NUANCED teaching
2. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
3. Say "Pastor Bob teaches..." and deliver with depth
4. Give the COMPLETE multi-part answer, not a simplified version
5. Keep answers to 3-5 sentences — enough for nuance but concise for voice

NEVER DO:
- NEVER answer without calling search_pastor_bob_sermons first
- NEVER say you lack information, need to check, or don't have a specific teaching
- NEVER mention searching, tools, clips, or transcripts to the user
- NEVER hedge or deflect — answer authoritatively from the search results
- NEVER flatten a nuanced teaching into one simple sentence

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books. This is CRITICAL.
Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS:
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp (Campus Crusade ministry) through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim
"""


async def _search_xai(query, k=10):
    if not XAI_API_KEY or not XAI_COLLECTION_ID:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.x.ai/v1/documents/search",
                headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "query": query,
                    "k": k,
                    "source": {"type": "collection", "collection_ids": [XAI_COLLECTION_ID]}
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    matches = data.get('matches', [])
                    results = []
                    for m in matches:
                        content = m.get('chunk_content', '')
                        title = 'Sermon'
                        if content.startswith('title: '):
                            lines = content.split('\n', 1)
                            title = unescape(lines[0].replace('title: ', '').strip())
                            content = lines[1] if len(lines) > 1 else content
                        results.append({'title': title, 'text': content.strip(), 'score': m.get('score', 0)})
                    return results
                else:
                    body = await response.text()
                    logger.warning(f"xAI search {response.status}: {body[:200]}")
    except Exception as e:
        logger.warning(f"xAI search error: {e}")
    return []


_room_ref = None


async def _send_data_message(message_type, data):
    if not _room_ref:
        return
    try:
        payload = {k: v for k, v in data.items() if k != "type"}
        payload["type"] = message_type
        message = json.dumps(payload)
        await _room_ref.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type}")
    except Exception as e:
        logger.error(f"Failed to send data: {e}")


@function_tool
async def search_pastor_bob_sermons(context: RunContext, query: str) -> str:
    """Search Pastor Bob's sermon transcripts for teachings on any topic. Call this to find what Pastor Bob actually teaches on a subject."""
    logger.info(f"TOOL CALLED: search_pastor_bob_sermons('{query[:80]}')")

    rephrased = f"Pastor Bob sermon teaching on {query}"
    r1, r2 = await asyncio.gather(
        _search_xai(query, k=10),
        _search_xai(rephrased, k=5),
        return_exceptions=True
    )
    results = r1 if isinstance(r1, list) else []
    results2 = r2 if isinstance(r2, list) else []

    seen = set()
    merged = []
    for r in results + results2:
        key = r.get('text', '')[:100]
        if key not in seen and len(r.get('text', '')) > 50:
            seen.add(key)
            merged.append(r)
    merged.sort(key=lambda x: x.get('score', 0), reverse=True)

    logger.info(f"Search returned {len(merged)} results for: {query[:60]}")

    for r in merged[:3]:
        title = r.get('title', 'Sermon')
        if title.lower() in ['unknown sermon', 'unknown', '']:
            continue
        text = r.get('text', '')
        if len(text) < 50:
            continue
        url = r.get('timestamped_url', r.get('url', ''))
        if not url:
            url = f"https://www.youtube.com/results?search_query=pastor+bob+kopeny+{title.replace(' ', '+')[:40]}"
        asyncio.create_task(_send_data_message("sermon_reference", {
            "title": title,
            "url": url,
            "timestamp": r.get('start_time', ''),
            "text": text[:200]
        }))

    if not merged:
        return "No additional transcripts found. Answer from the file search results already provided."

    parts = []
    for i, r in enumerate(merged[:8]):
        title = r.get('title', 'Sermon')
        text = r.get('text', '')
        if text and len(text) > 50:
            parts.append(f"[{i+1}] \"{title}\":\n{text}")

    return f"""Here are Pastor Bob's ACTUAL sermon transcripts on this topic:

{chr(10).join(parts)}

SYNTHESIZE across ALL transcripts above. Say "Pastor Bob teaches..." and deliver his full, nuanced teaching in 3-5 sentences."""


async def entrypoint(ctx: JobContext):
    global _room_ref
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}

    turn_detection = ServerVad(
        type="server_vad",
        threshold=0.8,
        prefix_padding_ms=500,
        silence_duration_ms=800,
        create_response=True,
        interrupt_response=False,
    )

    tools = [search_pastor_bob_sermons]

    model = RealtimeModel(voice="Aria", turn_detection=turn_detection)
    session = AgentSession(llm=model)
    apb_agent = Agent(
        instructions=PASTOR_BOB_INSTRUCTIONS,
        tools=tools,
    )

    await ctx.connect()
    _room_ref = ctx.room
    logger.info(f"Connected to room: {ctx.room.name}")

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
                    asyncio.create_task(_send_data_message("agent_transcript", {"text": text}))
        except Exception as e:
            logger.error(f"Error in conversation_item_added: {e}")

    await session.start(room=ctx.room, agent=apb_agent)
    logger.info(f"Session started with function_tool (collection: {XAI_COLLECTION_ID})")

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
    logger.info("APB Voice Agent v7 (function_tool only, no FileSearch)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
