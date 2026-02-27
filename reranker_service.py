#!/usr/bin/env python3
"""
Hybrid search service with cross-encoder reranking.
Provides a local API that server.js calls for high-quality semantic search.

Uses local mpnet model for 768-dim query embeddings to match our collections.
Cross-encoder reranker for quality scoring.

Endpoints:
  POST /search - Unified search across sermons, illustrations, website
  GET /health - Health check
"""

import os, sys, json, time, hashlib, gc, threading, re
from datetime import datetime, timedelta
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

import logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

from flask import Flask, request, jsonify
import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder

app = Flask(__name__)

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

EMBEDDING_MODEL = 'sentence-transformers/all-mpnet-base-v2'
RERANKER_MODEL = 'cross-encoder/ms-marco-MiniLM-L-6-v2'

embedder = None
reranker = None
chroma_client = None
sermon_collection = None
illustration_collection = None
website_collection = None

PINNED_STORIES = {
    "becky_story": {
        "keywords": ["becky", "wife", "how did bob meet", "how did pastor bob meet", "how they met",
                      "bob and becky", "bob meet becky", "married", "engagement", "how bob met",
                      "love story", "bob's wife", "pastor bob's wife", "when did bob get married",
                      "bob get married", "who is bob married to", "who did bob marry", "becky kopeny"],
        "results": [
            {
                "text": "Pastor Bob shares the full story of how he met Becky - from meeting her briefly at church, to God putting her name in his mind at the intersection of Chapman and Kramer while driving to seminary, to the Lord revealing she had gotten engaged the night before, to God telling him to propose three weeks after their first date.",
                "title": "How To Press On (03/26/2017)",
                "url": "https://www.youtube.com/watch?v=sGIJP13TxPQ",
                "timestamped_url": "https://www.youtube.com/watch?v=sGIJP13TxPQ&t=2382s",
                "start_time": "39:42",
                "video_id": "sGIJP13TxPQ",
                "source": "sermon",
                "rerank_score": 1.0
            },
            {
                "text": "Pastor Bob shares that when he first met Becky she was engaged to be married. They were just friends and he encouraged her spiritually. He shares about caring for someone and not knowing how they feel.",
                "title": "Who Cares? (12/10/2017)",
                "url": "https://www.youtube.com/watch?v=BRd6nCCTLKI",
                "timestamped_url": "https://www.youtube.com/watch?v=BRd6nCCTLKI&t=2014s",
                "start_time": "33:34",
                "video_id": "BRd6nCCTLKI",
                "source": "sermon",
                "rerank_score": 0.95
            }
        ]
    },
    "testimony": {
        "keywords": ["testimony", "how was bob saved", "when was bob saved", "how did bob get saved",
                      "bob's testimony", "pastor bob saved", "bob come to christ", "bob receive christ",
                      "when did bob become a christian", "how did bob become", "bob's salvation",
                      "bob get saved", "pastor bob's testimony", "bob become a believer",
                      "how bob got saved", "when bob got saved", "bob's faith journey",
                      "how did pastor bob come to know", "jeff maples", "gene schaeffer",
                      "jr high camp", "junior high camp", "8th grade"],
        "results": [
            {
                "text": "Pastor Bob shares his testimony of how he received Christ. Two men - Jeff Maples and Gene Schaeffer, who were in their 30s - shared Christ with him at a Jr. High church camp when he was 13. His friend Fred invited him. They shared for about five minutes and asked if he would receive Christ. He said yes. He thanks God for the unbroken chain of people who shared the gospel down to him.",
                "title": "Be Faithful - 2 Timothy 1",
                "url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "timestamped_url": "https://www.youtube.com/watch?v=72R6uNs2ka4",
                "start_time": "",
                "video_id": "72R6uNs2ka4",
                "source": "sermon",
                "rerank_score": 1.0
            }
        ]
    }
}

def detect_pinned_stories(query):
    q = query.lower().replace("'", "'")
    results = []
    seen_vids = set()
    for story_key, story in PINNED_STORIES.items():
        for kw in story["keywords"]:
            if kw in q:
                for r in story["results"]:
                    if r["video_id"] not in seen_vids:
                        results.append(r)
                        seen_vids.add(r["video_id"])
                break
    return results, seen_vids


