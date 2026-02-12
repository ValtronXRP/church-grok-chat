import asyncio
import logging
import os
import json
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.xai.realtime import RealtimeModel
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://localhost:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== BANNED PHRASES (NEVER SAY THESE) ===
- "I don't have a specific teaching"
- "I'd need to check"
- "I don't have that in my materials"
- "I don't have specific sermon transcripts"
- Any variation of hedging or saying you lack information

=== VERIFIED FACTS ABOUT PASTOR BOB ===
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

=== RULES ===
1. NEVER invent stories or teachings Pastor Bob didn't actually give.
2. Be warm, helpful, and conversational.
3. NEVER mention clips, sidebar, videos, or search tools in your verbal response.
4. Bible book names: Say "First John" NOT "one John". Always spell out First, Second, Third.
5. Keep answers concise for voice - 2-4 sentences unless the user asks for more detail.
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


async def fast_sermon_search(query):
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{RERANKER_URL}/search/fast",
                json={"query": query, "n_results": 5},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    timing = data.get('timing_ms', 0)
                    logger.info(f"Fast search: {len(results)} results ({timing}ms)")
                    return results
    except Exception as e:
        logger.warning(f"Fast search error: {e}")
    return []


async def entrypoint(ctx: JobContext):
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}
    responding = asyncio.Lock()

    turn_detection = ServerVad(
        type="server_vad",
        threshold=0.5,
        prefix_padding_ms=300,
        silence_duration_ms=700,
        create_response=False,
        interrupt_response=True,
    )

    model = RealtimeModel(voice="Aria", turn_detection=turn_detection)
    session = AgentSession(llm=model)
    apb_agent = Agent(instructions=PASTOR_BOB_INSTRUCTIONS)

    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")

    async def handle_user_query(user_text):
        async with responding:
            logger.info(f"Processing query: '{user_text[:80]}'")

            asyncio.create_task(send_data_message(ctx.room, "user_transcript", {"text": user_text}))

            sermons = await fast_sermon_search(user_text)

            valid = []
            for r in sermons:
                title = (r.get('title') or '').lower()
                text = r.get('text', '')
                if title in ['unknown sermon', 'unknown', '']:
                    continue
                if len(text) < 50:
                    continue
                valid.append(r)

            for r in valid[:3]:
                await send_data_message(ctx.room, "sermon_reference", {
                    "title": r.get('title', 'Sermon'),
                    "url": r.get('timestamped_url', r.get('url', '')),
                    "timestamp": r.get('start_time', ''),
                    "text": r.get('text', '')[:200]
                })

            if valid:
                context = f'The user asked: "{user_text}"\n\nHere are Pastor Bob\'s ACTUAL sermon segments on this topic. You MUST use these to answer. Say "Pastor Bob teaches..." and share what he says:\n\n'
                for i, r in enumerate(valid[:5]):
                    context += f'[Segment {i+1}] "{r.get("title", "Sermon")}":\n'
                    context += f'"{r.get("text", "")[:600]}"\n\n'
                context += "Now answer the user's question using the sermon content above. Be warm and conversational."
            else:
                context = f'The user asked: "{user_text}"\n\nGive a solid biblical answer consistent with Calvary Chapel teaching. Do NOT say you lack information. Just teach warmly.'

            logger.info(f"Generating reply with {len(valid)} sermon segments")
            await session.generate_reply(instructions=context)
            logger.info("Reply generated")

    @session.on("user_input_transcribed")
    def on_user_input(event):
        try:
            text = getattr(event, 'text', '') or getattr(event, 'transcript', '') or ''
            text = text.strip()
            if text and len(text) > 3:
                logger.info(f"User said: {text[:80]}")
                asyncio.create_task(handle_user_query(text))
        except Exception as e:
            logger.error(f"Error in user_input_transcribed: {e}")

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
