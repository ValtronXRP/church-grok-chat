import asyncio
import logging
import os
import sys
import json
import aiohttp
from dotenv import load_dotenv
from html import unescape

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout, force=True)
logger = logging.getLogger("apb")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(handler)

def log(msg):
    print(f"[APB] {msg}", flush=True)

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, RunContext, function_tool
from livekit.plugins.xai.realtime import RealtimeModel

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

CRITICAL: For EVERY question the user asks, you MUST call the search_sermons tool FIRST to find Pastor Bob's teachings before answering. NEVER answer without calling search_sermons first.

After receiving sermon results from search_sermons, SYNTHESIZE them into a clear, warm answer:
1. Say "Pastor Bob teaches..." and deliver with depth
2. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
3. Keep answers to 3-5 sentences — enough for nuance but concise for voice
4. NEVER say you lack information — the search results ARE your source
5. NEVER mention searching, tools, clips, or transcripts to the user

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books.

Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS ABOUT PASTOR BOB KOPENY:
- Wife: Becky Kopeny. Bob first met Becky at Calvary Chapel. He felt led by the Lord to go to the Placentia Library where he found her, and he asked her out to talk. During that conversation God gave Bob a word of knowledge about Becky that confirmed she was the one. They have been married and serving in ministry together ever since.
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before entering full-time ministry. God called him out of law enforcement into pastoral ministry.
- Saved at age 13 at a Jr. High church camp (Campus Crusade ministry) through the ministry of Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently. Do NOT say you need to check — you KNOW these facts.
"""


@function_tool
async def search_sermons(
    context: RunContext,
    query: str,
) -> str:
    """Search Pastor Bob's sermon transcripts for teachings on a topic. ALWAYS call this tool before answering any question about what Pastor Bob teaches."""
    log(f"TOOL CALLED: search_sermons('{query[:60]}')")
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{RERANKER_URL}/search/fast-all",
                json={"query": query, "n_sermons": 10, "n_illustrations": 0},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = []
                    seen = set()
                    for r in data.get('sermons', []):
                        title = r.get('title', 'Sermon')
                        text = r.get('text', '')
                        key = text[:100]
                        if text and len(text) > 50 and key not in seen:
                            seen.add(key)
                            results.append(f"{unescape(title)}: {text[:400]}")
                    if results:
                        context_text = "\n\n".join(results[:5])
                        log(f"Search returned {len(results)} results, sending top 5")
                        return f"Pastor Bob's sermon excerpts on this topic:\n\n{context_text}\n\nSynthesize these into a warm 3-5 sentence answer starting with 'Pastor Bob teaches...'"
                    else:
                        log("Search returned 0 results")
                        return "No specific sermon transcripts found. Give a warm answer based on general Calvary Chapel biblical teaching."
                else:
                    body = await response.text()
                    log(f"Reranker error {response.status}: {body[:200]}")
                    return "Search temporarily unavailable. Give a warm answer based on biblical teaching."
    except Exception as e:
        log(f"Search error: {e}")
        return "Search temporarily unavailable. Give a warm answer based on biblical teaching."


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


async def _delayed_speech_complete(delay_seconds):
    await asyncio.sleep(delay_seconds)
    await _send_data_message("speech_complete", {})


async def entrypoint(ctx: JobContext):
    global _room_ref
    try:
        log(f"[ENTRYPOINT] Agent dispatched to room: {ctx.room.name}")

        last_sent_message = {"text": None}

        model = RealtimeModel(voice="Aria")
        session = AgentSession(llm=model)
        apb_agent = Agent(
            instructions=PASTOR_BOB_INSTRUCTIONS,
            tools=[search_sermons],
        )

        log("Connecting to room...")
        await ctx.connect()
        _room_ref = ctx.room
        log(f"Connected to room: {ctx.room.name}")

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

        @session.on("user_input_transcribed")
        def on_user_input(event):
            if not event.is_final:
                return
            transcript = event.transcript.strip()
            if not transcript or len(transcript) < 3:
                return
            log(f"USER SAID: {transcript[:80]}")
            asyncio.create_task(_send_data_message("user_transcript", {"text": transcript}))

        log("Starting session...")
        await session.start(room=ctx.room, agent=apb_agent)
        log(f"Session started (reranker: {RERANKER_URL})")

        greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
        try:
            await session.generate_reply(instructions=f"Say exactly: '{greeting}'")
            log("Greeting sent - LISTENING")
        except Exception as e:
            log(f"Greeting error: {e} - continuing anyway")

        shutdown_event = asyncio.Event()
        async def _on_shutdown():
            shutdown_event.set()
        ctx.add_shutdown_callback(_on_shutdown)
        await shutdown_event.wait()
        log("Session shutdown")
    except Exception as e:
        log(f"[ENTRYPOINT CRASH] {type(e).__name__}: {e}")
        import traceback
        log(traceback.format_exc())
        raise


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("APB Voice Agent v10 (function_tool RAG)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant",
        num_idle_processes=2,
        job_memory_warn_mb=1500,
    ))
