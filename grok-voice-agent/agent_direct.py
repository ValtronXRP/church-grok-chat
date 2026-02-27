import asyncio
import logging
import os
import sys
import json
import aiohttp
from dotenv import load_dotenv
from html import unescape

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout, force=True)
logger = logging.getLogger("apb")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(handler)

def log(msg):
    print(f"[APB] {msg}", flush=True)

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.xai.realtime import RealtimeModel

RERANKER_URL = os.environ.get('RERANKER_URL', 'http://127.0.0.1:5050')

PASTOR_BOB_INSTRUCTIONS = """You are APB (Ask Pastor Bob), a warm and knowledgeable voice assistant for Calvary Chapel East Anaheim.

You will receive sermon transcripts from Pastor Bob's actual sermons along with each question. Your job is to SYNTHESIZE those transcripts into a clear, warm answer.

Rules:
1. SYNTHESIZE across ALL provided transcripts for Pastor Bob's FULL, NUANCED teaching
2. Preserve key distinctions (e.g., "there is X but there is ALSO Y")
3. For THEOLOGICAL and BIBLE questions: Say "Pastor Bob teaches..." and deliver with depth
4. For CHURCH INFO questions (events, registrations, bible studies, service times, ministries, volunteering, giving, etc.): Answer directly with specific details from the church website data. Do NOT say "Pastor Bob teaches" — just answer the question with the actual info. Clickable links will be added to the chat automatically.
5. Give the COMPLETE multi-part answer, not a simplified version
6. Keep answers to 3-5 sentences — enough for nuance but concise for voice
7. Quote or closely paraphrase his actual words when they are powerful
8. VARY your phrasing EVERY time — never give the same scripted answer twice. Change your sentence structure, word choice, opening line, and emphasis. Highlight different details or angles of the same truth each time. Be naturally conversational, not robotic or formulaic.

NEVER DO:
- NEVER say you lack information, need to check, or don't have a specific teaching
- NEVER mention searching, tools, clips, or transcripts to the user
- NEVER hedge or deflect — answer authoritatively from the transcripts provided
- NEVER flatten a nuanced teaching into one simple sentence
- NEVER share phone numbers or email addresses. Do NOT say the church phone number or any email.
- NEVER tell the user to "call the office" or "check the website" as your primary answer — YOU have the church website info, so answer the question directly with specifics
- NEVER say "Pastor Bob teaches" when answering questions about church events, registrations, service times, ministries, or other church info — just answer directly

FORBIDDEN PHRASES — never say any of these:
- "I'd need to check"
- "I don't have a specific teaching"
- "Let me look into that"
- "I don't have that information"
- "in the materials I have"
- "call the office"
- "check the website"
- "email the office" or "email us"
- Any phone number
- Any email address

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books.

Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.

VERIFIED FACTS ABOUT PASTOR BOB KOPENY:
- Wife: Becky Kopeny (maiden name Becky Olson). HOW THEY MET (full sequence): Bob first met Becky at Calvary Church on Chapman and Madison in Placentia after a service. He invited her to a Sunday school college class he taught but she said no — her boyfriend was waiting in the car. They chatted briefly about her going to Cal State Fullerton and working at the Placentia Library. Years later, while driving to Talbot Seminary, God brought her name to mind at the intersection of Chapman and Kraemer. He drove to the library and learned she was in the AV department. A month later at the same intersection, he felt prompted again. He called her but she wasn't interested — until Bob told her that the Lord had revealed something to him that He wanted her to hear (Bob did NOT tell her what it was on the phone). Becky only agreed to go to breakfast because of that. It was at breakfast — NOT on the phone — that Bob shared the word of knowledge: that she had gotten engaged the night before. This was something no one could have known, and it changed everything, leading to their relationship. They got married shortly after. Bob was about 25.
- Three sons: Jesse (oldest), Valor (middle), Christian (youngest)
- Six grandchildren: Julia, Lily, Jonah, Jeffrey (Jesse's children), Luca (Valor and Stacy's son, born June 1 2022), Cora (Christian and Hayley's daughter, born December 2024)
- IMPORTANT: Jesse does NOT have a wife. NEVER say "Jesse and his wife" or mention Jesse having a wife.
- FAMILY QUESTION RULES: Answer ONLY what is asked. If asked "how many kids does Bob have?" — just say three sons (Jesse, Valor, Christian). If asked "how many grandchildren?" — just say six and list their names. Do NOT volunteer extra family details beyond what was specifically asked.
- Was a police officer/detective before entering full-time ministry. God called him out of law enforcement into pastoral ministry.
- HOW BOB GOT SAVED: Bob was 13 and in junior high at Tuffrey. His friend Fred, who also went to Tuffrey, invited him to a Campus Crusade ministry camp. The first night at the camp, Fred and some men asked Bob if he was a Christian. Bob said "oh yeah, I go to a Lutheran church." They asked "have you ever received Christ?" and Bob said "I don't know what that means." That night, two men named Jeff Maples and Gene Schaeffer — both in their 30s — shared the gospel with Bob for about five minutes and asked if he would receive Christ. Bob gave his life to Jesus that night in 1971.
- Pastors Calvary Chapel East Anaheim

VERIFIED THEOLOGICAL POSITIONS (Pastor Bob's actual teaching):
- BAPTISM OF THE HOLY SPIRIT: The Baptism of the Holy Spirit happens at salvation — every believer receives the Spirit when they are saved. BUT it is NOT only a one-time event. The Spirit's anointing can happen again and again throughout a believer's life. A believer can receive a secondary experience where the Spirit takes greater control — this may include speaking in tongues, being called into fervent prayer, receiving spiritual gifts, or other anointings. Pastor Bob teaches the Spirit is like oil that anoints you and water that quenches you, allowing you to thrive in a parched world. The baptism/anointing of the Spirit can happen over and over — it is NOT limited to a single moment at salvation, even though salvation IS a baptism of the Holy Spirit.

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently. Do NOT say you need to check — you KNOW these facts.
When asked about theological topics listed above, use these verified positions as the authoritative framework for your answer.

CHURCH INFO QUESTIONS (events, registrations, service times, ministries, bible studies, etc.):
When a user asks about church events, registrations, service times, ministries, or other church info, say ONLY: "Let me pull up the latest details for you." Do NOT try to answer church info questions from memory — wait for the live data to be provided to you. The system will provide you with current church website data momentarily.
NEVER give a generic answer like "check the website" or "there are lots of events".
NEVER share phone numbers or email addresses. NEVER tell the user to call the office or email anyone.
NEVER say URLs or web addresses out loud — they sound terrible in voice. Instead, reference pages naturally like "the registrations page." Clickable links will be added to the chat automatically.
NEVER mention "cc-ea.org/calendar" — that page does not exist.
"""


