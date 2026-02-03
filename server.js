const express = require('express');
const { AccessToken } = require('livekit-server-sdk');
const axios = require('axios');
const { CloudClient } = require('chromadb');
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

// Load illustrations database
let illustrationsDB = [];
try {
  const fs = require('fs');
  const illustrationsPath = './illustrations/illustrations.json';
  if (fs.existsSync(illustrationsPath)) {
    illustrationsDB = JSON.parse(fs.readFileSync(illustrationsPath, 'utf-8'));
    console.log(`Loaded ${illustrationsDB.length} illustrations from database`);
  } else {
    // Try progress file
    const progressPath = './illustrations/illustrations_progress.json';
    if (fs.existsSync(progressPath)) {
      illustrationsDB = JSON.parse(fs.readFileSync(progressPath, 'utf-8'));
      console.log(`Loaded ${illustrationsDB.length} illustrations from progress file`);
    }
  }
} catch (err) {
  console.log('No illustrations database found:', err.message);
}

// ============================================
// SERMON SEARCH HELPER FUNCTIONS
// ============================================
async function searchSermons(query, nResults = 6) {
  // Query Chroma directly (no HTTP self-call)
  if (!sermonCollection) {
    // Try to get fresh collection reference
    try {
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments' });
      const sc = await sermonCollection.count();
      console.log(`Lazy-loaded sermon_segments (${sc} segments)`);
    } catch (e) {
      console.log('sermon_segments collection not available:', e.message);
      return [];
    }
  }
  try {
    console.log(`Searching sermon_segments for: "${query}" (n=${nResults})`);
    const results = await sermonCollection.query({ queryTexts: [query], nResults: nResults });
    const formatted = [];
    if (results.ids && results.ids[0]) {
      for (let i = 0; i < results.ids[0].length; i++) {
        const meta = results.metadatas[0][i] || {};
        const dist = results.distances ? results.distances[0][i] : 1;
        formatted.push({
          text: results.documents[0][i] || '',
          title: meta.title || 'Sermon',
          video_id: meta.video_id || '',
          start_time: meta.start_time || '',
          url: meta.url || '',
          timestamped_url: meta.timestamped_url || meta.url || '',
          relevance_score: 1 - dist
        });
      }
    }
    console.log(`Found ${formatted.length} sermon results`);
    return formatted;
  } catch (err) {
    console.log('Sermon search error, retrying with fresh collection:', err.message);
    // Retry with fresh collection reference
    try {
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments' });
      const results = await sermonCollection.query({ queryTexts: [query], nResults: nResults });
      const formatted = [];
      if (results.ids && results.ids[0]) {
        for (let i = 0; i < results.ids[0].length; i++) {
          const meta = results.metadatas[0][i] || {};
          const dist = results.distances ? results.distances[0][i] : 1;
          formatted.push({
            text: results.documents[0][i] || '',
            title: meta.title || 'Sermon',
            video_id: meta.video_id || '',
            start_time: meta.start_time || '',
            url: meta.url || '',
            timestamped_url: meta.timestamped_url || meta.url || '',
            relevance_score: 1 - dist
          });
        }
      }
      console.log(`Retry found ${formatted.length} sermon results`);
      return formatted;
    } catch (retryErr) {
      console.log('Sermon search retry failed:', retryErr.message);
      return [];
    }
  }
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
// ILLUSTRATION SEARCH FUNCTION
// ============================================
function searchIllustrations(query, limit = 3) {
  if (!illustrationsDB || illustrationsDB.length === 0) {
    console.log('No illustrations database loaded');
    return [];
  }
  
  const queryLower = query.toLowerCase();
  // Extract key topic words (filter out common words)
  const stopWords = ['what', 'does', 'pastor', 'bob', 'teach', 'about', 'how', 'can', 'the', 'and', 'for', 'with', 'that', 'this', 'from', 'have', 'more', 'when', 'why', 'who', 'which', 'there', 'their', 'been', 'would', 'could', 'should'];
  const queryWords = queryLower.split(/\s+/).filter(w => w.length > 2 && !stopWords.includes(w));
  
  console.log(`Searching ${illustrationsDB.length} illustrations for: "${query}"`);
  console.log(`Key topic words: ${queryWords.join(', ')}`);
  
  // Score each illustration by topic match - require EXACT topic matches
  const scored = illustrationsDB.map(ill => {
    let score = 0;
    const topics = (ill.topics || []).map(t => t.toLowerCase());
    const text = (ill.text || '').toLowerCase();
    const title = (ill.illustration || '').toLowerCase();
    
    // Check topic matches - more flexible matching
    for (const topic of topics) {
      for (const word of queryWords) {
        // EXACT topic match (topic IS the word, not just contains it)
        if (topic === word) {
          score += 20;
        }
        // Topic starts with the word (e.g., "faith" matches "faith in god")
        else if (topic.startsWith(word + ' ') || topic.startsWith(word + '-') || topic.startsWith(word + "'")) {
          score += 15;
        }
        // Topic ends with the word (e.g., "trust" matches "learning to trust")
        else if (topic.endsWith(' ' + word)) {
          score += 12;
        }
        // Word is standalone in topic (e.g., "faith" in "keeping faith strong")
        else if (topic.includes(' ' + word + ' ')) {
          score += 10;
        }
        // Word appears with word boundary (regex-based, more flexible)
        else {
          const wordBoundaryRegex = new RegExp('\\b' + word + '\\b', 'i');
          if (wordBoundaryRegex.test(topic)) {
            score += 8;
          }
        }
      }
    }
    
    // Bonus for text containing key words (lower weight)
    for (const word of queryWords) {
      const wordRegex = new RegExp('\\b' + word + '\\b', 'i');
      if (wordRegex.test(text)) score += 3;
      if (wordRegex.test(title)) score += 5;
    }
    
    return { ...ill, score };
  });
  
  // Return top matches with score >= 10 (require at least one good topic match)
  // Also deduplicate by title + timestamp
  const seen = new Set();
  const results = scored
    .filter(ill => ill.score >= 10)
    .sort((a, b) => b.score - a.score)
    .filter(ill => {
      const key = `${ill.illustration || ''}-${ill.timestamp || ''}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, limit);
  
  console.log(`Found ${results.length} illustration matches (top scores: ${results.map(r => r.score).join(', ')})`);
  return results;
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
    let sermonResults = [];  // Declare at function scope so it's available for video sending
    let isMoreRequest = false;  // Track if user wants more clips
    const lastUserMessage = messages[messages.length - 1];
    
    if (lastUserMessage && lastUserMessage.role === 'user') {
      const userText = lastUserMessage.content.toLowerCase().trim();
      isMoreRequest = userText === 'more' || userText === 'more links' || userText === 'show more' || userText === 'more clips';
      
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
      // For "more" requests, get additional results
      try {
        const numResults = isMoreRequest ? 12 : 6;  // Get more for "more" requests
        sermonResults = await searchSermons(searchQuery, numResults);
      } catch (searchError) {
        console.log('Sermon search skipped due to error:', searchError.message);
        sermonResults = [];
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
    
    // Search for relevant illustrations from Chroma Cloud
    let illustrationResults = [];
    if (lastUserMessage && lastUserMessage.role === 'user' && !isMoreRequest && illustrationCollection) {
      try {
        const illResponse = await axios.post(`http://localhost:${PORT}/api/illustration/search`, {
          query: lastUserMessage.content,
          n_results: 3
        }, { timeout: 5000 });
        illustrationResults = illResponse.data.results || [];
        if (illustrationResults.length > 0) {
          console.log(`Found ${illustrationResults.length} relevant illustrations from Chroma`);
        }
      } catch (err) {
        console.log('Illustration search error:', err.message);
      }
    }
    
    // Send illustrations as separate event
    if (illustrationResults && illustrationResults.length > 0) {
      const illustrationsToSend = illustrationResults.map(ill => ({
        title: ill.illustration || 'Illustration',
        text: ill.text || '',
        topics: ill.topics || [],
        tone: ill.tone || '',
        url: ill.video_url || '',
        timestamp: ill.timestamp || ''
      }));
      console.log(`Sending ${illustrationsToSend.length} illustrations to client`);
      res.write(`data: ${JSON.stringify({ illustrations: illustrationsToSend })}\n\n`);
    }
    
    // Send sermon videos as separate event BEFORE Grok's response
    if (sermonResults && sermonResults.length > 0) {
      console.log(`Filtering ${sermonResults.length} sermon results for videos`);
      
      // Filter out songs, music, and non-teaching content
      const filteredResults = sermonResults.filter(r => {
        const title = (r.title || '').toLowerCase();
        const text = (r.text || '').toLowerCase();
        
        // Skip if title contains "Unknown" with no real title
        if (title === 'unknown sermon' || title === 'unknown' || title === 'sermon') return false;
        
        // Skip if title indicates it's a song/music
        const songIndicators = ['worship song', 'hymn', 'music video', 'singing', 'choir', 'worship set'];
        if (songIndicators.some(ind => title.includes(ind))) return false;
        
        // Skip if text is very short (likely not a teaching segment)
        if (text.length < 100) return false;
        
        // Skip if text has repeated worship phrases (likely lyrics)
        const worshipPhrases = (text.match(/\b(la la|hallelujah|glory glory|praise him|oh lord|we worship|we praise|sing to|lift your|raise your hands?|clap your)\b/gi) || []).length;
        if (worshipPhrases > 2) return false;
        
        // Skip if text is mostly music notation or repeated phrases
        const words = text.split(/\s+/);
        const uniqueWords = new Set(words);
        if (words.length > 20 && uniqueWords.size < words.length * 0.4) return false;  // Too repetitive
        
        // Skip announcements and logistics
        const announcementPhrases = (text.match(/\b(sign up|register|next week|potluck|meet in|parking lot|nursery|children'?s ministry|youth group|ladies'? group|men'?s group)\b/gi) || []).length;
        if (announcementPhrases > 1) return false;
        
        return true;
      });
      
      console.log(`After filtering: ${filteredResults.length} videos, isMoreRequest: ${isMoreRequest}`);
      
      // For "more" requests, skip the first 5 (already shown) and show next batch
      const startIndex = isMoreRequest ? 5 : 0;
      const videosToSend = filteredResults.slice(startIndex, startIndex + 5).map(r => ({
        title: r.title || 'Sermon Clip',
        url: r.timestamped_url || r.url,
        timestamp: r.start_time || '',
        text: (r.text || '').substring(0, 150)
      }));
      
      if (videosToSend.length > 0) {
        console.log(`Sending ${videosToSend.length} sermon videos to client (from index ${startIndex})`);
        res.write(`data: ${JSON.stringify({ sermon_videos: videosToSend })}\n\n`);
      } else if (isMoreRequest) {
        console.log('No more videos available');
      }
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

// Chroma Cloud direct connection
let chromaClient = null;
let sermonCollection = null;
let illustrationCollection = null;

async function initChromaCloud() {
  const apiKey = process.env.CHROMA_API_KEY;
  const tenant = process.env.CHROMA_TENANT;
  const database = process.env.CHROMA_DATABASE || 'APB';
  if (!apiKey || !tenant) {
    console.log('Chroma Cloud not configured (missing CHROMA_API_KEY or CHROMA_TENANT)');
    return;
  }
  try {
    chromaClient = new CloudClient({ apiKey, tenant, database });
    try {
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments' });
      const sc = await sermonCollection.count();
      console.log(`Chroma Cloud: sermon_segments loaded (${sc} segments)`);
    } catch (e) { console.log('sermon_segments not found:', e.message); }
    try {
      illustrationCollection = await chromaClient.getCollection({ name: 'illustrations_v4' });
      const ic = await illustrationCollection.count();
      console.log(`Chroma Cloud: illustrations_v4 loaded (${ic} items)`);
    } catch (e) { console.log('illustrations_v4 not found:', e.message); }
  } catch (e) {
    console.error('Chroma Cloud init error:', e.message);
  }
}
initChromaCloud();

app.post('/api/sermon/search', async (req, res) => {
  try {
    const { query, n_results = 6 } = req.body;
    if (!query) return res.status(400).json({ error: 'Query required' });
    if (!sermonCollection) return res.json({ query, results: [] });
    const results = await sermonCollection.query({ queryTexts: [query], nResults: n_results });
    const formatted = [];
    if (results.ids && results.ids[0]) {
      for (let i = 0; i < results.ids[0].length; i++) {
        const meta = results.metadatas[0][i] || {};
        const dist = results.distances ? results.distances[0][i] : 1;
        formatted.push({
          text: results.documents[0][i] || '',
          title: meta.title || 'Sermon',
          video_id: meta.video_id || '',
          start_time: meta.start_time || '',
          url: meta.url || '',
          timestamped_url: meta.timestamped_url || meta.url || '',
          relevance_score: 1 - dist,
          main_topic: meta.main_topic || '',
          summary: meta.summary || ''
        });
      }
    }
    res.json({ query, count: formatted.length, results: formatted });
  } catch (error) {
    console.error('Sermon search error:', error.message);
    res.status(500).json({ error: 'Sermon search failed', results: [] });
  }
});

app.post('/api/illustration/search', async (req, res) => {
  try {
    const { query, n_results = 3 } = req.body;
    if (!query) return res.status(400).json({ error: 'Query required' });
    if (!illustrationCollection) return res.json({ query, results: [] });
    const fetchMore = Math.min(n_results * 3, 10);
    const results = await illustrationCollection.query({ queryTexts: [query], nResults: fetchMore });
    const formatted = [];
    if (results.ids && results.ids[0]) {
      const queryLower = query.toLowerCase();
      const stopWords = new Set(['what','does','pastor','bob','teach','about','how','can','the','and','for','with','that','this','from','have','more','when','why','who','which','there','their','been','would','could','should','going','into','also','just','very','really','much','some','only','than','then','them','these','those','will','being','doing','want','need','know','think','make','like','look','help','give','most','find','here','thing','many','well','back','because','people','tell','say','ask','use','all','way','its','get','got','are','was','were','has','had','not','but','our','out','you','your','his','her','she','him','did','one','two']);
      const veryCommon = new Set(['god','jesus','bible','lord','christ','faith','pray','prayer','life','love','sin','church','believe','hope','spirit','holy','heaven','hell','heart','soul','world','truth','word','grace']);
      const queryWords = queryLower.replace(/[^a-z0-9\s]/g, '').split(/\s+/).filter(w => w.length > 2 && !stopWords.has(w));
      const specificWords = queryWords.filter(w => !veryCommon.has(w));
      const commonWords = queryWords.filter(w => veryCommon.has(w));
      
      for (let i = 0; i < results.ids[0].length; i++) {
        const meta = results.metadatas[0][i] || {};
        const dist = results.distances ? results.distances[0][i] : 1;
        const topics = meta.topics ? meta.topics.split(',') : [];
        const topicsLower = topics.map(t => t.toLowerCase().trim());
        const docText = (results.documents[0][i] || '').toLowerCase();
        const summary = (meta.summary || '').toLowerCase();
        
        let relevanceScore = 0;
        let distinctMatches = 0;
        const prefixMatch = (text, word) => {
          if (text.includes(word)) return true;
          if (word.length >= 4) {
            const root = word.substring(0, Math.max(4, word.length - 2));
            const words = text.split(/[\s,]+/);
            return words.some(w => w.startsWith(root));
          }
          return false;
        };
        for (const word of specificWords) {
          let matched = false;
          if (topicsLower.some(t => prefixMatch(t, word))) { relevanceScore += 5; matched = true; }
          if (prefixMatch(summary, word)) { relevanceScore += 3; matched = true; }
          const wordRegex = new RegExp('\\b' + word.substring(0, Math.max(4, word.length - 2)), 'i');
          if (wordRegex.test(docText)) { relevanceScore += 1; matched = true; }
          if (matched) distinctMatches++;
        }
        for (const word of commonWords) {
          if (topicsLower.some(t => prefixMatch(t, word))) relevanceScore += 1;
          if (prefixMatch(summary, word)) relevanceScore += 1;
        }
        
        const minDistinct = specificWords.length >= 2 ? 2 : 1;
        if (distinctMatches >= minDistinct && relevanceScore >= 6) {
          formatted.push({
            illustration: meta.summary || '',
            type: meta.type || '',
            text: results.documents[0][i] || '',
            video_url: meta.youtube_url || '',
            timestamp: meta.timestamp || '',
            topics: topics,
            tone: meta.emotional_tone || '',
            video_id: meta.video_id || '',
            relevance_score: 1 - dist,
            topic_score: relevanceScore
          });
        }
      }
      formatted.sort((a, b) => (b.topic_score + b.relevance_score) - (a.topic_score + a.relevance_score));
    }
    const limited = formatted.slice(0, n_results);
    console.log(`Illustration search: "${query}" -> ${formatted.length} relevant of ${fetchMore} fetched, returning ${limited.length}`);
    res.json({ query, count: limited.length, results: limited });
  } catch (error) {
    console.error('Illustration search error:', error.message);
    try {
      console.log('Retrying with fresh collection reference...');
      illustrationCollection = await chromaClient.getCollection({ name: 'illustrations_v4' });
      const retryResults = await illustrationCollection.query({ queryTexts: [req.body.query], nResults: 3 });
      if (retryResults.ids && retryResults.ids[0] && retryResults.ids[0].length > 0) {
        const retryFormatted = retryResults.ids[0].map((id, i) => ({
          illustration: (retryResults.metadatas[0][i] || {}).summary || '',
          type: (retryResults.metadatas[0][i] || {}).type || '',
          text: retryResults.documents[0][i] || '',
          video_url: (retryResults.metadatas[0][i] || {}).youtube_url || '',
          timestamp: (retryResults.metadatas[0][i] || {}).timestamp || '',
          topics: ((retryResults.metadatas[0][i] || {}).topics || '').split(','),
          tone: (retryResults.metadatas[0][i] || {}).emotional_tone || '',
          video_id: (retryResults.metadatas[0][i] || {}).video_id || '',
          relevance_score: retryResults.distances ? 1 - retryResults.distances[0][i] : 0,
          topic_score: 5
        }));
        console.log(`Retry succeeded: ${retryFormatted.length} results`);
        return res.json({ query: req.body.query, count: retryFormatted.length, results: retryFormatted.slice(0, req.body.n_results || 3) });
      }
    } catch (retryErr) {
      console.error('Retry also failed:', retryErr.message);
    }
    res.status(500).json({ error: 'Illustration search failed', results: [] });
  }
});

app.get('/api/sermon/health', async (req, res) => {
  res.json({
    status: chromaClient ? 'ok' : 'not_configured',
    sermons: sermonCollection ? 'loaded' : 'not_loaded',
    illustrations: illustrationCollection ? 'loaded' : 'not_loaded'
  });
});

app.listen(PORT, () => {
  console.log(`\n🚀 Server running on http://localhost:${PORT}`);
  console.log(`📺 Open chat at: http://localhost:${PORT}/chat.html`);
  console.log(`✅ Health check: http://localhost:${PORT}/health\n`);
});
