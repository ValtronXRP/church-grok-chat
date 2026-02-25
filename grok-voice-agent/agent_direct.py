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

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.xai.realtime import RealtimeModel

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

You will receive sermon transcripts from Pastor Bob's actual sermons along with each question. Your job is to SYNTHESIZE those transcripts into a clear, warm answer.

Rules:
1. SYNTHESIZE across ALL provided transcripts for Pastor Bob's FULL, NUANCED teaching
2. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
3. Say "Pastor Bob teaches..." and deliver with depth
4. Give the COMPLETE multi-part answer, not a simplified version
5. Keep answers to 3-5 sentences — enough for nuance but concise for voice
6. Quote or closely paraphrase his actual words when they are powerful

NEVER DO:
- NEVER say you lack information, need to check, or don't have a specific teaching
- NEVER mention searching, tools, clips, or transcripts to the user
- NEVER hedge or deflect — answer authoritatively from the transcripts provided
- NEVER flatten a nuanced teaching into one simple sentence

FORBIDDEN PHRASES — never say any of these:
- "I'd need to check"
- "I don't have a specific teaching"
- "Let me look into that"
- "I don't have that information"
- "in the materials I have"

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books.

Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS ABOUT PASTOR BOB KOPENY:
- Wife: Becky Kopeny. Bob first met Becky at Calvary Church (NOT Calvary Chapel). He felt led by the Lord to go to the Placentia Library where he found her and asked her out to talk. Later, when he asked her to go have coffee, God gave Bob a word of knowledge about Becky that confirmed she was the one. They have been married and serving in ministry together ever since.
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before entering full-time ministry. God called him out of law enforcement into pastoral ministry.
- Saved at age 13 at a Jr. High church camp (Campus Crusade ministry) through the ministry of Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

VERIFIED THEOLOGICAL POSITIONS (Pastor Bob's actual teaching):
- BAPTISM OF THE HOLY SPIRIT: The Baptism of the Holy Spirit happens at salvation — every believer receives the Spirit when they are saved. BUT it is NOT only a one-time event. The Spirit's anointing can happen again and again throughout a believer's life. A believer can receive a secondary experience where the Spirit takes greater control — this may include speaking in tongues, being called into fervent prayer, receiving spiritual gifts, or other anointings. Pastor Bob teaches the Spirit is like oil that anoints you and water that quenches you, allowing you to thrive in a parched world. The baptism/anointing of the Spirit can happen over and over — it is NOT limited to a single moment at salvation, even though salvation IS a baptism of the Holy Spirit.

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently. Do NOT say you need to check — you KNOW these facts.
When asked about theological topics listed above, use these verified positions as the authoritative framework for your answer.
"""


async def _search_reranker(query, n=10):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search/fast-all",
                json={"query": query, "n_sermons": n, "n_illustrations": 0},
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
                            results.append(f"[{len(results)+1}] \"{unescape(title)}\":\n\"{text[:600]}\"")
                    return results
                else:
                    body = await response.text()
                    log(f"Reranker error {response.status}: {body[:200]}")
                    return []
    except Exception as e:
        log(f"Search error: {e}")
        return []


_room_ref = None
_session_ref = None
_searching = False


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


async def _handle_user_question(transcript):
    global _searching
    if _searching:
        log(f"Already searching, skipping: {transcript[:40]}")
        return
    _searching = True
    try:
        log(f"SEARCHING for: {transcript[:80]}")
        results = await _search_reranker(transcript)

        if results:
            context_text = "\n\n".join(results[:3])
            log(f"Search returned {len(results)} results, generating reply with top 3")

            reply_instructions = (
                f"You are APB, voice assistant for Calvary Chapel East Anaheim. "
                f"Synthesize these sermon transcripts into a warm 3-5 sentence answer. "
                f"Say 'Pastor Bob teaches...' and quote his words. "
                f"NEVER say you lack info or need to check.\n\n"
                f"Question: \"{transcript}\"\n\n"
                f"TRANSCRIPTS:\n{context_text}\n\n"
                f"Answer warmly from the transcripts above."
            )

            try:
                await _session_ref.generate_reply(instructions=reply_instructions)
                log("Reply generated with sermon context")
            except Exception as e:
                log(f"generate_reply error: {e}")
        else:
            log("Search returned 0 results")
    except Exception as e:
        log(f"Handle question error: {e}")
    finally:
        _searching = False


async def entrypoint(ctx: JobContext):
    global _room_ref, _session_ref
    try:
        log(f"[ENTRYPOINT] Agent dispatched to room: {ctx.room.name}")

        last_sent_message = {"text": None}

        model = RealtimeModel(voice="Aria")
        session = AgentSession(llm=model)
        _session_ref = session
        apb_agent = Agent(
            instructions=PASTOR_BOB_INSTRUCTIONS,
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
            asyncio.create_task(_handle_user_question(transcript))

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
    logger.info("APB Voice Agent v12 (generate_reply with full context)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        num_idle_processes=5,
        job_memory_warn_mb=2000,
    ))