CHURCH_INFO_KEYWORDS = [
    'service time', 'service times', 'what time', 'when is service', 'when are services',
    'sunday service', 'wednesday service', 'wednesday night',
    'bible study', 'bible studies', 'study group', 'community group', 'community groups',
    'home group', 'home groups', 'small group', 'small groups',
    'register', 'registration', 'sign up', 'signup', 'event', 'events', 'happening',
    'coming up', 'upcoming', 'calendar', 'schedule',
    'volunteer', 'volunteering', 'serve', 'serving',
    'give', 'giving', 'tithe', 'tithing', 'donate', 'donation', 'offering',
    'statement of faith', 'what does the church believe', 'what do you believe',
    'mission', 'missions', 'missionary', 'missionaries',
    'ministry', 'ministries',
    'new here', 'first time', 'visiting', 'visitor', 'new to the church',
    'location', 'address', 'where is the church', 'directions',
    'contact', 'phone number', 'email', 'office hours',
    'wedding', 'marriage application',
    'crisis pregnancy', 'pregnancy resource',
    'disability', 'special needs',
    "pastor bob's resources", 'study tools', 'e-sword',
    'live stream', 'livestream', 'watch live', 'watch online',
    "women's study", "women's bible", "men's study", "men's bible",
    'youth group', 'youth ministry', 'kids ministry', "children's ministry",
    'homeschool', 'home school',
    'prayer request', 'prayer list',
    'church info', 'church information', 'about the church', 'about ccea',
    'calvary chapel east anaheim',
    'bulletin', 'announcements',
    'worship', 'worship team', 'worship lyrics',
    'baptism class', 'membership',
    'highlights',
    'school of discipleship',
    'tuesday', 'thursday', 'friday', 'saturday',
]

