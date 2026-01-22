"""
APB Voice Agent - Fixed version with detailed logging
"""

import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("apb")

# Also enable LiveKit logging
logging.getLogger("livekit").setLevel(logging.DEBUG)

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import xai


class APBAssistant(Agent):
    def __init__(self):
        super().__init__(
            instructions="""You are APB, a friendly voice assistant for a church website.
            Be warm, helpful, and conversational. Keep responses brief since this is voice."""
        )


async def entrypoint(ctx: JobContext):
    """Main entry point when agent joins a room"""
    
    logger.info("=" * 60)
    logger.info(f"AGENT STARTING")
    logger.info(f"Room: {ctx.room.name}")
    logger.info("=" * 60)
    
    # CRITICAL: Connect to the room with audio subscription
    logger.info("Connecting to room with audio subscription...")
    await ctx.connect(auto_subscribe="audio")
    logger.info("✅ Connected to room")
    
    # Log room state
    logger.info(f"Local participant: {ctx.room.local_participant.identity}")
    logger.info(f"Remote participants: {len(ctx.room.remote_participants)}")
    
    # List remote participants and their tracks
    for identity, participant in ctx.room.remote_participants.items():
        logger.info(f"  Participant: {identity}")
        for sid, pub in participant.track_publications.items():
            logger.info(f"    Track: {pub.kind} - subscribed={pub.subscribed} muted={pub.muted}")
    
    # Wait for a participant (user) to join
    logger.info("Waiting for user to join...")
    participant = await ctx.wait_for_participant()
    logger.info(f"✅ User joined: {participant.identity}")
    
    # Log their tracks
    logger.info(f"User has {len(participant.track_publications)} track publications")
    for sid, pub in participant.track_publications.items():
        logger.info(f"  Track {sid}: kind={pub.kind} subscribed={pub.subscribed}")
    
    # Create xAI voice session
    logger.info("Creating xAI Realtime session...")
    
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(
            voice="Ara",
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800,
            }
        ),
    )
    
    logger.info("Starting session...")
    await session.start(
        room=ctx.room,
        agent=APBAssistant(),
    )
    logger.info("✅ Session started")
    
    # Send greeting
    logger.info("Generating greeting...")
    await session.generate_reply(
        instructions="Say a brief, warm greeting like 'Hey there! I'm APB. How can I help you today?'"
    )
    
    logger.info("=" * 60)
    logger.info("AGENT READY - Listening for speech...")
    logger.info("=" * 60)
    
    # Keep running
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    # Check environment
    required = ['LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'XAI_API_KEY']
    missing = [v for v in required if not os.getenv(v)]
    
    if missing:
        logger.error(f"Missing env vars: {missing}")
        exit(1)
    
    logger.info("Environment OK")
    logger.info(f"LIVEKIT_URL: {os.getenv('LIVEKIT_URL')}")
    logger.info(f"XAI_API_KEY: {'*' * 10}...{os.getenv('XAI_API_KEY')[-4:]}")
    
    # Configure xAI
    xai_key = os.getenv("XAI_API_KEY")
    xai.api_key = xai_key
    
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        # Don't specify agent_name - let it use default
    ))
