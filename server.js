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
const RERANKER_URL = process.env.RERANKER_URL || 'http://127.0.0.1:5050';
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
function computeKeywordRelevance(text, query) {
  const stopWords = new Set(['what', 'does', 'how', 'can', 'the', 'and', 'for', 'with', 'that', 'this', 'from', 'have', 'about', 'pastor', 'bob', 'teach', 'say', 'tell', 'bible']);
  const queryWords = query.toLowerCase().replace(/[^a-z0-9\s]/g, '').split(/\s+/).filter(w => w.length > 2 && !stopWords.has(w));
  const textLower = text.toLowerCase();
  let matches = 0;
  for (const word of queryWords) {
    if (textLower.includes(word)) matches++;
    const variations = {
      'baptism': ['baptize', 'baptized', 'baptizing'],
      'holy': ['spirit', 'ghost'],
      'spirit': ['holy', 'spiritual'],
      'forgive': ['forgiveness', 'forgiving', 'forgiven'],
      'faith': ['faithful', 'believe', 'trust'],
      'pray': ['prayer', 'praying'],
      'salvation': ['saved', 'save', 'saving'],
      'sin': ['sinful', 'sinner', 'sins'],
      'love': ['loving', 'loved', 'loves']
    };
    if (variations[word]) {
      for (const v of variations[word]) {
        if (textLower.includes(v)) { matches += 0.5; break; }
      }
    }
  }
  return queryWords.length > 0 ? matches / queryWords.length : 0;
}

function isWorshipContent(text, title) {
  const textLower = (text || '').toLowerCase();
  const titleLower = (title || '').toLowerCase();
  if (titleLower === 'unknown sermon' || titleLower === 'unknown' || titleLower === '') return true;
  const worshipIndicators = ['worship song', 'hymn', 'music video', 'singing', 'choir', 'la la la', 'hallelujah hallelujah'];
  if (worshipIndicators.some(w => titleLower.includes(w))) return true;
  const worshipPhrases = /\b(la la|glory glory|praise him praise him|hallelujah hallelujah)\b/gi;
  if ((textLower.match(worshipPhrases) || []).length > 2) return true;
  if (textLower.length < 100) return true;
  return false;
}