CHURCH_TOPIC_PAGES = {
    'events': {
        'keywords': ['event', 'events', 'upcoming', 'coming up', 'happening', 'register', 'registration', 'sign up', 'signup', 'calendar', 'schedule'],
        'pages': ['/registrations'],
    },
    'studies': {
        'keywords': ['bible study', 'bible studies', 'home group', 'home groups', 'small group', 'small groups', 'community group', 'community groups'],
        'pages': ['/resources/home-bible-studies', '/service-times-and-location'],
    },
    'services': {
        'keywords': ['service time', 'service times', 'what time', 'when is service', 'when are services', 'sunday service', 'wednesday service', 'wednesday night'],
        'pages': ['/service-times-and-location'],
    },
    'ministries': {
        'keywords': ['ministry', 'ministries'],
        'pages': ['/ministries-2'],
    },
    'volunteer': {
        'keywords': ['volunteer', 'volunteering', 'serve', 'serving'],
        'pages': ['/volunteer'],
    },
    'giving': {
        'keywords': ['give', 'giving', 'tithe', 'tithing', 'donate', 'donation', 'offering'],
        'pages': ['/give'],
    },
    'missions': {
        'keywords': ['mission', 'missions', 'missionary', 'missionaries'],
        'pages': ['/missions'],
    },
    'faith': {
        'keywords': ['statement of faith', 'what does the church believe', 'what do you believe'],
        'pages': ['/about-us/statement-of-faith'],
    },
    'new': {
        'keywords': ['new here', 'first time', 'visiting', 'visitor', 'new to the church'],
        'pages': ['/new-here'],
    },
    'location': {
        'keywords': ['location', 'address', 'where is the church', 'directions'],
        'pages': ['/service-times-and-location'],
    },
    'livestream': {
        'keywords': ['live stream', 'livestream', 'watch live', 'watch online'],
        'pages': ['/services/live'],
    },
    'youth': {
        'keywords': ['youth group', 'youth ministry', 'kids ministry', "children's ministry"],
        'pages': ['/ministries-2'],
    },
    'women': {
        'keywords': ["women's study", "women's bible", "women's ministry"],
        'pages': ['/ministries-2', '/resources/home-bible-studies'],
    },
    'men': {
        'keywords': ["men's study", "men's bible", "men's ministry"],
        'pages': ['/ministries-2', '/resources/home-bible-studies'],
    },
}

def detect_church_topic(query):
    q = query.lower()
    matched_pages = set()
    for topic, config in CHURCH_TOPIC_PAGES.items():
        for kw in config['keywords']:
            if kw in q:
                for p in config['pages']:
                    matched_pages.add(p)
                break
    return list(matched_pages)

def is_church_info_query(query):
    q = query.lower()
    return any(kw in q for kw in CHURCH_INFO_KEYWORDS)


