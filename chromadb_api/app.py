"""
ChromaDB API for Pastor Bob Sermon & Illustration Search
With Hybrid Search: Vector similarity + Keyword matching + Topic filtering
Supports both local ChromaDB and Chroma Cloud
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import re
import chromadb
from chromadb.config import Settings

app = Flask(__name__)
CORS(app)

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')
DB_PATH = os.environ.get('CHROMADB_PATH', '../sermon_vector_db')

client = None
sermon_collection = None
illustration_collection = None

STOPWORDS = {'what', 'does', 'pastor', 'bob', 'teach', 'about', 'how', 'why', 
             'when', 'where', 'says', 'tell', 'the', 'and', 'for', 'that', 
             'this', 'with', 'from', 'have', 'has', 'can', 'you', 'your'}

TOPIC_SYNONYMS = {
    'sovereignty': ['sovereign', 'control', 'authority', 'god in charge', 'god controls'],
    'faith': ['believe', 'trust', 'believing', 'faithful', 'belief'],
    'forgiveness': ['forgive', 'forgiven', 'forgiving', 'pardon', 'mercy'],
    'prayer': ['pray', 'praying', 'prayed', 'intercession'],
    'love': ['loving', 'loved', 'agape', 'charity', 'compassion'],
    'healing': ['heal', 'healed', 'restoration', 'wholeness'],
    'salvation': ['saved', 'saving', 'redemption', 'born again'],
    'sin': ['sins', 'sinful', 'transgression', 'repent'],
    'grace': ['gracious', 'unmerited', 'favor'],
    'worship': ['praise', 'glorify', 'honor', 'adoration'],
    'marriage': ['married', 'husband', 'wife', 'spouse'],
    'anxiety': ['anxious', 'worry', 'worried', 'fear', 'stress'],
    'peace': ['peaceful', 'calm', 'rest', 'tranquility'],
    'joy': ['joyful', 'rejoice', 'gladness', 'happiness'],
    'hope': ['hoping', 'hopeful', 'expectation'],
    'obedience': ['obey', 'obedient', 'submit', 'follow'],
    'suffering': ['suffer', 'pain', 'trials', 'hardship'],
    'temptation': ['tempted', 'tempting', 'resist'],
    'humility': ['humble', 'meek', 'meekness'],
    'anger': ['angry', 'wrath', 'rage', 'resentment'],
}

def init_db():
    global client, sermon_collection, illustration_collection
    
    if CHROMA_API_KEY and CHROMA_TENANT:
        print(f"Connecting to Chroma Cloud (tenant: {CHROMA_TENANT}, db: {CHROMA_DATABASE})")
        client = chromadb.CloudClient(
            api_key=CHROMA_API_KEY,
            tenant=CHROMA_TENANT,
            database=CHROMA_DATABASE
        )
    else:
        print(f"Using local ChromaDB at: {DB_PATH}")
        client = chromadb.PersistentClient(
            path=DB_PATH,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
    
    try:
        sermon_collection = client.get_collection(name="sermon_segments")
        print(f"Loaded sermon collection: {sermon_collection.count()} segments")
    except Exception as e:
        print(f"sermon_segments collection not found: {e}")
        sermon_collection = None
    
    try:
        illustration_collection = client.get_collection(name="illustrations")
        print(f"Loaded illustration collection: {illustration_collection.count()} items")
    except Exception as e:
        print(f"illustrations collection not found: {e}")
        illustration_collection = None

def extract_keywords(query):
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if len(w) > 2 and w not in STOPWORDS]
    
    expanded = set(keywords)
    for word in keywords:
        if word in TOPIC_SYNONYMS:
            expanded.update(TOPIC_SYNONYMS[word])
        for topic, synonyms in TOPIC_SYNONYMS.items():
            if word in synonyms:
                expanded.add(topic)
                expanded.update(synonyms[:2])
    
    return list(expanded)

def keyword_match_score(text, keywords):
    text_lower = text.lower()
    matches = sum(1 for kw in keywords if kw in text_lower)
    
    for kw in keywords:
        count = text_lower.count(kw)
        if count >= 2:
            matches += 0.5
    
    return matches / len(keywords) if keywords else 0

def topic_match_score(topics_str, keywords):
    if not topics_str:
        return 0
    topics = [t.strip().lower() for t in topics_str.split(',')]
    
    for kw in keywords:
        if kw in topics:
            return 1.0
        for topic in topics:
            if kw in topic or topic in kw:
                return 0.8
    return 0

@app.route('/api/sermon/search', methods=['POST'])
def search_sermons():
    try:
        if not sermon_collection:
            return jsonify({'error': 'Sermon collection not initialized', 'results': []}), 200
        
        data = request.json
        query = data.get('query', '')
        n_results = data.get('n_results', 5)
        
        if not query:
            return jsonify({'error': 'Query required'}), 400
        
        keywords = extract_keywords(query)
        print(f"Query: {query}, Keywords: {keywords}")
        
        results = sermon_collection.query(
            query_texts=[query],
            n_results=n_results * 5
        )
        
        scored_results = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                meta = results['metadatas'][0][i]
                vector_score = 1 - results['distances'][0][i] if 'distances' in results and results['distances'] else 0.5
                
                kw_score = keyword_match_score(doc, keywords)
                topic_score = topic_match_score(meta.get('topics', ''), keywords)
                
                combined_score = (vector_score * 0.3) + (kw_score * 0.5) + (topic_score * 0.2)
                
                if kw_score < 0.2 and topic_score == 0:
                    continue
                
                start_ms = meta.get('start_ms', 0)
                if isinstance(start_ms, str):
                    start_ms = int(start_ms) if start_ms.isdigit() else 0
                seconds = start_ms // 1000
                
                scored_results.append({
                    'text': doc,
                    'title': meta.get('title', 'Sermon'),
                    'video_id': meta.get('video_id', ''),
                    'url': meta.get('url', ''),
                    'timestamped_url': f"{meta.get('url', '')}&t={seconds}s",
                    'start_time': meta.get('start_time', ''),
                    'end_time': meta.get('end_time', ''),
                    'topics': meta.get('topics', '').split(',') if meta.get('topics') else [],
                    'relevance_score': combined_score,
                    'keyword_matches': kw_score,
                    'topic_match': topic_score
                })
        
        scored_results.sort(key=lambda x: x['relevance_score'], reverse=True)
        final_results = scored_results[:n_results]
        
        return jsonify({
            'query': query,
            'keywords': keywords,
            'count': len(final_results),
            'results': final_results
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/illustration/search', methods=['POST'])
def search_illustrations():
    try:
        if not illustration_collection:
            return jsonify({'error': 'Illustration collection not initialized', 'results': []}), 200
        
        data = request.json
        query = data.get('query', '')
        n_results = data.get('n_results', 3)
        
        if not query:
            return jsonify({'error': 'Query required'}), 400
        
        keywords = extract_keywords(query)
        
        results = illustration_collection.query(
            query_texts=[query],
            n_results=n_results * 4
        )
        
        scored_results = []
        seen = set()
        
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                meta = results['metadatas'][0][i]
                
                key = f"{meta.get('illustration', '')}-{meta.get('timestamp', '')}"
                if key in seen:
                    continue
                seen.add(key)
                
                vector_score = 1 - results['distances'][0][i] if 'distances' in results and results['distances'] else 0.5
                kw_score = keyword_match_score(doc, keywords)
                
                topics = []
                topics_raw = meta.get('topics', '[]')
                if isinstance(topics_raw, str):
                    try:
                        topics = json.loads(topics_raw)
                    except:
                        topics = [t.strip() for t in topics_raw.split(',')]
                else:
                    topics = topics_raw or []
                
                topic_score = 0
                for kw in keywords:
                    for topic in topics:
                        if kw in topic.lower():
                            topic_score = 1.0
                            break
                
                combined_score = (vector_score * 0.3) + (kw_score * 0.4) + (topic_score * 0.3)
                
                if kw_score < 0.15 and topic_score == 0:
                    continue
                
                scored_results.append({
                    'illustration': meta.get('illustration', ''),
                    'text': doc,
                    'video_url': meta.get('video_url', ''),
                    'timestamp': meta.get('timestamp', ''),
                    'topics': topics,
                    'tone': meta.get('tone', ''),
                    'relevance_score': combined_score
                })
        
        scored_results.sort(key=lambda x: x['relevance_score'], reverse=True)
        final_results = scored_results[:n_results]
        
        return jsonify({
            'query': query,
            'keywords': keywords,
            'count': len(final_results),
            'results': final_results
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    try:
        sermon_count = sermon_collection.count() if sermon_collection else 0
        illustration_count = illustration_collection.count() if illustration_collection else 0
        mode = 'cloud' if CHROMA_API_KEY else 'local'
        
        return jsonify({
            'status': 'healthy',
            'mode': mode,
            'sermons': sermon_count,
            'illustrations': illustration_count
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def stats():
    try:
        mode = 'cloud' if CHROMA_API_KEY else 'local'
        return jsonify({
            'mode': mode,
            'sermon_segments': sermon_collection.count() if sermon_collection else 0,
            'illustrations': illustration_collection.count() if illustration_collection else 0
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5001))
    print(f"ChromaDB API starting on port {port}")
    print("Using hybrid search: vector + keyword + topic matching")
    app.run(host='0.0.0.0', port=port)
