const express = require('express');
const { AccessToken } = require('livekit-server-sdk');
const path = require('path');
require('dotenv').config();

const app = express();
app.use(express.json());
app.use(express.static('public'));

// Configuration
const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY;
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.LIVEKIT_URL;

// Use a CONSISTENT room name - this is critical!
// The agent will auto-join any room, but we want consistency
const ROOM_NAME = 'apb-voice-room';

console.log('\n🔧 Configuration:');
console.log(`   LIVEKIT_URL: ${LIVEKIT_URL}`);
console.log(`   LIVEKIT_API_KEY: ${LIVEKIT_API_KEY ? '✅ Set' : '❌ Missing'}`);
console.log(`   ROOM_NAME: ${ROOM_NAME}`);

app.post('/token', async (req, res) => {
  try {
    const participantName = `user_${Date.now()}`;
    
    console.log(`\n📞 Token request`);
    console.log(`   Room: ${ROOM_NAME}`);
    console.log(`   Participant: ${participantName}`);

    const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
      identity: participantName,
      ttl: '1h',
    });
    
    at.addGrant({
      room: ROOM_NAME,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    });
    
    const token = await at.toJwt();
    
    // Send back all the info the client needs
    const response = {
      token: token,
      url: LIVEKIT_URL,
      roomName: ROOM_NAME,
      participant: participantName
    };
    
    console.log(`   ✅ Token generated`);
    console.log(`   Response:`, JSON.stringify({ roomName: ROOM_NAME, url: LIVEKIT_URL }));
    
    res.json(response);
    
  } catch (error) {
    console.error('❌ Error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', room: ROOM_NAME });
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`\n🚀 Server: http://localhost:${PORT}`);
  console.log(`   Chat: http://localhost:${PORT}/chat.html`);
  console.log(`   Room: ${ROOM_NAME}\n`);
});
