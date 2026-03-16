const express = require('express');
const { AccessToken } = require('livekit-server-sdk');
const axios = require('axios');
const { CloudClient } = require('chromadb');
const SermonSearch = require('./sermonSearch');
const fs = require('fs');
const path = require('path');
require('dotenv').config();

const app = express();
app.use(express.json());

// Add CORS headers for all routes
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
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

function isNonBobContent(text, title) {
  const textLower = (text || '').toLowerCase();
  const titleLower = (title || '').toLowerCase();
  if (titleLower === 'unknown sermon' || titleLower === 'unknown' || titleLower === '') return true;
  const nonBobPatterns = [
    "women's", "womens", "women\u2019s", "ladies",
    "adventure kid", "children's ministry", "kids ministry", "kids church",
    "worship song", "hymn", "music video", "singing", "choir",
    "servicio", "espa\u00f1ol", "espanol", "en vivo", "iglesia", "spanish",
    "guest speaker", "guest pastor", "guest:",
    "men's bible study", "mens bible study", "men's study", "mens study",
    "men's friday", "mens friday", "men's breakfast", "mens breakfast",
    "men's conference", "mens conference", "men\u2019s conference", "men's retreat",
    "vlog #", "vlog #",
    "weekly devotional", "daily devotional", "midweek devotional", "devotional with",
    "community groups |", "community group |",
    "home bible study session", "home bible study |",
    "heart & stone | session",
    "talent camp", "summer talent camp",
  ];
  if (nonBobPatterns.some(p => titleLower.includes(p))) return true;
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
  },
  homosexuality: {
    keywords: ['homosexuality', 'homosexual', 'gay wedding', 'gay marriage', 'gay couple', 'lgbtq', 'same sex', 'same-sex', 'attend a gay wedding', 'transgender'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 3',
        url: 'https://www.youtube.com/watch?v=ej9SjOG6WtM',
        timestamped_url: 'https://www.youtube.com/watch?v=ej9SjOG6WtM&t=486s',
        start_time: '8:06',
        video_id: 'ej9SjOG6WtM',
        text: 'Pastor Bob addresses whether Christians should attend gay weddings. He would never attend because he feels it endorses what is happening, but understands others may feel differently. Discusses loving people without condoning sin.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 16',
        url: 'https://www.youtube.com/watch?v=LxLSHI_p6Yc',
        timestamped_url: 'https://www.youtube.com/watch?v=LxLSHI_p6Yc&t=3188s',
        start_time: '53:08',
        video_id: 'LxLSHI_p6Yc',
        text: 'Pastor Bob discusses how to relate to homosexual family members and friends. Never compromise values, but minister truth in love. You can be with homosexual couples — just don\'t endorse sin.',
        relevance_score: 0.95
      },
      {
        title: 'Ask Pastor Bob | Episode 9',
        url: 'https://www.youtube.com/watch?v=wwruBqgB6s8',
        timestamped_url: 'https://www.youtube.com/watch?v=wwruBqgB6s8&t=2804s',
        start_time: '46:44',
        video_id: 'wwruBqgB6s8',
        text: 'Military wife asks about LGBTQ family dynamics and children. Pastor Bob counsels to maintain relationships while teaching children God\'s standards. Love without condoning sin, use as teaching opportunities.',
        relevance_score: 0.9
      }
    ]
  },
  gilboa: {
    keywords: ['gilboa', 'mount gilboa', 'saul died', 'saul death', 'shield of saul', 'david lament', 'curse literal', 'curse on the mountain', 'curse on mount', 'gilboa curse', 'is the curse literal', 'dew nor rain'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 8',
        url: 'https://www.youtube.com/watch?v=u53G6pe6pYc',
        timestamped_url: 'https://www.youtube.com/watch?v=u53G6pe6pYc&t=3895s',
        start_time: '64:55',
        video_id: 'u53G6pe6pYc',
        text: 'Pastor Bob teaches that the curse King David pronounced on Mount Gilboa in Second Samuel 1:21 — "Ye mountains of Gilboa, let there be no dew, neither let there be rain, upon you" — is a LITERAL curse, NOT merely poetic language. It is an actual supernatural declaration spoken by the power of God\'s anointed king that has held for over 3,000 years. That region remains unusually dry even today with minimal rainfall or dew compared to surrounding areas. This demonstrates the enduring authority of words spoken under God\'s anointing — biblical curses like this carry real, ongoing weight.',
        relevance_score: 1.0
      }
    ]
  },
  six_seven: {
    keywords: ['6 and 7', 'six and seven', 'number 6', 'number 7', 'biblical numbers', 'significance of numbers', 'number six', 'number seven', '6 7', 'numerology bible', 'meme 6', '6-7', 'the meme', '67', 'six seven', '6/7', 'six-seven', 'viral tiktok', 'tiktok meme', 'the numbers', 'meme 67', 'meme six seven', 'what is 67', 'what does 67 mean', 'what does 6 7 mean'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 21',
        url: 'https://www.youtube.com/watch?v=K56a6OVC87g',
        timestamped_url: 'https://www.youtube.com/watch?v=K56a6OVC87g&t=25s',
        start_time: '0:25',
        video_id: 'K56a6OVC87g',
        text: 'Pastor Bob discusses the famous 6-7 meme (also called the 67 meme or six seven meme) — his teaching about the biblical significance of the numbers 6 and 7 went viral on TikTok with six million views. Someone found the clip and repurposed it as a meme. The number 6 represents man — created on day 6, always falls short. The number 7 represents completion and perfection — God rested on day 7. Pastor Bob says he still doesn\'t know why the Lord allowed it to blow up but wonders how to use the internet and social media to draw people to Christ.',
        relevance_score: 1.0
      }
    ]
  },
  demons_possession: {
    keywords: ['demon possessed', 'demon possession', 'can christians be possessed', 'demonic possession', 'exorcism', 'cast out demons', 'demonic influence', 'spiritual oppression'],
    clips: [
      {
        title: 'Can Christians be POSSESSED by demons? | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=swZ0kGoe9gQ',
        timestamped_url: 'https://www.youtube.com/watch?v=swZ0kGoe9gQ&t=5s',
        start_time: '0:05',
        video_id: 'swZ0kGoe9gQ',
        text: 'Pastor Bob directly addresses whether Christians can be possessed by demons. Believers can be influenced but not possessed — what we yield to controls us.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 14',
        url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk',
        timestamped_url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk&t=1654s',
        start_time: '27:34',
        video_id: 'FuyAFd1iqwk',
        text: 'Pastor Bob teaches that believers can be influenced by demonic forces but not possessed. Ephesians 5 — don\'t be drunk with wine, be filled with Spirit. What we yield to controls us — flesh or Spirit.',
        relevance_score: 0.95
      }
    ]
  },
  ivf: {
    keywords: ['ivf', 'in vitro', 'fertilization', 'fertilized egg', 'test tube baby', 'fertility treatment'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 11',
        url: 'https://www.youtube.com/watch?v=q4RHIF5IPZ4',
        timestamped_url: 'https://www.youtube.com/watch?v=q4RHIF5IPZ4&t=109s',
        start_time: '1:49',
        video_id: 'q4RHIF5IPZ4',
        text: 'Pastor Bob supports IVF but is concerned about treating fertilized eggs as human lives. Psalm 51 — David says "in sin my mother conceived me." Life begins at conception. Use all fertilized eggs, don\'t discard any.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 12',
        url: 'https://www.youtube.com/watch?v=pbjvnVVa49M',
        timestamped_url: 'https://www.youtube.com/watch?v=pbjvnVVa49M&t=2613s',
        start_time: '43:33',
        video_id: 'pbjvnVVa49M',
        text: 'Extended IVF discussion including chromosomal issues. Pastor Bob reaffirms support for IVF technology while emphasizing treating all fertilized eggs as human lives.',
        relevance_score: 0.9
      }
    ]
  },
  rapture: {
    keywords: ['rapture', 'pre-tribulation', 'pre-trib', 'caught up in the air', 'will christians go through tribulation', 'left behind', 'tribulation period'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 21',
        url: 'https://www.youtube.com/watch?v=K56a6OVC87g',
        timestamped_url: 'https://www.youtube.com/watch?v=K56a6OVC87g&t=836s',
        start_time: '13:56',
        video_id: 'K56a6OVC87g',
        text: 'Pastor Bob teaches pre-tribulation rapture from 1 Thessalonians 4 — "harpado, literally snatched away, caught up together to meet them in the clouds." Church is absent from Revelation after chapter 3.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 12',
        url: 'https://www.youtube.com/watch?v=pbjvnVVa49M',
        timestamped_url: 'https://www.youtube.com/watch?v=pbjvnVVa49M&t=3278s',
        start_time: '54:38',
        video_id: 'pbjvnVVa49M',
        text: 'Pastor Bob defends pre-trib rapture position. Church not destined for wrath but for salvation. Lot removed before Sodom\'s destruction as the biblical protection model.',
        relevance_score: 0.9
      }
    ]
  },
  suffering_evil: {
    keywords: ['why does god allow suffering', 'problem of evil', 'theodicy', 'why suffering', 'why does god allow evil', 'why do bad things happen'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 14',
        url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk',
        timestamped_url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk&t=264s',
        start_time: '4:24',
        video_id: 'FuyAFd1iqwk',
        text: 'Pastor Bob addresses how a good and powerful God can allow suffering. The problem only exists when viewing this temporal life alone. Life is like a birth process — painful but purposeful. Romans teaches afflictions produce character.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 16',
        url: 'https://www.youtube.com/watch?v=LxLSHI_p6Yc',
        timestamped_url: 'https://www.youtube.com/watch?v=LxLSHI_p6Yc&t=427s',
        start_time: '7:07',
        video_id: 'LxLSHI_p6Yc',
        text: 'Pastor Bob discusses Job\'s suffering. God allows suffering to develop and display faith. Millions have been blessed by Job\'s story through history. Even Jesus, the perfect sinless Son, suffered unjustly.',
        relevance_score: 0.95
      }
    ]
  },
  prayer_how: {
    keywords: ['how to pray', 'prayer life', 'prayer list', 'grow in prayer', 'prayer method', 'develop prayer', 'fervent prayer', 'effectual prayer'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 13',
        url: 'https://www.youtube.com/watch?v=YAVSXH9u1eE',
        timestamped_url: 'https://www.youtube.com/watch?v=YAVSXH9u1eE&t=114s',
        start_time: '1:54',
        video_id: 'YAVSXH9u1eE',
        text: 'Pastor Bob shares his personal prayer development. He initially resisted prayer lists but discovered them throughout scripture (Lord\'s Prayer, high priest\'s breastplate). Prays out loud, uses lists, thanks God for the same things differently each day.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 21',
        url: 'https://www.youtube.com/watch?v=K56a6OVC87g',
        timestamped_url: 'https://www.youtube.com/watch?v=K56a6OVC87g&t=4583s',
        start_time: '76:23',
        video_id: 'K56a6OVC87g',
        text: 'Pastor Bob talks about the privilege and honor of prayer — communing with the King of Kings. Discusses how to pray with depth and not just demonstrating it but living it.',
        relevance_score: 0.9
      }
    ]
  },
  lose_salvation: {
    keywords: ['lose salvation', 'lose my salvation', 'lose our salvation', 'eternal security', 'once saved always saved', 'can i lose', 'fall away'],
    clips: [
      {
        title: 'Ask Pastor Bob | Can We Lose Our Salvation?',
        url: 'https://www.youtube.com/watch?v=VIkQFbHWq4g',
        timestamped_url: 'https://www.youtube.com/watch?v=VIkQFbHWq4g&t=135s',
        start_time: '2:15',
        video_id: 'VIkQFbHWq4g',
        text: 'Pastor Bob directly addresses whether believers can lose their salvation — a dedicated full episode on eternal security.',
        relevance_score: 1.0
      },
      {
        title: '"Can I lose my salvation?" | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=FVH7iWbhbL4',
        timestamped_url: 'https://www.youtube.com/watch?v=FVH7iWbhbL4&t=5s',
        start_time: '0:05',
        video_id: 'FVH7iWbhbL4',
        text: 'Short-form clip where Pastor Bob answers the question "Can I lose my salvation?" directly.',
        relevance_score: 0.95
      }
    ]
  },
  flat_earth: {
    keywords: ['flat earth', 'is the earth flat', 'four corners of the earth', 'earth is flat'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 14',
        url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk',
        timestamped_url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk&t=1389s',
        start_time: '23:09',
        video_id: 'FuyAFd1iqwk',
        text: 'Pastor Bob addresses flat earth theory. Isaiah 40:22 says "circle of the earth," Job 26:7 says God "hangs earth on nothing." "Four corners" and "ends of earth" are figurative expressions. Ancient people generally knew earth was round.',
        relevance_score: 1.0
      }
    ]
  },
  babies_heaven: {
    keywords: ['babies go to heaven', 'infants heaven', 'baby dies', 'child dies', 'what happens to babies', 'age of accountability', 'unborn baby heaven', 'miscarriage heaven', 'stillborn'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 10',
        url: 'https://www.youtube.com/watch?v=5HcKh75neAY',
        timestamped_url: 'https://www.youtube.com/watch?v=5HcKh75neAY&t=843s',
        start_time: '14:03',
        video_id: '5HcKh75neAY',
        text: 'Pastor Bob teaches that babies are likely in heaven based on God\'s mercy. David\'s confidence about joining his deceased son (2 Samuel 12). God\'s judgment is based on knowledge — Paul found mercy because he acted in ignorance.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 21',
        url: 'https://www.youtube.com/watch?v=K56a6OVC87g',
        timestamped_url: 'https://www.youtube.com/watch?v=K56a6OVC87g&t=3042s',
        start_time: '50:42',
        video_id: 'K56a6OVC87g',
        text: 'Pastor Bob addresses whether babies in the womb are saved or unsaved. Discusses what we should expect for babies and young children who pass away.',
        relevance_score: 0.95
      }
    ]
  },
  hell_eternal: {
    keywords: ['is hell eternal', 'eternal punishment', 'hell forever', 'annihilationism', 'does hell last forever', 'gehenna'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 5',
        url: 'https://www.youtube.com/watch?v=xzy4Mrd2GiU',
        timestamped_url: 'https://www.youtube.com/watch?v=xzy4Mrd2GiU&t=2463s',
        start_time: '41:03',
        video_id: 'xzy4Mrd2GiU',
        text: 'Pastor Bob explains Gehenna — the Valley of Hinnom, a garbage dump with fires burning and corpses. Discusses hell as eternal, not temporary punishment. Perfect justice demands eternal punishment for sin against an infinite God.',
        relevance_score: 1.0
      }
    ]
  },
  trust_bible: {
    keywords: ['trust the bible', 'bible reliable', 'bible trustworthy', 'bible true', 'how do we know the bible is true', 'bible accuracy', 'bible evidence'],
    clips: [
      {
        title: 'Ask Pastor Bob | Why Can We Trust The Bible?',
        url: 'https://www.youtube.com/watch?v=LjwTOep0z-4',
        timestamped_url: 'https://www.youtube.com/watch?v=LjwTOep0z-4&t=135s',
        start_time: '2:15',
        video_id: 'LjwTOep0z-4',
        text: 'Dedicated episode where Pastor Bob makes the case for why we can trust the Bible. Covers manuscript evidence, historical reliability, and fulfilled prophecy.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 15',
        url: 'https://www.youtube.com/watch?v=QpxYcM046F4',
        timestamped_url: 'https://www.youtube.com/watch?v=QpxYcM046F4&t=243s',
        start_time: '4:03',
        video_id: 'QpxYcM046F4',
        text: 'Pastor Bob discusses Josephus (Jewish historian, first century), Tacitus (Roman historian, 116 AD mentions "Christus" under Pontius Pilate), and other non-Christian historical sources that verify biblical events.',
        relevance_score: 0.95
      }
    ]
  },
  antichrist: {
    keywords: ['antichrist', 'mark of the beast', 'mark of beast', '666', 'beast of revelation', 'who is the antichrist', 'number of the beast'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 11',
        url: 'https://www.youtube.com/watch?v=q4RHIF5IPZ4',
        timestamped_url: 'https://www.youtube.com/watch?v=q4RHIF5IPZ4&t=1188s',
        start_time: '19:48',
        video_id: 'q4RHIF5IPZ4',
        text: 'Pastor Bob teaches the Antichrist won\'t be revealed until the church is removed (2 Thessalonians 2). Initially brings peace, accepted as false messiah by Israel. Technology signs like palm scanners show proximity to prophetic fulfillment.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 24',
        url: 'https://www.youtube.com/watch?v=AD9E4e7NfnE',
        timestamped_url: 'https://www.youtube.com/watch?v=AD9E4e7NfnE&t=748s',
        start_time: '12:28',
        video_id: 'AD9E4e7NfnE',
        text: 'Pastor Bob discusses the spirit of antichrist from 1 John — the unwillingness to admit the person of Christ. Distinguishes between the spirit of antichrist and the future Antichrist figure.',
        relevance_score: 0.9
      }
    ]
  },
  predestination: {
    keywords: ['predestination', 'calvinism', 'reformed theology', 'free will', 'election', 'chosen', 'irresistible grace', 'tulip', 'calvinist'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 23',
        url: 'https://www.youtube.com/watch?v=k9H6_InZ9Q4',
        timestamped_url: 'https://www.youtube.com/watch?v=k9H6_InZ9Q4&t=1363s',
        start_time: '22:43',
        video_id: 'k9H6_InZ9Q4',
        text: 'Pastor Bob discusses the reformed camp\'s emphasis that man cannot come to God on his own, references Martin Luther. God\'s foreknowledge and predestination don\'t determine someone\'s destiny — human accountability is maintained.',
        relevance_score: 1.0
      },
      {
        title: 'Ask Pastor Bob | Episode 9',
        url: 'https://www.youtube.com/watch?v=wwruBqgB6s8',
        timestamped_url: 'https://www.youtube.com/watch?v=wwruBqgB6s8&t=2305s',
        start_time: '38:25',
        video_id: 'wwruBqgB6s8',
        text: 'Pastor Bob on predestination and human responsibility. God weaves human sin into His predetermined plan. Examples: Joseph\'s brothers, the Crucifixion (Acts 2). Human accountability maintained while God accomplishes His purpose.',
        relevance_score: 0.9
      }
    ]
  },
  mental_health: {
    keywords: ['mental health', 'depression', 'anxiety', 'is depression a sin', 'mental illness', 'christian depression'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 9',
        url: 'https://www.youtube.com/watch?v=wwruBqgB6s8',
        timestamped_url: 'https://www.youtube.com/watch?v=wwruBqgB6s8&t=629s',
        start_time: '10:29',
        video_id: 'wwruBqgB6s8',
        text: 'Pastor Bob teaches mental health and sin are not automatically connected but can be related. Cites Cain\'s depression in Genesis 4. Philippians 4:8 — focus on what\'s pure, lovely, worthy of praise. We can change what we think about.',
        relevance_score: 1.0
      }
    ]
  },
  ufos: {
    keywords: ['ufo', 'ufos', 'extraterrestrial', 'aliens', 'alien life', 'life on other planets'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 10',
        url: 'https://www.youtube.com/watch?v=5HcKh75neAY',
        timestamped_url: 'https://www.youtube.com/watch?v=5HcKh75neAY&t=136s',
        start_time: '2:16',
        video_id: '5HcKh75neAY',
        text: 'Pastor Bob is open to the possibility of extraterrestrial life but says there\'s no biblical connection. References Larry Norman song about Jesus as the ultimate UFO. Don\'t connect dots God doesn\'t connect.',
        relevance_score: 1.0
      }
    ]
  },
  addiction_enabling: {
    keywords: ['addiction', 'drug addiction', 'enabling', 'tough love', 'prodigal son', 'addicted child', 'substance abuse', 'alcoholism'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 10',
        url: 'https://www.youtube.com/watch?v=5HcKh75neAY',
        timestamped_url: 'https://www.youtube.com/watch?v=5HcKh75neAY&t=1777s',
        start_time: '29:37',
        video_id: '5HcKh75neAY',
        text: 'Parent asks about 29-year-old addicted son. Pastor Bob counsels tough love — don\'t enable addiction. Prodigal son had to hit bottom before returning. Continue praying, maintain boundaries, don\'t take addicted person into home.',
        relevance_score: 1.0
      }
    ]
  },
  creation_earth_age: {
    keywords: ['age of earth', 'young earth', 'old earth', 'creation vs evolution', 'gap theory', 'fossils', 'dinosaurs', 'how old is the earth'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 14',
        url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk',
        timestamped_url: 'https://www.youtube.com/watch?v=FuyAFd1iqwk&t=1982s',
        start_time: '33:02',
        video_id: 'FuyAFd1iqwk',
        text: 'Pastor Bob is a young earth creationist, rejects theistic evolution. Discusses day-age theory, gap theory, and appearance of age. Adam created as adult, stars created with light already traveling. Honest about not solving all scientific dating questions.',
        relevance_score: 1.0
      }
    ]
  },
  easter_resurrection: {
    keywords: ['resurrection evidence', 'did jesus rise', 'empty tomb', 'proof of resurrection', 'easter meaning', 'why did jesus have to die', 'crucifixion necessary'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 15',
        url: 'https://www.youtube.com/watch?v=QpxYcM046F4',
        timestamped_url: 'https://www.youtube.com/watch?v=QpxYcM046F4&t=131s',
        start_time: '2:11',
        video_id: 'QpxYcM046F4',
        text: 'Easter special episode. Pastor Bob covers evidence for the resurrection: empty tomb with enemy guards, Dr. Simon Greenleaf (Harvard Law) concluded evidence sufficient, Frank Morrison tried to disprove it and became convinced. Disciples transformed from cowards to bold martyrs.',
        relevance_score: 1.0
      }
    ]
  },
  fig_tree: {
    keywords: ['fig tree', 'cursing the fig tree', 'why did jesus curse the fig tree', 'fruitless fig tree'],
    clips: [
      {
        title: 'Ask Pastor Bob | Episode 12',
        url: 'https://www.youtube.com/watch?v=pbjvnVVa49M',
        timestamped_url: 'https://www.youtube.com/watch?v=pbjvnVVa49M&t=1309s',
        start_time: '21:49',
        video_id: 'pbjvnVVa49M',
        text: 'Pastor Bob explains Jesus cursing the fig tree (Mark 11) as symbolism of Israel having leaves (appearance of religion) but no fruit. John 15 — "without me you can do nothing." Only by the Spirit can we bear fruit.',
        relevance_score: 1.0
      }
    ]
  },
  scariest_verse: {
    keywords: ['scariest verse', 'scary verse', 'most terrifying verse', 'depart from me'],
    clips: [
      {
        title: 'is this the SCARIEST verse in the Bible? | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=VUO1KpYh9YA',
        timestamped_url: 'https://www.youtube.com/watch?v=VUO1KpYh9YA&t=5s',
        start_time: '0:05',
        video_id: 'VUO1KpYh9YA',
        text: 'Pastor Bob discusses what may be the scariest verse in the Bible.',
        relevance_score: 1.0
      }
    ]
  },
  women_church: {
    keywords: ['women in church', 'women leadership', 'women pastor', 'women preach', 'can women preach', 'women role in church', 'female pastor'],
    clips: [
      {
        title: 'What are womens role in church leadership? | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=Zn2dlrNXYBw',
        timestamped_url: 'https://www.youtube.com/watch?v=Zn2dlrNXYBw&t=5s',
        start_time: '0:05',
        video_id: 'Zn2dlrNXYBw',
        text: 'Pastor Bob discusses women\'s role in church leadership. Women can minister but not as senior pastors or elders. Joel 2/Acts 2 — sons and daughters prophesy. Not in authoritative teaching roles over men in church context.',
        relevance_score: 1.0
      }
    ]
  },
  what_give_god: {
    keywords: ['what can i give to god', 'give to god', 'offering to god', 'sacrifice to god'],
    clips: [
      {
        title: '"What can I give to God?" | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=60lrWVviPKc',
        timestamped_url: 'https://www.youtube.com/watch?v=60lrWVviPKc&t=5s',
        start_time: '0:05',
        video_id: '60lrWVviPKc',
        text: 'Pastor Bob answers the question "What can I give to God?" from a biblical perspective.',
        relevance_score: 1.0
      }
    ]
  },
  how_bob_saved: {
    keywords: ['how pastor bob met the lord', 'how bob was saved', 'bob saved', 'pastor bob testimony podcast'],
    clips: [
      {
        title: 'How Pastor Bob Met the Lord | Ask Pastor Bob',
        url: 'https://www.youtube.com/watch?v=Rg24InyZwVw',
        timestamped_url: 'https://www.youtube.com/watch?v=Rg24InyZwVw&t=5s',
        start_time: '0:05',
        video_id: 'Rg24InyZwVw',
        text: 'Podcast clip of Pastor Bob sharing how he met the Lord at 13 years old at a Campus Crusade camp. His friend Fred invited him, and Jeff Maples and Gene Schaeffer shared the gospel with him for about five minutes.',
        relevance_score: 1.0
      }
    ]
  }
};

