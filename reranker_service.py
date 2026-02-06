#!/usr/bin/env python3
"""
Hybrid search service with cross-encoder reranking.
Provides a local API that server.js calls for high-quality semantic search.

Endpoints:
  POST /search - Unified search across sermons, illustrations, website
  GET /health - Health check
"""

import os, json, time, hashlib
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from flask import Flask, request, jsonify
import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder
import numpy as np

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

    print("Loading embedding model...")
    embedder = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
    print(f"Embedder loaded: dim={embedder.get_sentence_embedding_dimension()}")

    print("Loading cross-encoder reranker...")
    reranker = CrossEncoder(RERANKER_MODEL, device='cpu')
    print("Reranker loaded")

    print("Connecting to Chroma Cloud...")
    print(f"  CHROMA_TENANT: {CHROMA_TENANT[:10]}..." if CHROMA_TENANT else "  CHROMA_TENANT: NOT SET")
    print(f"  CHROMA_DATABASE: {CHROMA_DATABASE}" if CHROMA_DATABASE else "  CHROMA_DATABASE: NOT SET")
    print(f"  CHROMA_API_KEY: {'set' if CHROMA_API_KEY else 'NOT SET'}")
    
    try:
        chroma_client = chromadb.CloudClient(
            api_key=CHROMA_API_KEY,
            tenant=CHROMA_TENANT,
            database=CHROMA_DATABASE
        )
    except Exception as e:
        print(f"ERROR connecting to Chroma: {e}")
        chroma_client = None

    try:
        sermon_collection = chroma_client.get_collection('sermon_segments_v2')
        sc = sermon_collection.count()
        print(f"sermon_segments_v2: {sc} segments")
    except Exception as e:
        print(f"sermon_segments_v2 not found, trying sermon_segments: {e}")
        try:
            sermon_collection = chroma_client.get_collection('sermon_segments')
            sc = sermon_collection.count()
            print(f"sermon_segments (fallback): {sc} segments")
        except:
            print("No sermon collection available")
            sermon_collection = None

    try:
        illustration_collection = chroma_client.get_collection('illustrations_v5')
        ic = illustration_collection.count()
        print(f"illustrations_v5: {ic} illustrations")
    except:
        try:
            illustration_collection = chroma_client.get_collection('illustrations_v4')
            ic = illustration_collection.count()
            print(f"illustrations_v4 (fallback): {ic} illustrations")
        except:
            print("No illustration collection available")
            illustration_collection = None

    try:
        website_collection = chroma_client.get_collection('church_website')
        wc = website_collection.count()
        print(f"church_website: {wc} pages")
    except:
        print("No website collection available")
        website_collection = None

    print("All models and collections ready")


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
    scores = reranker.predict(pairs)

    import math
    for i, c in enumerate(candidates):
        score = float(scores[i])
        c['rerank_score'] = 0.0 if math.isnan(score) else score

    candidates.sort(key=lambda x: x['rerank_score'], reverse=True)

    return candidates[:n_results]


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'embedder': EMBEDDING_MODEL,
        'reranker': RERANKER_MODEL,
        'sermons': sermon_collection.count() if sermon_collection else 0,
        'illustrations': illustration_collection.count() if illustration_collection else 0,
        'website': website_collection.count() if website_collection else 0
    })


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


init_models()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', os.environ.get('RERANKER_PORT', 5050)))
    print(f"\nReranker service starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
