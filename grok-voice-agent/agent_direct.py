import asyncio
import logging
import os
import json
import re
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("apb")

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool, RunContext
from livekit.plugins import openai

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://localhost:5050')

def time_to_seconds(time_str):
    if not time_str:
        return 0
    parts = time_str.split(':')
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0

PINNED_STORY_CLIPS = {
    "becky_story": {
        "keywords": ["becky", "wife", "how did bob meet", "how did pastor bob meet", "how they met",
                      "bob and becky", "bob meet becky", "married", "engagement", "how bob met",
                      "love story", "bob's wife", "pastor bob's wife", "when did bob get married",
                      "bob get married", "who is bob married to", "who did bob marry", "becky kopeny"],
        "clips": [
            {
                "title": "How To Press On (03/26/2017)",
                "url": "https://www.youtube.com/watch?v=sGIJP13TxPQ",
                "timestamped_url": "https://www.youtube.com/watch?v=sGIJP13TxPQ&t=2382s",
                "start_time": "39:42",
                "video_id": "sGIJP13TxPQ",
                "text": "Pastor Bob shares the full story of how he met Becky - from meeting her briefly at church, to God putting her name in his mind at the intersection of Chapman and Kramer while driving to seminary, to the Lord revealing she had gotten engaged the night before, to God telling him to propose three weeks after their first date."
            },
            {
                "title": "Who Cares? (12/10/2017)",
                "url": "https://www.youtube.com/watch?v=BRd6nCCTLKI",
                "timestamped_url": "https://www.youtube.com/watch?v=BRd6nCCTLKI&t=2014s",
                "start_time": "33:34",
                "video_id": "BRd6nCCTLKI",
                "text": "Pastor Bob shares that when he first met Becky she was engaged to be married. They were just friends and he encouraged her spiritually."
            }
        ]
    },
    "testimony": {
        "keywords": ["testimony", "how was bob saved", "when was bob saved", "how did bob get saved",
                      "bob's testimony", "pastor bob saved", "bob come to christ", "bob receive christ",
                      "when did bob become a christian", "how did bob become", "bob's salvation",
                      "bob get saved", "pastor bob's testimony", "bob become a believer",
                      "how bob got saved", "when bob got saved", "bob's faith journey",
                      "how did pastor bob come to know", "fred", "jeff maples", "gene schaeffer",
                      "jr high camp", "junior high camp", "8th grade"],
        "clips": [
            {
                "title": "Be Faithful - 2 Timothy 1",
                "url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "timestamped_url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "start_time": "",
                "video_id": "72R6uNs2ka4",
                "text": "Pastor Bob shares his testimony - Jeff Maples and Gene Schaeffer shared Christ with him at a Jr. High church camp when he was 13. His friend Fred invited him. They shared for five minutes and asked if he would receive Christ."
            }
        ]
    }
}

def detect_personal_story(query):
    q = query.lower()
    matches = []
    for story_key, story in PINNED_STORY_CLIPS.items():
        for kw in story["keywords"]:
            if kw in q:
                matches.append(story_key)
                break
    return matches

def filter_results(results):
    filtered = []
    for r in results:
        title = (r.get('title') or '').lower()
        text = (r.get('text') or '').lower()
        if title in ['unknown sermon', 'unknown', '']:
            continue
        if any(ind in title for ind in ['worship song', 'hymn', 'music video', 'singing', 'choir']):
            continue
        if len(text) < 50:
            continue
        worship_count = len(re.findall(r'\b(la la|hallelujah|glory glory|praise him)\b', text, re.I))
        if worship_count > 2:
            continue
        filtered.append(r)
    return filtered

async def do_sermon_search(query, n_results=6):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search",
                json={"query": query, "type": "all", "n_results": n_results, "n_candidates": 20},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    sermons = [r for r in results if r.get('source') == 'sermon']
                    illustrations = [r for r in results if r.get('source') == 'illustration']
                    website = [r for r in results if r.get('source') == 'website']
                    logger.info(f"Reranker: {len(sermons)} sermons, {len(illustrations)} illustrations, {len(website)} website ({data.get('timing_ms', 0)}ms)")
                    return sermons, illustrations, website
    except Exception as e:
        logger.warning(f"Reranker error: {e}")
    return [], [], []


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
                "silence_duration_ms": 200,
                "create_response": True,
                "interrupt_response": True,
            },
            **kwargs
        )

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== CRITICAL: YOU MUST USE THE search_pastor_bob_sermons TOOL ===
For EVERY question about the Bible, theology, faith, Christian living, or anything Pastor Bob might have taught on, you MUST call the search_pastor_bob_sermons tool FIRST before answering. This tool searches through 34,000+ real sermon segments and returns Pastor Bob's actual words. After getting results, use them to answer warmly: "Pastor Bob teaches..."

