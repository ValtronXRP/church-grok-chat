#!/bin/bash
# Start script for Railway deployment
# Runs Node.js server, Python voice agent, and ChromaDB API

echo "Starting Church Grok Chat Services..."
echo "Chroma Cloud mode: ${CHROMA_API_KEY:+enabled}"

# Set SERMON_API_URL if not set (internal communication)
export SERMON_API_URL=${SERMON_API_URL:-http://localhost:5001}
echo "SERMON_API_URL: $SERMON_API_URL"

# Start ChromaDB API in the background
echo "Starting ChromaDB API..."
cd chromadb_api
python app.py &
CHROMADB_PID=$!
cd ..

# Wait for ChromaDB to start
sleep 3

# Start the voice agent in the background
echo "Starting voice agent..."
cd grok-voice-agent
python agent_direct.py &
AGENT_PID=$!
cd ..

# Start the Node.js server
echo "Starting web server..."
npm start &
SERVER_PID=$!

# Wait for all processes
echo "Services started:"
echo "  ChromaDB API PID: $CHROMADB_PID"
echo "  Voice Agent PID: $AGENT_PID"
echo "  Web Server PID: $SERVER_PID"
wait $CHROMADB_PID $AGENT_PID $SERVER_PID