const PINNED_STORY_CLIPS = {
  becky_story: {
    keywords: ['becky', 'wife', 'how did bob meet', 'how did pastor bob meet', 'how they met', 'bob and becky', 'bob meet becky', 'married', 'engagement', 'how bob met', 'love story', 'bob\'s wife', 'pastor bob\'s wife', 'when did bob get married', 'bob get married', 'who is bob married to', 'who did bob marry', 'becky kopeny'],
    clips: [
      {
        title: 'How To Press On (03/26/2017)',
        url: 'https://www.youtube.com/watch?v=sGIJP13TxPQ',
        timestamped_url: 'https://www.youtube.com/watch?v=sGIJP13TxPQ&t=2382s',
        start_time: '39:42',
        video_id: 'sGIJP13TxPQ',
        text: 'Pastor Bob shares the full story of how he met Becky - from meeting her briefly at church, to God putting her name in his mind at the intersection of Chapman and Kramer while driving to seminary, to the Lord revealing she had gotten engaged the night before, to God telling him to propose three weeks after their first date.',
        relevance_score: 1.0
      },
      {
        title: 'Who Cares? (12/10/2017)',
        url: 'https://www.youtube.com/watch?v=BRd6nCCTLKI',
        timestamped_url: 'https://www.youtube.com/watch?v=BRd6nCCTLKI&t=2014s',
        start_time: '33:34',
        video_id: 'BRd6nCCTLKI',
        text: 'Pastor Bob shares that when he first met Becky she was engaged to be married. They were just friends and he encouraged her spiritually. He shares about caring for someone and not knowing how they feel.',
        relevance_score: 0.95
      },
      {
        title: 'Getting God\'s Guidance - Numbers 9:1-23',
        url: 'https://www.youtube.com/watch?v=y-vXvEoyJb4',
        timestamped_url: 'https://www.youtube.com/watch?v=y-vXvEoyJb4&t=5448s',
        start_time: '1:30:48',
        video_id: 'y-vXvEoyJb4',
        text: 'Pastor Bob shares about going into the library, finding Becky, learning she was dating a guy seriously heading toward engagement. He shares about God\'s guidance and how when you\'re in God\'s will, things can move very quickly - they were engaged three weeks after their first date.',
        relevance_score: 0.9
      }
    ]
  },
  testimony: {
    keywords: ['testimony', 'how was bob saved', 'when was bob saved', 'how did bob get saved', 'bob\'s testimony', 'pastor bob saved', 'bob come to christ', 'bob receive christ', 'when did bob become a christian', 'how did bob become', 'bob\'s salvation', 'bob get saved', 'pastor bob\'s testimony', 'bob become a believer', 'how bob got saved', 'when bob got saved', 'bob\'s faith journey', 'how did pastor bob come to know', 'fred', 'jeff maples', 'gene schaeffer', 'jr high camp', 'junior high camp', '8th grade'],
    clips: [
      {
        title: 'Be Faithful - 2 Timothy 1',
        url: 'https://www.youtube.com/watch?v=72R6uNs2ka4',
        timestamped_url: 'https://www.youtube.com/watch?v=72R6uNs2ka4',
        start_time: '',
        video_id: '72R6uNs2ka4',
        text: 'Pastor Bob shares his testimony of how he received Christ. Two men - Jeff Maples and Gene Schaeffer, who were in their 30s - shared Christ with him at a Jr. High church camp when he was 13. They shared for about five minutes and asked if he would receive Christ. He said yes. He thanks God for the unbroken chain of people who shared the gospel down to him.',
        relevance_score: 1.0
      }
    ]
  }
};

function detectPersonalStoryQuery(query) {
  const q = query.toLowerCase().replace(/['']/g, "'");
  const matches = [];
  for (const [storyKey, story] of Object.entries(PINNED_STORY_CLIPS)) {
    for (const kw of story.keywords) {
      if (q.includes(kw)) {
        matches.push(storyKey);
        break;
      }
    }
  }
  return matches;
}

async function searchFast(query, nResults = 5) {
  try {
    const response = await axios.post(`${RERANKER_URL}/search/fast`, {
      query,
      n_results: nResults
    }, { timeout: 10000 });

    if (response.data && response.data.results) {
      const results = response.data.results;
      console.log(`Fast search returned ${results.length} results (${response.data.timing_ms}ms)`);
      return results.map(r => ({
        text: r.text,
        title: r.title || 'Sermon',
        video_id: r.video_id || '',
        start_time: r.start_time || '',
        url: r.url || '',
        timestamped_url: r.timestamped_url || r.url || '',
        relevance_score: r.distance || 0,
        source: 'sermon'
      }));
    }
  } catch (err) {
    console.log(`Fast search error: ${err.message}`);
  }
  return [];
}

async function searchHybrid(query, nResults = 6, searchType = 'all') {
  try {
    const response = await axios.post(`${RERANKER_URL}/search`, {
      query,
      type: searchType,
      n_results: nResults,
      n_candidates: 20
    }, { timeout: 120000 });

    if (response.data && response.data.results) {
      const results = response.data.results;
      console.log(`Reranker returned ${results.length} results (${response.data.timing_ms}ms, ${response.data.pinned_count || 0} pinned)`);
      
      const sermons = results.filter(r => r.source === 'sermon').map(r => ({
        text: r.text,
        title: r.title || 'Sermon',
        video_id: r.video_id || '',
        start_time: r.start_time || '',
        url: r.url || '',
        timestamped_url: r.timestamped_url || r.url || '',
        relevance_score: r.rerank_score || 0,
        source: 'sermon'
      }));
      const illustrations = results.filter(r => r.source === 'illustration').map(r => ({
        text: r.text,
        title: r.title || r.summary || 'Illustration',
        topics: r.topics ? r.topics.split(',') : [],
        tone: r.emotional_tone || '',
        url: r.youtube_url || r.url || '',
        timestamp: r.start_time || '',
        source: 'illustration'
      }));
      const website = results.filter(r => r.source === 'website').map(r => ({
        text: r.text,
        page: r.page || '',
        url: r.url || '',
        relevance_score: r.rerank_score || 0,
        source: 'website'
      }));
      return { sermons, illustrations, website };
    }
  } catch (err) {
    console.log(`Reranker unavailable (${err.message}), falling back to direct Chroma`, err.response?.status, err.response?.data);
  }
  return null;
}

