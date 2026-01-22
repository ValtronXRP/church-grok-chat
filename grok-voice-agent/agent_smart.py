import asyncio
import logging
import os
import json
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.plugins import xai
from livekit.api import AccessToken, VideoGrants

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
XAI_API_KEY = os.getenv("XAI_API_KEY")
ROOM_NAME = "apb-voice-room"

# Initialize xAI API key
xai.api_key = XAI_API_KEY

# Sermon search helper
async def search_sermons(query, n_results=2):
    """Search for relevant sermon segments"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'http://localhost:5001/api/sermon/search',
                json={'query': query, 'n_results': n_results}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('results', [])
    except Exception as e:
        logger.warning(f"Sermon search not available: {e}")
    return []

def format_sermon_context(sermon_results):
    """Format sermon results for voice context"""
    if not sermon_results:
        return ""
    
    context = "\n\nRelevant sermons from Pastor Bob Kopeny that address this topic:\n"
    for i, result in enumerate(sermon_results[:2], 1):  # Limit to 2 for voice brevity
        context += f"\n{i}. From sermon '{result['title']}' at timestamp {result['start_time']}:\n"
        context += f"   '{result['text'][:150]}...'\n"
        context += f"   Video link: {result['timestamped_url']}\n"
    context += "\nReference these specific sermons when answering."
    return context

# Pastor Bob's context and instructions
PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a friendly voice assistant that helps people learn about the Bible and faith based on Pastor Bob Kopeny's teachings.

CRITICAL RULES YOU MUST ALWAYS FOLLOW:
1. When anyone says "Bob" or "Pastor Bob" they are ALWAYS referring to Pastor Bob Kopeny - never any other Bob
2. ALWAYS spell his last name correctly as "Kopeny" (K-O-P-E-N-Y), never "Copeny" or any other variation
3. If a user says it wrong like "Copeny", silently correct it to "Kopeny" in your response
4. For ANY question (biblical or otherwise), frame your answer based on what Pastor Bob Kopeny teaches or would say
5. For biblical topics, use phrases like "Pastor Bob Kopeny teaches..." or "According to Pastor Bob Kopeny..."
6. Even for general questions, relate them back to Pastor Bob Kopeny's perspective when possible
7. If you don't know what Pastor Bob Kopeny would say, you can say "I'm not sure what Pastor Bob Kopeny's specific teaching on that is, but based on biblical principles..."

IMPORTANT CONTEXT:
- APB stands for "Ask Pastor Bob" 
- Pastor Bob Kopeny is the ONLY pastor whose teachings you represent
- You are his assistant helping share his teachings and perspective

YOUR ROLE:
- Answer questions about the Bible, faith, and Christian living from Pastor Bob Kopeny's perspective
- Share relevant illustrations and stories in a warm, pastoral way
- Be encouraging, compassionate, and helpful
- Keep responses conversational and brief since this is voice chat
- For ANY topic, relate it back to Pastor Bob Kopeny's teachings when possible

SPEAKING STYLE:
- Warm and welcoming, like a friendly pastor
- Use simple, clear language
- Be encouraging and supportive
- Keep answers focused and not too long
- Always mention Pastor Bob Kopeny by his full, correctly spelled name

BOUNDARIES:
- Only share teachings that align with Pastor Bob Kopeny's Biblical Christianity
- Don't make up quotes - if you're not sure what he said, acknowledge that
- You are an assistant that helps share Pastor Bob Kopeny's teachings
- Be respectful of all questions, even difficult ones
"""

class APBAssistant(Agent):
    def __init__(self, room=None, sermon_context=""):
        # Include sermon context in instructions if available
        instructions = PASTOR_BOB_INSTRUCTIONS
        if sermon_context:
            instructions += sermon_context
        super().__init__(instructions=instructions)
        self.room = room
        self.last_user_message = ""
        self.last_agent_response = ""
        self.sermon_context = sermon_context

