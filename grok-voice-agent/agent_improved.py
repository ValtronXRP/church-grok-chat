"""
Improved Grok Voice Agent with Context Sharing
Built with LiveKit Agents and xAI Grok API
"""

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import xai, silero
from dotenv import load_dotenv
import logging
import asyncio
import os

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def entrypoint(ctx: JobContext):
    """Main entry point for the voice agent with context awareness."""
    
    logger.info(f"Starting voice session in room: {ctx.room.name}")
    logger.info(f"Participant: {ctx.participant}")
    
    # Initialize with room context - check for any metadata
    initial_ctx = llm.ChatContext().append(
        role="system",
        text="""You are APB (Ask Pastor Bob), a warm and welcoming AI assistant for church-related questions and spiritual guidance. 
        
Your role is to:
- Greet visitors warmly and make them feel welcome
- Answer questions about church services, events, and activities
- Provide spiritual guidance and biblical insights when asked
- Help people find information about getting involved
- Be patient, kind, and reflect love and compassion

Speaking style:
- Be conversational and natural, not robotic
- Keep responses concise but helpful (aim for 2-3 sentences when possible)
- Use a warm, friendly tone
- If you don't know something specific, offer to help them connect with someone who can help

Service Times:
- Sunday: 9:00 AM Traditional, 11:00 AM Contemporary, 6:00 PM Evening Prayer
- Wednesday: 7:00 PM Bible Study

Remember: You represent the church community, so always be gracious and welcoming to everyone."""
    )
    
    # Check if there's any conversation context passed from text chat
    if ctx.room.metadata:
        try:
            import json
            metadata = json.loads(ctx.room.metadata)
            if 'context' in metadata:
                for msg in metadata['context']:
                    initial_ctx.append(
                        role=msg['role'],
                        text=msg['content']
                    )
                logger.info(f"Loaded {len(metadata['context'])} context messages")
        except Exception as e:
            logger.warning(f"Could not load context: {e}")
    
    # Create the voice assistant with improved settings
    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=xai.STT(),
        llm=xai.LLM(model="grok-3"),
        tts=xai.TTS(voice="Ara"),  # Warm female voice for church setting
        chat_ctx=initial_ctx,
        allow_interruptions=True,
        interrupt_speech_duration=0.5,  # Faster interruption response
    )
    
    # Start the assistant
    assistant.start(ctx.room)
    
    # Subscribe to room events
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Log successful connection
    logger.info(f"Voice assistant started successfully in room {ctx.room.name}")
    
    # Optional: Send initial greeting if no participants yet
    participants_count = len(ctx.room.remote_participants)
    if participants_count == 0:
        await asyncio.sleep(1)  # Brief pause
        await assistant.say(
            "Hello! I'm Pastor Bob's assistant. I'm here to help with any questions about our church, "
            "services, or if you need spiritual guidance. How can I help you today?",
            allow_interruptions=True
        )


if __name__ == "__main__":
    # Use environment variables
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        logger.error("XAI_API_KEY not found in environment variables!")
        exit(1)
    
    # Configure xAI plugin
    xai.api_key = api_key
    
    # Run the agent
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="grok-voice-assistant",  # Must match server dispatch name
        )
    )