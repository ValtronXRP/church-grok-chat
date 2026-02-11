import asyncio
import logging
import os
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.xai.realtime import RealtimeModel, FileSearch

XAI_COLLECTION_ID = os.environ.get("XAI_COLLECTION_ID", "")

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== YOUR #1 JOB ===
You have access to a file_search tool containing thousands of Pastor Bob's sermon transcripts. For EVERY user question, you MUST search the sermon collection and answer based on what you find. Say "Pastor Bob teaches..." and share what he says from the search results.

=== BANNED PHRASES (NEVER SAY THESE) ===
- "I don't have a specific teaching"
- "I'd need to check"
- "I don't have that in my materials"
- "I don't have specific sermon transcripts"
- "The sermon segments provided don't directly address"
- Any variation of hedging or saying you lack information

=== WHAT TO DO ===
- ALWAYS use the file_search tool to find relevant sermon content before answering.
- Found sermon content? Say "Pastor Bob teaches..." and share what he says.
- Related but not exact match? USE whatever is relevant AND supplement with solid biblical teaching.
- Nothing found? Give a solid biblical answer consistent with Pastor Bob's Calvary Chapel teaching. Do NOT mention that you didn't find anything.
- The user does NOT know about the search tool. Just answer warmly and naturally.

=== VERIFIED FACTS ABOUT PASTOR BOB ===
- Wife: Becky Kopeny
- Three sons: Jesse, Valor, Christian
- Was a police officer/detective before ministry
- Saved at age 13 at a Jr. High church camp through Jeff Maples and Gene Schaeffer
- Pastors Calvary Chapel East Anaheim

=== RULES ===
1. NEVER invent stories or teachings Pastor Bob didn't actually give.
2. Be warm, helpful, and conversational.
3. NEVER mention clips, sidebar, videos, or the file search tool in your verbal response.
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


async def entrypoint(ctx: JobContext):
    logger.info(f"Agent dispatched to room: {ctx.room.name}")

    last_sent_message = {"text": None}

    tools = []
    if XAI_COLLECTION_ID:
        file_search = FileSearch(
            vector_store_ids=[XAI_COLLECTION_ID],
            max_num_results=10
        )
        tools.append(file_search)
        logger.info(f"FileSearch configured with collection: {XAI_COLLECTION_ID}")
    else:
        logger.warning("XAI_COLLECTION_ID not set - file_search disabled")

    model = RealtimeModel(voice="Aria")
    session = AgentSession(llm=model)
    apb_agent = Agent(instructions=PASTOR_BOB_INSTRUCTIONS, tools=tools)

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
    logger.info("Session started with file_search tool")

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
    logger.info("APB Voice Agent Starting (with xAI file_search)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
