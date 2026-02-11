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
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai

SERMONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sermons_static.json')
SERMON_API_URL = os.environ.get('SERMON_API_URL', 'https://web-production-b652a.up.railway.app')
RERANKER_URL = os.environ.get('RERANKER_URL', 'http://localhost:5050')
sermons_data = []

def load_sermons():
    global sermons_data
    try:
        with open(SERMONS_FILE, 'r') as f:
            sermons_data = json.load(f)
        logger.info(f"Loaded {len(sermons_data)} static sermon segments (fallback)")
    except Exception as e:
        logger.error(f"Failed to load sermons: {e}")
        sermons_data = []

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

async def search_sermons_api(query, n_results=6):
    """Search via reranker (768-dim mpnet + cross-encoder)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search",
                json={"query": query, "type": "sermons", "n_results": n_results, "n_candidates": 20},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = [r for r in data.get('results', []) if r.get('source') == 'sermon']
                    logger.info(f"Reranker found {len(results)} sermon segments for: {query}")
                    return results
    except Exception as e:
        logger.warning(f"Reranker API error: {e}")
    return None

async def search_illustrations_api(query, n_results=3):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search",
                json={"query": query, "type": "illustrations", "n_results": n_results, "n_candidates": 20},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = [r for r in data.get('results', []) if r.get('source') == 'illustration']
                    logger.info(f"Reranker found {len(results)} illustrations for: {query}")
                    return results
    except Exception as e:
        logger.warning(f"Illustration reranker error: {e}")
    return []

def search_sermons_local(query, n_results=5):
    """Fallback: search static JSON (583 segments)"""
    if not query or not sermons_data:
        return []
    
    query_lower = query.lower()
    query_words = query_lower.split()
    
    scored = []
    for sermon in sermons_data:
        text_lower = sermon.get('text', '').lower()
        word_matches = sum(1 for word in query_words if len(word) > 3 and word in text_lower)
        topics = sermon.get('topics', [])
        topic_score = 0.3 if any(t.lower() in query_lower for t in topics) else 0
        score = (word_matches / len(query_words) if query_words else 0) + topic_score
        
        if score > 0.2:
            scored.append((score, sermon))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    results = []
    for score, sermon in scored[:n_results]:
        start_seconds = time_to_seconds(sermon.get('start_time', '0'))
        timestamped_url = f"{sermon.get('url', '')}&t={start_seconds}s"
        results.append({
            'text': sermon.get('text', ''),
            'title': sermon.get('title', 'Unknown Sermon'),
            'video_id': sermon.get('video_id', ''),
            'start_time': sermon.get('start_time', ''),
            'url': sermon.get('url', ''),
            'timestamped_url': timestamped_url,
            'relevance_score': score
        })
    
    return results

async def search_hybrid(query, n_results=6, search_type='all'):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RERANKER_URL}/search",
                json={"query": query, "type": search_type, "n_results": n_results, "n_candidates": 30},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    sermons = [r for r in results if r.get('source') == 'sermon']
                    illustrations = [r for r in results if r.get('source') == 'illustration']
                    website = [r for r in results if r.get('source') == 'website']
                    logger.info(f"Reranker: {len(sermons)} sermons, {len(illustrations)} illustrations, {len(website)} website ({data.get('timing_ms', 0)}ms)")
                    return {'sermons': sermons, 'illustrations': illustrations, 'website': website}
    except Exception as e:
        logger.warning(f"Reranker unavailable ({e}), falling back")
    return None

async def search_sermons(query, n_results=6):
    results = await search_sermons_api(query, n_results)
    if results:
        return results
    logger.info(f"Falling back to local search for: {query}")
    return search_sermons_local(query, n_results)

class FixedXAIRealtimeModel(openai.realtime.RealtimeModel):
    def __init__(self, voice="Aria", api_key=None, **kwargs):
        api_key = api_key or os.environ.get("XAI_API_KEY")
        super().__init__(
            base_url="wss://api.x.ai/v1/realtime",
            model="",
            voice=voice,
            api_key=api_key,
            modalities=["audio"],
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 200,
                "create_response": False,
                "interrupt_response": True,
            },
            **kwargs
        )

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim. Your job is to answer questions based on Pastor Bob Kopeny's sermon teachings.

=== YOUR #1 JOB ===
When sermon segments are provided, you MUST read them and answer the user's question using that content. Say "Pastor Bob teaches..." and share what he says. The segments are his REAL words from REAL sermons. Always give a substantive answer.

=== BANNED PHRASES (NEVER SAY THESE) ===
- "I'd need to check"
- "I don't have a specific teaching"
- "I'd have to look into"
- "I'm not sure what Pastor Bob says about"
- Any variation of hedging, deflecting, or saying you lack information

=== WHAT TO DO INSTEAD ===
- Sermon segments provided? USE THEM. Read them, extract the answer, share it warmly.
- No sermon segments provided? Give a solid biblical answer. Do NOT say "Pastor Bob doesn't have a teaching on this" — just answer the question from Scripture.
- Personal questions about Pastor Bob? Use the VERIFIED FACTS below.

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
"""

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