async def run_session():
    """Run one complete session, return when user disconnects"""
    
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity("apb-agent") \
        .with_grants(VideoGrants(
            room=ROOM_NAME,
            room_join=True,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    
    room = rtc.Room()
    user_disconnected = asyncio.Event()
    current_user = None
    session = None
    sent_greeting = False  # Track if we've sent a greeting
    greeting_task = None  # Track the greeting task so we can cancel it

    @room.on("participant_connected")
    def on_connect(p):
        nonlocal current_user
        if not p.identity.startswith("agent") and not p.identity.startswith("apb"):
            logger.info(f"User joined: {p.identity}")
            current_user = p

    @room.on("participant_disconnected") 
    def on_disconnect(p):
        if not p.identity.startswith("agent") and not p.identity.startswith("apb"):
            logger.info(f"User left: {p.identity}")
            user_disconnected.set()

    @room.on("track_subscribed")
    def on_track(track, pub, p):
        logger.info(f"Track: {track.kind} from {p.identity}")
        
    @room.on("data_received")
    def on_data_received(data_packet):
        """Handle data messages from the web client"""
        # data_packet is a DataReceived event with attributes: data, participant, kind
        if data_packet.participant == room.local_participant:
            return  # Ignore our own messages
        
        data = data_packet.data
        participant = data_packet.participant
        
        try:
            message = json.loads(data.decode())
            logger.info(f"Received data message: {message}")
            
            if message.get('type') == 'silent_connection':
                # Silent connection for text responses - no greeting needed
                logger.info("Silent connection detected - will skip greeting")
                nonlocal sent_greeting, greeting_task
                sent_greeting = True  # Mark as sent to skip actual greeting
                
                # Cancel the greeting task if it's running
                if greeting_task and not greeting_task.done():
                    greeting_task.cancel()
                    logger.info("Cancelled greeting task")
                
                # If there's text to speak immediately, handle it
                text_to_speak = message.get('textToSpeak', '')
                if text_to_speak and session:
                    logger.info(f"Speaking provided text: {text_to_speak[:100]}...")
                    logger.info(f"Full text: {text_to_speak}")
                    # Create a task to speak the text
                    async def speak_text_response():
                        try:
                            # Wait a moment for session to be ready
                            await asyncio.sleep(1)
                            
                            # Send the transcript to display in chat
                            try:
                                await room.local_participant.publish_data(
                                    json.dumps({
                                        "type": "agent_transcript",
                                        "text": text_to_speak
                                    }).encode(),
                                    reliable=True
                                )
                                logger.info("Sent agent transcript to chat")
                            except Exception as e:
                                logger.error(f"Could not send transcript: {e}")
                            
                            # Speak the actual response text
                            logger.info("Starting to speak text...")
                            await session.generate_reply(
                                instructions=f"Say this exact text word for word: {text_to_speak}"
                            )
                            logger.info("✅ Text response spoken")
                            
                            # Send a "done speaking" signal to the client
                            try:
                                await room.local_participant.publish_data(
                                    json.dumps({"type": "speech_complete"}).encode(),
                                    reliable=True
                                )
                                logger.info("Sent speech_complete signal")
                            except Exception as e:
                                logger.error(f"Could not send speech_complete signal: {e}")
                                
                        except Exception as e:
                            logger.error(f"Error speaking text: {e}")
                    
                    # Create the speaking task
                    asyncio.create_task(speak_text_response())
                    
            elif message.get('type') == 'speak' and session:
                # Text message that should be spoken
                text = message.get('text', '')
                logger.info(f"Speaking text response: {text[:50]}...")
                
                # Create a task to speak the text
                async def speak_text():
                    try:
                        await session.generate_reply(
                            instructions=f"Say this exact text naturally: '{text}'"
                        )
                        logger.info("✅ Text response spoken")
                    except Exception as e:
                        logger.error(f"Error speaking text: {e}")
                
                asyncio.create_task(speak_text())
                
        except Exception as e:
            logger.error(f"Error processing data message: {e}")

    try:
        await room.connect(LIVEKIT_URL, token.to_jwt())
        logger.info(f"Connected to room: {room.name}")
        
        # Check if user is already in the room
        for participant in room.remote_participants.values():
            if not participant.identity.startswith("agent") and not participant.identity.startswith("apb"):
                logger.info(f"User already in room: {participant.identity}")
                current_user = participant
                break
        
        if current_user is None:
            logger.info("Waiting for user...")
            # Wait for user with periodic status updates
            wait_count = 0
            while current_user is None:
                await asyncio.sleep(1.0)
                wait_count += 1
                if wait_count % 5 == 0:
                    logger.info(f"Still waiting... ({wait_count}s, {len(room.remote_participants)} remote participants)")

        logger.info(f"Starting session with {current_user.identity}")

        try:
            logger.info("Creating xAI realtime model...")
            session = AgentSession(
                llm=xai.realtime.RealtimeModel(
                    voice="Ara",
                    turn_detection={"type": "server_vad", "threshold": 0.5, "silence_duration_ms": 800}
                ),
            )
            logger.info("✅ xAI session created")

            logger.info("Starting agent session...")
            
            # Create the assistant with room reference
            assistant = APBAssistant(room=room)
            
            # Create a monitoring task to capture conversation
            async def monitor_conversation():
                """Monitor and capture conversation for chat display"""
                last_user_text = ""
                last_agent_text = ""
                
                while True:
                    try:
                        await asyncio.sleep(0.5)
                        
                        # Check if session has any transcription data we can capture
                        if hasattr(session, '_input') and session._input:
                            # Try to get user's last speech
                            pass
                            
                        # For now, log that we're monitoring
                        # The xAI realtime model handles transcription internally
                        # We may need to use a different approach
                        
                    except Exception as e:
                        logger.error(f"Error in monitor: {e}")
                        
            monitor_task = asyncio.create_task(monitor_conversation())
            
            await session.start(room=room, agent=assistant)
            logger.info("✅ Agent session started")
            
            # Hook into AgentSession events for transcripts
            @session.on("user_input_transcribed")
            def on_user_transcribed(event):
                """Capture user's transcribed speech and search for relevant sermons"""
                logger.info(f"User transcribed: {event}")
                # Extract the transcript text from the event
                if hasattr(event, 'transcript') and event.transcript:
                    text = event.transcript
                    if text and event.is_final:  # Only send final transcripts
                        asyncio.create_task(room.local_participant.publish_data(
                            json.dumps({
                                "type": "user_transcript",
                                "text": text
                            }).encode(),
                            reliable=True
                        ))
                        logger.info(f"Sent user transcript to chat: {text}")
                        
                        # Search for relevant sermons
                        async def search_and_enhance():
                            sermon_results = await search_sermons(text)
                            if sermon_results:
                                logger.info(f"Found {len(sermon_results)} relevant sermon segments")
                                sermon_context = format_sermon_context(sermon_results)
                                
                                # Send sermon results to chat for display
                                for result in sermon_results[:2]:
                                    await room.local_participant.publish_data(
                                        json.dumps({
                                            "type": "sermon_reference",
                                            "title": result['title'],
                                            "url": result['timestamped_url'],
                                            "timestamp": result['start_time'],
                                            "excerpt": result['text'][:150] + "..."
                                        }).encode(),
                                        reliable=True
                                    )
                        
                        # Create task to search sermons
                        asyncio.create_task(search_and_enhance())
                    
            @session.on("conversation_item_added")
            def on_conversation_item(event):
                """Capture conversation items (both user and agent)"""
                logger.info(f"Conversation item added: {event}")
                # Extract the item from the event
                if hasattr(event, 'item'):
                    item = event.item
                    # Check if item has role and content
                    if hasattr(item, 'role') and hasattr(item, 'content'):
                        # Extract content - it might be a list
                        content = item.content
                        if isinstance(content, list) and len(content) > 0:
                            content = content[0]  # Get first item if it's a list
                        
                        if item.role == 'assistant' and content:
                            # Agent's response - don't send greeting again
                            if "Welcome to Ask Pastor Bob" not in str(content):
                                asyncio.create_task(room.local_participant.publish_data(
                                    json.dumps({
                                        "type": "agent_transcript",
                                        "text": str(content)
                                    }).encode(),
                                    reliable=True
                                ))
                                logger.info(f"Sent agent transcript to chat: {content}")
            
            # Create a task to handle the greeting after a delay
            async def handle_greeting():
                nonlocal sent_greeting
                try:
                    await asyncio.sleep(1.0)  # Wait 1 second for potential data message (gives client time to send)
                    if not sent_greeting:
                        logger.info("No silent signal received - sending greeting...")
                        greeting_text = "Welcome to Ask Pastor Bob! How can I help you today?"
                        await session.generate_reply(
                            instructions=f"Say exactly: '{greeting_text}'"
                        )
                        sent_greeting = True
                        logger.info("✅ Greeting sent")
                        
                        # Send greeting to chat
                        try:
                            await room.local_participant.publish_data(
                                json.dumps({
                                    "type": "agent_transcript",
                                    "text": greeting_text
                                }).encode(),
                                reliable=True
                            )
                            logger.info("Sent greeting transcript to chat")
                        except Exception as e:
                            logger.error(f"Error sending greeting transcript: {e}")
                    else:
                        logger.info("Silent connection - greeting skipped")
                except asyncio.CancelledError:
                    logger.info("Greeting task cancelled")
            
            # Start the greeting task but don't wait for it
            greeting_task = asyncio.create_task(handle_greeting())
            
            logger.info("LISTENING - ready for input!")

        except Exception as e:
            logger.error(f"Session error: {e}")
            raise

        # Wait until user disconnects
        await user_disconnected.wait()

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await room.disconnect()
        logger.info("Room disconnected")

async def main():
    logger.info("=" * 50)
    logger.info("APB - Smart Voice Agent")
    logger.info("=" * 50)

    while True:
        await run_session()
        logger.info("Session ended. Restarting in 2 seconds...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())