async def _search_reranker(query, n=10):
    is_church = is_church_info_query(query)
    n_website = 8 if is_church else 0
    website_pages = detect_church_topic(query) if is_church else []
    n_sermons = 0 if is_church else n
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"query": query, "n_sermons": n_sermons, "n_illustrations": 0, "n_website": n_website}
            if website_pages:
                payload["website_pages"] = website_pages
            async with session.post(
                f"{RERANKER_URL}/search/fast-all",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = []
                    seen = set()
                    for r in data.get('sermons', []):
                        title = r.get('title', 'Sermon')
                        text = r.get('text', '')
                        key = text[:100]
                        if text and len(text) > 50 and key not in seen:
                            seen.add(key)
                            results.append(f"[{len(results)+1}] \"{unescape(title)}\":\n\"{text[:600]}\"")
                    website_results = []
                    for r in data.get('website', []):
                        page = r.get('page', '')
                        text = r.get('text', '')
                        url = r.get('url', '')
                        if text and len(text) > 20:
                            website_results.append(f"[{page}] ({url}):\n{text[:400]}")
                    return results, website_results
                else:
                    body = await response.text()
                    log(f"Reranker error {response.status}: {body[:200]}")
                    return [], []
    except Exception as e:
        log(f"Search error: {e}")
        return [], []


_room_ref = None
_session_ref = None
_searching = False


async def _send_data_message(message_type, data):
    if not _room_ref:
        return
    try:
        payload = {k: v for k, v in data.items() if k != "type"}
        payload["type"] = message_type
        message = json.dumps(payload)
        await _room_ref.local_participant.publish_data(message.encode('utf-8'), reliable=True)
        logger.info(f"Sent {message_type}")
    except Exception as e:
        logger.error(f"Failed to send data: {e}")


async def _handle_user_question(transcript):
    global _searching
    if _searching:
        log(f"Already searching, skipping: {transcript[:40]}")
        return
    _searching = True
    try:
        log(f"SEARCHING for: {transcript[:80]}")
        results, website_results = await _search_reranker(transcript)

        if results or website_results:
            context_text = "\n\n".join(results[:3])
            log(f"Search returned {len(results)} sermon results, {len(website_results)} website results")

            if website_results:
                website_text = "\n\n".join(website_results[:6])
                injected_input = (
                    f"[SYSTEM: Answer the user's question using ONLY the relevant data below. "
                    f"Be CONCISE — 2-4 sentences max. List only the items that directly answer what was asked. "
                    f"Do NOT list every ministry or event — only what's relevant to the question. "
                    f"Do NOT say URLs out loud — links appear in chat automatically. "
                    f"Do NOT say 'check the website', share phone numbers or email addresses, or give generic answers.]\n\n"
                    f"{website_text}"
                )
                try:
                    await _session_ref.generate_reply(user_input=injected_input)
                    log("Reply generated with website data via user_input injection")
                except Exception as e:
                    log(f"generate_reply error (website): {e}")
            else:
                reply_instructions = (
                    f"You are APB, voice assistant for Calvary Chapel East Anaheim. "
                    f"Synthesize these sermon transcripts into a warm 3-5 sentence answer. "
                    f"Say 'Pastor Bob teaches...' and quote his words. "
                    f"NEVER say you lack info or need to check. "
                    f"VARY your phrasing — different structure, word choice, and emphasis each time. Be natural, not formulaic.\n\n"
                    f"Question: \"{transcript}\"\n\n"
                    f"TRANSCRIPTS:\n{context_text}\n\n"
                    f"Answer warmly from the transcripts above."
                )

                try:
                    await _session_ref.generate_reply(instructions=reply_instructions)
                    log("Reply generated with sermon context")
                except Exception as e:
                    log(f"generate_reply error: {e}")
        else:
            log("Search returned 0 results, generating fallback reply")
            try:
                await _session_ref.generate_reply(
                    instructions=(
                        f"You are APB, voice assistant for Calvary Chapel East Anaheim. "
                        f"Answer this question warmly from the Bible and general Christian knowledge. "
                        f"NEVER say you lack info or need to check. Keep it to 3-5 sentences.\n\n"
                        f"Question: \"{transcript}\""
                    )
                )
                log("Fallback reply generated")
            except Exception as e:
                log(f"Fallback generate_reply error: {e}")
    except Exception as e:
        log(f"Handle question error: {e}")
    finally:
        _searching = False


async def entrypoint(ctx: JobContext):
    global _room_ref, _session_ref
    try:
        log(f"[ENTRYPOINT] Agent dispatched to room: {ctx.room.name}")

        last_sent_message = {"text": None}

        model = RealtimeModel(
            voice="Aria",
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": True,
                "interrupt_response": True,
            },
        )
        session = AgentSession(llm=model)
        _session_ref = session
        apb_agent = Agent(
            instructions=PASTOR_BOB_INSTRUCTIONS,
        )

        log("Connecting to room...")
        await ctx.connect()
        _room_ref = ctx.room
        log(f"Connected to room: {ctx.room.name}")

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
                        asyncio.create_task(_send_data_message("agent_transcript", {"text": text}))
            except Exception as e:
                logger.error(f"Error in conversation_item_added: {e}")

        @session.on("user_input_transcribed")
        def on_user_input(event):
            if not event.is_final:
                return
            transcript = event.transcript.strip()
            if not transcript or len(transcript) < 3:
                return
            log(f"USER SAID: {transcript[:80]}")
            asyncio.create_task(_send_data_message("user_transcript", {"text": transcript}))
            asyncio.create_task(_handle_user_question(transcript))

        log("Starting session...")
        await session.start(room=ctx.room, agent=apb_agent)
        log(f"Session started (reranker: {RERANKER_URL})")

        greeting = "Welcome to Ask Pastor Bob! How can I help you today?"
        try:
            await session.generate_reply(instructions=f"Say exactly: '{greeting}'")
            log("Greeting sent - LISTENING")
        except Exception as e:
            log(f"Greeting error: {e} - continuing anyway")

        shutdown_event = asyncio.Event()
        async def _on_shutdown():
            shutdown_event.set()
        ctx.add_shutdown_callback(_on_shutdown)
        await shutdown_event.wait()
        log("Session shutdown")
    except Exception as e:
        log(f"[ENTRYPOINT CRASH] {type(e).__name__}: {e}")
        import traceback
        log(traceback.format_exc())
        raise


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("APB Voice Agent v12 (generate_reply with full context)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        num_idle_processes=5,
        job_memory_warn_mb=2000,
    ))
