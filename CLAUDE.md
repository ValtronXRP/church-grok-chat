# Ask Pastor Bob - Development Notes

## Current State (2026-02-21)

### Data Sources
- **JSON3 Folders 1-3**: 594 sermon files
- **JSON3 Folder 4**: 119 sermon files  
- **Batch files (SERMONS_ZIP_05)**: 457 sermons in 10 batch JSON files
- **Total**: ~1,170 sermon sources

### ChromaDB Collections (APB database) - CURRENT
| Collection | Records | Embedding Dim | Status |
|------------|---------|---------------|--------|
| sermon_segments_v2 | 31,928 | 768 (mpnet) | ACTIVE - ENRICHED, correct timestamps, worship filtered |
| illustrations_v5 | 23,559 | 768 (mpnet) | ACTIVE - all illustrations loaded |
| church_website | ~69 | 768 (mpnet) | ACTIVE - auto-refreshes hourly from cc-ea.org |

### Old Collections (DO NOT USE)
- sermon_segments: 72,948 (384-dim, poor semantic understanding)
- illustrations_v4: 11,521 (384-dim)

### Semantic Search Quality - FIXED!
Test query: "What is the baptism of the Holy Spirit?"
- **Before**: Returned bathtub baptism illustrations (keyword matching)
- **After**: Returns R.A. Torrey quotes about Holy Spirit baptism (semantic understanding)

### Church Website Database (cc-ea.org)
- **Separate from sermons/illustrations** — stored in `church_website` ChromaDB collection
- **Scraper**: `scrape_church_website.py` scrapes 20 public pages from cc-ea.org
- **Auto-refresh**: Background thread in reranker_service.py refreshes hourly. Manual: `POST /refresh-website`
- **On-demand only**: Website DB is ONLY queried when user asks about church info (events, studies, service times, registrations, etc.) — detected by `isChurchInfoQuery()` in server.js and `is_church_info_query()` in agent_direct.py
- **URL inclusion**: Agents are instructed to include the cc-ea.org source URL in their response so users can visit the page

### Architecture
```
Frontend (chat.html) ─┬─ Text Chat ──→ server.js ──→ reranker_service.py ──→ ChromaDB
                      │                    ↓
                      │         mpnet (768-dim embeddings)
                      │         + cross-encoder reranking
                      │
                      ├─ Voice Agent ──→ LiveKit (xAI RealtimeModel)
                      │                    ↓
                      │         user_input_transcribed event
                      │                    ↓
                      │         _search_reranker() ──→ reranker_service.py ──→ ChromaDB
                      │                    ↓
                      │         session.generate_reply(instructions=search_results)
                      │
                      ├─ Sermon Clips (left sidebar) ──→ /api/clips ──→ reranker_service.py ──→ ChromaDB
                      │         Populated independently from user transcript text input
                      │         Embedded YouTube players with timestamped URLs
                      │
                      └─ Illustration Clips (right sidebar) ──→ /api/clips ──→ ChromaDB illustrations_v5
```

### Reranker Service (Bundled in Main Container)
- **Memory**: ~2GB (mpnet model + cross-encoder), runs inside main container (8GB Hobby plan)
- **Port**: 5050 (internal, via RERANKER_PORT env var)
- **Endpoint**: `/search/fast-all` - returns sermons + illustrations with reranking
- **First query**: ~60s (CPU warmup), subsequent: ~15s
- **Status**: LIVE and working

### Key Files
- `reranker_service.py` - Flask service with 768-dim embeddings + reranking
- `rebuild_embeddings.py` - Original script to rebuild collections
- `rebuild_enriched.py` - Enriched rebuild with proper titles, correct timestamps, worship filtering
- `upload_to_xai_collection.py` - Upload enriched segments to xAI collection (NOT currently used)
- `video_title_map.json` - 728 sermon titles (batch files + YouTube oEmbed)
- `server.js` - Main backend, calls reranker service, /api/clips endpoint
- `grok-voice-agent/agent_direct.py` - Voice agent v12 (generate_reply with full context)
- `public/chat.html` - Frontend with voice, text chat, sermon clips, illustration clips

### Voice Agent v12 - WORKING (2026-02-21)

