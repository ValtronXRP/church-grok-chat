/**
 * Voice Bridge - Connects LiveKit voice to existing text chat API
 * This bridges voice input/output to the working text chat system
 */

const express = require('express');
const bodyParser = require('body-parser');
const axios = require('axios');

const app = express();
app.use(bodyParser.json());

// Store active voice sessions
const voiceSessions = new Map();

// Bridge voice to text chat API
app.post('/api/voice/transcribe', async (req, res) => {
  try {
    const { sessionId, transcript, userId } = req.body;
    
    console.log(`Voice transcription from ${userId}: ${transcript}`);
    
    // Send to existing chat API
    const chatResponse = await axios.post('http://localhost:5000/api/chat', {
      message: transcript,
      userId: userId || 'voice-user',
      isVoice: true
    });
    
    const responseText = chatResponse.data.response;
    console.log(`Chat response: ${responseText}`);
    
    // Return text to be spoken
    res.json({
      success: true,
      response: responseText,
      sessionId: sessionId
    });
    
  } catch (error) {
    console.error('Voice bridge error:', error);
    res.status(500).json({
      success: false,
      error: error.message
    });
  }
});

// Get greeting for voice session
app.post('/api/voice/greeting', async (req, res) => {
  const { sessionId, userId } = req.body;
  
  voiceSessions.set(sessionId, {
    userId: userId,
    startTime: new Date()
  });
  
  res.json({
    success: true,
    greeting: "Welcome to Ask Pastor Bob! How can I help you today?",
    sessionId: sessionId
  });
});

// End voice session
app.post('/api/voice/end', (req, res) => {
  const { sessionId } = req.body;
  voiceSessions.delete(sessionId);
  
  res.json({
    success: true,
    message: "Session ended"
  });
});

const PORT = process.env.VOICE_BRIDGE_PORT || 5002;
app.listen(PORT, () => {
  console.log(`Voice bridge running on port ${PORT}`);
  console.log(`Bridging voice to chat API at http://localhost:5000`);
});