async function searchSermons(query, nResults = 6) {
  if (!sermonCollection) {
    try {
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments_v2' });
      const sc = await sermonCollection.count();
      console.log(`Loaded sermon_segments_v2 (${sc} segments)`);
    } catch (e) {
      console.log('sermon_segments_v2 collection not available:', e.message);
      return [];
    }
  }
  try {
    console.log(`Searching sermon_segments for: "${query}" (n=${nResults * 2})`);
    const results = await sermonCollection.query({ queryTexts: [query], nResults: nResults * 2 });
    const formatted = [];
    if (results.ids && results.ids[0]) {
      for (let i = 0; i < results.ids[0].length; i++) {
        const meta = results.metadatas[0][i] || {};
        const dist = results.distances ? results.distances[0][i] : 1;
        const text = results.documents[0][i] || '';
        const vectorScore = 1 - dist;
        const keywordScore = computeKeywordRelevance(text, query);
        const combinedScore = (vectorScore * 0.6) + (keywordScore * 0.4);
        formatted.push({
          text: text,
          title: meta.title || 'Sermon',
          video_id: meta.video_id || '',
          start_time: meta.start_time || '',
          url: meta.url || '',
          timestamped_url: meta.timestamped_url || meta.url || '',
          relevance_score: combinedScore,
          vector_score: vectorScore,
          keyword_score: keywordScore
        });
      }
    }
    formatted.sort((a, b) => b.relevance_score - a.relevance_score);
    // Deduplicate by text content (same text can appear with different titles)
    const seen = new Set();
    const deduped = formatted.filter(r => {
      const key = r.text.substring(0, 200);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const filtered = deduped
      .filter(r => !isWorshipContent(r.text, r.title))
      .filter(r => r.relevance_score > 0.15 || r.keyword_score > 0.2)
      .slice(0, nResults);
    console.log(`Found ${formatted.length} results, ${deduped.length} after dedup, returning ${filtered.length} after relevance filtering`);
    return filtered;
  } catch (err) {
    console.log('Sermon search error, retrying with fresh collection:', err.message);
    try {
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments_v2' });
      const results = await sermonCollection.query({ queryTexts: [query], nResults: nResults * 2 });
      const formatted = [];
      if (results.ids && results.ids[0]) {
        for (let i = 0; i < results.ids[0].length; i++) {
          const meta = results.metadatas[0][i] || {};
          const dist = results.distances ? results.distances[0][i] : 1;
          const text = results.documents[0][i] || '';
          const vectorScore = 1 - dist;
          const keywordScore = computeKeywordRelevance(text, query);
          const combinedScore = (vectorScore * 0.6) + (keywordScore * 0.4);
          formatted.push({
            text, title: meta.title || 'Sermon', video_id: meta.video_id || '',
            start_time: meta.start_time || '', url: meta.url || '',
            timestamped_url: meta.timestamped_url || meta.url || '',
            relevance_score: combinedScore
          });
        }
      }
      formatted.sort((a, b) => b.relevance_score - a.relevance_score);
      const filtered = formatted
        .filter(r => !isWorshipContent(r.text, r.title))
        .filter(r => r.relevance_score > 0.25)
        .slice(0, nResults);
      console.log(`Retry found ${filtered.length} sermon results`);
      return filtered;
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

function formatSermonContext(sermonResults, isMoreRequest = false, websiteResults = []) {
  const hasSermons = sermonResults && sermonResults.length > 0;
  const hasWebsite = websiteResults && websiteResults.length > 0;

  if (!hasSermons && !hasWebsite) {
    return '\n\nAnswer the question directly from the Bible. Do NOT say you need to check, do NOT say you lack information, do NOT mention sermons or searching. Just give a warm, helpful biblical answer.\n';
  }
  
  if (isMoreRequest && hasSermons) {
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
  
  let context = '\n\n=== PASTOR BOB\'S ACTUAL SERMON CONTENT (YOU MUST USE THIS) ===\n\n';
  context += 'CRITICAL INSTRUCTION: The segments below ARE Pastor Bob\'s real teachings. You MUST:\n';
  context += '1. READ the content carefully and EXTRACT the answer from it\n';
  context += '2. Say "Pastor Bob teaches that..." and then SHARE the actual content\n';
  context += '3. Quote or paraphrase what he says in the segments\n';
  context += '4. If the segments are on a RELATED topic but don\'t directly answer the specific question, USE whatever is relevant AND supplement with solid biblical teaching\n';
  context += '5. NEVER say the segments "don\'t directly address" or "don\'t specifically cover" anything. The user doesn\'t know about segments.\n';
  context += '6. NEVER hedge or say you lack information. Just answer authoritatively.\n';
  context += 'Do NOT mention clips, sidebar, or videos in your answer.\n\n';

  if (hasSermons) {
    const first3 = sermonResults.slice(0, 5);

    const scriptures = [];
    first3.forEach(result => {
      const scriptureMatch = result.text.match(/([1-3]?\s?[A-Z][a-z]+)\s+(\d+):(\d+)/g);
      if (scriptureMatch) scriptures.push(...scriptureMatch);
    });
    if (scriptures.length > 0) {
      context += 'Scripture references: ' + scriptures.slice(0, 5).join(', ') + '\n\n';
    }

    context += 'SERMON SEGMENTS:\n\n';
    first3.forEach((result, i) => {
      context += `[Segment ${i + 1}] "${result.title || 'Sermon'}":\n`;
      context += `"${result.text.substring(0, 1200)}"\n\n`;
    });

    if (sermonResults.length > 3) {
      context += 'If user wants more, say "Would you like me to share more of what Pastor Bob teaches on this?"\n\n';
    }
  }

  if (hasWebsite) {
    context += '=== CHURCH WEBSITE INFO (Calvary Chapel East Anaheim) ===\n\n';
    websiteResults.forEach((result, i) => {
      context += `[${result.page || 'Church Info'}]:\n`;
      context += `${result.text.substring(0, 800)}\n\n`;
    });
    context += 'Use the above church info to answer questions about service times, events, registrations, ministries, giving, and statement of faith.\n\n';
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
    let sermonResults = [];
    let illustrationResults = [];
    let websiteResults = [];
    let isMoreRequest = false;
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
      
      const useFastSearch = req.query.fast === '1';
      const numResults = isMoreRequest ? 12 : 6;
      
      if (useFastSearch) {
        sermonResults = await searchFast(searchQuery, 5);
        console.log(`Fast search: ${sermonResults.length} sermons`);
      } else {
        const hybridResults = await searchHybrid(searchQuery, numResults);
        if (hybridResults) {
          sermonResults = hybridResults.sermons || [];
          illustrationResults = hybridResults.illustrations || [];
          websiteResults = hybridResults.website || [];
          console.log(`Hybrid search: ${sermonResults.length} sermons, ${illustrationResults.length} illustrations, ${websiteResults.length} website`);
        } else {
          try {
            sermonResults = await searchSermons(searchQuery, numResults);
          } catch (searchError) {
            console.log('Sermon search skipped due to error:', searchError.message);
            sermonResults = [];
          }
        }
      }
      
      // Detect personal story queries and prepend pinned clips
      const storyMatches = detectPersonalStoryQuery(searchQuery);
      if (storyMatches.length > 0 && !isMoreRequest) {
        const pinnedClips = [];
        const pinnedVideoIds = new Set();
        for (const storyKey of storyMatches) {
          const story = PINNED_STORY_CLIPS[storyKey];
          if (story) {
            for (const clip of story.clips) {
              pinnedClips.push(clip);
              pinnedVideoIds.add(clip.video_id);
            }
          }
        }
        sermonResults = sermonResults.filter(r => !pinnedVideoIds.has(r.video_id));
        sermonResults = [...pinnedClips, ...sermonResults];
        console.log(`Pinned ${pinnedClips.length} personal story clips for: ${storyMatches.join(', ')}`);
      }
      
      if (sermonResults.length > 0 || websiteResults.length > 0) {
        console.log(`Found ${sermonResults.length} sermon segments, ${websiteResults.length} website results`);
        
        const sermonContext = formatSermonContext(sermonResults, isMoreRequest, websiteResults);
        console.log(`Added sermon context (${sermonContext.length} chars), isMore: ${isMoreRequest}`);
        
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
    
    // If hybrid search didn't provide illustrations, fall back to old illustration search
    if ((!illustrationResults || illustrationResults.length === 0) && lastUserMessage && lastUserMessage.role === 'user' && !isMoreRequest && illustrationCollection) {
      try {
        const illResponse = await axios.post(`http://localhost:${PORT}/api/illustration/search`, {
          query: lastUserMessage.content,
          n_results: 3
        }, { timeout: 5000 });
        const oldIllResults = illResponse.data.results || [];
        if (oldIllResults.length > 0) {
          illustrationResults = oldIllResults.map(ill => ({
            title: ill.illustration || 'Illustration',
            text: ill.text || '',
            topics: ill.topics || [],
            tone: ill.tone || '',
            url: ill.video_url || '',
            timestamp: ill.timestamp || ''
          }));
          console.log(`Found ${illustrationResults.length} illustrations from fallback search`);
        }
      } catch (err) {
        console.log('Illustration search error:', err.message);
      }
    }
    
    // Send illustrations as separate event
    if (illustrationResults && illustrationResults.length > 0) {
      const illustrationsToSend = illustrationResults.map(ill => ({
        title: ill.title || ill.illustration || 'Illustration',
        text: ill.text || '',
        topics: ill.topics || [],
        tone: ill.tone || ill.emotional_tone || '',
        url: ill.url || ill.video_url || '',
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
      sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments_v2' });
      const sc = await sermonCollection.count();
      console.log(`Chroma Cloud: sermon_segments_v2 loaded (${sc} segments)`);
    } catch (e) {
      console.log('sermon_segments_v2 not found, trying sermon_segments:', e.message);
      try {
        sermonCollection = await chromaClient.getCollection({ name: 'sermon_segments' });
        const sc = await sermonCollection.count();
        console.log(`Chroma Cloud: sermon_segments fallback loaded (${sc} segments)`);
      } catch (e2) { console.log('sermon_segments also not found:', e2.message); }
    }
    try {
      illustrationCollection = await chromaClient.getCollection({ name: 'illustrations_v5' });
      const ic = await illustrationCollection.count();
      console.log(`Chroma Cloud: illustrations_v5 loaded (${ic} items)`);
    } catch (e) {
      console.log('illustrations_v5 not found, trying illustrations_v4:', e.message);
      try {
        illustrationCollection = await chromaClient.getCollection({ name: 'illustrations_v4' });
        const ic = await illustrationCollection.count();
        console.log(`Chroma Cloud: illustrations_v4 fallback loaded (${ic} items)`);
      } catch (e2) { console.log('illustrations_v4 also not found:', e2.message); }
    }
  } catch (e) {
    console.error('Chroma Cloud init error:', e.message);
  }
}
initChromaCloud();

app.post('/api/sermon/search', async (req, res) => {
  try {
    const { query, n_results = 6 } = req.body;
    if (!query) return res.status(400).json({ error: 'Query required' });
    try {
      const rerankerResponse = await axios.post(`${RERANKER_URL}/search`, {
        query,
        type: 'sermons',
        n_results: n_results,
        n_candidates: 20
      }, { timeout: 120000 });
      if (rerankerResponse.data && rerankerResponse.data.results) {
        const formatted = rerankerResponse.data.results
          .filter(r => r.source === 'sermon')
          .map(r => ({
            text: r.text || '',
            title: r.title || 'Sermon',
            video_id: r.video_id || '',
            start_time: r.start_time || '',
            url: r.url || '',
            timestamped_url: r.timestamped_url || r.url || '',
            relevance_score: r.rerank_score || 0,
            main_topic: r.main_topic || '',
            summary: r.summary || ''
          }));
        console.log(`Sermon search via reranker: ${formatted.length} results for "${query}"`);
        return res.json({ query, count: formatted.length, results: formatted });
      }
    } catch (rerankerErr) {
      console.log(`Sermon reranker fallback: ${rerankerErr.message}`, rerankerErr.response?.status, rerankerErr.response?.data);
    }
    res.json({ query, count: 0, results: [] });
  } catch (error) {
    console.error('Sermon search error:', error.message);
    res.status(500).json({ error: 'Sermon search failed', results: [] });
  }
});

app.post('/api/illustration/search', async (req, res) => {
  try {
    const { query, n_results = 3 } = req.body;
    if (!query) return res.status(400).json({ error: 'Query required' });
    try {
      const rerankerResponse = await axios.post(`${RERANKER_URL}/search`, {
        query,
        type: 'illustrations',
        n_results: n_results,
        n_candidates: 20
      }, { timeout: 120000 });
      if (rerankerResponse.data && rerankerResponse.data.results) {
        const formatted = rerankerResponse.data.results
          .filter(r => r.source === 'illustration')
          .map(r => ({
            illustration: r.summary || r.title || '',
            type: r.type || '',
            text: r.text || '',
            video_url: r.youtube_url || r.url || '',
            timestamp: r.start_time || r.timestamp || '',
            topics: r.topics ? r.topics.split(',') : [],
            tone: r.emotional_tone || r.tone || '',
            video_id: r.video_id || '',
            relevance_score: r.rerank_score || 0,
            topic_score: 10
          }));
        console.log(`Illustration search via reranker: ${formatted.length} results for "${query}"`);
        return res.json({ query, count: formatted.length, results: formatted });
      }
    } catch (rerankerErr) {
      console.log(`Illustration reranker fallback: ${rerankerErr.message}`, rerankerErr.response?.status, rerankerErr.response?.data);
    }
    res.json({ query, count: 0, results: [] });
  } catch (error) {
    console.error('Illustration search error:', error.message);
    res.status(500).json({ error: 'Illustration search failed', results: [] });
  }
});

app.get('/api/sermon/health', async (req, res) => {
  let rerankerStatus = 'unknown';
  let rerankerError = null;
  try {
    const r = await axios.get(`${RERANKER_URL}/ping`, { timeout: 5000 });
    rerankerStatus = r.data ? 'ok' : 'no_data';
  } catch (e) {
    rerankerStatus = 'error';
    rerankerError = e.message;
  }
  res.json({
    status: chromaClient ? 'ok' : 'not_configured',
    sermons: sermonCollection ? 'loaded' : 'not_loaded',
    illustrations: illustrationCollection ? 'loaded' : 'not_loaded',
    reranker_url: RERANKER_URL,
    reranker: rerankerStatus,
    reranker_error: rerankerError
  });
});

app.listen(PORT, () => {
  console.log(`\n🚀 Server running on http://localhost:${PORT}`);
  console.log(`📺 Open chat at: http://localhost:${PORT}/chat.html`);
  console.log(`✅ Health check: http://localhost:${PORT}/health\n`);
});
