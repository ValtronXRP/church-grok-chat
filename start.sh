#!/bin/bash
# Start script for Railway deployment
# Runs Node.js server, Python voice agent, ChromaDB API, and Reranker service

echo "Starting Church Grok Chat Services..."
echo "Chroma Cloud mode: ${CHROMA_API_KEY:+enabled}"

# Railway sets PORT for the main service. We reserve it for Node.js (the public-facing server).
MAIN_PORT=${PORT:-3001}

# Internal service ports (not exposed to Railway)
CHROMADB_PORT=5001
RERANKER_PORT_NUM=5050

export SERMON_API_URL=http://localhost:$CHROMADB_PORT
export RERANKER_URL=http://127.0.0.1:$RERANKER_PORT_NUM
echo "Main server port: $MAIN_PORT"
echo "SERMON_API_URL: $SERMON_API_URL"
echo "RERANKER_URL: $RERANKER_URL"

# Start the reranker service (uses RERANKER_PORT env var, not PORT)
echo "Starting reranker service on port $RERANKER_PORT_NUM..."
RERANKER_PORT=$RERANKER_PORT_NUM PORT=$RERANKER_PORT_NUM python reranker_service.py &
RERANKER_PID=$!

# Start ChromaDB API on its own port
echo "Starting ChromaDB API on port $CHROMADB_PORT..."
cd chromadb_api
PORT=$CHROMADB_PORT python app.py &
CHROMADB_PID=$!
cd ..

# Wait for services to start
sleep 5

# Start the voice agent in the background with crash detection
echo "Starting voice agent..."
(
  cd grok-voice-agent
  while true; do
    echo "[start.sh] Launching voice agent..."
    python agent_direct.py dev 2>&1
    EXIT_CODE=$?
    echo "[start.sh] Voice agent exited with code $EXIT_CODE"
    if [ $EXIT_CODE -ne 0 ]; then
      echo "[start.sh] Voice agent crashed! Restarting in 5 seconds..."
      sleep 5
    else
      echo "[start.sh] Voice agent exited cleanly"
      break
    fi
  done
) &
AGENT_PID=$!

# Start the Node.js server on Railway's PORT
echo "Starting web server on port $MAIN_PORT..."
PORT=$MAIN_PORT npm start &
SERVER_PID=$!

# Start sermon auto-ingest scheduler (runs on Monday and Wednesday at 6 AM PST)
echo "Starting sermon auto-ingest scheduler (Mon/Wed 6 AM PST)..."
(
  LAST_INGEST_DAY=""
  while true; do
    CURRENT_DAY=$(TZ='America/Los_Angeles' date +%u)
    CURRENT_HOUR=$(TZ='America/Los_Angeles' date +%H)
    TODAY_KEY=$(TZ='America/Los_Angeles' date +%Y-%m-%d)
    # Monday=1, Wednesday=3, run at 6 AM PST
    if { [ "$CURRENT_DAY" = "1" ] || [ "$CURRENT_DAY" = "3" ]; } && [ "$CURRENT_HOUR" = "06" ] && [ "$LAST_INGEST_DAY" != "$TODAY_KEY" ]; then
      echo "[auto-ingest] Starting scheduled sermon ingest ($(TZ='America/Los_Angeles' date))..."
      python auto_ingest_sermons.py --full-scan 2>&1 | while read line; do echo "[auto-ingest] $line"; done
      LAST_INGEST_DAY="$TODAY_KEY"
      echo "[auto-ingest] Ingest complete. Next check in 1 hour."
    fi
    sleep 3600
  done
) &
INGEST_PID=$!

# Wait for all processes
echo "Services started:"
echo "  Reranker PID: $RERANKER_PID (port $RERANKER_PORT_NUM)"
echo "  ChromaDB API PID: $CHROMADB_PID (port $CHROMADB_PORT)"
echo "  Voice Agent PID: $AGENT_PID"
echo "  Web Server PID: $SERVER_PID (port $MAIN_PORT)"
echo "  Auto-Ingest PID: $INGEST_PID (Mon/Wed 6AM PST)"
wait $RERANKER_PID $CHROMADB_PID $AGENT_PID $SERVER_PID
