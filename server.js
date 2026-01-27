const express = require('express');
const { AccessToken } = require('livekit-server-sdk');
const axios = require('axios');
const SermonSearch = require('./sermonSearch');
require('dotenv').config();

const app = express();
app.use(express.json());

// Add CORS headers for all routes
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  
  // Handle preflight requests
  if (req.method === 'OPTIONS') {
    return res.sendStatus(200);
  }
  
  next();
});

// Add CSP headers to allow YouTube embedding and LiveKit
app.use((req, res, next) => {
  res.setHeader(
    'Content-Security-Policy',
    "default-src 'self'; " +
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.youtube.com https://s.ytimg.com https://cdn.jsdelivr.net https://unpkg.com; " +
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
    "font-src 'self' https://fonts.gstatic.com; " +
    "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com https://youtube.com; " +
    "img-src 'self' data: https: blob:; " +
    "media-src 'self' https: blob:; " +
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

// Initialize local sermon search
const sermonSearcher = new SermonSearch();

// ============================================
// SERMON SEARCH HELPER FUNCTIONS
// ============================================
async function searchSermons(query) {
  // PRIMARY: Use ChromaDB vector search API (109K+ segments)
  try {
    console.log(`Searching ChromaDB for: "${query}"`);
    const response = await axios.post(`${SERMON_API_URL}/api/sermon/search`, {
      query: query,
      n_results: 6
    }, {
      timeout: 5000
    });
    
    if (response.data && response.data.results && response.data.results.length > 0) {
      console.log(`ChromaDB found ${response.data.results.length} sermon results`);
      return response.data.results;
    }
  } catch (apiError) {
    console.log('ChromaDB API error, falling back to local:', apiError.message);
  }
  
  // FALLBACK: Use local static JSON search (583 segments)
  try {
    console.log(`Falling back to local search for: "${query}"`);
    const results = sermonSearcher.search(query, 5);
    console.log(`Found ${results.length} local sermon results`);
    return results;
  } catch (error) {
    console.error('Local sermon search error:', error);
  }
  
  return [];
}

// Keep the old filtering function for API results
async function searchSermonsOld(query) {
  try {
    const response = await axios.post(`${SERMON_API_URL}/api/sermon/search`, {
      query: query,
      n_results: 5
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

function formatSermonContext(sermonResults, isMoreRequest = false) {
  if (!sermonResults || sermonResults.length === 0) {
    return '\n\n⚠️ NO SERMON SEGMENTS FOUND: Please still answer based on general biblical principles, but mention that no specific sermons from Pastor Bob Kopeny were found on this topic.\n';
  }
  
  const first3 = sermonResults.slice(0, 3);
  const hasMore = sermonResults.length > 3;
  
  // Collect illustrations and scripture references
  const illustrations = [];
  const scriptures = [];
  first3.forEach(result => {
    const text_lower = result.text.toLowerCase();
    if (text_lower.includes('remember when') || text_lower.includes('story') || 
        text_lower.includes('once ') || text_lower.includes('example') ||
        text_lower.includes('illustration') || text_lower.includes('let me tell you') ||
        text_lower.includes('imagine') || text_lower.includes('picture this')) {
      illustrations.push({ text: result.text.substring(0, 400), url: result.timestamped_url });
    }
    // Look for scripture references
    const scriptureMatch = result.text.match(/([1-3]?\s?[A-Z][a-z]+)\s+(\d+):(\d+)/g);
    if (scriptureMatch) {
      scriptures.push(...scriptureMatch);
    }
  });
  
  if (isMoreRequest) {
    const additional = sermonResults.slice(3);
    if (additional.length === 0) {
      return '\n\nNo additional sermon segments available on this topic.\n';
    }
    
    let context = '\n\nProvide additional videos. Format each as:\n';
    context += '"Here are more related videos:"\n';
    context += 'Then for each video, put the link on its own line:\n\n';
    additional.forEach((result, i) => {
      context += `${result.timestamped_url}\n`;
      context += `Brief description: ${result.text.substring(0, 100)}...\n\n`;
    });
    return context;
  }
  
  let context = '\n\n🔴 REQUIRED RESPONSE STRUCTURE:\n\n';
  context += '1. SUMMARY: 2-3 sentences on what Pastor Bob teaches about this topic\n\n';
  context += '2. SCRIPTURE: Mention any Bible verses Pastor Bob references (see list below)\n\n';
  context += '3. ILLUSTRATION: If there\'s a story or illustration from Pastor Bob, share it naturally\n\n';
  context += '4. VIDEO SEGMENTS: Say "Here are some sermon clips where Pastor Bob discusses this:"\n';
  context += '   Then for EACH video, put the YouTube link on its OWN LINE followed by a brief description.\n';
  context += '   Format example:\n';
  context += '   https://www.youtube.com/watch?v=VIDEO_ID&t=123s\n';
  context += '   Pastor Bob explains how forgiveness frees us from bitterness.\n\n';
  if (hasMore) {
    context += '5. END WITH: "If you\'d like more sermon clips, just say more."\n\n';
  }
  
  context += '⚠️ CRITICAL: Each YouTube URL must be on its own line for embedding to work!\n';
  context += 'Do NOT wrap URLs in parentheses or combine with other text on same line.\n\n';
  
  context += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  context += 'SCRIPTURE REFERENCES FOUND:\n';
  context += scriptures.length > 0 ? scriptures.slice(0, 5).join(', ') : 'None specific';
  context += '\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  
  if (illustrations.length > 0) {
    context += 'ILLUSTRATION FROM PASTOR BOB:\n';
    context += `"${illustrations[0].text}"\n`;
    context += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  }
  
  context += 'VIDEO SEGMENTS TO INCLUDE (use exact URLs):\n\n';
  first3.forEach((result, i) => {
    context += `Video ${i + 1}: ${result.timestamped_url}\n`;
    context += `Summary: ${result.text.substring(0, 150)}...\n\n`;
  });
  context += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n';
  
  if (hasMore) {
    context += `\n📌 ${sermonResults.length - 3} more videos available if user says "more"\n`;
  }
  
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
      const userText = lastUserMessage.content.toLowerCase().trim();
      const isMoreRequest = userText === 'more' || userText === 'more links' || userText === 'show more';
      
      // For "more" requests, find the previous topic from conversation
      let searchQuery = lastUserMessage.content;
      if (isMoreRequest) {
        // Look back for the last substantive user question
        for (let i = messages.length - 2; i >= 0; i--) {
          if (messages[i].role === 'user') {
            const prevText = messages[i].content.toLowerCase().trim();
            if (prevText !== 'more' && prevText !== 'more links' && prevText !== 'show more') {
              searchQuery = messages[i].content;
              console.log(`"More" request - using previous query: "${searchQuery}"`);
              break;
            }
          }
        }
      }
      
      // Search for relevant sermons (don't let this break the chat)
      let sermonResults = [];
      try {
        sermonResults = await searchSermons(searchQuery);
      } catch (searchError) {
        console.log('Sermon search skipped due to error:', searchError.message);
      }
      
      if (sermonResults.length > 0) {
        console.log(`Found ${sermonResults.length} relevant sermon segments`);
        
        // Add sermon context to the system message
        const sermonContext = formatSermonContext(sermonResults, isMoreRequest);
        console.log(`Added sermon context (${sermonContext.length} chars), isMore: ${isMoreRequest}`);
        
        // Find and update the system message
        const systemMsgIndex = enhancedMessages.findIndex(m => m.role === 'system');
        if (systemMsgIndex >= 0) {
          enhancedMessages[systemMsgIndex] = {
            ...enhancedMessages[systemMsgIndex],
            content: enhancedMessages[systemMsgIndex].content + sermonContext
          };
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
    
    // Send sermon videos as separate event BEFORE Grok's response
    if (sermonResults && sermonResults.length > 0) {
      const videosToSend = sermonResults.slice(0, 5).map(r => ({
        title: r.title || 'Sermon Clip',
        url: r.timestamped_url || r.url,
        timestamp: r.start_time || '',
        text: (r.text || '').substring(0, 150)
      }));
      res.write(`data: ${JSON.stringify({ sermon_videos: videosToSend })}\n\n`);
    }
    
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
// TOKEN ENDPOINT - Creates unique room per user session
// ============================================
app.post('/token', async (req, res) => {
  try {
    // Generate unique room name for each user session (private conversations)
    const sessionId = `${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
    const roomName = `apb-session-${sessionId}`;
    const participantName = `user_${sessionId}`;
    const context = req.body.context || [];

    const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
      identity: participantName,
      metadata: JSON.stringify({
        request_agent: 'apb-voice-assistant'
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

    // Create a separate token for agent dispatch API call
    const dispatchAt = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
      identity: 'server-dispatch'
    });
    dispatchAt.addGrant({
      roomAdmin: true,
      room: roomName
    });
    const dispatchToken = await dispatchAt.toJwt();

    // Try to dispatch agent to the room
    const httpUrl = LIVEKIT_URL.replace('wss://', 'https://');
    
    setTimeout(async () => {
      try {
        const dispatchResponse = await axios.post(
          `${httpUrl}/twirp/livekit.AgentDispatchService/CreateDispatch`,
          {
            room: roomName,
            agent_name: 'apb-voice-assistant'
          },
          {
            headers: {
              'Authorization': `Bearer ${dispatchToken}`,
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
