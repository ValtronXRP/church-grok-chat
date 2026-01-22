#!/bin/bash

echo "Starting APB Voice System..."
echo "================================"

# Kill any existing processes
echo "Cleaning up old processes..."
pkill -f "node.*server.js" 2>/dev/null
pkill -f "python.*agent" 2>/dev/null
sleep 2

# Start the Node.js server
echo "Starting server..."
cd /Users/valorkopeny/Desktop/church-grok-chat
node server.js > server.log 2>&1 &
SERVER_PID=$!
echo "Server started (PID: $SERVER_PID)"

# Wait for server to be ready
sleep 2

# Start the Python agent
echo "Starting voice agent..."
cd /Users/valorkopeny/Desktop/church-grok-chat/grok-voice-agent
source venv311/bin/activate
python agent.py start &
AGENT_PID=$!
echo "Agent started (PID: $AGENT_PID)"

echo ""
echo "================================"
echo "✅ System ready!"
echo ""
echo "📺 Open: http://localhost:3001/chat.html"
echo ""
echo "To stop: Press Ctrl+C"
echo "================================"

# Wait and handle shutdown
trap "echo 'Shutting down...'; kill $SERVER_PID $AGENT_PID 2>/dev/null; exit" INT TERM
wait