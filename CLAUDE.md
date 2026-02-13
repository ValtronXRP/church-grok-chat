# Ask Pastor Bob - Development Notes

## Current State (2026-02-10)

### Data Sources
- **JSON3 Folders 1-3**: 594 sermon files
- **JSON3 Folder 4**: 119 sermon files  
- **Batch files (SERMONS_ZIP_05)**: 457 sermons in 10 batch JSON files
- **Total**: ~1,170 sermon sources

### ChromaDB Collections (APB database) - CURRENT
| Collection | Records | Embedding Dim | Status |
|------------|---------|---------------|--------|
| sermon_segments_v2 | 34,553 | 768 (mpnet) | ACTIVE - high quality semantic search |
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
User Query → server.js → reranker_service.py → ChromaDB
                              ↓
                    mpnet (768-dim embeddings)
                    + cross-encoder reranking
```

### Reranker Service (Bundled in Main Container)
- **Memory**: ~2GB (mpnet model + cross-encoder), runs inside main container (8GB Hobby plan)
- **Port**: 5050 (internal, via RERANKER_PORT env var)
- **First query**: ~60s (CPU warmup), subsequent: ~15s
- **Status**: LIVE and working as of 2026-02-10

### Key Files
- `reranker_service.py` - Flask service with 768-dim embeddings + reranking
- `rebuild_embeddings.py` - Script to rebuild collections (uses illustrations_v4_all.json)
- `server.js` - Main backend, calls reranker service
- `grok-voice-agent/agent_direct.py` - Voice agent

### Chroma Cloud Credentials
```
CHROMA_API_KEY=ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd
CHROMA_TENANT=4b12a7c7-2fb4-4edc-9b6e-c2a77305136b
CHROMA_DATABASE=APB
```

### Railway Deployment
- Main app: https://web-production-b652a.up.railway.app/
- Reranker: bundled in main container (port 5050 internal)

### Voice Agent - WORKING CONFIG (2026-02-13) — DO NOT CHANGE ANSWER LOGIC
The voice agent is confirmed working with 3 consecutive correct answers.

**Critical: Do NOT modify these components:**
- `grok-voice-agent/agent_direct.py` — answer generation logic, instructions, tool, search
- `RealtimeModel(voice="Aria")` with `ServerVad(threshold=0.8, silence_duration_ms=800, create_response=True, interrupt_response=False)`
- `@function_tool search_pastor_bob_sermons` — dual parallel xAI search, 8 results returned
- `Agent(instructions=PASTOR_BOB_INSTRUCTIONS, tools=[search_pastor_bob_sermons])` — function_tool ONLY, no FileSearch

**Architecture:**
```
User speaks → xAI Realtime VAD → model calls search_pastor_bob_sermons → 
  xAI Documents Search API (collection_27b3cd99) → 16 results merged → 
  model synthesizes → speaks response
```

**Key settings that make it work:**
- `function_tool` only (no FileSearch — it caused `unknown AI function 'collections_search'` errors)
- `interrupt_response=False` — prevents agent from being cut off
- `create_response=True` — model auto-responds after VAD detects silence
- Instructions: "CRITICAL: You MUST call search_pastor_bob_sermons for EVERY question"
- Frontend: no auto-disconnect on `agent_transcript`, only on `speech_complete` with 2-minute timeout

### Remaining Tasks
1. **Optimize reranker speed** - first query ~60s due to CPU warmup, consider caching or warming

### Deployment Notes
- Reranker bundled into main container via `start.sh` (not separate Railway service)
- `start.sh` manages port allocation: Node.js on $PORT (8080), ChromaDB API on 5001, Reranker on 5050
- `combined_requirements.txt` used to resolve Python dependency conflicts (numpy/scipy)
- IPv4 (127.0.0.1) used for internal service communication (not localhost, which resolves to IPv6)
