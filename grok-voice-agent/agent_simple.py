#!/usr/bin/env python

import asyncio
import logging
from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli, llm
from livekit.agents.voice import VoiceAssistant
from livekit.plugins import xai, silero
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def entrypoint(ctx: JobContext):
    """Simple working voice agent entrypoint."""
    
    # Log connection info
    logger.info(f"Job assigned: {ctx.job.id}")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Participants: {len(ctx.room.remote_participants)}")
    
    # Create initial context
    initial_ctx = llm.ChatContext().append(
        role="system",
        text="""You are APB (Ask Pastor Bob), a warm church assistant.
        
Service Times:
- Sunday: 9:00 AM Traditional, 11:00 AM Contemporary, 6:00 PM Evening Prayer
- Wednesday: 7:00 PM Bible Study

Keep responses brief and friendly (2-3 sentences)."""
    )
    
    # Create voice assistant with xAI
    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=xai.STT(),
        llm=xai.LLM(model="grok-3"),
        tts=xai.TTS(voice="Ara"),
        chat_ctx=initial_ctx,
        allow_interruptions=True,
        interrupt_speech_duration=0.5,
    )
    
    # Start the assistant
    assistant.start(ctx.room)
    
    # Connect and subscribe to audio
    await ctx.connect(auto_subscribe="audio_only")
    
    # Send greeting
    await asyncio.sleep(1.5)
    await assistant.say(
        "Hello! Welcome to our church. I'm here to help with any questions about services, events, or spiritual guidance. How can I help you today?"
    )
    
    logger.info("Agent connected and greeting sent")


if __name__ == "__main__":
    # Set API key
    xai_key = os.getenv("XAI_API_KEY")
    if not xai_key:
        logger.error("XAI_API_KEY not found!")
        exit(1)
    
    xai.api_key = xai_key
    
    # Run agent
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="grok-voice-assistant",
        )
    )