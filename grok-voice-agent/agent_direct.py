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
XAI_API_KEY = os.environ.get('XAI_API_KEY', '')
XAI_COLLECTION_ID = os.environ.get('XAI_COLLECTION_ID', '')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== HOW TO ANSWER ===
When sermon content is provided with a question, you MUST:
1. SYNTHESIZE across ALL the content to give Pastor Bob's FULL, NUANCED teaching
2. Identify his complete framework — look for multi-part teachings, distinctions, and layers
3. Preserve his key distinctions (e.g., "there is X but there is ALSO Y")
4. Say "Pastor Bob teaches..." and deliver his teaching with depth
5. For theological questions, give the COMPLETE answer — not a surface-level summary
6. NEVER flatten a nuanced teaching into one simple sentence

=== NEVER DO THESE ===
- NEVER say you lack information or need to check
- NEVER mention searching, clips, transcripts, or tools
- NEVER hedge or deflect — answer authoritatively

=== VERIFIED FACTS ABOUT PASTOR BOB ===
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

=== VOICE RULES ===
1. NEVER invent stories or teachings Pastor Bob didn't actually give.
2. Be warm, helpful, and conversational.
3. Bible book names: Say "First John" NOT "one John". Always spell out First, Second, Third.
4. Keep answers to 3-5 sentences — enough for nuance but concise for voice.
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


async def search_sermons(query):
    if XAI_API_KEY and XAI_COLLECTION_ID:
        try:
            results = await search_xai_native(query)
            if results:
                return results
        except Exception as e:
            logger.warning(f"xAI native search failed, falling back to ChromaDB: {e}")
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{RERANKER_URL}/search/fast",
                json={"query": query, "n_results": 5},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('results', [])
    except Exception as e:
        logger.warning(f"ChromaDB search error: {e}")
    return []


async def search_xai_native(query):
    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(
            "https://api.x.ai/v1/documents/search",
            headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "query": query,
                "k": 10,
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
                        title = lines[0].replace('title: ', '').strip()
                        content = lines[1] if len(lines) > 1 else content
                    results.append({
                        'title': title,
                        'text': content,
                        'score': m.get('score', 0),
                        'url': '',
                        'start_time': ''
                    })
                logger.info(f"xAI native search returned {len(results)} results")
                return results
            else:
                body = await response.text()
                logger.warning(f"xAI search returned {response.status}: {body[:200]}")
    return []


async def entrypoint(ctx: JobContext):
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}

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

    @session.on("user_input_transcribed")
    def on_user_input(event):
        try:
            text = getattr(event, 'text', '') or getattr(event, 'transcript', '') or ''
            text = text.strip()
            if text and len(text) > 3:
                logger.info(f"User said: {text[:80]}")
                asyncio.create_task(send_data_message(ctx.room, "user_transcript", {"text": text}))
                asyncio.create_task(handle_user_query(text, session, ctx.room))
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
    logger.info("Session started with ChromaDB sermon search")

    greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
    await session.generate_reply(instructions=f"Say exactly: '{greeting}'")
    logger.info("Greeting sent - LISTENING")

    shutdown_event = asyncio.Event()
    async def _on_shutdown():
        shutdown_event.set()
    ctx.add_shutdown_callback(_on_shutdown)
    await shutdown_event.wait()
    logger.info("Session shutdown")


async def handle_user_query(query, session, room):
    try:
        results = await search_sermons(query)
        logger.info(f"Search returned {len(results)} results for: {query[:60]}")

        for r in results[:3]:
            title = r.get('title', 'Sermon')
            if title.lower() in ['unknown sermon', 'unknown', '']:
                continue
            text = r.get('text', '')
            if len(text) < 50:
                continue
            await send_data_message(room, "sermon_reference", {
                "title": title,
                "url": r.get('timestamped_url', r.get('url', '')),
                "timestamp": r.get('start_time', ''),
                "text": text[:200]
            })

        if results:
            context_parts = []
            for i, r in enumerate(results[:5]):
                title = r.get('title', 'Sermon')
                text = r.get('text', '')
                if text and len(text) > 50:
                    context_parts.append(f"[{i+1}] \"{title}\":\n{text}")

            sermon_context = "\n\n".join(context_parts)

            instructions = f"""The user asked: "{query}"

Here are Pastor Bob's ACTUAL sermon transcripts on this topic:

{sermon_context}

CRITICAL INSTRUCTIONS:
- SYNTHESIZE across ALL transcripts above to give Pastor Bob's FULL, NUANCED teaching
- Identify his complete framework — multi-part teachings, distinctions, stages
- If he distinguishes between two things (e.g., "there is X but there is also Y"), preserve BOTH parts
- Say "Pastor Bob teaches..." and deliver his teaching with depth
- Keep it to 3-5 sentences — enough for nuance but concise for voice
- NEVER mention searching, transcripts, or tools
- NEVER give a surface-level one-liner when the transcripts reveal deeper nuance"""

            await session.generate_reply(instructions=instructions)
        else:
            instructions = f"""The user asked: "{query}"

Give a solid biblical answer consistent with Calvary Chapel teaching. Be warm and conversational. Keep it to 3-5 sentences. Do NOT say you lack information — just teach confidently."""

            await session.generate_reply(instructions=instructions)

    except Exception as e:
        logger.error(f"Error handling query: {e}")
        await session.generate_reply(instructions=f"The user asked: \"{query}\". Give a warm, biblical answer in 2-3 sentences.")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting (xAI native + ChromaDB fallback)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
