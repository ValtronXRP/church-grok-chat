# Ask Pastor Bob - Development Notes

## Current State (2026-02-18)

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
| church_website | 10 | 768 (mpnet) | ACTIVE |

### Old Collections (DO NOT USE)
- sermon_segments: 72,948 (384-dim, poor semantic understanding)
- illustrations_v4: 11,521 (384-dim)

### Semantic Search Quality - FIXED!
Test query: "What is the baptism of the Holy Spirit?"
- **Before**: Returned bathtub baptism illustrations (keyword matching)
- **After**: Returns R.A. Torrey quotes about Holy Spirit baptism (semantic understanding)

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
- `grok-voice-agent/agent_direct.py` - Voice agent v9 (reranker search, timeout handling)
- `public/chat.html` - Frontend with voice, text chat, sermon clips, illustration clips

### Voice Agent v9 - RERANKER SEARCH + TIMEOUT HANDLING (2026-02-18)

v8 used xAI Documents Search API which hit quota limits and had 0 indexed documents.
v9 switches to local reranker service (same ChromaDB data, reliable).

**Architecture:**
```
User speaks → xAI Realtime VAD (create_response=False) → 
  user_input_transcribed event fires → agent runs _search_reranker() → 
  reranker_service.py (localhost:5050/search/fast-all) → ChromaDB → results merged → 
  session.generate_reply(instructions=search_results) → model speaks answer
```

**Key settings:**
- `create_response=False` — model does NOT auto-respond; we control when it speaks
- `interrupt_response=False` — prevents agent from being cut off
- `user_input_transcribed` event with `is_final=True` triggers search
- `_search_reranker()` calls local reranker at `127.0.0.1:5050/search/fast-all`
- `session.generate_reply(instructions=...)` feeds search results directly to model
- No `function_tool` needed — search happens in our code, not model's decision
- `generate_reply()` has internal 10s timeout (NOT configurable) — context kept small (~1100 chars)
- No `asyncio.wait_for` wrappers (they made timeouts worse by triggering retries)
- Greeting failure does NOT crash the session — continues listening
- `start.sh` auto-restarts voice agent on crash with 5s delay
- Frontend: auto-disconnect after 10 seconds of inactivity (`AUTO_DISCONNECT_DELAY = 10000`)

**Verified Facts in Agent Instructions:**
- Wife: Becky Kopeny (met at Calvary Chapel, Placentia Library, word of knowledge)
- Three sons: Jesse, Valor, Christian
- Former police officer/detective, called into ministry
- Saved at age 13 at Jr. High camp (Campus Crusade)
- Pastors Calvary Chapel East Anaheim

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

### Remaining Tasks
1. **Optimize reranker speed** - first query ~60s due to CPU warmup, consider caching or warming
2. **Monitor voice agent reliability** - verify timeout fixes prevent the revert-to-generic issue

### Deployment Notes
- Reranker bundled into main container via `start.sh` (not separate Railway service)
- `start.sh` manages port allocation: Node.js on $PORT (8080), ChromaDB API on 5001, Reranker on 5050
- `combined_requirements.txt` used to resolve Python dependency conflicts (numpy/scipy)
- IPv4 (127.0.0.1) used for internal service communication (not localhost, which resolves to IPv6)
- Voice agent RERANKER_URL env var must point to reranker service (default: http://127.0.0.1:5050)