class APBAssistant(Agent):
    def __init__(self):
        super().__init__(instructions=PASTOR_BOB_INSTRUCTIONS)
        self._room = None
        self._all_sermon_results = []
        self._current_sermon_results = []
        self._last_query = None

    async def on_user_turn_completed(self, turn_ctx, new_message):
        user_text = ""
        if hasattr(new_message, 'text_content'):
            user_text = new_message.text_content
        elif hasattr(new_message, 'content'):
            content = new_message.content
            if isinstance(content, str):
                user_text = content
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, str):
                        user_text += c
                    elif hasattr(c, 'text'):
                        user_text += c.text
                    elif hasattr(c, 'transcript'):
                        user_text += c.transcript
        
        if not user_text:
            return
        
        logger.info(f"on_user_turn_completed: '{user_text[:80]}'")
        
        import re
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
        
        filtered_results = []
        website_results = []
        illustration_results = []
        
        hybrid = await search_hybrid(user_text, 10)
        if hybrid:
            raw_sermons = hybrid.get('sermons', [])
            filtered_results = filter_results(raw_sermons)
            illustration_results = hybrid.get('illustrations', [])
            website_results = hybrid.get('website', [])
            logger.info(f"Search: {len(filtered_results)} sermons, {len(illustration_results)} illustrations")
        else:
            results = await search_sermons(user_text, 10)
            filtered_results = filter_results(results)
            illustration_results = await search_illustrations_api(user_text, 3) or []
        
        story_matches = detect_personal_story(user_text)
        if story_matches:
            pinned = []
            pinned_vids = set()
            for sk in story_matches:
                story = PINNED_STORY_CLIPS.get(sk)
                if story:
                    for clip in story["clips"]:
                        pinned.append(clip)
                        pinned_vids.add(clip["video_id"])
            filtered_results = [r for r in filtered_results if r.get("video_id") not in pinned_vids]
            filtered_results = pinned + filtered_results
        
        self._all_sermon_results = filtered_results
        self._current_sermon_results = filtered_results[:3]
        self._last_query = user_text
        
        if self._room:
            for r in filtered_results[:3]:
                await send_data_message(self._room, "sermon_reference", {
                    "title": r.get('title', 'Sermon'),
                    "url": r.get('timestamped_url', r.get('url', '')),
                    "timestamp": r.get('start_time', ''),
                    "text": r.get('text', '')[:200]
                })
            for ill in illustration_results:
                await send_data_message(self._room, "illustration", {
                    "title": ill.get('illustration', ill.get('title', ill.get('summary', 'Illustration'))),
                    "text": ill.get('text', '')[:300],
                    "url": ill.get('video_url', ill.get('youtube_url', ill.get('url', ''))),
                    "illustration_type": ill.get('type', ''),
                    "tone": ill.get('tone', ill.get('emotional_tone', ''))
                })
        
        if filtered_results or website_results:
            sermon_context = "\n\n=== PASTOR BOB'S ACTUAL SERMON CONTENT (USE THIS TO ANSWER) ===\n\n"
            for i, r in enumerate(filtered_results[:5]):
                sermon_context += f'[Segment {i+1}] "{r.get("title", "Sermon")}":\n'
                sermon_context += f'"{r.get("text", "")[:1200]}"\n\n'
            if website_results:
                sermon_context += "\n=== CHURCH WEBSITE INFO ===\n"
                for wr in website_results[:2]:
                    sermon_context += f"[{wr.get('page', 'Church Info')}]: {wr.get('text', '')[:600]}\n"
            sermon_context += "\nUSE THE CONTENT ABOVE. Say 'Pastor Bob teaches...' and share what he says."
            
            try:
                turn_ctx.add_message(role="system", content=sermon_context)
                logger.info(f"Injected sermon context ({len(filtered_results)} sermons) into chat context")
            except Exception as e:
                logger.warning(f"Could not add to turn_ctx: {e}, trying generate_reply")
        else:
            try:
                turn_ctx.add_message(role="system", content="\nAnswer the question directly from the Bible. Be warm and helpful.")
            except Exception as e:
                logger.warning(f"Could not add fallback to turn_ctx: {e}")

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
    """Main entrypoint for the agent - called when dispatched to a room"""
    logger.info(f"Agent dispatched to room: {ctx.room.name}")
    
    all_sermon_results = []
    current_sermon_results = []
    last_query = {"text": None}
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
    
    session = AgentSession(llm=FixedXAIRealtimeModel(voice="Aria"))
    
    apb_agent = APBAssistant()
    apb_agent._room = ctx.room
    
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
                    if apb_agent._current_sermon_results:
                        response_with_links += "\n\nRelated sermon videos:\n"
                        for r in apb_agent._current_sermon_results:
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
    load_sermons()
    logger.info("=" * 50)
    logger.info("APB Voice Agent Starting (Multi-Session Mode)")
    logger.info("=" * 50)
    
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="apb-voice-assistant"
    ))
