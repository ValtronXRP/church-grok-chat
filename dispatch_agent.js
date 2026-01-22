const axios = require('axios');
require('dotenv').config();

const LIVEKIT_URL = process.env.LIVEKIT_URL;
const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY;
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET;

async function dispatchAgent(roomName) {
  const httpUrl = LIVEKIT_URL.replace('wss://', 'https://');
  
  try {
    const response = await axios.post(
      `${httpUrl}/twirp/livekit.AgentDispatchService/CreateDispatch`,
      {
        room: roomName,
        agent_name: 'grok-voice-assistant',
        metadata: JSON.stringify({ type: 'voice-assistant' })
      },
      {
        headers: {
          'Authorization': `Bearer ${LIVEKIT_API_KEY}:${LIVEKIT_API_SECRET}`,
          'Content-Type': 'application/json'
        }
      }
    );
    
    console.log('Agent dispatched:', response.data);
    return response.data;
  } catch (error) {
    console.error('Dispatch error:', error.response?.data || error.message);
    throw error;
  }
}

// Export for use in server
module.exports = { dispatchAgent };

// Test if run directly
if (require.main === module) {
  const roomName = process.argv[2] || 'test-room';
  console.log(`Dispatching agent to room: ${roomName}`);
  dispatchAgent(roomName).then(() => {
    console.log('Done');
    process.exit(0);
  }).catch((err) => {
    console.error('Failed:', err.message);
    process.exit(1);
  });
}