def init_models():
    global embedder, reranker, chroma_client, sermon_collection, illustration_collection, website_collection

    t0 = time.time()
    logger.info("Loading embedding model (768-dim for matching our collections)...")
    sys.stdout.flush()
    embedder = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
    logger.info(f"Embedder loaded: dim={embedder.get_sentence_embedding_dimension()} ({time.time()-t0:.1f}s)")
    sys.stdout.flush()
    gc.collect()

    t1 = time.time()
    logger.info("Loading cross-encoder reranker...")
    sys.stdout.flush()
    reranker = CrossEncoder(RERANKER_MODEL, device='cpu')
    logger.info(f"Reranker loaded ({time.time()-t1:.1f}s)")
    sys.stdout.flush()
    gc.collect()

    logger.info("Connecting to Chroma Cloud...")
    logger.info(f"  CHROMA_TENANT: {CHROMA_TENANT[:10]}..." if CHROMA_TENANT else "  CHROMA_TENANT: NOT SET")
    logger.info(f"  CHROMA_DATABASE: {CHROMA_DATABASE}" if CHROMA_DATABASE else "  CHROMA_DATABASE: NOT SET")
    logger.info(f"  CHROMA_API_KEY: {'set' if CHROMA_API_KEY else 'NOT SET'}")
    sys.stdout.flush()
    
    try:
        chroma_client = chromadb.CloudClient(
            api_key=CHROMA_API_KEY,
            tenant=CHROMA_TENANT,
            database=CHROMA_DATABASE
        )
        logger.info("Chroma Cloud connected")
    except Exception as e:
        logger.error(f"ERROR connecting to Chroma: {e}")
        chroma_client = None
    sys.stdout.flush()

    if chroma_client is None:
        logger.error("No Chroma client - skipping collection loading")
        return

    try:
        sermon_collection = chroma_client.get_collection('sermon_segments_v2')
        sc = sermon_collection.count()
        logger.info(f"sermon_segments_v2: {sc} segments")
    except Exception as e:
        logger.warning(f"sermon_segments_v2 not found, trying sermon_segments: {e}")
        try:
            sermon_collection = chroma_client.get_collection('sermon_segments')
            sc = sermon_collection.count()
            logger.info(f"sermon_segments (fallback): {sc} segments")
        except:
            logger.warning("No sermon collection available")
            sermon_collection = None

    try:
        illustration_collection = chroma_client.get_collection('illustrations_v5')
        ic = illustration_collection.count()
        logger.info(f"illustrations_v5: {ic} illustrations")
    except:
        try:
            illustration_collection = chroma_client.get_collection('illustrations_v4')
            ic = illustration_collection.count()
            logger.info(f"illustrations_v4 (fallback): {ic} illustrations")
        except:
            logger.warning("No illustration collection available")
            illustration_collection = None

    try:
        website_collection = chroma_client.get_collection('church_website')
        wc = website_collection.count()
        logger.info(f"church_website: {wc} pages")
    except:
        logger.warning("No website collection available")
        website_collection = None

    logger.info(f"All models and collections ready (total {time.time()-t0:.1f}s)")
    sys.stdout.flush()


def search_and_rerank(query, collection, n_candidates=20, n_results=6, source_type="sermon"):
    if collection is None:
        return []

    query_emb = embedder.encode([query], normalize_embeddings=True).tolist()

    results = collection.query(
        query_embeddings=query_emb,
        n_results=n_candidates,
        include=['metadatas', 'documents', 'distances']
    )

    if not results['ids'] or not results['ids'][0]:
        return []

    candidates = []
    seen_texts = set()
    for i in range(len(results['ids'][0])):
        text = results['documents'][0][i] or ''
        text_key = text[:200]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        meta = results['metadatas'][0][i] or {}
        dist = results['distances'][0][i] if results['distances'] else 1.0

        candidates.append({
            'text': text,
            'title': meta.get('title', ''),
            'video_id': meta.get('video_id', ''),
            'start_time': meta.get('start_time', ''),
            'url': meta.get('url', ''),
            'timestamped_url': meta.get('timestamped_url', meta.get('url', '')),
            'vector_dist': dist,
            'source': source_type,
            'page': meta.get('page', ''),
            'topics': meta.get('topics', ''),
            'summary': meta.get('summary', ''),
            'emotional_tone': meta.get('emotional_tone', ''),
            'youtube_url': meta.get('youtube_url', ''),
        })

    if not candidates:
        return []

    pairs = [[query, c['text']] for c in candidates]
    try:
        scores = reranker.predict(pairs)
        import math
        for i, c in enumerate(candidates):
            score = float(scores[i])
            c['rerank_score'] = (1.0 - c['vector_dist']) if math.isnan(score) else score
    except Exception as e:
        logger.error(f"Reranker error: {e}, using vector distance")
        for c in candidates:
            c['rerank_score'] = 1.0 - c['vector_dist']

    candidates.sort(key=lambda x: x['rerank_score'], reverse=True)

    return candidates[:n_results]


