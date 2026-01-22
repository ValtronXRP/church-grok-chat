"""
Grok Voice Agent - Church Website Voice Assistant
Built with LiveKit Agents and xAI Grok Voice API
"""

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    function_tool,
)
from livekit.agents.llm import function_tool
from livekit.plugins import xai
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChurchAssistant(Agent):
    """
    A friendly voice assistant for church website visitors.
    Customize the instructions to match your church's personality and information.
    """
    
    def __init__(self):
        super().__init__(
            instructions="""You are a warm and welcoming voice assistant for our church website.
            
Your role is to:
- Greet visitors warmly and make them feel welcome
- Answer questions about our church, services, and events
- Help people find information about getting involved
- Provide directions and contact information when asked
- Be patient, kind, and reflect the loving spirit of our community

Speaking style:
- Be conversational and natural, not robotic
- Keep responses concise but helpful
- Use a warm, friendly tone
- If you don't know something specific, offer to help them connect with someone who can help

Remember: You represent our church community, so always be gracious and welcoming to everyone."""
        )


# Define tools that the voice agent can use
@function_tool
async def get_service_times(context: RunContext) -> str:
    """Get the church service schedule and times."""
    # TODO: Replace with your actual service times
    return """
    Our weekly services are:
    
    Sunday:
    - 9:00 AM: Traditional Worship Service
    - 11:00 AM: Contemporary Worship Service
    - 6:00 PM: Evening Prayer Service
    
    Wednesday:
    - 7:00 PM: Midweek Bible Study
    
    All are welcome to join us!
    """


@function_tool
async def get_church_address(context: RunContext) -> str:
    """Get the church location and address."""
    # TODO: Replace with your actual address
    return """
    Our church is located at:
    123 Main Street
    Your City, State 12345
    
    We have parking available in the lot behind the building.
    The main entrance is on Main Street.
    """


@function_tool
async def get_upcoming_events(context: RunContext) -> str:
    """Get information about upcoming church events."""
    # TODO: Replace with your actual events or connect to a calendar API
    return """
    Here are our upcoming events:
    
    This Week:
    - Sunday: Regular worship services
    - Wednesday: Bible Study at 7 PM
    
    Coming Soon:
    - Community Potluck Dinner
    - Youth Group Meeting
    - Women's Bible Study
    
    Check our website for the full calendar!
    """


@function_tool
async def get_contact_info(context: RunContext) -> str:
    """Get church contact information."""
    # TODO: Replace with your actual contact info
    return """
    You can reach us at:
    
    Phone: (555) 123-4567
    Email: info@ourchurch.org
    
    Office Hours:
    Monday - Friday: 9 AM to 4 PM
    
    For urgent pastoral care, please call our main number.
    """


@function_tool
async def get_ministries_info(context: RunContext) -> str:
    """Get information about church ministries and ways to get involved."""
    # TODO: Replace with your actual ministries
    return """
    We have several ministries you can get involved with:
    
    - Children's Ministry: Sunday school and kids programs
    - Youth Group: For teens, meets weekly
    - Women's Ministry: Bible studies and fellowship
    - Men's Ministry: Studies and service projects
    - Music Ministry: Choir and worship team
    - Outreach: Community service opportunities
    
    Talk to any of our staff to learn more about getting involved!
    """


async def entrypoint(ctx: JobContext):
    """
    Main entry point for the voice agent.
    This function is called when a new session starts.
    """
    logger.info(f"Starting new voice session in room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    
    # Create the agent session with Grok Voice
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(
            voice="Ara",  # Warm female voice
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": True,
                "interrupt_response": True,
            },
        ),
    )
    
    # Create our church assistant agent
    agent = ChurchAssistant()
    
    # Start the session
    await session.start(
        room=ctx.room,
        agent=agent,
    )
    
    # Generate an initial greeting
    await session.generate_reply(
        instructions="""Say: 'Hello! Welcome to our church. I'm here to help with any questions about services, events, or if you need spiritual guidance. How can I help you today?'"""
    )
    
    logger.info("Voice agent session started successfully")


if __name__ == "__main__":
    from livekit import agents
    
    # Run the agent with auto-subscribe and request handler
    import os
    
    # Set environment variable to auto-subscribe to all rooms
    os.environ["LIVEKIT_AGENT_AUTO_SUBSCRIBE"] = "1"
    
    def request_handler(req):
        logger.info(f"Job request received: {req.job.id} for room {req.job.room.name}")
        # Accept all requests
        return True
    
    # IMPORTANT: The working version didn't specify an agent_name!
    # When no name is specified, it uses a default that might match your LiveKit Cloud config
    
    def request_handler(req):
        logger.info(f"🎯 JOB REQUEST RECEIVED!")
        logger.info(f"Job ID: {req.job.id}")
        logger.info(f"Room: {req.job.room.name}")
        return True  # Accept all requests
    
    logger.info("Starting agent WITHOUT explicit name (using default)")
    
    # Try WITHOUT agent_name first - this might be what was working!
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            # agent_name="grok-voice-assistant",  # COMMENTED OUT - try default
            request_fnc=request_handler,
        )
    )
