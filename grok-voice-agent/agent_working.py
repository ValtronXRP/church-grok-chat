#!/usr/bin/env python3
"""
APB Voice Agent - Working Version
Uses LiveKit with xAI for chat and voice synthesis
"""

import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli, llm
from livekit.agents.voice import VoiceAssistant
from livekit.plugins import xai, silero

# Pastor Bob's instructions
PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a friendly voice assistant that helps people learn about the Bible and faith based on Pastor Bob Kopeny's teachings.

IMPORTANT CONTEXT:
- APB stands for "Ask Pastor Bob"
- Pastor Bob Kopeny is the specific pastor whose teachings you represent
- Keep responses conversational and brief since this is voice chat (2-3 sentences max)

YOUR ROLE:
- Answer questions about the Bible, faith, and Christian living
- Be encouraging, compassionate, and helpful
- Share teachings in a warm, pastoral way

Service Times:
- Sunday: 9:00 AM Traditional, 11:00 AM Contemporary, 6:00 PM Evening Prayer
- Wednesday: 7:00 PM Bible Study
"""

async def entrypoint(ctx: JobContext):
    """Voice agent entry point"""
    
    logger.info(f"Starting voice session for room: {ctx.room.name}")
    
    # Create initial chat context
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=PASTOR_BOB_INSTRUCTIONS
    )
    
    # Create voice assistant
    assistant = VoiceAssistant(
        vad=silero.VAD.load(),  # Voice activity detection
        stt=xai.STT(),  # Speech to text
        llm=xai.LLM(model="grok-2-1212"),  # Language model
        tts=xai.TTS(voice="Ara"),  # Text to speech
        chat_ctx=initial_ctx,
        allow_interruptions=True,
        interrupt_speech_duration=0.5,
    )
    
    # Start the assistant
    assistant.start(ctx.room)
    
    # Connect to room
    await ctx.connect(auto_subscribe="audio_only")
    
    # Send greeting after a brief delay
    await asyncio.sleep(1.5)
    await assistant.say("Welcome to Ask Pastor Bob! How can I help you today?")
    
    logger.info("Voice agent ready and listening")

if __name__ == "__main__":
    # Set API key
    xai_key = os.getenv("XAI_API_KEY")
    if not xai_key:
        logger.error("XAI_API_KEY not found in environment!")
        exit(1)
    
    xai.api_key = xai_key
    
    # Run the agent
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # Don't specify agent_name to use default
        )
    )