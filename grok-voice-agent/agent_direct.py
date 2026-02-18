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

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, RunContext
from livekit.plugins.xai.realtime import RealtimeModel
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

You will be given sermon transcripts from Pastor Bob's actual sermons along with each question. Your job is to SYNTHESIZE those transcripts into a clear, warm answer.

Rules:
1. SYNTHESIZE across ALL provided transcripts for Pastor Bob's FULL, NUANCED teaching
2. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
3. Say "Pastor Bob teaches..." and deliver with depth
4. Give the COMPLETE multi-part answer, not a simplified version
5. Keep answers to 3-5 sentences — enough for nuance but concise for voice

NEVER DO:
- NEVER say you lack information, need to check, or don't have a specific teaching
- NEVER mention searching, tools, clips, or transcripts to the user
- NEVER hedge or deflect — answer authoritatively from the transcripts provided
- NEVER flatten a nuanced teaching into one simple sentence

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books. This is CRITICAL.
Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS ABOUT PASTOR BOB KOPENY:
- Wife: Becky Kopeny. Bob first met Becky at Calvary Chapel. He felt led by the Lord to go to the Placentia Library where he found her, and he asked her out to talk. During that conversation God gave Bob a word of knowledge about Becky that confirmed she was the one. They have been married and serving in ministry together ever since.
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before entering full-time ministry. God called him out of law enforcement into pastoral ministry.
- Saved at age 13 at a Jr. High church camp (Campus Crusade ministry) through the ministry of Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently and with detail. Do NOT say you need to check — you KNOW these facts.
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
                    for r in data.get('sermons', []):
                        title = r.get('title', 'Sermon')
                        text = r.get('text', '')
                        if text and len(text) > 50:
                            results.append({
                                'title': unescape(title),
                                'text': text.strip(),
                                'score': r.get('rerank_score', r.get('score', 0)),
                                'url': r.get('timestamped_url', r.get('url', '')),
                                'start_time': r.get('start_time', '')
                            })
                    return results
                else:
                    body = await response.text()
                    logger.warning(f"Reranker search {response.status}: {body[:200]}")
    except Exception as e:
        logger.warning(f"Reranker search error: {e}")
    return []


async def _do_search(query):
    results = await _search_reranker(query, n=10)
    seen = set()
    merged = []
    for r in results:
        key = r.get('text', '')[:100]
        if key not in seen and len(r.get('text', '')) > 50:
            seen.add(key)
            merged.append(r)
    return merged


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


async def entrypoint(ctx: JobContext):
    global _room_ref
    try:
        log(f"[ENTRYPOINT] Agent dispatched to room: {ctx.room.name}")
        logger.info(f"[ENTRYPOINT] Agent dispatched to room: {ctx.room.name}")

        last_sent_message = {"text": None}
        is_searching = {"active": False}

        turn_detection = ServerVad(
            type="server_vad",
            threshold=0.8,
            prefix_padding_ms=500,
            silence_duration_ms=800,
            create_response=False,
            interrupt_response=False,
        )

        model = RealtimeModel(voice="Aria", turn_detection=turn_detection)
        session = AgentSession(llm=model)
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
            if not transcript or len(transcript) < 10:
                return
            if is_searching["active"]:
                logger.info(f"Already searching, skipping: {transcript[:60]}")
                return
            log(f"USER SAID: {transcript[:80]}")
            asyncio.create_task(_send_data_message("user_transcript", {"text": transcript}))
            asyncio.create_task(_search_and_reply(session, transcript, is_searching))

        async def _search_and_reply(session, query, is_searching):
            is_searching["active"] = True
            try:
                log(f"SEARCHING for: {query[:60]}")
                merged = await _do_search(query)
                log(f"Search returned {len(merged)} results for: {query[:60]}")

                parts = []
                for i, r in enumerate(merged[:3]):
                    title = r.get('title', 'Sermon')
                    text = r.get('text', '')
                    if text and len(text) > 50:
                        parts.append(f"{title}: {text[:300]}")

                if parts:
                    search_context = chr(10).join(parts)
                    reply_instructions = f"""User asked: "{query}"
Pastor Bob's sermon excerpts:
{search_context}
Synthesize into 3-5 warm sentences starting with "Pastor Bob teaches...""""
                else:
                    reply_instructions = f"""User asked: "{query}"
Give a warm 3-5 sentence answer based on biblical teaching. Start with "Based on biblical teaching..." Never say you lack information."""

                log(f"Generating reply with {len(parts)} transcript segments ({len(reply_instructions)} chars)")
                try:
                    await session.generate_reply(instructions=reply_instructions)
                    log("Reply generation started")
                except Exception as gen_err:
                    log(f"generate_reply error: {gen_err}")

            except Exception as e:
                log(f"Search/reply error: {e}")
                import traceback
                log(traceback.format_exc())
                try:
                    await session.generate_reply(
                        instructions=f'The user asked: "{query}". Give a warm, helpful answer based on general Calvary Chapel biblical teaching in 3-5 sentences.'
                    )
                except Exception:
                    pass
            finally:
                is_searching["active"] = False

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
    logger.info("APB Voice Agent v9 (reranker search, no xAI collection)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
