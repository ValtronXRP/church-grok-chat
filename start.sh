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

# Start the voice agent in the background
echo "Starting voice agent..."
cd grok-voice-agent
python agent_direct.py start 2>/dev/null || python agent_direct.py 2>/dev/null &
AGENT_PID=$!
cd ..

# Start the Node.js server on Railway's PORT
echo "Starting web server on port $MAIN_PORT..."
PORT=$MAIN_PORT npm start &
SERVER_PID=$!

# Wait for all processes
echo "Services started:"
echo "  Reranker PID: $RERANKER_PID (port $RERANKER_PORT_NUM)"
echo "  ChromaDB API PID: $CHROMADB_PID (port $CHROMADB_PORT)"
echo "  Voice Agent PID: $AGENT_PID"
echo "  Web Server PID: $SERVER_PID (port $MAIN_PORT)"
wait $RERANKER_PID $CHROMADB_PID $AGENT_PID $SERVER_PID
