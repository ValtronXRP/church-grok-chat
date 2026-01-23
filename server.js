const express = require('express');
const { AccessToken } = require('livekit-server-sdk');
const axios = require('axios');
require('dotenv').config();

const app = express();
app.use(express.json());

// Add CSP headers to allow YouTube embedding
app.use((req, res, next) => {
  res.setHeader(
    'Content-Security-Policy',
    "default-src 'self'; " +
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.youtube.com https://s.ytimg.com; " +
    "style-src 'self' 'unsafe-inline'; " +
    "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; " +
    "img-src 'self' data: https:; " +
    "connect-src 'self' ws: wss: https:;"
  );
  next();
});

app.use(express.static('public'));

// Redirect root to chat.html
app.get('/', (req, res) => {
  res.redirect('/chat.html');
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ 
    status: 'ok',
    services: {
      livekit: LIVEKIT_URL ? 'configured' : 'missing',
      xai: XAI_API_KEY ? 'configured' : 'missing'
    }
  });
});

const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY;
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.LIVEKIT_URL;
const XAI_API_KEY = process.env.XAI_API_KEY;
const PORT = process.env.PORT || 3001;
const SERMON_API_URL = process.env.SERMON_API_URL || 'http://localhost:5001';
const LIVEKIT_HTTP_URL = LIVEKIT_URL ? LIVEKIT_URL.replace('wss://', 'https://') : '';

// ============================================
// SERMON SEARCH HELPER FUNCTIONS
// ============================================
async function searchSermons(query) {
  try {
    // Add timeout to prevent hanging
    const response = await axios.post(`${SERMON_API_URL}/api/sermon/search`, {
      query: query,
      n_results: 5  // Get more initially
    }, {
      timeout: 2000  // 2 second timeout
    });
    
    if (response.data && response.data.results) {
      let results = response.data.results;
      console.log(`Sermon search found ${results.length} results for: "${query}"`);
      
      // Additional filtering: only keep results that are truly relevant
      // Filter out results that are too abstract or off-topic
      const filteredResults = results.filter(result => {
        // Check if the segment actually discusses the topic
        const text = result.text.toLowerCase();
        const queryLower = query.toLowerCase();
        
        // Extract the key topic from the query
        const keyWords = ['forgiveness', 'forgive', 'faith', 'prayer', 'love', 'healing', 
                         'salvation', 'grace', 'sin', 'worship', 'hope', 'peace', 'joy'];
        const queryTopic = keyWords.find(word => queryLower.includes(word));
        
        if (queryTopic) {
          // Check if the segment actually discusses this topic
          const relatedWords = {
            'forgiveness': ['forgive', 'forgiven', 'pardon', 'mercy'],
            'faith': ['believe', 'trust', 'faithful'],
            'prayer': ['pray', 'praying', 'lord help'],
            'love': ['love', 'loving', 'beloved'],
            'healing': ['heal', 'healed', 'restore'],
            'salvation': ['saved', 'save', 'savior', 'cross'],
            'grace': ['grace', 'mercy', 'undeserved'],
            'sin': ['sin', 'wrong', 'transgression'],
            'worship': ['worship', 'praise', 'glorify'],
            'hope': ['hope', 'promise', 'future'],
            'peace': ['peace', 'calm', 'rest'],
            'joy': ['joy', 'rejoice', 'glad']
          };
          
          const topicWords = relatedWords[queryTopic] || [queryTopic];
          return topicWords.some(word => text.includes(word));
        }
        
        // If no specific topic found, keep all results
        return true;
      });
      
      // Return top 3 most relevant
      return filteredResults.slice(0, 3);
    }
  } catch (error) {
    console.log('Sermon search skipped:', error.message);
    // Don't let sermon search failure break the chat
  }
  return [];
}