const CHURCH_INFO_KEYWORDS = [
  'service time', 'service times', 'what time is service', 'when is service', 'when are services',
  'community group', 'community groups', 'home group', 'home groups', 'small group', 'small groups',
  'join a group', 'host a group',
  'register', 'registration', 'sign up for', 'signup',
  'coming up at', 'upcoming event', 'calendar', 'schedule',
  'volunteer at', 'volunteering at',
  'how to give', 'how to tithe', 'donate to', 'donation',
  'statement of faith', 'what does the church believe', 'what do you believe',
  'missionary', 'missionaries',
  'new here', 'first time', 'visiting the church', 'visitor',
  'location', 'address', 'where is the church', 'directions',
  'contact', 'phone number', 'office hours',
  'wedding application', 'marriage application',
  'crisis pregnancy', 'pregnancy resource',
  'disability ministry', 'special needs ministry',
  'pastor bob\'s resources', 'study tools', 'e-sword',
  'live stream', 'livestream', 'watch live', 'watch online',
  'youth group', 'youth ministry', 'kids ministry', 'children\'s ministry',
  'homeschool', 'home school',
  'prayer request', 'prayer list',
  'church info', 'church information', 'about the church', 'about ccea',
  'calvary chapel east anaheim',
  'bulletin', 'announcements',
  'worship team', 'worship lyrics',
  'baptism class', 'membership class',
  'highlights',
  'school of discipleship',
  'church camp', 'summer camp',
  'blessfest', 'cars and coffee', 'cars & coffee',
  'royal rangers', 'mpact girls', 'adventure kids',
  'griefshare', 'divorcecare', 'divorce care', 'grief share',
  'newcomer', 'newcomers dinner',
  'how much does', 'what is the cost', 'what is the price',
];

