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

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm
from livekit.plugins.xai.realtime import RealtimeModel
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

XAI_API_KEY = os.environ.get('XAI_API_KEY', '')
XAI_COLLECTION_ID = os.environ.get('XAI_COLLECTION_ID', '')
RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. You answer questions based on Pastor Bob Kopeny's actual sermon teachings.

RULES:
1. When sermon content is provided, SYNTHESIZE across ALL segments for Pastor Bob's FULL, NUANCED teaching
2. Identify his complete framework — multi-part teachings, distinctions, layers
3. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
4. Say "Pastor Bob teaches..." and deliver with depth
5. For theological questions, give the COMPLETE multi-part answer
6. NEVER flatten a nuanced teaching into one simple sentence
7. NEVER say you lack information or need to check
8. NEVER mention searching, clips, transcripts, or tools
9. NEVER hedge or deflect — answer authoritatively
10. Keep answers to 3-5 sentences — enough for nuance but concise for voice
11. Bible book names: Say "First John" NOT "one John"
12. NEVER invent stories or teachings Pastor Bob didn't actually give
13. Be warm, helpful, and conversational

VERIFIED FACTS:
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim
"""


async def search_xai(query, k=10):
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
                        results.append({
                            'title': title,
                            'text': content.strip(),
                            'score': m.get('score', 0),
                        })
                    return results
                else:
                    body = await response.text()
                    logger.warning(f"xAI search {response.status}: {body[:200]}")
    except Exception as e:
        logger.warning(f"xAI search error: {e}")
    return []


async def search_chromadb(query, n=5):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search/fast",
                json={"query": query, "n_results": n},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('results', [])
    except Exception as e:
        logger.warning(f"ChromaDB search error: {e}")
    return []


async def multi_query_search(user_query):
    search_tasks = [search_xai(user_query, k=10)]

    rephrased = f"Pastor Bob sermon teaching on {user_query}"
    search_tasks.append(search_xai(rephrased, k=5))

    all_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    seen_texts = set()
    merged = []
    for result_set in all_results:
        if isinstance(result_set, Exception):
            logger.warning(f"Search query failed: {result_set}")
            continue
        for r in result_set:
            text_key = r.get('text', '')[:100]
            if text_key not in seen_texts and len(r.get('text', '')) > 50:
                seen_texts.add(text_key)
                merged.append(r)

    merged.sort(key=lambda x: x.get('score', 0), reverse=True)

    if not merged:
        logger.info("xAI returned no results, falling back to ChromaDB")
        merged = await search_chromadb(user_query, n=8)

    logger.info(f"Multi-query search returned {len(merged)} unique results for: {user_query[:60]}")
    return merged[:12]


async def send_data_message(room, message_type, data):
    try:
        payload = {k: v for k, v in data.items() if k != "type"}
        payload["type"] = message_type
        message = json.dumps(payload)
        await room.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type}")
    except Exception as e:
        logger.error(f"Failed to send data: {e}")


class PastorBobAgent(Agent):
    def __init__(self, room):
        super().__init__(instructions=PASTOR_BOB_INSTRUCTIONS)
        self._room = room

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        user_text = new_message.text_content or ''
        user_text = user_text.strip()
        if not user_text or len(user_text) < 4:
            return

        logger.info(f"User turn completed: {user_text[:80]}")
        asyncio.create_task(send_data_message(self._room, "user_transcript", {"text": user_text}))

        results = await multi_query_search(user_text)

        for r in results[:3]:
            title = r.get('title', 'Sermon')
            if title.lower() in ['unknown sermon', 'unknown', '']:
                continue
            text = r.get('text', '')
            if len(text) < 50:
                continue
            asyncio.create_task(send_data_message(self._room, "sermon_reference", {
                "title": title,
                "url": r.get('timestamped_url', r.get('url', '')),
                "timestamp": r.get('start_time', ''),
                "text": text[:200]
            }))

        if results:
            context_parts = []
            for i, r in enumerate(results[:8]):
                title = r.get('title', 'Sermon')
                text = r.get('text', '')
                if text and len(text) > 50:
                    context_parts.append(f"[{i+1}] \"{title}\":\n{text}")

            sermon_context = "\n\n".join(context_parts)

            turn_ctx.add_message(
                role="assistant",
                content=f"""I found Pastor Bob's actual sermon transcripts on this topic. I will now synthesize his FULL teaching from these segments:

{sermon_context}

CRITICAL: I must SYNTHESIZE across ALL segments above. I must identify Pastor Bob's complete framework — if he teaches multiple parts, stages, or makes distinctions, I MUST include ALL of them. I must NOT flatten his teaching into a simple one-liner. I will say "Pastor Bob teaches..." and deliver his full, nuanced teaching in 3-5 sentences."""
            )
        else:
            turn_ctx.add_message(
                role="assistant",
                content="I will give a solid biblical answer consistent with Calvary Chapel teaching. I will be warm and conversational in 3-5 sentences. I will NOT say I lack information."
            )


async def entrypoint(ctx: JobContext):
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}

    turn_detection = ServerVad(
        type="server_vad",
        threshold=0.5,
        prefix_padding_ms=300,
        silence_duration_ms=700,
        create_response=True,
        interrupt_response=True,
    )

    model = RealtimeModel(voice="Aria", turn_detection=turn_detection)
    session = AgentSession(llm=model)
    apb_agent = PastorBobAgent(room=ctx.room)

    await ctx.connect()
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
                    asyncio.create_task(send_data_message(ctx.room, "agent_transcript", {"text": text}))
        except Exception as e:
            logger.error(f"Error in conversation_item_added: {e}")

    await session.start(room=ctx.room, agent=apb_agent)
    logger.info("Session started with on_user_turn_completed RAG")

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
    logger.info("APB Voice Agent v3 (on_user_turn_completed RAG)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
