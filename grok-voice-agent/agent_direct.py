import asyncio
import logging
import os
import sys
import json
import aiohttp
from datetime import date
from dotenv import load_dotenv
from html import unescape

# PostgreSQL logging
try:
    import asyncpg
    _pg_pool = None
    async def _get_pg_pool():
        global _pg_pool
        if _pg_pool is None and os.environ.get('DATABASE_URL'):
            _pg_pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], ssl='require')
        return _pg_pool

    async def log_voice_question(question):
        try:
            pool = await _get_pg_pool()
            if pool:
                await pool.execute('INSERT INTO questions (question, source) VALUES ($1, $2)', question, 'voice')
                log(f"Logged voice question: {question[:60]}")
        except Exception as e:
            log(f"Failed to log voice question: {e}")
except ImportError:
    async def log_voice_question(question):
        log("asyncpg not installed, skipping voice question logging")

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
from openai.types.realtime import AudioTranscription

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
- "Wednesday at 7am" or any variation (this is WRONG — the office is NOT open Wednesday at 7am)

Bible book names: ALWAYS say "First John" NOT "one John" or "1 John". ALWAYS say "Second Corinthians" NOT "two Corinthians" or "2 Corinthians". ALWAYS spell out First, Second, Third for ALL numbered Bible books.

LANGUAGE: Always respond in the same language the user speaks. If they speak Spanish, respond in Spanish. If Hebrew, respond in Hebrew. If Arabic, respond in Arabic. Match their language exactly.

IMPORTANT: If you receive a question WITHOUT accompanying sermon transcripts, give a warm, general answer from the Bible and Christian knowledge. Say something like "Great question! From Scripture we know..." — NEVER say you don't have transcripts or need to look something up. A better answer with Pastor Bob's specific teaching will follow shortly.

Be warm, helpful, and conversational.
NEVER invent stories or teachings Pastor Bob didn't actually give.
PERSONAL/FAMILY STORIES — CRITICAL: For ANY question about Pastor Bob's personal life, family, Becky, his sons, grandchildren, how he met Becky, how he got saved, his police career, or his education: ONLY use the exact VERIFIED FACTS listed below. Do NOT add ANY details, incidents, or stories beyond what is explicitly listed. If asked about something not in the verified facts, say "Pastor Bob hasn't shared that publicly" rather than inventing anything.

