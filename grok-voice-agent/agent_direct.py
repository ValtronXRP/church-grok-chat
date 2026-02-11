import asyncio
import logging
import os
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai


class FixedXAIRealtimeModel(openai.realtime.RealtimeModel):
    def __init__(self, voice="Aria", api_key=None, **kwargs):
        api_key = api_key or os.environ.get("XAI_API_KEY")
        super().__init__(
            base_url="wss://api.x.ai/v1/realtime",
            model="",
            voice=voice,
            api_key=api_key,
            modalities=["audio", "text"],
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": False,
                "interrupt_response": True,
            },
            **kwargs
        )

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm voice assistant for Calvary Chapel East Anaheim. You speak when given instructions. Read content warmly and naturally in Grok's voice.
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
    awaiting_answer = {"active": False}

    session = AgentSession(llm=FixedXAIRealtimeModel(voice="Aria"))
    apb_agent = Agent(instructions=PASTOR_BOB_INSTRUCTIONS)

    def on_data_received(data_packet):
        try:
            raw_data = data_packet.data if hasattr(data_packet, 'data') else data_packet
            message = json.loads(raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
            msg_type = message.get('type')
            logger.info(f"Data received: {msg_type}")

            if msg_type == 'speak_answer' and message.get('text'):
                answer_text = message['text'].strip()
                if answer_text:
                    logger.info(f"GOT ANSWER TO SPEAK: {answer_text[:100]}...")
                    awaiting_answer["active"] = False
                    asyncio.create_task(speak_answer(answer_text))
            elif msg_type == 'silent_connection':
                text_to_speak = message.get('textToSpeak', '')
                if text_to_speak:
                    logger.info(f"Got text to speak: {text_to_speak[:80]}")
                    asyncio.create_task(speak_answer(text_to_speak))
        except Exception as e:
            logger.error(f"Data parse error: {e}")

    ctx.room.on("data_received", on_data_received)

    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")

    async def speak_answer(text):
        trimmed = text[:2000]
        instructions = f"Read the following answer to the user warmly and naturally. This is Pastor Bob's actual teaching from his sermons. Do NOT add your own commentary or say 'according to the search' or anything meta. Just share the teaching as if you know it:\n\n{trimmed}"
        logger.info(f"Generating speech for answer ({len(trimmed)} chars)")
        await session.generate_reply(instructions=instructions)
        logger.info("generate_reply called for speak_answer")

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
