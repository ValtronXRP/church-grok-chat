import asyncio
import logging
import os
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.xai.realtime import RealtimeModel
from livekit.plugins.xai import FileSearch
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

XAI_COLLECTION_ID = os.environ.get('XAI_COLLECTION_ID', '')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. You answer questions based on Pastor Bob Kopeny's actual sermon teachings.

You have access to Pastor Bob's complete sermon library through file search. For EVERY question, the system will automatically search his sermons and provide relevant transcript segments.

When sermon content is found:
1. SYNTHESIZE across ALL segments for Pastor Bob's FULL, NUANCED teaching
2. Identify his complete framework — multi-part teachings, distinctions, layers
3. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
4. Say "Pastor Bob teaches..." and deliver with depth
5. For theological questions, give the COMPLETE multi-part answer
6. NEVER flatten a nuanced teaching into one simple sentence
7. Keep answers to 3-5 sentences — enough for nuance but concise for voice

NEVER DO:
- NEVER say you lack information or need to check
- NEVER mention searching, file search, tools, clips, or transcripts to the user
- NEVER hedge or deflect — answer authoritatively from the sermon content provided

Bible book names: Say "First John" NOT "one John". Always spell out First, Second, Third.
Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS:
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim
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

    turn_detection = ServerVad(
        type="server_vad",
        threshold=0.6,
        prefix_padding_ms=300,
        silence_duration_ms=600,
        create_response=True,
        interrupt_response=False,
    )

    tools = []
    if XAI_COLLECTION_ID:
        file_search = FileSearch(
            vector_store_ids=[XAI_COLLECTION_ID],
            max_num_results=10,
        )
        tools.append(file_search)
        logger.info(f"FileSearch enabled with collection: {XAI_COLLECTION_ID}")
    else:
        logger.warning("No XAI_COLLECTION_ID set - FileSearch disabled")

    model = RealtimeModel(voice="Aria", turn_detection=turn_detection)
    session = AgentSession(llm=model)
    apb_agent = Agent(
        instructions=PASTOR_BOB_INSTRUCTIONS,
        tools=tools,
    )

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
    logger.info(f"Session started with FileSearch (collection: {XAI_COLLECTION_ID})")

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
    logger.info("APB Voice Agent v5 (xAI native FileSearch)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