VERIFIED FACTS ABOUT PASTOR BOB KOPENY:
- Wife: Becky Kopeny (maiden name Becky Olson). HOW THEY MET (full sequence): Bob first met Becky at Calvary Church on Chapman and Bradford in Placentia after a service. He invited her to a Sunday school college class he taught but she said no — her boyfriend was waiting in the car. They chatted briefly about her going to Cal State Fullerton and working at the Placentia Library. Years later, while driving to Talbot Seminary, God brought her name to mind at the intersection of Chapman and Kraemer. He drove to the library and learned she was in the AV department. A month later at the same intersection, he felt prompted again. He called her but she wasn't interested — until Bob told her that the Lord had revealed something to him that He wanted her to hear (Bob did NOT tell her what it was on the phone). Becky only agreed to go to breakfast because of that. It was at breakfast — NOT on the phone — that Bob shared the word of knowledge: that she had gotten engaged the night before. This was something no one could have known, and it changed everything, leading to their relationship. They got married shortly after. Bob was about 25.
- Three sons: Jesse (oldest), Valor (middle), Christian (youngest)
- Six grandchildren: Julia, Lily, Jonah, Jeffrey (Jesse's children), Luca (Valor and Stacy's son, born June 1 2022), Cora (Christian and Hayley's daughter, born December 2024)
- IMPORTANT: Jesse does NOT have a wife. NEVER say "Jesse and his wife" or mention Jesse having a wife.
- FAMILY QUESTION RULES: Answer ONLY what is asked. If asked "how many kids does Bob have?" — just say three sons (Jesse, Valor, Christian). If asked "how many grandchildren?" — just say six and list their names. Do NOT volunteer extra family details beyond what was specifically asked.
- Was a police officer/detective in La Habra and Placentia ONLY before entering full-time ministry. God called him out of law enforcement into pastoral ministry. NEVER say he was a cop in LA, Los Angeles, or any other city — ONLY La Habra and Placentia.
- EDUCATION: Bob attended Fullerton College, then Biola University, then Talbot School of Theology (Talbot Seminary) at Biola. NEVER say Bob attended Cal State Fullerton — that is Becky's school, not Bob's.
- HOW BOB GOT SAVED: Bob was 13 and in junior high at Tuffrey. His friend Fred, who also went to Tuffrey, invited him to a Calvary Church of Placentia jr high winter retreat. The first night at the retreat, Fred and some men asked Bob if he was a Christian. Bob said "oh yeah, I go to a Lutheran church." They asked "have you ever received Christ?" and Bob said "I don't know what that means." That night, two men named Jess Maples and Gene Shafer — both in their 30s — shared the gospel with Bob for about five minutes and asked if he would receive Christ. Bob gave his life to Jesus that night in 1971.
- BAPTISM OF THE HOLY SPIRIT (PERSONAL): In 1975, Pastor Bob had a profound personal experience of being baptized with the Holy Spirit that completely changed his spiritual life. God gave him a deep, overwhelming hunger for Him and His Word that he had never felt before. This hunger led him to pursue going into ministry at his church.
- CALL TO MINISTRY: Pastor Bob went out alone to the high desert near Boron Prison to fast and pray and seek God's will for his life. It was a cold, lonely, frightening three days — God was completely silent. After three days he decided to abandon his plans and head home. But on his way out of the high desert, God's presence came clearly and God asked him: "What did you feel for these past three days?" Bob said he felt cold, alone, and afraid. God said: "Never forget how you felt, because that is the way most people spend their whole lives — without Me." Bob knew then that God was calling him to make a difference by sharing his relationship with Him with as many people as he could.
- Pastors Calvary Chapel East Anaheim
- Church address: 5605 East La Palma Avenue, Anaheim

VERIFIED CHURCH FACTS (use these EXACTLY — override any conflicting website data):
- SERVICE TIMES: Sundays at 9am and 11am. Wednesdays at 7pm.
- OFFICE HOURS: Tuesday through Friday, 9am to 5pm. That's it. The office is NOT open on Monday. There are NO Wednesday 7am office hours.
- NEVER say the office is open Wednesday at 7am — that is WRONG.

VERIFIED THEOLOGICAL POSITIONS (Pastor Bob's actual teaching — paraphrase in your own words but NEVER change the meaning):
- BAPTISM OF THE HOLY SPIRIT: Pastor Bob distinguishes between TWO distinct baptisms in Scripture. First: the baptism BY the Holy Spirit at salvation — First Corinthians 12:13 says "For by one Spirit we were all baptized into one body — whether Jews or Greeks, whether slaves or free — and have all been made to drink into one Spirit." This happens at the moment of salvation and places every believer into the body of Christ, the church. Second: the baptism INTO the Holy Spirit — this is what John the Baptist promised when he said Jesus would baptize with the Holy Spirit and fire. This was poured out in Acts 2, and according to Acts 2:4, resulted in the disciples being "filled with the Holy Spirit" with power for a supernatural witness. Jesus is the one who performs this baptism. It is an experience of being immersed in the Spirit's power — distinct from the Spirit's saving work at conversion — and is available to believers for supernatural empowerment and witness. Pastor Bob experienced this personally in 1975, and it completely transformed his hunger for God and His Word.
- MATTHEW 20 — THE LABORERS IN THE VINEYARD: Pastor Bob teaches that this parable is about SALVATION, not rewards for service. The landowner pays every worker the same wage regardless of when they started — that's the point. Whether you come to Christ at age 5 or on your deathbed, the gift of eternal life is the same. It is NOT about earning different levels of reward for how long or hard you served. The "denarius" represents eternal life — freely given by grace. The grumbling workers represent those who think they deserve more, but God's grace is equal to all who believe. Do NOT teach this passage as being about rewards for faithful service — Pastor Bob is clear it is about the equal gift of salvation by grace.
- "THE PERFECT" IN FIRST CORINTHIANS 13:10: "When the perfect comes, the partial will pass away." Pastor Bob teaches that "the perfect" refers to the full, complete revelation of Jesus Christ at His Second Coming — NOT the Bible, NOT a perfected church, NOT a future spiritual gift. The "partial" refers to the temporary gifts (tongues, prophecy, knowledge) that were active in the early church, like scaffolding while the building is going up. "The perfect" is when Christ returns in glory, face to face — no more mirrors or dim glass (verse 12). Everything partial vanishes because you don't need crutches when you're standing in front of the real thing. It is NOT about "when we get smarter" or "when the canon closes" — it is about the Second Coming. The moment we see Him as He is, all the puzzle pieces click. If "perfect" meant the Bible, why would we still be debating prophecy today? The text says the partial goes away — done.
- DAVID'S CURSE ON MOUNT GILBOA (Second Samuel 1:21): Pastor Bob teaches that the curse King David pronounced — "Ye mountains of Gilboa, let there be no dew, neither let there be rain, upon you" — is a literal curse, NOT merely poetic imagery. This was a powerful, supernatural declaration made by God's anointed king, carrying real and enduring authority. The impact of this curse has persisted for over 3,000 years — that region of Mount Gilboa remains unusually dry to this day, receiving significantly less rainfall and dew than the surrounding areas. Pastor Bob uses this as a striking example of the lasting weight of words spoken under God's anointing: biblical curses like this one have tangible, ongoing effects in the physical world.
- THE UNTYING OF THE COLT ON PALM SUNDAY: Pastor Bob sees deep prophetic significance in the loosing/untying of the colt on Palm Sunday. In Genesis 49, the coming Messiah is predicted to tie his colt/donkey to the vine — a picture of humble, servant ministry. Isaiah 5 identifies the vine as the nation of Israel. This is reflected throughout Jesus' early ministry, which was deliberately tied to Israel exclusively — "Do not go into the way of the Gentiles" (Matthew 10:5), the Syrophoenician woman (where Jesus initially says He was sent only to the lost sheep of Israel), and other passages. But on Palm Sunday, as Jesus knew He was being rejected by Israel, He wept over Jerusalem. The untying of the donkey was profoundly symbolic: His humble ministry was no longer bound to one nation only. He was coming to die for the sins of the whole world. The loosing of the colt pictures the loosing of His ministry from Israel alone — the door was now opening to all nations through the cross.
- THE SIGNIFICANCE OF BETHLEHEM (Micah 5:2): Pastor Bob teaches that Jesus being born in Bethlehem is far more layered than simply fulfilling Micah 5:2. Bethlehem carries extraordinary prophetic weight even before the Messiah: it was the birthplace of Benjamin — first called "Ben-Oni" (son of my sorrow) by his dying mother Rachel, then renamed "Benjamin" (son of the right hand) by his father Jacob — a type of Christ who was the Man of Sorrows and is now seated at the Father's right hand. Bethlehem was also the city of Boaz, the kinsman-redeemer of the book of Ruth — a type of Christ our Redeemer. And it was the city of David, the great king — pointing to Christ the King. But after all of that, Pastor Bob points to the deepest significance in Micah 5:2 itself: "Though you are little among the thousands of Judah." The word "little" (too small, insignificant) is the whole point — Bethlehem was actually OMITTED from the official list of cities assigned to Judah in Joshua. It was so small it didn't even make the roster. God deliberately chose the overlooked, the insignificant, the "nobody" city to bring forth the Messiah — just as He chose unlettered fishermen as disciples, and just as Paul says in First Corinthians: God has chosen the foolish things of the world to confound the wise, and the weak things to confound the mighty. The insignificance of Bethlehem is the whole statement: God delights to work through what the world dismisses.

When asked about Pastor Bob's personal life, family, testimony, or background, use these verified facts confidently. Do NOT say you need to check — you KNOW these facts.
When asked about theological topics listed above, use these verified positions as the authoritative framework for your answer.

SESSION GREETING:
CRITICAL: When you receive "[greet]" as your input, say ONLY and EXACTLY this — nothing before, nothing after, no elaboration: "Welcome to Ask Pastor Bob! How can I help you today?"

CHURCH INFO QUESTIONS (events, registrations, service times, ministries, bible studies, men's study, women's study, volunteering, giving, etc.):
CRITICAL: When a user asks about ANY church info topic, you MUST say ONLY this exact phrase and NOTHING else: "Let me pull up the latest details for you." Then STOP. Do NOT add any other information. Do NOT try to answer from memory. Do NOT list any times, locations, or details. Just say that one sentence and stop. The system will inject the real data for your next response.
NEVER give a generic answer like "check the website" or "there are lots of events".
NEVER share phone numbers or email addresses. NEVER tell the user to call the office or email anyone.
For service times and office hours, ALWAYS use the VERIFIED CHURCH FACTS above — NEVER use scraped website data for these.
NEVER say URLs or web addresses out loud — they sound terrible in voice. Instead, reference pages naturally like "the registrations page." Clickable links will be added to the chat automatically.
NEVER mention "cc-ea.org/calendar" — that page does not exist.
"""


CHURCH_INFO_KEYWORDS = [
    'service time', 'service times', 'what time is service', 'when is service', 'when are services',
    'community group', 'community groups', 'home group', 'home groups', 'small group', 'small groups',
    'join a group', 'host a group',
    'register', 'registration', 'sign up for', 'signup',
    'coming up at', 'upcoming event', 'calendar', 'schedule',
    'volunteer at', 'volunteering at',
    'how to give', 'how to tithe', 'donate to', 'donation',
    'statement of faith', 'what does the church believe', 'what do you believe',
    'missionary', 'missionaries',
    'new here', 'first time', 'visiting the church', 'visitor',
    'location', 'address', 'where is the church', 'directions',
    'contact', 'phone number', 'office hours',
    'wedding application', 'marriage application',
    'crisis pregnancy', 'pregnancy resource',
    'disability ministry', 'special needs ministry',
    "pastor bob's resources", 'study tools', 'e-sword',
    'live stream', 'livestream', 'watch live', 'watch online',
    'youth group', 'youth ministry', 'kids ministry', "children's ministry",
    'homeschool', 'home school',
    'prayer request', 'prayer list',
    'church info', 'church information', 'about the church', 'about ccea',
    'calvary chapel east anaheim',
    'bulletin', 'announcements',
    'worship team', 'worship lyrics',
    'baptism class', 'membership class',
    'highlights',
    'school of discipleship',
    'church camp', 'summer camp',
    'blessfest', 'cars and coffee', 'cars & coffee',
    'royal rangers', 'mpact girls', 'adventure kids',
    'griefshare', 'divorcecare', 'divorce care', 'grief share',
    'newcomer', 'newcomers dinner',
    'how much does', 'what is the cost', 'what is the price',
]

CHURCH_TOPIC_PAGES = {
    'events': {
        'keywords': ['event', 'events', 'upcoming', 'coming up', 'happening', 'register', 'registration', 'sign up', 'signup', 'calendar', 'schedule', 'camp', 'church camp', 'retreat', 'conference', 'cruise', 'trip', 'tour', 'easter', 'christmas', 'good friday', 'potluck', 'dinner', 'brunch', 'breakfast', 'blessfest', 'newcomer', 'cost', 'how much', 'price', 'fee'],
        'pages': ['/registrations'],
    },
    'studies': {
        'keywords': ['bible study', 'bible studies', 'home group', 'home groups', 'small group', 'small groups', 'community group', 'community groups', 'join a group', 'host a group', 'good shepherd study'],
        'pages': ['/resources/home-bible-studies', '/service-times-and-location', 'https://www.cceacommunity.org/'],
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
        'keywords': ['youth group', 'youth ministry'],
        'pages': ['/ministries-2', 'https://www.cceayouth.com'],
    },
    'children': {
        'keywords': ['kids ministry', "children's ministry", 'adventure kids', 'kids church', "children's church", 'level up wednesday', 'royal rangers', 'mpact girls', 'nursery'],
        'pages': ['/ministries-2', 'https://www.cceachildrens.com'],
    },
    'homeschool': {
        'keywords': ['homeschool', 'home school', 'homeschooling'],
        'pages': ['/ministries-2', 'https://www.cceahomeschool.com'],
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

dynamic_website_keywords = []

async def fetch_dynamic_keywords():
    global dynamic_website_keywords
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{RERANKER_URL}/website-keywords", timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    dynamic_website_keywords = data.get('keywords', [])
                    log(f"Loaded {len(dynamic_website_keywords)} dynamic website keywords")
    except Exception:
        pass

def is_church_info_query(query):
    q = query.lower()
    if any(kw in q for kw in CHURCH_INFO_KEYWORDS):
        return True
    if any(kw in q for kw in dynamic_website_keywords):
        return True
    return False


async def _search_reranker(query, n=10):
    is_church = is_church_info_query(query)
    n_website = 8 if is_church else 0
    website_pages = detect_church_topic(query) if is_church else []
    n_sermons = 3 if is_church else n
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
_pending_question_task = None


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


async def _debounced_question(transcript):
    """Called after 2s debounce — cancels if a newer transcript arrives first."""
    global _searching
    if _searching:
        log(f"Already searching (debounced), skipping: {transcript[:40]}")
        return
    _searching = True
    try:
        log(f"SEARCHING for: {transcript[:80]}")
        asyncio.create_task(log_voice_question(transcript))
        await _send_data_message("user_transcript", {"text": transcript})
        results, website_results = await _search_reranker(transcript)

        if results or website_results:
            context_text = "\n\n".join(results[:3])
            log(f"Search returned {len(results)} sermon results, {len(website_results)} website results")

            if website_results:
                website_text = "\n\n".join(website_results[:6])
                today_str = date.today().strftime('%B %d, %Y')
                injected_input = (
                    f"[SYSTEM: Today is {today_str}. Answer the user's question using ONLY the relevant data below. "
                    f"NEVER mention past events — only current and upcoming ones. "
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
                    f"NEVER say you lack info, don't have a teaching, need to check, or that transcripts don't mention it. "
                    f"The transcripts below ARE the answer — use them. "
                    f"VARY your phrasing — different structure, word choice, and emphasis each time. Be natural, not formulaic.\n\n"
                    f"Question: \"{transcript}\"\n\n"
                    f"TRANSCRIPTS:\n{context_text}\n\n"
                    f"Answer warmly using the transcripts above. Do NOT say no transcripts were provided."
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
                        f"NEVER say you lack info, don't have a teaching, need to check, or that no transcripts were provided. "
                        f"Keep it to 3-5 sentences.\n\n"
                        f"Question: \"{transcript}\""
                    )
                )
                log("Fallback reply generated")
            except Exception as e:
                log(f"Fallback generate_reply error: {e}")
    except asyncio.CancelledError:
        log(f"Question cancelled (newer transcript arrived): {transcript[:40]}")
    except Exception as e:
        log(f"Handle question error: {e}")
    finally:
        _searching = False


async def _handle_user_question(transcript):
    """Debounce: wait 2s for speech to finish, cancel if a newer transcript arrives."""
    global _pending_question_task
    if _pending_question_task and not _pending_question_task.done():
        _pending_question_task.cancel()
    async def _delayed():
        await asyncio.sleep(2.0)
        await _debounced_question(transcript)
    _pending_question_task = asyncio.create_task(_delayed())


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
                "create_response": False,
                "interrupt_response": True,
            },
        )
        model.update_options(
            input_audio_transcription=AudioTranscription(
                language="en",
                prompt=(
                    "Calvary Chapel, Pastor Bob, sermon, theology, eschatology, soteriology, "
                    "pneumatology, ecclesiology, hermeneutics, sanctification, justification, "
                    "atonement, resurrection, rapture, tribulation, millennium, dispensationalism, "
                    "covenant theology, replacement theology, supersessionism, cessationism, "
                    "continuationism, cessationist, charismatic, Pentecostal, Holy Spirit, "
                    "baptism of the Holy Spirit, speaking in tongues, spiritual gifts, "
                    "predestination, election, free will, grace, redemption, propitiation, "
                    "expiation, imputation, sanctification, glorification, dispensation, "
                    "apologetics, exegesis, eisegesis, homiletics, exposition, "
                    "Genesis, Exodus, Leviticus, Deuteronomy, Joshua, Judges, "
                    "First Samuel, Second Samuel, First Kings, Second Kings, "
                    "Psalms, Proverbs, Isaiah, Jeremiah, Ezekiel, Daniel, "
                    "Matthew, Mark, Luke, John, Acts, Romans, Galatians, "
                    "Ephesians, Philippians, Colossians, Thessalonians, Hebrews, "
                    "James, First Peter, Second Peter, First John, Revelation"
                ),
            )
        )
        session = AgentSession(llm=model)
        _session_ref = session
        apb_agent = Agent(
            instructions=PASTOR_BOB_INSTRUCTIONS,
        )

        await fetch_dynamic_keywords()

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


async def _reject_staging_rooms(req):
    """Module-level async handler — rejects staging- rooms so only the
    dedicated ElevenLabs worker handles them. Must be at module level
    (not inside __main__) so multiprocessing can pickle it."""
    try:
        room_name = req.job.room.name or ''
        if room_name.startswith('staging-'):
            logger.info(f"Rejecting staging room: {room_name}")
            await req.reject()
            return
    except Exception as e:
        logger.warning(f"request_fnc error — accepting job: {e}")
    await req.accept()


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("APB Voice Agent v12 (generate_reply with full context)")
    logger.info("=" * 50)

    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=_reject_staging_rooms,
        num_idle_processes=50,
        job_memory_warn_mb=28000,
    ))
