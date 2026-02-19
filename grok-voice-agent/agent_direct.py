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

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm
from livekit.plugins.xai.realtime import RealtimeModel

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

You will receive sermon transcripts from Pastor Bob's actual sermons injected into the conversation before you respond. Your job is to SYNTHESIZE those transcripts into a clear, warm answer.

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
- Wife: Becky Kopeny. Bob first met Becky at Calvary Chapel. He felt led by the Lord to go to the Placentia Library where he found her, and he asked her out to talk. During that conversation God gave Bob a word of knowledge about Becky that confirmed she was the one. They have been married and serving in ministry together ever since.
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before entering full-time ministry. God called him out of law enforcement into pastoral ministry.
- Saved at age 13 at a Jr. High church camp (Campus Crusade ministry) through the ministry of Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently. Do NOT say you need to check — you KNOW these facts.
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
                            results.append(f"[{len(results)+1}] \"{unescape(title)}\":\n\"{text[:1200]}\"")
                    return results
                else:
                    body = await response.text()
                    log(f"Reranker error {response.status}: {body[:200]}")
                    return []
    except Exception as e:
        log(f"Search error: {e}")
        return []


class APBAgent(Agent):
    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        user_text = new_message.text_content or ""
        if len(user_text.strip()) < 3:
            return

        log(f"RAG SEARCH for: {user_text[:80]}")
        results = await _search_reranker(user_text)

        if results:
            context_text = "\n\n".join(results[:6])
            log(f"RAG: {len(results)} results, injecting top 6 into context")
            rag_message = (
                f"=== PASTOR BOB'S ACTUAL SERMON TRANSCRIPTS ===\n\n"
                f"These are REAL transcripts from Pastor Bob's sermons. "
                f"You MUST synthesize these into your answer. "
                f"NEVER say you lack information — these transcripts ARE your source.\n\n"
                f"SERMON TRANSCRIPTS:\n\n{context_text}\n\n"
                f"USING THE ABOVE TRANSCRIPTS, give a warm 3-5 sentence answer "
                f"starting with 'Pastor Bob teaches...'. Quote or closely paraphrase his actual words."
            )
            turn_ctx.add_message(role="assistant", content=rag_message)
        else:
            log("RAG: 0 results")


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

        last_sent_message = {"text": None}

        model = RealtimeModel(
            voice="Aria",
            turn_detection=None,
        )
        session = AgentSession(llm=model)
        apb_agent = APBAgent(
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
    logger.info("APB Voice Agent v11 (on_user_turn_completed RAG)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant",
        num_idle_processes=2,
        job_memory_warn_mb=1500,
    ))