@app.route('/health', methods=['GET'])
def health():
    if not models_initialized:
        return jsonify({'status': 'starting', 'message': 'Models loading on first request'})
    return jsonify({
        'status': 'ok',
        'mode': 'local-mpnet-768dim',
        'embedder': EMBEDDING_MODEL,
        'reranker': RERANKER_MODEL,
        'sermons': sermon_collection.count() if sermon_collection else 0,
        'illustrations': illustration_collection.count() if illustration_collection else 0,
        'website': website_collection.count() if website_collection else 0
    })

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'alive'})


@app.route('/search', methods=['POST'])
def search():
    data = request.json
    query = data.get('query', '')
    search_type = data.get('type', 'all')
    n_results = data.get('n_results', 6)
    n_candidates = data.get('n_candidates', 20)

    print(f"[SEARCH] query='{query[:50]}...' type={search_type} n={n_results}")

    if not query:
        return jsonify({'error': 'No query provided'}), 400

    start = time.time()
    all_results = []

    pinned, pinned_vids = detect_pinned_stories(query)
    if pinned:
        all_results.extend(pinned)

    if search_type in ('all', 'sermons') and sermon_collection:
        sermon_results = search_and_rerank(query, sermon_collection, n_candidates, n_results, 'sermon')
        sermon_results = [r for r in sermon_results if r.get('video_id') not in pinned_vids]
        title_lower = query.lower()
        sermon_results = [r for r in sermon_results if not is_worship_content(r.get('text', ''), r.get('title', ''))]
        all_results.extend(sermon_results)

    if search_type in ('all', 'illustrations') and illustration_collection:
        ill_results = search_and_rerank(query, illustration_collection, 10, 3, 'illustration')
        all_results.extend(ill_results)

    if search_type in ('all', 'website') and website_collection:
        web_results = search_and_rerank(query, website_collection, 10, 3, 'website')
        all_results.extend(web_results)

    elapsed = time.time() - start

    response = {
        'query': query,
        'results': all_results,
        'timing_ms': round(elapsed * 1000),
        'pinned_count': len(pinned)
    }
    return jsonify(response)


@app.route('/search/fast', methods=['POST'])
def search_fast():
    data = request.json
    query = data.get('query', '')
    n_results = data.get('n_results', 5)

    if not query:
        return jsonify({'error': 'No query provided'}), 400

    start = time.time()
    all_results = []

    pinned, pinned_vids = detect_pinned_stories(query)
    if pinned:
        all_results.extend(pinned)

    if sermon_collection:
        query_emb = embedder.encode([query], normalize_embeddings=True).tolist()
        results = sermon_collection.query(
            query_embeddings=query_emb,
            n_results=n_results + 5,
            include=['metadatas', 'documents', 'distances']
        )
        if results['ids'] and results['ids'][0]:
            seen = set()
            for i in range(len(results['ids'][0])):
                text = results['documents'][0][i] or ''
                key = text[:200]
                if key in seen:
                    continue
                seen.add(key)
                meta = results['metadatas'][0][i] or {}
                dist = results['distances'][0][i] if results['distances'] else 1.0
                title = meta.get('title', '')
                if is_worship_content(text, title):
                    continue
                vid = meta.get('video_id', '')
                if vid in pinned_vids:
                    continue
                all_results.append({
                    'text': text,
                    'title': title,
                    'video_id': vid,
                    'start_time': meta.get('start_time', ''),
                    'url': meta.get('url', ''),
                    'timestamped_url': meta.get('timestamped_url', meta.get('url', '')),
                    'rerank_score': 1.0 - dist,
                    'source': 'sermon',
                })
                if len(all_results) >= n_results + len(pinned):
                    break

    elapsed = time.time() - start
    return jsonify({
        'query': query,
        'results': all_results[:n_results + len(pinned)],
        'timing_ms': round(elapsed * 1000),
        'pinned_count': len(pinned)
    })