**Failed approaches (DO NOT USE):**
- v9: `create_response=False` + manual `generate_reply()` — race condition, model auto-responds before search
- v10: `@function_tool` — tool executes and returns results but xAI RealtimeModel silently ignores them (known bug: LiveKit Issue #2383)
- v11: `on_user_turn_completed` hook — never fires with xAI RealtimeModel (hooks/nodes don't work with realtime models)
- Explicit dispatch (`CreateDispatch` API + `agent_name`) — stale workers from previous deploys intercept dispatches, causing sessions with no entrypoint
- Large context payload (6 results × 1200 chars + full system prompt) — exceeds `generate_reply` 10s internal timeout, model generates text but audio never plays

**v12 (WORKING):** `user_input_transcribed` event → search → `generate_reply(instructions=compact_context)` which REPLACES the model's instructions for realtime models, forcing the response to use sermon transcripts.

**Architecture:**
```
User speaks → xAI Realtime Model (native VAD, auto turn detection) →
  user_input_transcribed event fires (is_final=True) →
  _handle_user_question() called →
  _search_reranker(transcript) → reranker_service.py (localhost:5050/search/fast-all) → ChromaDB →
  Returns top 3 sermon excerpts (600 chars each) →
  session.generate_reply(instructions=COMPACT_PROMPT + SERMON_TRANSCRIPTS) →
  generate_reply REPLACES instructions and CANCELS any auto-response →
  Model speaks answer using the provided transcripts
```

**Dispatch: Automatic (NO agent_name, NO CreateDispatch API)**
- `WorkerOptions` has NO `agent_name` — worker registers as unnamed
- Server `/token` endpoint creates room + returns token (NO explicit dispatch call)
- LiveKit auto-dispatches agent when participant joins any room
- This avoids stale worker routing that caused 50%+ session failures with explicit dispatch

**Context Payload (CRITICAL — DO NOT INCREASE):**
- Top 3 results at 600 chars each (~1800 chars of transcripts)
- Compact instructions (~200 chars) instead of full system prompt (~2000 chars)
- Total payload ~2KB — fits within `generate_reply` 10s internal timeout
- PREVIOUSLY: 6 results × 1200 chars + full system prompt = ~10KB → timed out, audio never played
- If payload is too large, the model generates text (visible in chat) but voice audio is killed by timeout

**Room Name Reuse (sessionStorage):**
- Frontend generates room name `apb-session-{timestamp}-{random}` and stores in `sessionStorage`
- Same tab reuses the same room name across refreshes (prevents idle process exhaustion)
- Room name cleared on explicit disconnect (button click or countdown timer)
- Server accepts client-provided room name if it starts with `apb-session-`

**Key settings:**
- `user_input_transcribed` event with `is_final=True` triggers search
- `_search_reranker()` calls reranker at `127.0.0.1:5050/search/fast-all`
- `session.generate_reply(instructions=...)` REPLACES instructions for realtime models (not append)
- Compact prompt + top 3 sermon transcripts (600 chars each) sent as instructions
- `_searching` flag prevents concurrent searches from overlapping
- `generate_reply()` has internal 10s timeout (NOT configurable) — produces timeout error after agent finishes speaking but this is harmless
- Greeting uses `session.generate_reply(instructions=...)` (one-time)
- `conversation_item_added` event sends transcript to frontend via data message
- `start.sh` runs `python agent_direct.py dev` (dev mode = `load_threshold=inf`, never rejects jobs)
- `start.sh` auto-restarts voice agent on crash with 5s delay
- Frontend: visible countdown timer starts when agent stops speaking (ActiveSpeakersChanged event)
- `num_idle_processes=5` and `job_memory_warn_mb=2000`
- `pagehide` event on frontend calls `room.disconnect()` for cleanup
- Frontend calls `room.disconnect()` before creating new connection

**Verified Facts in Agent Instructions (KEEP UPDATED):**
- Wife: Becky Kopeny (maiden name Becky Olson). Bob first met Becky at Calvary Church on Chapman and Madison in Placentia after a service — invited her to his Sunday school college class, she said no (boyfriend in car). Brief chat: Cal State Fullerton, worked at Placentia Library. Years later, driving to Talbot Seminary, God brought her name to mind at Chapman and Kraemer. He drove to the library, learned she was in the AV department. A month later at same intersection, prompted again. He called her but she wasn't interested — until Bob told her the Lord had revealed something to him that He wanted her to hear (Bob did NOT say what it was on the phone). Becky only agreed to breakfast because of that. It was at breakfast — NOT on the phone — that Bob shared the word of knowledge: that she'd gotten engaged the night before. Something no one could have known. This changed everything. They got married shortly after. Bob was about 25.
- Three sons: Jesse, Valor, Christian
- Six grandchildren: Jesse's 4 (Julia, Lily, Jonah, Jeffrey), Valor's son Luca (born June 1 2022), Christian's daughter Cora (born Dec 2024)
- Former police officer/detective, called into ministry
- HOW BOB GOT SAVED: Bob was 13 at Tuffrey Junior High. His friend Fred (also at Tuffrey) invited him to a Campus Crusade ministry camp. First night, Fred and some men asked if he was a Christian — Bob said "I go to a Lutheran church." They asked "have you ever received Christ?" — Bob said "I don't know what that means." That night Jeff Maples and Gene Schaeffer (both in their 30s) shared the gospel for 5 minutes. Bob gave his life to Jesus in 1971.
- Pastors Calvary Chapel East Anaheim

**Verified Theological Positions (KEEP IN SYNC with agent_direct.py AND chat.html system prompt):**
- BAPTISM OF THE HOLY SPIRIT: Happens at salvation (every believer receives the Spirit), BUT is NOT only a one-time event. The Spirit's anointing can happen again and again. A believer can receive secondary experiences where the Spirit takes greater control — speaking in tongues, fervent prayer, spiritual gifts, other anointings. The Spirit is like oil that anoints and water that quenches, allowing you to thrive in a parched world. The baptism/anointing of the Spirit can happen over and over — NOT limited to a single moment at salvation.

**Voice Agent Rules (KEEP IN SYNC with agent_direct.py):**
- Bible book names: ALWAYS say "First John" NOT "one John" or "1 John", "Second Corinthians" NOT "two Corinthians"
- ALWAYS spell out First, Second, Third for ALL numbered Bible books
- Say "Pastor Bob teaches..." and deliver with depth
- Keep answers to 3-5 sentences for voice
- NEVER say "I'd need to check", "I don't have a specific teaching", "Let me look into that"
- NEVER mention searching, tools, clips, segments, or transcripts
- NEVER invent stories or teachings Pastor Bob didn't actually give
- NEVER flatten a nuanced teaching into one simple sentence

### Sermon Clips (Left Sidebar) - WORKING
- Populated independently from voice agent
- Frontend calls `/api/clips` with user's transcript text
- server.js queries reranker_service.py which searches ChromaDB sermon_segments_v2
- Returns timestamped YouTube embeds
- Generic livestream titles filtered out (Sunday Morning Live, Wednesday Night Live, etc.)
- Clips now show correct timestamps throughout full sermons (not just first 1-2 minutes)

### Illustration Clips (Right Sidebar) - WORKING
- Populated from ChromaDB illustrations_v5 collection
- 23,559 illustration segments

### Text Chat - WORKING
- Uses Grok API via server.js
- Conversation history maintained
- Calls reranker for sermon context before generating response

### ChromaDB Rebuild Details (2026-02-18)
- 31,928 chunks (down from 34,553 after worship/intro filtering)
- 100% have proper sermon titles via video_title_map.json
- Correct timestamps throughout full sermons (fixed chunk_segments() overlap bug)
- Worship, music, announcements, intros filtered via find_sermon_start()
- SERMON_START_INDICATORS: Bible book names, "let's pray", "turn to", etc.
- WORSHIP_INDICATORS / INTRO_INDICATORS: "praise", "worship", "announcements", etc.

### xAI Collection (NOT ACTIVE)
- `collection_cf25e166-a73c-46a0-a03c-24afa4a6e6a6` (pastor_bob_sermons_v2)
- Upload hit quota limit at ~25,100/31,928 documents
- ALL uploaded documents had `processing_status: Skipped`, `last_indexed_at: null`
- Voice agent switched to local reranker instead

### Chroma Cloud Credentials
```
CHROMA_API_KEY=ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd
CHROMA_TENANT=4b12a7c7-2fb4-4edc-9b6e-c2a77305136b
CHROMA_DATABASE=APB
```

### Railway Deployment
- Main app: https://web-production-b652a.up.railway.app/
- Reranker: bundled in main container (port 5050 internal)
- Voice agent: separate LiveKit-based service

### Bugs Fixed (2026-02-18)
1. **Chunk timestamps stuck at 0-25s**: `chunk_segments()` overlap logic never reset `current_start_sec`. Rewrote with `next_start_sec` tracking.
2. **Worship/intro indexed instead of teaching**: Added `find_sermon_start()` to skip worship content.
3. **Double transcription in chat**: Removed duplicate `addMessage` from `user_transcript` handler.
4. **Generic livestream clips**: Filtered titles starting with "Sunday Morning Live", etc.
5. **Voice agent revert after refresh**: `generate_reply` timeouts caused silent failures. Added `asyncio.wait_for()` with retry.
6. **Auto-disconnect too long**: Changed from 120s to 10s.

### Bugs Fixed (2026-02-21)
7. **Voice agent not receiving dispatches**: Stale workers from previous deploys intercepted explicit `CreateDispatch` calls. Switched to automatic dispatch (no `agent_name`).
8. **Voice audio not playing (text only)**: Context payload (6×1200 chars + full prompt = ~10KB) exceeded `generate_reply` 10s timeout. Reduced to 3×600 chars + compact prompt = ~2KB.
9. **Idle process exhaustion on refresh**: Each refresh created new unique room, consuming idle processes. Added `sessionStorage` room name reuse per tab.

### Remaining Tasks
1. **Optimize reranker speed** - first query ~60s due to CPU warmup, consider caching or warming
2. **Re-enable user interruption** - once core RAG is stable, test interrupt_response=True
3. **generate_reply 10s timeout** - harmless error after agent speaks, log shows `Error in _realtime_reply_task` but audio plays fine

### Deployment Notes
- Reranker bundled into main container via `start.sh` (not separate Railway service)
- `start.sh` manages port allocation: Node.js on $PORT (8080), ChromaDB API on 5001, Reranker on 5050
- `start.sh` runs voice agent in dev mode: `python agent_direct.py dev` (load_threshold=inf)
- `combined_requirements.txt` used to resolve Python dependency conflicts (numpy/scipy)
- IPv4 (127.0.0.1) used for internal service communication (not localhost, which resolves to IPv6)
- Voice agent RERANKER_URL env var must point to reranker service (default: http://127.0.0.1:5050)
- NO `agent_name` on WorkerOptions — automatic dispatch, no `CreateDispatch` API call
- Server `/token` endpoint accepts client room name, only generates new if not provided
