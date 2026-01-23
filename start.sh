#!/bin/bash
# Start script for Railway deployment
# Runs both the Node.js server and Python voice agent

echo "Starting Church Grok Chat Services..."

# Start the voice agent in the background
echo "Starting voice agent..."
cd grok-voice-agent
python3.11 agent_direct.py &
AGENT_PID=$!
cd ..

# Start the Node.js server
echo "Starting web server..."
npm start &
SERVER_PID=$!

# Wait for both processes
echo "Services started. Agent PID: $AGENT_PID, Server PID: $SERVER_PID"
wait $AGENT_PID $SERVER_PID