@app.route('/search/fast-all', methods=['POST'])
def search_fast_all():
    data = request.json
    query = data.get('query', '')
    n_sermons = data.get('n_sermons', 6)
    n_illustrations = data.get('n_illustrations', 3)
    n_website = data.get('n_website', 0)
    website_pages = data.get('website_pages', [])

    if not query:
        return jsonify({'error': 'No query provided'}), 400

    start = time.time()
    query_emb = embedder.encode([query], normalize_embeddings=True).tolist()

    sermon_results = []
    ill_results = []
    website_results = []

    pinned, pinned_vids = detect_pinned_stories(query)
    if pinned:
        sermon_results.extend(pinned)

    if sermon_collection:
        results = sermon_collection.query(
            query_embeddings=query_emb,
            n_results=n_sermons + 5,
            include=['metadatas', 'documents', 'distances']
        )
        if results['ids'] and results['ids'][0]:
            seen = set()
            for i in range(len(results['ids'][0])):
                text = results['documents'][0][i] or ''
                key = text[:200]
                if key in seen:
                    continue
                seen.add(key)
                meta = results['metadatas'][0][i] or {}
                dist = results['distances'][0][i] if results['distances'] else 1.0
                title = meta.get('title', '')
                if is_worship_content(text, title):
                    continue
                vid = meta.get('video_id', '')
                if vid in pinned_vids:
                    continue
                sermon_results.append({
                    'text': text,
                    'title': title,
                    'video_id': vid,
                    'start_time': meta.get('start_time', ''),
                    'url': meta.get('url', ''),
                    'timestamped_url': meta.get('timestamped_url', meta.get('url', '')),
                    'rerank_score': 1.0 - dist,
                    'source': 'sermon',
                })
                if len(sermon_results) >= n_sermons + len(pinned):
                    break

    if illustration_collection:
        results = illustration_collection.query(
            query_embeddings=query_emb,
            n_results=n_illustrations + 3,
            include=['metadatas', 'documents', 'distances']
        )
        if results['ids'] and results['ids'][0]:
            seen = set()
            for i in range(len(results['ids'][0])):
                text = results['documents'][0][i] or ''
                key = text[:200]
                if key in seen:
                    continue
                seen.add(key)
                meta = results['metadatas'][0][i] or {}
                title = meta.get('title', meta.get('summary', 'Illustration'))
                if len(text) < 30:
                    continue
                ill_results.append({
                    'text': text,
                    'title': title,
                    'topics': meta.get('topics', ''),
                    'tone': meta.get('emotional_tone', ''),
                    'url': meta.get('youtube_url', meta.get('url', '')),
                    'timestamp': meta.get('start_time', ''),
                    'source': 'illustration',
                })
                if len(ill_results) >= n_illustrations:
                    break

    if n_website > 0 and website_collection:
        query_kwargs = {
            'query_embeddings': query_emb,
            'n_results': n_website + 3,
            'include': ['metadatas', 'documents', 'distances'],
        }
        if website_pages:
            if len(website_pages) == 1:
                query_kwargs['where'] = {'path': website_pages[0]}
            else:
                query_kwargs['where'] = {'path': {'$in': website_pages}}
        results = website_collection.query(**query_kwargs)
        if results['ids'] and results['ids'][0]:
            seen = set()
            for i in range(len(results['ids'][0])):
                text = results['documents'][0][i] or ''
                key = text[:200]
                if key in seen:
                    continue
                seen.add(key)
                meta = results['metadatas'][0][i] or {}
                if len(text) < 20:
                    continue
                website_results.append({
                    'text': text,
                    'page': meta.get('page', ''),
                    'url': meta.get('url', ''),
                    'source': 'website',
                })
                if len(website_results) >= n_website:
                    break

    elapsed = time.time() - start
    return jsonify({
        'query': query,
        'sermons': sermon_results[:n_sermons + len(pinned)],
        'illustrations': ill_results[:n_illustrations],
        'website': website_results[:n_website] if n_website > 0 else [],
        'timing_ms': round(elapsed * 1000),
        'pinned_count': len(pinned)
    })


@app.route('/search/sermons', methods=['POST'])
def search_sermons():
    data = request.json
    query = data.get('query', '')
    n_results = data.get('n_results', 6)

    if not query:
        return jsonify({'error': 'No query'}), 400

    pinned, pinned_vids = detect_pinned_stories(query)
    results = search_and_rerank(query, sermon_collection, 20, n_results, 'sermon') if sermon_collection else []
    results = [r for r in results if r.get('video_id') not in pinned_vids]
    results = [r for r in results if not is_worship_content(r.get('text', ''), r.get('title', ''))]
    all_results = pinned + results

    return jsonify({'query': query, 'results': all_results})