function formatSermonContext(sermonResults) {
  if (!sermonResults || sermonResults.length === 0) {
    return '\n\n⚠️ NO SERMON SEGMENTS FOUND: Please still answer based on general biblical principles, but mention that no specific sermons from Pastor Bob Kopeny were found on this topic.\n';
  }
  
  let context = '\n\n🔴 ABSOLUTELY CRITICAL - YOUR RESPONSE MUST INCLUDE THE FOLLOWING:\n\n';
  context += '📺 VIDEO SEGMENTS FOUND: ' + sermonResults.length + ' relevant clips\n\n';
  context += 'MANDATORY RESPONSE STRUCTURE:\n';
  context += '1. START with: "I found [X] sermon segments from Pastor Bob Kopeny on this topic..."\n';
  context += '2. INCLUDE each YouTube link with timestamp\n';
  context += '3. IF there\'s a story/illustration, QUOTE IT DIRECTLY\n';
  context += '4. EXPLAIN what Pastor Bob teaches\n\n';
  
  context += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  context += 'SERMON SEGMENTS (MUST INCLUDE ALL IN RESPONSE):\n';
  context += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  
  sermonResults.forEach((result, i) => {
    // Try to detect content type
    const text_lower = result.text.toLowerCase();
    let hasIllustration = false;
    
    // Check for story/illustration markers
    if (text_lower.includes('remember when') || text_lower.includes('story') || 
        text_lower.includes('once ') || text_lower.includes('example') ||
        text_lower.includes('illustration') || text_lower.includes('let me tell you') ||
        text_lower.includes('imagine') || text_lower.includes('picture this')) {
      hasIllustration = true;
    }
    
    context += `\n📹 SEGMENT ${i + 1}:\n`;
    context += `Sermon: "${result.title}"\n`;
    context += `Timestamp: ${result.start_time} - ${result.end_time}\n`;
    context += `YouTube Link: ${result.timestamped_url}\n`;
    
    if (hasIllustration) {
      context += `\n🎯 ILLUSTRATION FOUND - QUOTE THIS:\n`;
      context += `"${result.text.substring(0, 400)}..."\n`;
      context += `\n📌 INSTRUCTION: Include this illustration as a direct quote in your response!\n`;
    } else {
      context += `\n📝 TEACHING CONTENT:\n`;
      context += `${result.text.substring(0, 400)}...\n`;
      context += `\n📌 INSTRUCTION: Summarize this teaching point clearly\n`;
    }
    
    context += '────────────────────────────────────\n';
  });
  
  context += '\n🔴 RESPONSE REQUIREMENTS:\n';
  context += '1. MENTION the number of video segments found\n';
  context += '2. INCLUDE every YouTube link with "Watch at [timestamp]: [link]"\n';
  context += '3. QUOTE any illustrations/stories directly\n';
  context += '4. EXPLAIN Pastor Bob Kopeny\'s teaching clearly\n';
  context += '5. SUGGEST watching the videos for more detail\n\n';
  
  context += 'EXAMPLE START:\n';
  context += '"I found 3 sermon segments from Pastor Bob Kopeny about [topic]. In his sermon \'[title]\' (watch at 15:32: [link]), he teaches that... He shares a powerful illustration: \'[quote story]\'. For more insights, watch his teaching at [timestamp]: [link]"\n';
  
  return context;
}