let dynamicWebsiteKeywords = [];
let lastKeywordFetch = 0;

async function fetchDynamicKeywords() {
  try {
    const res = await axios.get(`${RERANKER_URL}/website-keywords`, { timeout: 5000 });
    if (res.data && res.data.keywords) {
      dynamicWebsiteKeywords = res.data.keywords;
      lastKeywordFetch = Date.now();
      console.log(`Loaded ${dynamicWebsiteKeywords.length} dynamic website keywords`);
    }
  } catch (err) {
    // silent
  }
}

setTimeout(fetchDynamicKeywords, 30000);
setInterval(fetchDynamicKeywords, 3600000);

function isChurchInfoQuery(query) {
  const q = query.toLowerCase().replace(/['']/g, "'");
  if (CHURCH_INFO_KEYWORDS.some(kw => q.includes(kw))) return true;
  if (dynamicWebsiteKeywords.some(kw => q.includes(kw))) return true;
  return false;
}

const CHURCH_TOPIC_PAGES = {
  events: { keywords: ['event', 'events', 'upcoming', 'coming up', 'happening', 'register', 'registration', 'sign up', 'signup', 'calendar', 'schedule', 'camp', 'church camp', 'retreat', 'conference', 'cruise', 'trip', 'tour', 'easter', 'christmas', 'good friday', 'potluck', 'dinner', 'brunch', 'breakfast', 'blessfest', 'newcomer', 'cost', 'how much', 'price', 'fee'], pages: ['/registrations'] },
  studies: { keywords: ['bible study', 'bible studies', 'home group', 'home groups', 'small group', 'small groups', 'community group', 'community groups', 'join a group', 'host a group', 'good shepherd study'], pages: ['/resources/home-bible-studies', '/service-times-and-location', 'https://www.cceacommunity.org/'] },
  services: { keywords: ['service time', 'service times', 'what time', 'when is service', 'when are services', 'sunday service', 'wednesday service', 'wednesday night'], pages: ['/service-times-and-location'] },
  ministries: { keywords: ['ministry', 'ministries'], pages: ['/ministries-2'] },
  volunteer: { keywords: ['volunteer', 'volunteering', 'serve', 'serving'], pages: ['/volunteer'] },
  giving: { keywords: ['give', 'giving', 'tithe', 'tithing', 'donate', 'donation', 'offering'], pages: ['/give'] },
  missions: { keywords: ['mission', 'missions', 'missionary', 'missionaries'], pages: ['/missions'] },
  faith: { keywords: ['statement of faith', 'what does the church believe', 'what do you believe'], pages: ['/about-us/statement-of-faith'] },
  newHere: { keywords: ['new here', 'first time', 'visiting', 'visitor', 'new to the church'], pages: ['/new-here'] },
  location: { keywords: ['location', 'address', 'where is the church', 'directions'], pages: ['/service-times-and-location'] },
  livestream: { keywords: ['live stream', 'livestream', 'watch live', 'watch online'], pages: ['/services/live'] },
  youth: { keywords: ['youth group', 'youth ministry'], pages: ['/ministries-2', 'https://www.cceayouth.com'] },
  children: { keywords: ['kids ministry', "children's ministry", 'adventure kids', 'kids church', "children's church", 'level up wednesday', 'royal rangers', 'mpact girls', 'kidcheck', 'kid check', 'nursery'], pages: ['/ministries-2', 'https://www.cceachildrens.com'] },
  homeschool: { keywords: ['homeschool', 'home school', 'homeschooling'], pages: ['/ministries-2', 'https://www.cceahomeschool.com'] },
  women: { keywords: ["women's study", "women's bible", "women's ministry"], pages: ['/ministries-2', '/resources/home-bible-studies'] },
  men: { keywords: ["men's study", "men's bible", "men's ministry"], pages: ['/ministries-2', '/resources/home-bible-studies'] },
};