@app.route('/search/illustrations', methods=['POST'])
def search_illustrations():
    data = request.json
    query = data.get('query', '')
    n_results = data.get('n_results', 3)

    results = search_and_rerank(query, illustration_collection, 10, n_results, 'illustration') if illustration_collection else []
    return jsonify({'query': query, 'results': results})


@app.route('/search/website', methods=['POST'])
def search_website():
    data = request.json
    query = data.get('query', '')
    n_results = data.get('n_results', 3)

    results = search_and_rerank(query, website_collection, 10, n_results, 'website') if website_collection else []
    return jsonify({'query': query, 'results': results})


def is_worship_content(text, title):
    text_lower = (text or '').lower()
    title_lower = (title or '').lower()
    if title_lower in ['unknown sermon', 'unknown', '']:
        return True
    worship_indicators = ['worship song', 'hymn', 'music video', 'singing', 'choir']
    if any(w in title_lower for w in worship_indicators):
        return True
    import re
    worship_count = len(re.findall(r'\b(la la|glory glory|praise him praise him|hallelujah hallelujah)\b', text_lower))
    if worship_count > 2:
        return True
    if len(text_lower) < 100:
        return True
    return False


models_initialized = False

def ensure_models():
    global models_initialized
    if not models_initialized:
        init_models()
        models_initialized = True


MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

DATE_PATTERNS = [
    re.compile(r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)[,\s]+(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(\d{1,2})(?:\s*,\s*(\d{4}))?', re.IGNORECASE),
    re.compile(r'(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(\d{1,2})(?:\s*(?:st|nd|rd|th))?(?:\s*,\s*(\d{4}))?', re.IGNORECASE),
    re.compile(r'(\d{1,2})/(\d{1,2})/(\d{2,4})'),
]

def _extract_date_from_text(text):
    for pattern in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            try:
                if groups[0].lower() in MONTH_NAMES:
                    month = MONTH_NAMES[groups[0].lower()]
                    day = int(groups[1])
                    year = int(groups[2]) if groups[2] else datetime.now().year
                else:
                    month = int(groups[0])
                    day = int(groups[1])
                    year = int(groups[2]) if len(groups) > 2 and groups[2] else datetime.now().year
                    if year < 100:
                        year += 2000
                return datetime(year, month, day)
            except (ValueError, TypeError):
                continue
    return None

def _filter_past_events(lines):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    filtered = []
    skip_block = False
    for line in lines:
        detected = _extract_date_from_text(line)
        if detected and detected < today - timedelta(days=7):
            skip_block = True
            continue
        if detected and detected >= today - timedelta(days=7):
            skip_block = False
        if skip_block and line and not any(c.isdigit() for c in line[:3]):
            continue
        skip_block = False
        filtered.append(line)
    return filtered


def _refresh_website_db():
    global website_collection
    try:
        import requests as http_requests
        from bs4 import BeautifulSoup

        PAGES = [
            ('/', 'Home'),
            ('/about-us', 'About Us'),
            ('/about-us/contact-us', 'Contact Us'),
            ('/about-us/statement-of-faith', 'Statement of Faith'),
            ('/give', 'Give'),
            ('/ministries-2', 'Ministries'),
            ('/missions', 'Missions'),
            ('/new-here', 'New Here'),
            ('/registrations', 'Registrations'),
            ('/resources', 'Resources'),
            ('/resources/christian-understanding-of-disability', 'Christian Understanding of Disability'),
            ('/resources/crisis-pregnancy', 'Crisis Pregnancy'),
            ('/resources/home-bible-studies', 'Home Bible Studies'),
            ('/resources/pastor-bob-s-resources', "Pastor Bob's Resources"),
            ('/resources/wedding-application2', 'Wedding Application'),
            ('/service-times-and-location', 'Service Times and Location'),
            ('/services', 'Services'),
            ('/services/live', 'Live Stream'),
            ('/volunteer', 'Volunteer'),
            ('/highlights', 'Highlights'),
        ]

        session = http_requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        })

        all_chunks = []
        for i, (path, name) in enumerate(PAGES):
            if i > 0:
                time.sleep(2)
            try:
                r = session.get(f'https://cc-ea.org{path}', timeout=15, allow_redirects=True)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, 'html.parser')
                for tag in soup(['script', 'style', 'link', 'meta', 'noscript']):
                    tag.decompose()
                text = soup.get_text(separator='\n', strip=True)
                all_lines = [l.strip() for l in text.split('\n') if l.strip()]
                nav_end = 0
                for idx, l in enumerate(all_lines):
                    if l == 'Volunteer':
                        nav_end = idx + 1
                        break
                footer_start = len(all_lines)
                for idx in range(len(all_lines) - 1):
                    if all_lines[idx] == 'Follow Us' and all_lines[idx + 1] == 'Subscribe':
                        footer_start = idx
                        break
                body = all_lines[nav_end:footer_start]
                seen = set()
                lines = []
                for l in body:
                    if len(l) > 2 and l not in seen and not l.endswith('- ccea'):
                        ll = l.lower()
                        if any(x in ll for x in ['office hour', 'phone', 'fax', 'email', '@cc-ea', '@ccea', '714-', '(714)', 'call the office', 'call us']):
                            continue
                        seen.add(l)
                        lines.append(l)
                lines = _filter_past_events(lines)
                content = '\n'.join(lines)
                if len(content) <= 800:
                    all_chunks.append({'page': name, 'url': f'https://cc-ea.org{path}', 'path': path, 'content': content, 'chunk_index': 0})
                else:
                    current, current_len, ci = [], 0, 0
                    for para in lines:
                        if current_len + len(para) > 800 and current:
                            all_chunks.append({'page': name, 'url': f'https://cc-ea.org{path}', 'path': path, 'content': '\n'.join(current), 'chunk_index': ci})
                            current, current_len = [para], len(para)
                            ci += 1
                        else:
                            current.append(para)
                            current_len += len(para)
                    if current:
                        all_chunks.append({'page': name, 'url': f'https://cc-ea.org{path}', 'path': path, 'content': '\n'.join(current), 'chunk_index': ci})
                logger.info(f"  Scraped: {name} ({len(content)} chars)")
            except Exception as e:
                logger.warning(f"  Failed to scrape {name}: {e}")

        if not all_chunks:
            logger.warning("No website content scraped, skipping DB update")
            return False

        ids, documents, metadatas = [], [], []
        for chunk in all_chunks:
            chunk_id = hashlib.md5(f"{chunk['path']}_{chunk['chunk_index']}".encode()).hexdigest()
            ids.append(chunk_id)
            documents.append(chunk['content'])
            metadatas.append({'page': chunk['page'], 'url': chunk['url'], 'path': chunk['path'], 'chunk_index': chunk['chunk_index'], 'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')})

        embs = embedder.encode(documents, normalize_embeddings=True).tolist()

        coll = chroma_client.get_or_create_collection(name='church_website', metadata={'description': 'cc-ea.org pages'})
        for i in range(0, len(ids), 50):
            batch_end = min(i + 50, len(ids))
            coll.upsert(ids=ids[i:batch_end], documents=documents[i:batch_end], metadatas=metadatas[i:batch_end], embeddings=embs[i:batch_end])
        website_collection = coll
        logger.info(f"Website DB refreshed: {coll.count()} chunks from {len(PAGES)} pages")
        return True
    except Exception as e:
        logger.error(f"Website refresh failed: {e}")
        return False