// ============================================
// NEW: SECURE TEXT CHAT ENDPOINT WITH SERMON SEARCH
// ============================================
app.post('/api/chat', async (req, res) => {
  try {
    const { messages, model = 'grok-3', temperature = 0.7, max_tokens = 1000 } = req.body;
    
    if (!messages || !Array.isArray(messages)) {
      return res.status(400).json({ error: 'Invalid messages format' });
    }
    
    if (!XAI_API_KEY) {
      console.error('XAI_API_KEY not configured');
      return res.status(500).json({ error: 'Server configuration error' });
    }
    
    console.log(`Chat request - Model: ${model}, Messages: ${messages.length}`);
    
    // Check if we should search for relevant sermons
    let enhancedMessages = [...messages];
    const lastUserMessage = messages[messages.length - 1];
    
    if (lastUserMessage && lastUserMessage.role === 'user') {
      // Search for relevant sermons (don't let this break the chat)
      let sermonResults = [];
      try {
        sermonResults = await searchSermons(lastUserMessage.content);
      } catch (searchError) {
        console.log('Sermon search skipped due to error:', searchError.message);
        // Continue without sermon results
      }
      
      if (sermonResults.length > 0) {
        console.log(`Found ${sermonResults.length} relevant sermon segments`);
        console.log(`First result: ${sermonResults[0].title} at ${sermonResults[0].start_time}`);
        
        // Add sermon context to the system message
        const sermonContext = formatSermonContext(sermonResults);
        console.log(`Added sermon context (${sermonContext.length} chars) to system message`);
        
        // Find and update the system message
        const systemMsgIndex = enhancedMessages.findIndex(m => m.role === 'system');
        if (systemMsgIndex >= 0) {
          enhancedMessages[systemMsgIndex] = {
            ...enhancedMessages[systemMsgIndex],
            content: enhancedMessages[systemMsgIndex].content + sermonContext
          };
          console.log('System message updated with sermon context');
        } else {
          console.log('WARNING: No system message found to add sermon context!');
        }
      } else {
        console.log('No relevant sermon segments found');
      }
    }
    
    const response = await fetch('https://api.x.ai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${XAI_API_KEY}`
      },
      body: JSON.stringify({
        messages: enhancedMessages,
        model,
        temperature,
        max_tokens,
        stream: true
      })
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      console.error('Grok API error:', response.status, errorText);
      return res.status(response.status).json({
        error: `API request failed: ${response.status}`
      });
    }
    
    // Set SSE headers
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('Access-Control-Allow-Origin', '*');
    
    // Stream the response
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        res.write(chunk);
      }
    } finally {
      res.end();
    }
    
  } catch (error) {
    res.status(500).json({ error: 'Internal server error' });
  }
});

// ============================================
// ORIGINAL WORKING /TOKEN ENDPOINT - UNCHANGED
// ============================================
app.post('/token', async (req, res) => {
  try {
    // IMPORTANT: Use the FIXED room name that agent_direct.py connects to!
    const roomName = "apb-voice-room";  // This is the room the agent monitors
    const participantName = `user_${Date.now()}`;
    const context = req.body.context || [];

    const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
      identity: participantName,
      metadata: JSON.stringify({
        request_agent: 'grok-voice-assistant'
      })
    });
    at.addGrant({
      room: roomName,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      roomCreate: true,
      agent: true
    });
    const token = await at.toJwt();

    // Try to dispatch agent to the room
    const httpUrl = LIVEKIT_URL.replace('wss://', 'https://');
    
    setTimeout(async () => {
      try {
        const dispatchResponse = await axios.post(
          `${httpUrl}/twirp/livekit.AgentDispatchService/CreateDispatch`,
          {
            room: roomName,
            agent_name: 'grok-voice-assistant'
          },
          {
            headers: {
              'Authorization': `Bearer ${LIVEKIT_API_KEY}:${LIVEKIT_API_SECRET}`,
              'Content-Type': 'application/json'
            }
          }
        );
        console.log(`Agent dispatched to room ${roomName}`);
      } catch (dispatchError) {
        console.error('Agent dispatch failed:', dispatchError.response?.data || dispatchError.message);
      }
    }, 1000); // Delay to ensure room exists
    
    console.log(`Room ${roomName} created, dispatching agent...`);

    res.json({ 
      token, 
      url: LIVEKIT_URL,
      roomName: roomName,
      participant: participantName
    });
  } catch (error) {
    console.error('Token generation error:', error);
    res.status(500).json({ error: 'Failed to generate token' });
  }
});

app.listen(PORT, () => {
  console.log(`\n🚀 Server running on http://localhost:${PORT}`);
  console.log(`📺 Open chat at: http://localhost:${PORT}/chat.html`);
  console.log(`✅ Health check: http://localhost:${PORT}/health\n`);
});