function detectChurchTopicPages(query) {
  const q = query.toLowerCase().replace(/['']/g, "'");
  const matched = new Set();
  for (const [, config] of Object.entries(CHURCH_TOPIC_PAGES)) {
    for (const kw of config.keywords) {
      if (q.includes(kw)) {
        config.pages.forEach(p => matched.add(p));
        break;
      }
    }
  }
  return [...matched];
}

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
      .filter(r => !isNonBobContent(r.text, r.title))
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
        .filter(r => !isNonBobContent(r.text, r.title))
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
  
  let context = '\n\n=== PASTOR BOB\'S ACTUAL SERMON TRANSCRIPTS ===\n\n';
  context += 'These are REAL transcripts from Pastor Bob\'s sermons. You MUST:\n';
  context += '1. SYNTHESIZE across ALL segments below to build a COMPLETE, NUANCED answer\n';
  context += '2. Identify the FULL theological framework Pastor Bob teaches — look for multi-part teachings, distinctions, stages, or nuances across segments\n';
  context += '3. Say "Pastor Bob teaches..." and share his actual teaching with its full depth\n';
  context += '4. If he makes distinctions (e.g., "there is X but there is also Y"), preserve those distinctions in your answer\n';
  context += '5. Quote or closely paraphrase his actual words when they are powerful\n';
  context += '6. NEVER flatten a nuanced teaching into a simple one-line answer\n';
  context += '7. NEVER say you lack information — the transcripts below ARE your source\n';
  context += 'Do NOT mention clips, sidebar, segments, transcripts, or videos in your answer.\n\n';

  if (hasSermons) {
    const topResults = sermonResults.slice(0, 6);

    context += 'SERMON TRANSCRIPTS:\n\n';
    topResults.forEach((result, i) => {
      context += `[${i + 1}] "${result.title || 'Sermon'}":\n`;
      context += `"${result.text.substring(0, 1500)}"\n\n`;
    });

    if (sermonResults.length > 5) {
      context += 'If user wants more, say "Would you like me to share more of what Pastor Bob teaches on this?"\n\n';
    }
  }

  if (hasWebsite) {
    context += '=== CHURCH WEBSITE INFO (from cc-ea.org) ===\n\n';
    websiteResults.forEach((result, i) => {
      context += `[${result.page || 'Church Info'}] (${result.url}):\n`;
      context += `${result.text.substring(0, 800)}\n\n`;
    });
    context += 'CRITICAL INSTRUCTIONS FOR CHURCH INFO QUESTIONS:\n';
    context += '1. Answer ONLY what was asked — if they asked about events, list only events. If about Bible studies, list only studies.\n';
    context += '2. List specific details: names, dates, times, costs, locations.\n';
    context += '3. Do NOT dump unrelated info (e.g., don\'t list volunteer roles when asked about events).\n';
    context += '4. Do NOT say "Pastor Bob teaches", share phone numbers or email addresses, or tell the user to call or email the office.\n';
    context += '5. If the data includes registration or sign-up links in [text](url) format, INCLUDE them in your response so users can click to register.\n';
    context += '6. If asked about a specific ministry, include the direct link to that ministry\'s page if available.\n';
    context += '7. For Community Groups, always include the link to https://www.cceacommunity.org/\n';
    context += '8. For Children\'s Ministry / Adventure Kids, include the link to https://www.cceachildrens.com\n';
    context += '9. For Homeschool Community, include the link to https://www.cceahomeschool.com\n';
    context += '10. Be concise and direct.\n\n';
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
      
      const isChurch = isChurchInfoQuery(searchQuery);
      const numSermons = isMoreRequest ? 12 : (isChurch ? 3 : 6);
      const numIllustrations = isMoreRequest ? 0 : (isChurch ? 0 : 3);
      const numWebsite = isChurch ? 10 : 0;
      const websitePages = isChurch ? detectChurchTopicPages(searchQuery) : [];
      
      try {
        const searchPayload = {
          query: searchQuery,
          n_sermons: numSermons,
          n_illustrations: numIllustrations,
          n_website: numWebsite
        };
        if (websitePages.length > 0) searchPayload.website_pages = websitePages;
        const fastResponse = await axios.post(`${RERANKER_URL}/search/fast-all`, searchPayload, { timeout: 10000 });
        
        if (fastResponse.data) {
          sermonResults = (fastResponse.data.sermons || []).map(r => ({
            text: r.text,
            title: r.title || 'Sermon',
            video_id: r.video_id || '',
            start_time: r.start_time || '',
            url: r.url || '',
            timestamped_url: r.timestamped_url || r.url || '',
            relevance_score: r.rerank_score || 0,
            source: 'sermon'
          }));
          illustrationResults = (fastResponse.data.illustrations || []).map(r => ({
            text: r.text,
            title: r.title || 'Illustration',
            topics: r.topics ? r.topics.split(',') : [],
            tone: r.tone || '',
            url: r.url || '',
            timestamp: r.timestamp || '',
            source: 'illustration'
          }));
          websiteResults = (fastResponse.data.website || []).map(r => ({
            text: r.text,
            page: r.page || '',
            url: r.url || '',
            source: 'website'
          }));
          console.log(`Fast search: ${sermonResults.length} sermons, ${illustrationResults.length} illustrations, ${websiteResults.length} website (${fastResponse.data.timing_ms}ms)`);
        }
      } catch (err) {
        console.log(`Fast search error: ${err.message}, falling back to direct search`);
        try {
          sermonResults = await searchSermons(searchQuery, numSermons);
        } catch (searchError) {
          console.log('Sermon search also failed:', searchError.message);
          sermonResults = [];
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
        if (sermonResults.length > 0) {
          console.log(`Top sermon: "${(sermonResults[0].title || '').substring(0, 60)}" text: "${(sermonResults[0].text || '').substring(0, 200)}"`);
        }
        
        const lastIdx = enhancedMessages.length - 1;
        const userMsg = enhancedMessages[lastIdx];
        enhancedMessages[lastIdx] = {
          role: 'user',
          content: sermonContext + '\n\nUSER QUESTION: ' + userMsg.content
        };
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
      const videosToSend = filteredResults.slice(startIndex, startIndex + 5)
      .filter(r => {
        const url = r.timestamped_url || r.url || '';
        const vidMatch = url.match(/v=([a-zA-Z0-9_-]{11})/);
        if (!vidMatch) return false;
        const title = r.title || '';
        if (/^\d{8}-\d{2}-[A-Z]{3}/.test(title)) return false;
        if (isNonBobContent(r.text, title)) return false;
        return true;
      })
      .map(r => ({
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
    const clientRoom = req.body.roomName;
    const sessionId = `${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
    const roomName = clientRoom && clientRoom.startsWith('apb-session-') ? clientRoom : `apb-session-${sessionId}`;
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

    console.log(`Token created for room ${roomName} (auto-dispatch)`);

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

app.post('/api/clips', async (req, res) => {
  try {
    const { query } = req.body;
    if (!query) return res.status(400).json({ error: 'Query required' });

    const fastResponse = await axios.post(`${RERANKER_URL}/search/fast-all`, {
      query,
      n_sermons: 8,
      n_illustrations: 5
    }, { timeout: 15000 });

    const sermons = [];
    const illustrations = [];
    const seenVids = new Set();

    const storyMatches = detectPersonalStoryQuery(query);
    if (storyMatches.length > 0) {
      for (const storyKey of storyMatches) {
        const story = PINNED_STORY_CLIPS[storyKey];
        if (story) {
          for (const clip of story.clips.slice(0, 2)) {
            sermons.push({
              title: clip.title,
              url: clip.timestamped_url || clip.url,
              timestamp: clip.start_time || '',
              text: clip.text.substring(0, 150)
            });
            seenVids.add(`${clip.video_id}_${clip.start_time || ''}`);
          }
        }
      }
    }

    if (fastResponse.data) {
      for (const r of (fastResponse.data.sermons || [])) {
        const url = r.timestamped_url || r.url || '';
        const vidMatch = url.match(/v=([a-zA-Z0-9_-]{11})/);
        if (!vidMatch) continue;
        const vid = vidMatch[1];
        if (vid.startsWith('audio_')) continue;
        const title = r.title || '';
        if (!title || title.toLowerCase() === 'sermon' || title.toLowerCase() === 'unknown sermon') continue;
        if (/^\d{8}-\d{2}-[A-Z]{3}/.test(title)) continue;
        const tLower = title.toLowerCase();
        if (tLower.startsWith('sunday morning live') || tLower.startsWith('wednesday night live') || tLower.startsWith('sunday night live')) continue;
        if (isNonBobContent(r.text, title)) continue;
        const text = (r.text || '').substring(0, 150);
        if (text.length < 50) continue;
        const clipKey = `${vid}_${r.start_time || ''}`;
        if (seenVids.has(clipKey)) continue;
        seenVids.add(clipKey);
        sermons.push({
          title,
          url,
          timestamp: r.start_time || '',
          text
        });
        if (sermons.length >= 3) break;
      }
      for (const r of (fastResponse.data.illustrations || [])) {
        const url = r.url || '';
        const vidMatch = url.match(/v=([a-zA-Z0-9_-]+)/);
        if (vidMatch) {
          illustrations.push({
            title: r.title || 'Illustration',
            url,
            text: (r.text || '').substring(0, 150),
            tone: r.tone || '',
            timestamp: r.timestamp || ''
          });
        }
      }
    }

    console.log(`Clips API: ${sermons.length} sermon clips, ${illustrations.length} illustration clips for "${query.substring(0, 60)}"`);
    res.json({ sermon_videos: sermons, illustrations });
  } catch (error) {
    console.error('Clips API error:', error.message);
    res.json({ sermon_videos: [], illustrations: [] });
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

app.post('/api/ingest-sermons', async (req, res) => {
  try {
    const full = req.query.full === 'true';
    const url = `${RERANKER_URL}/ingest-sermons${full ? '?full=true' : ''}`;
    const response = await axios.post(url, {}, { timeout: 600000 });
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============================================
// ANALYTICS
// ============================================
const ANALYTICS_FILE = path.join(__dirname, 'analytics_data.json');

let analyticsData = { sessions: {}, events: [] };
try {
  if (fs.existsSync(ANALYTICS_FILE)) {
    analyticsData = JSON.parse(fs.readFileSync(ANALYTICS_FILE, 'utf8'));
  }
} catch (e) {
  console.log('Analytics: starting fresh');
}

function saveAnalytics() {
  try {
    const maxEvents = 50000;
    if (analyticsData.events.length > maxEvents) {
      analyticsData.events = analyticsData.events.slice(-maxEvents);
    }
    fs.writeFileSync(ANALYTICS_FILE, JSON.stringify(analyticsData), 'utf8');
  } catch (e) {}
}

setInterval(saveAnalytics, 30000);

app.post('/api/analytics/event', express.text({ type: '*/*' }), (req, res) => {
  try {
    const event = JSON.parse(req.body);
    const { sessionId } = event;
    if (!sessionId) return res.sendStatus(204);

    if (!analyticsData.sessions[sessionId]) {
      analyticsData.sessions[sessionId] = {
        id: sessionId,
        startTime: event.timestamp,
        device: event.device || 'unknown',
        referrer: event.referrer || '',
        userAgent: event.userAgent || '',
        textChats: 0,
        voiceSessions: 0,
        shareClicks: 0,
        lastSeen: event.timestamp,
        duration: 0
      };
    }

    const session = analyticsData.sessions[sessionId];
    session.lastSeen = event.timestamp;
    session.duration = event.sessionDuration || session.duration;

    if (event.event === 'text_chat') session.textChats = event.count || session.textChats + 1;
    if (event.event === 'voice_start') {
      session.voiceSessions = event.count || session.voiceSessions + 1;
      if (!analyticsData.voiceIntervals) analyticsData.voiceIntervals = [];
      analyticsData.voiceIntervals.push({ sessionId, start: event.timestamp, end: null });
    }
    if (event.event === 'voice_end') {
      if (analyticsData.voiceIntervals) {
        const open = analyticsData.voiceIntervals.find(v => v.sessionId === sessionId && !v.end);
        if (open) open.end = event.timestamp;
      }
    }
    if (event.event === 'share_click') session.shareClicks = event.count || session.shareClicks + 1;
    if (event.event === 'session_end') {
      session.textChats = event.totalTextChats || session.textChats;
      session.voiceSessions = event.totalVoiceSessions || session.voiceSessions;
      session.shareClicks = event.totalShareClicks || session.shareClicks;
      session.duration = event.sessionDuration || session.duration;
      session.ended = true;
    }

    analyticsData.events.push({
      sessionId,
      event: event.event,
      timestamp: event.timestamp,
      device: event.device
    });

    res.sendStatus(204);
  } catch (e) {
    res.sendStatus(204);
  }
});

app.post('/api/feedback', (req, res) => {
  try {
    const { message, sessionId, device, timestamp } = req.body;
    if (!message || !message.trim()) return res.status(400).json({ error: 'Empty feedback' });
    if (!analyticsData.feedback) analyticsData.feedback = [];
    analyticsData.feedback.push({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      message: message.trim().slice(0, 2000),
      sessionId: sessionId || 'unknown',
      device: device || 'unknown',
      timestamp: timestamp || Date.now(),
      read: false
    });
    saveAnalytics();
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/feedback/:id/read', (req, res) => {
  if (!analyticsData.feedback) return res.status(404).json({ error: 'No feedback' });
  const item = analyticsData.feedback.find(f => f.id === req.params.id);
  if (!item) return res.status(404).json({ error: 'Not found' });
  item.read = true;
  saveAnalytics();
  res.json({ ok: true });
});

app.delete('/api/feedback/:id', (req, res) => {
  if (!analyticsData.feedback) return res.status(404).json({ error: 'No feedback' });
  const idx = analyticsData.feedback.findIndex(f => f.id === req.params.id);
  if (idx === -1) return res.status(404).json({ error: 'Not found' });
  analyticsData.feedback.splice(idx, 1);
  saveAnalytics();
  res.json({ ok: true });
});

app.get('/api/analytics/data', (req, res) => {
  const sessions = Object.values(analyticsData.sessions);
  const now = Date.now();
  const day = 86400000;

  const today = sessions.filter(s => s.startTime > now - day);
  const week = sessions.filter(s => s.startTime > now - 7 * day);
  const month = sessions.filter(s => s.startTime > now - 30 * day);

  function summarize(list) {
    const total = list.length;
    const withText = list.filter(s => s.textChats > 0).length;
    const withVoice = list.filter(s => s.voiceSessions > 0).length;
    const withShare = list.filter(s => s.shareClicks > 0).length;
    const totalTextChats = list.reduce((a, s) => a + (s.textChats || 0), 0);
    const totalVoiceSessions = list.reduce((a, s) => a + (s.voiceSessions || 0), 0);
    const totalShareClicks = list.reduce((a, s) => a + (s.shareClicks || 0), 0);
    const durations = list.map(s => s.duration || 0).filter(d => d > 0);
    const avgDuration = durations.length ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length) : 0;
    const maxDuration = durations.length ? Math.max(...durations) : 0;
    const mobile = list.filter(s => s.device === 'mobile').length;
    const desktop = list.filter(s => s.device === 'desktop').length;
    return { total, withText, withVoice, withShare, totalTextChats, totalVoiceSessions, totalShareClicks, avgDuration, maxDuration, mobile, desktop };
  }

  const dailyCounts = {};
  sessions.forEach(s => {
    const d = new Date(s.startTime).toISOString().split('T')[0];
    if (!dailyCounts[d]) dailyCounts[d] = { sessions: 0, textChats: 0, voiceSessions: 0 };
    dailyCounts[d].sessions++;
    dailyCounts[d].textChats += s.textChats || 0;
    dailyCounts[d].voiceSessions += s.voiceSessions || 0;
  });

  const last30Days = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(now - i * day).toISOString().split('T')[0];
    last30Days.push({ date: d, ...(dailyCounts[d] || { sessions: 0, textChats: 0, voiceSessions: 0 }) });
  }

  const hourlyCounts = {};
  today.forEach(s => {
    const h = parseInt(new Date(s.startTime).toLocaleString('en-US', { hour: 'numeric', hour12: false, timeZone: 'America/Los_Angeles' }));
    if (!hourlyCounts[h]) hourlyCounts[h] = { sessions: 0, textChats: 0, voiceSessions: 0 };
    hourlyCounts[h].sessions++;
    hourlyCounts[h].textChats += (s.textChats || 0);
    hourlyCounts[h].voiceSessions += (s.voiceSessions || 0);
  });
  const hourlyData = [];
  for (let h = 0; h < 24; h++) {
    hourlyData.push({ hour: h, sessions: (hourlyCounts[h]?.sessions || 0), textChats: (hourlyCounts[h]?.textChats || 0), voiceSessions: (hourlyCounts[h]?.voiceSessions || 0) });
  }

  const recentSessions = sessions
    .sort((a, b) => b.startTime - a.startTime)
    .slice(0, 50)
    .map(s => ({
      id: s.id.slice(0, 8),
      device: s.device,
      textChats: s.textChats,
      voiceSessions: s.voiceSessions,
      shareClicks: s.shareClicks,
      duration: s.duration,
      startTime: s.startTime,
      ended: !!s.ended
    }));

  const voiceIntervals = (analyticsData.voiceIntervals || []).map(v => ({
    ...v,
    end: v.end || (analyticsData.sessions[v.sessionId]?.ended ? analyticsData.sessions[v.sessionId].lastSeen : now)
  }));
  let peakConcurrent = 0;
  let peakTime = null;
  const concurrentEvents = [];
  voiceIntervals.forEach(v => {
    concurrentEvents.push({ t: v.start, delta: 1 });
    concurrentEvents.push({ t: v.end, delta: -1 });
  });
  concurrentEvents.sort((a, b) => a.t - b.t);
  let current = 0;
  const concurrentHistory = [];
  concurrentEvents.forEach(e => {
    current += e.delta;
    if (current > peakConcurrent) {
      peakConcurrent = current;
      peakTime = e.t;
    }
    concurrentHistory.push({ t: e.t, count: current });
  });
  const activeVoiceNow = voiceIntervals.filter(v => v.start <= now && (v.end >= now || !analyticsData.sessions[v.sessionId]?.ended)).length;

  res.json({
    today: summarize(today),
    week: summarize(week),
    month: summarize(month),
    allTime: summarize(sessions),
    dailyChart: last30Days,
    hourlyChart: hourlyData,
    recentSessions,
    totalSessions: sessions.length,
    serverUptime: Math.round(process.uptime()),
    feedback: (analyticsData.feedback || []).sort((a, b) => b.timestamp - a.timestamp).slice(0, 100),
    voiceConcurrency: {
      peakConcurrent,
      peakTime,
      activeNow: activeVoiceNow,
      totalVoiceSessions: voiceIntervals.length
    }
  });
});

let ingestRunning = false;
let lastIngestResult = null;

app.post('/api/ingest/run', (req, res) => {
  if (ingestRunning) return res.json({ status: 'already_running' });
  ingestRunning = true;
  lastIngestResult = { status: 'running', startedAt: Date.now() };
  const { spawn } = require('child_process');
  const proc = spawn('python', ['auto_ingest_sermons.py', '--full-scan'], { cwd: __dirname });
  let output = '';
  proc.stdout.on('data', d => { output += d.toString(); });
  proc.stderr.on('data', d => { output += d.toString(); });
  proc.on('close', code => {
    ingestRunning = false;
    lastIngestResult = { status: code === 0 ? 'success' : 'error', code, output: output.slice(-2000), finishedAt: Date.now() };
  });
  res.json({ status: 'started' });
});

app.get('/api/ingest/status', (req, res) => {
  res.json({ running: ingestRunning, lastResult: lastIngestResult });
});

app.get('/chat.html/a', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'analytics.html'));
});

app.listen(PORT, () => {
  console.log(`\n🚀 Server running on http://localhost:${PORT}`);
  console.log(`📺 Open chat at: http://localhost:${PORT}/chat.html`);
  console.log(`📊 Analytics at: http://localhost:${PORT}/chat.html/a`);
  console.log(`✅ Health check: http://localhost:${PORT}/health\n`);
});