=== BANNED PHRASES (NEVER SAY THESE) ===
- "I'd need to check"
- "I don't have a specific teaching"
- "I'd have to look into"
- "I'm not sure what Pastor Bob says about"
- Any variation of hedging, deflecting, or saying you lack information

=== WHAT TO DO ===
1. User asks a question -> Call search_pastor_bob_sermons tool with their question
2. Tool returns sermon content -> Use it to answer: "Pastor Bob teaches..."
3. Tool returns no results -> Give a solid biblical answer without mentioning the lack of results

=== VERIFIED FACTS ABOUT PASTOR BOB (ONLY USE THESE) ===

FAMILY:
- Wife: BECKY KOPENY (maiden name: Becky Olson)
- HOW THEY MET (FULL STORY): Bob first met Becky briefly at church years before - she was walking by, mentioned she went to Cal State Fullerton and worked at the Placentia library, and her boyfriend was waiting in the car. Years later, while driving to Talbot Seminary, Bob stopped at the intersection of Chapman and Kramer in Placentia. Out of the blue, "Becky Cal State Fullerton Placentia library" came into his mind. He wasn't looking for a date. He drove in and asked if she still worked there. She did. She told him she was dating a guy seriously, heading toward engagement. A month later at the same intersection, "Becky" came to mind again. He went back, asked her out. They went to breakfast and prayed together. After a week of prayer, he called again. She was hard to reach. When he finally got her, she said "how about right now" to meeting. They went for coffee. At that coffee, the Lord revealed to Bob BEFORE Becky told him that she had gotten engaged to the other man the night before. They became just friends - Bob encouraged her spiritually. Eventually her engagement ended. Three weeks after their first date, Bob felt God telling him to propose. He resisted because he'd taught others to date for a year then be engaged for a year. They married about 3.5 months after their first date. Bob was about 25.

THREE SONS:
1. JESSE - oldest (born July 24, 1984) - 4 children: Julia, Lily, Jonah, Jeffrey
2. VALOR - middle (born Dec 2, 1985) - married to STACY, son LUCA (born June 1, 2022)
3. CHRISTIAN - youngest (born May 16, 1989) - married to HAYLEY, daughter CORA (born Dec 2024)

EDUCATION:
- Biola University - Bible major
- Talbot Seminary (at Biola) - first class was Koine Greek

CAREER BEFORE MINISTRY:
- Police Officer at La Habra Police Department
- Detective at Placentia Police Department
- Attended and graduated from two police academies (paid his own way in the 70s)

TESTIMONY (HOW BOB WAS SAVED):
- Raised Lutheran, parents brought him to church every Sunday
- Saved in 8th GRADE (age 13) at a JR. HIGH CHURCH CAMP that his friend FRED invited him to attend
- Fred brought Bob to his church and to the camp
- Two men - JEFF MAPLES and GENE SCHAEFFER (in their 30s) - shared Christ with him one night for about five minutes and asked if he would receive Christ. He said yes.
- Did NOT have a dramatic experience when saved - didn't feel different
- By 9th grade, still saved but "you wouldn't have known it" - wasn't living it out
- Later prayed a "surrendering prayer" for full surrender to God