def _hourly_website_refresh():
    logger.info("Initial website refresh starting (immediate)...")
    try:
        _refresh_website_db()
    except Exception as e:
        logger.error(f"Initial website refresh error: {e}")
    while True:
        time.sleep(3600)
        logger.info("Hourly website refresh starting...")
        try:
            _refresh_website_db()
        except Exception as e:
            logger.error(f"Hourly refresh error: {e}")


@app.route('/refresh-website', methods=['POST'])
def refresh_website():
    success = _refresh_website_db()
    if success:
        count = website_collection.count() if website_collection else 0
        return jsonify({'status': 'ok', 'documents': count})
    return jsonify({'status': 'error'}), 500


@app.before_request
def before_request():
    from flask import request as req
    if req.endpoint not in ('health', 'ping'):
        ensure_models()

logger.info("Pre-loading models at startup...")
sys.stdout.flush()
try:
    ensure_models()
    logger.info("Models ready, accepting requests.")
except Exception as e:
    logger.error(f"Failed to load models at startup: {e}")
sys.stdout.flush()

refresh_thread = threading.Thread(target=_hourly_website_refresh, daemon=True)
refresh_thread.start()
logger.info("Website refresh thread started (immediate refresh + hourly)")

if __name__ == '__main__':
    port = int(os.environ.get('RERANKER_PORT', os.environ.get('PORT', 5050)))
    print(f"\nReranker service starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