=== RULES ===
1. NEVER invent stories, quotes, or teachings.
2. Spell his name correctly: KOPENY (not Copeny).
3. Be warm, helpful, and conversational.
4. NEVER mention clips, sidebar, or videos in your verbal response.
5. Bible book names: Say "First John" NOT "one John". Say "Second Corinthians" NOT "two Corinthians". Always spell out First, Second, Third.
6. ALWAYS call search_pastor_bob_sermons before answering theological or biblical questions.
"""

_room_ref = {"room": None}
_last_results = {"sermons": []}

class APBAssistant(Agent):
    def __init__(self):
        super().__init__(
            instructions=PASTOR_BOB_INSTRUCTIONS,
            tools=[search_pastor_bob_sermons],
        )


@function_tool()
async def search_pastor_bob_sermons(context: RunContext, query: str) -> str:
    """Search Pastor Bob's 34,000+ sermon segments to find his actual teachings on any topic. Call this for ANY question about the Bible, theology, faith, Christian living, stories, or anything Pastor Bob might have taught on. Returns his real sermon content that you MUST use to answer the question."""
    logger.info(f"TOOL CALLED: search_pastor_bob_sermons('{query[:80]}')")

    sermons_raw, illustrations, website = await do_sermon_search(query, 8)
    sermons = filter_results(sermons_raw)

    story_matches = detect_personal_story(query)
    if story_matches:
        pinned = []
        pinned_vids = set()
        for sk in story_matches:
            story = PINNED_STORY_CLIPS.get(sk)
            if story:
                for clip in story["clips"]:
                    pinned.append(clip)
                    pinned_vids.add(clip["video_id"])
        sermons = [r for r in sermons if r.get("video_id") not in pinned_vids]
        sermons = pinned + sermons

    _last_results["sermons"] = sermons[:5]

    room = _room_ref.get("room")
    if room:
        for r in sermons[:3]:
            try:
                payload = json.dumps({
                    "type": "sermon_reference",
                    "title": r.get('title', 'Sermon'),
                    "url": r.get('timestamped_url', r.get('url', '')),
                    "timestamp": r.get('start_time', ''),
                    "text": r.get('text', '')[:200]
                })
                await room.local_participant.publish_data(payload.encode('utf-8'), reliable=True)
            except Exception as e:
                logger.error(f"Failed to send sermon ref: {e}")
        for ill in illustrations[:3]:
            try:
                payload = json.dumps({
                    "type": "illustration",
                    "title": ill.get('illustration', ill.get('title', ill.get('summary', 'Illustration'))),
                    "text": ill.get('text', '')[:300],
                    "url": ill.get('video_url', ill.get('youtube_url', ill.get('url', ''))),
                })
                await room.local_participant.publish_data(payload.encode('utf-8'), reliable=True)
            except Exception as e:
                logger.error(f"Failed to send illustration: {e}")

    if not sermons and not website:
        return "No specific sermon segments found on this topic. Answer the question warmly from your biblical knowledge. Do NOT say you lack information."

    result = "=== PASTOR BOB'S ACTUAL SERMON CONTENT ===\n\n"
    for i, r in enumerate(sermons[:5]):
        result += f'[Segment {i+1}] "{r.get("title", "Sermon")}":\n'
        result += f'"{r.get("text", "")[:1200]}"\n\n'

    if website:
        result += "\n=== CHURCH WEBSITE INFO ===\n"
        for wr in website[:2]:
            result += f"[{wr.get('page', 'Church Info')}]: {wr.get('text', '')[:600]}\n"

    result += "\nIMPORTANT: Use the content above to answer. Say 'Pastor Bob teaches...' and share what he says from these segments."
    logger.info(f"Tool returning {len(sermons)} sermons, {len(illustrations)} illustrations")
    return result


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

    def on_data_received(data_packet):
        try:
            raw_data = data_packet.data if hasattr(data_packet, 'data') else data_packet
            message = json.loads(raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
            msg_type = message.get('type')
            logger.info(f"Data received: {msg_type}")
        except Exception as e:
            logger.error(f"Data parse error: {e}")

    ctx.room.on("data_received", on_data_received)

    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")

    _room_ref["room"] = ctx.room

    session = AgentSession(llm=FixedXAIRealtimeModel(voice="Aria"))

    apb_agent = APBAssistant()

    @session.on("user_input_transcribed")
    def on_user_transcript(event):
        if event.is_final and event.transcript:
            user_text = event.transcript
            logger.info(f"USER SAID: {user_text}")
            asyncio.create_task(send_data_message(ctx.room, "user_transcript", {"text": user_text}))

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
                                text += c.text
                            elif hasattr(c, 'transcript'):
                                text += c.transcript
                    elif isinstance(content, str):
                        text = content

                if text and text != last_sent_message["text"]:
                    last_sent_message["text"] = text
                    logger.info(f"AGENT SAID: {text[:100]}...")
                    response_with_links = text
                    current_results = _last_results.get("sermons", [])
                    if current_results:
                        response_with_links += "\n\nRelated sermon videos:\n"
                        for r in current_results[:3]:
                            url = r.get('timestamped_url', r.get('url', ''))
                            response_with_links += f"- {r.get('title', 'Sermon')} ({r.get('start_time', '')}): {url}\n"
                    asyncio.create_task(send_data_message(ctx.room, "agent_transcript", {"text": response_with_links}))
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
    logger.info("APB Voice Agent Starting (Tool-Based Search)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
