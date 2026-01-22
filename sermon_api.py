"""
Sermon Search API - Provides REST endpoints for searching Pastor Bob Kopeny's sermon database
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
from typing import Dict, List, Optional
import json

# Add the sermon indexer to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sermon_indexer import SermonIndexer

app = Flask(__name__)
CORS(app)  # Enable CORS for web client access

# Initialize the sermon indexer
indexer = SermonIndexer(db_path="./sermon_vector_db")

@app.route('/api/sermon/search', methods=['POST'])
def search_sermons():
    """
    Search for sermon segments matching a query
    
    Expected JSON body:
    {
        "query": "What does Pastor Bob say about faith?",
        "n_results": 5,
        "filters": {
            "topics": "faith",
            "segment_type": "teaching",
            "scripture": "Hebrews"
        }
    }
    """
    try:
        data = request.json
        query = data.get('query', '')
        n_results = data.get('n_results', 5)
        filters = data.get('filters', {})
        
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        # Enhanced query processing for better semantic matching
        # Extract key concepts from the query
        key_concepts = extract_key_concepts(query)
        
        # Search with enhanced query
        enhanced_query = f"{query} {' '.join(key_concepts)}"
        
        # Get more results initially to filter
        initial_results = indexer.search(enhanced_query, n_results=n_results * 3, filter_dict=filters)
        
        # Filter for relevance based on semantic similarity
        relevant_results = []
        for result in initial_results:
            # Check if result is actually relevant to the query concept
            relevance_score = calculate_relevance(query, result['text'], key_concepts)
            
            # Include results with moderate to high relevance
            if relevance_score >= 0.3:  # Lower threshold to catch more results
                result['relevance_score'] = relevance_score
                relevant_results.append(result)
        
        # Sort by relevance and distance combined
        relevant_results.sort(key=lambda x: (x['relevance_score'] * 0.6 + (1 - x['distance']) * 0.4), reverse=True)
        
        # Take only the requested number of results
        final_results = relevant_results[:n_results]
        
        # Format response
        response = {
            'query': query,
            'count': len(final_results),
            'results': final_results
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def extract_key_concepts(query):
    """Extract key theological concepts from query"""
    concepts = []
    
    # Fix common typos first
    typo_fixes = {
        'forgivness': 'forgiveness',
        'forgivenes': 'forgiveness',
        'anxeity': 'anxiety',
        'marrage': 'marriage',
        'pateince': 'patience',
        'belive': 'believe',
        'pryer': 'prayer',
        'fath': 'faith'
    }
    
    query_lower = query.lower()
    for typo, correct in typo_fixes.items():
        if typo in query_lower:
            query_lower = query_lower.replace(typo, correct)
    
    # Concept mappings for better search
    concept_map = {
        'forgiveness': ['forgive', 'forgiven', 'pardon', 'mercy', 'grace', 'reconciliation'],
        'faith': ['believe', 'trust', 'belief', 'confidence', 'faithful'],
        'prayer': ['pray', 'praying', 'intercession', 'petition', 'supplication'],
        'love': ['loving', 'charity', 'compassion', 'affection', 'agape'],
        'sin': ['transgression', 'iniquity', 'wrong', 'evil', 'repent'],
        'salvation': ['saved', 'redemption', 'deliverance', 'rescue', 'eternal life'],
        'healing': ['heal', 'healed', 'restoration', 'wholeness', 'recovery'],
        'hope': ['expectation', 'confidence', 'assurance', 'promise'],
        'grace': ['mercy', 'favor', 'blessing', 'kindness', 'unmerited'],
        'worship': ['praise', 'glorify', 'honor', 'adoration', 'reverence'],
        'dishonesty': ['lying', 'lies', 'deceit', 'dishonest', 'truth', 'honest', 'integrity'],
        'honesty': ['truth', 'truthful', 'honest', 'integrity', 'genuine', 'authentic']
    }
    
    # query_lower already fixed above
    for concept, related_terms in concept_map.items():
        if concept in query_lower or any(term in query_lower for term in related_terms):
            concepts.extend([concept] + related_terms[:2])  # Add concept and top related terms
    
    return list(set(concepts))  # Remove duplicates

def calculate_relevance(query, text, key_concepts):
    """Calculate how relevant a text segment is to the query"""
    import re
    from difflib import SequenceMatcher
    
    query_lower = query.lower()
    text_lower = text.lower()
    
    # Handle common typos by checking similarity
    def fix_typos(word):
        """Fix common typos in query words"""
        corrections = {
            'forgivness': 'forgiveness',
            'forgivenes': 'forgiveness',
            'forgivness': 'forgiveness',
            'anxeity': 'anxiety',
            'marrage': 'marriage',
            'pateince': 'patience',
            'humilty': 'humility',
            'salvtion': 'salvation',
            'worshp': 'worship',
            'pryer': 'prayer',
            'prayr': 'prayer',
            'fath': 'faith',
            'belive': 'believe',
            'beleve': 'believe',
            'tust': 'trust',
            'lov': 'love',
            'gace': 'grace',
            'mercey': 'mercy',
            'repentence': 'repentance',
            'baptizm': 'baptism',
            'comunion': 'communion',
            'purpse': 'purpose',
            'temtation': 'temptation'
        }
        return corrections.get(word, word)
    
    # Fix typos in query
    query_words = query_lower.split()
    fixed_query = ' '.join([fix_typos(word) for word in query_words])
    if fixed_query != query_lower:
        query_lower = fixed_query
        print(f"Corrected query: {fixed_query}")
    
    # Start with lower base relevance for better coverage
    relevance_score = 0.0
    
    # Extract the main topic word from query - COMPREHENSIVE LIST
    topic_keywords = {
        # Core theological concepts
        'forgiveness': ['forgive', 'forgiven', 'forgiving', 'unforgiveness', 'pardoning', 'pardon', 'reconcile'],
        'faith': ['faith', 'believe', 'believing', 'trust', 'trusting', 'faithful', 'doubt', 'doubting'],
        'prayer': ['pray', 'praying', 'prayed', 'prayers', 'intercession', 'petition', 'asking god'],
        'love': ['love', 'loves', 'loving', 'loved', 'agape', 'charity', 'compassion', 'kindness'],
        'healing': ['heal', 'healing', 'healed', 'heals', 'wholeness', 'restoration', 'restore', 'recovery'],
        'salvation': ['salvation', 'saved', 'saving', 'savior', 'redemption', 'born again', 'eternal life', 'gospel'],
        'sin': ['sin', 'sins', 'sinful', 'sinning', 'transgression', 'iniquity', 'wrong', 'evil', 'temptation'],
        'hope': ['hope', 'hoping', 'hopeful', 'expectation', 'promise', 'future', 'confidence'],
        'grace': ['grace', 'gracious', 'mercy', 'merciful', 'undeserved', 'favor', 'blessing'],
        'worship': ['worship', 'worshipping', 'praise', 'praising', 'glorify', 'honor', 'adoration'],
        
        # Character and behavior
        'honesty': ['honest', 'honesty', 'dishonest', 'dishonesty', 'lying', 'lie', 'lies', 'truth', 'truthful', 'deceit', 'deceive', 'integrity'],
        'humility': ['humble', 'humility', 'meek', 'meekness', 'pride', 'proud', 'arrogant', 'boasting'],
        'patience': ['patient', 'patience', 'wait', 'waiting', 'endurance', 'persevere', 'longsuffering'],
        'anger': ['anger', 'angry', 'wrath', 'rage', 'resentment', 'bitterness', 'forgive', 'temper'],
        'courage': ['courage', 'brave', 'bravery', 'fear not', 'bold', 'boldness', 'strength'],
        'wisdom': ['wisdom', 'wise', 'understanding', 'knowledge', 'discernment', 'foolish'],
        
        # Life situations
        'marriage': ['marriage', 'married', 'husband', 'wife', 'spouse', 'wedding', 'divorce', 'relationship'],
        'parenting': ['parent', 'parents', 'children', 'kids', 'father', 'mother', 'family', 'raising'],
        'work': ['work', 'job', 'career', 'workplace', 'boss', 'employee', 'calling', 'vocation'],
        'money': ['money', 'finances', 'tithe', 'tithing', 'giving', 'stewardship', 'wealth', 'debt', 'provision'],
        'suffering': ['suffer', 'suffering', 'pain', 'trial', 'trials', 'tribulation', 'hardship', 'difficulty'],
        'death': ['death', 'dying', 'eternal life', 'heaven', 'hell', 'eternity', 'grief', 'loss'],
        
        # Emotions
        'fear': ['fear', 'afraid', 'anxiety', 'anxious', 'worry', 'worried', 'stress', 'panic'],
        'joy': ['joy', 'joyful', 'rejoice', 'rejoicing', 'happiness', 'glad', 'celebration', 'cheerful'],
        'peace': ['peace', 'peaceful', 'calm', 'rest', 'tranquility', 'serenity', 'stillness'],
        'depression': ['depression', 'depressed', 'sad', 'sadness', 'despair', 'hopeless', 'discouraged'],
        'loneliness': ['lonely', 'loneliness', 'alone', 'isolated', 'solitude', 'abandoned'],
        
        # Spiritual practices
        'bible': ['bible', 'scripture', 'word of god', 'verse', 'biblical', 'testament'],
        'church': ['church', 'congregation', 'fellowship', 'community', 'body of christ', 'assembly'],
        'baptism': ['baptism', 'baptize', 'baptized', 'immersion', 'water baptism'],
        'communion': ['communion', 'lord\'s supper', 'bread', 'wine', 'remembrance'],
        'fasting': ['fast', 'fasting', 'abstain', 'abstinence', 'self-denial'],
        'service': ['serve', 'service', 'serving', 'ministry', 'help', 'helping', 'volunteer'],
        
        # Additional common topics
        'purpose': ['purpose', 'meaning', 'why', 'reason', 'calling', 'destiny', 'plan'],
        'temptation': ['temptation', 'tempted', 'tempting', 'resist', 'struggle', 'weakness'],
        'obedience': ['obey', 'obedience', 'obedient', 'submit', 'submission', 'follow', 'commandments'],
        'repentance': ['repent', 'repentance', 'turn away', 'confession', 'confess', 'sorry'],
        'judgment': ['judge', 'judgment', 'judging', 'condemn', 'criticism', 'justice'],
        'miracle': ['miracle', 'miraculous', 'supernatural', 'healing', 'wonder', 'sign'],
        'spirit': ['spirit', 'holy spirit', 'spiritual', 'spirituality', 'ghost'],
        'jesus': ['jesus', 'christ', 'messiah', 'lord', 'savior', 'son of god'],
        'god': ['god', 'lord', 'father', 'almighty', 'creator', 'divine']
    }
    
    # Find which topic is being asked about
    main_topic = None
    topic_words = []
    for topic, words in topic_keywords.items():
        if any(word in query_lower for word in words):
            main_topic = topic
            topic_words = words
            break
    
    if main_topic:
        # STRICT: Require at least 2 mentions of the topic or related words
        topic_count = sum(1 for word in topic_words if word in text_lower)
        if topic_count < 2:
            return 0.0  # Not relevant if topic barely mentioned
        
        # Check for substantive discussion (not just passing mention)
        # Look for phrases that indicate teaching about the topic
        teaching_indicators = [
            f"{main_topic} is",
            f"{main_topic} means",
            f"about {main_topic}",
            f"concerning {main_topic}",
            f"regarding {main_topic}",
            f"when you {main_topic}",
            f"to {main_topic}",
            f"of {main_topic}",
        ]
        
        has_substantive_discussion = any(indicator in text_lower for indicator in teaching_indicators)
        if not has_substantive_discussion:
            # Check for action words related to the topic
            if main_topic == 'forgiveness':
                has_substantive_discussion = any(phrase in text_lower for phrase in 
                    ['must forgive', 'need to forgive', 'choose to forgive', 'forgive them',
                     'forgive others', 'forgiven by', 'forgiveness of', 'ask for forgiveness'])
            elif main_topic == 'faith':
                has_substantive_discussion = any(phrase in text_lower for phrase in 
                    ['have faith', 'walk by faith', 'faith in', 'by faith', 'through faith',
                     'your faith', 'our faith', 'step of faith'])
            elif main_topic == 'prayer':
                has_substantive_discussion = any(phrase in text_lower for phrase in 
                    ['when you pray', 'in prayer', 'through prayer', 'power of prayer',
                     'pray for', 'pray to', 'prayer life', 'answered prayer'])
            elif main_topic == 'honesty':
                has_substantive_discussion = any(phrase in text_lower for phrase in 
                    ['tell the truth', 'be honest', 'white lie', 'dishonest', 'lying to',
                     'speak truth', 'telling lies', 'deceitful', 'deceive', 'truth-teller'])
        
        if has_substantive_discussion:
            relevance_score = 0.7 + (topic_count / 10.0) * 0.3  # More mentions = higher score
        else:
            return 0.1  # Very low score for passing mentions
    else:
        # No clear topic - use general word matching but be more lenient
        query_words = set(re.findall(r'\b\w{3,}\b', query_lower))  # Words 3+ chars
        text_words = set(re.findall(r'\b\w+\b', text_lower))
        
        # Remove common words
        stop_words = {'the', 'and', 'for', 'what', 'does', 'bob', 'pastor', 'kopeny', 'teach', 'about', 'how', 'why', 'when', 'where', 'says', 'tell'}
        query_words = query_words - stop_words
        
        if len(query_words) > 0:
            common_words = query_words.intersection(text_words)
            overlap = len(common_words) / len(query_words)
            if overlap >= 0.5:  # At least 50% of important words match
                relevance_score = 0.5 + (overlap * 0.3)  # Score 0.5-0.8
            elif overlap >= 0.25:  # At least 25% match
                relevance_score = 0.3 + (overlap * 0.2)  # Score 0.3-0.4
            else:
                return 0.0
    
    return min(relevance_score, 1.0)  # Cap at 1.0

@app.route('/api/sermon/get_segment', methods=['GET'])
def get_segment():
    """
    Get a specific sermon segment by video ID and timestamp
    
    Query params:
    - video_id: YouTube video ID
    - start_ms: Start time in milliseconds
    """
    try:
        video_id = request.args.get('video_id')
        start_ms = request.args.get('start_ms', type=int)
        
        if not video_id or start_ms is None:
            return jsonify({'error': 'video_id and start_ms are required'}), 400
        
        # Search for exact segment
        where_clause = {
            'video_id': video_id,
            'start_ms': start_ms
        }
        
        results = indexer.collection.get(
            where=where_clause,
            limit=1
        )
        
        if results and results['documents']:
            metadata = results['metadatas'][0]
            seconds = metadata['start_ms'] // 1000
            timestamped_url = f"{metadata['url']}&t={seconds}s"
            
            response = {
                'text': results['documents'][0],
                'metadata': metadata,
                'timestamped_url': timestamped_url
            }
            return jsonify(response), 200
        else:
            return jsonify({'error': 'Segment not found'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sermon/topics', methods=['GET'])
def get_topics():
    """Get all available topics in the database"""
    try:
        # Get unique topics from the collection
        all_metadata = indexer.collection.get(limit=10000)['metadatas']
        
        topics_set = set()
        for metadata in all_metadata:
            if metadata.get('topics'):
                topics = metadata['topics'].split(',')
                topics_set.update(t.strip() for t in topics if t.strip())
        
        return jsonify({
            'topics': sorted(list(topics_set))
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sermon/stats', methods=['GET'])
def get_stats():
    """Get statistics about the sermon database"""
    try:
        count = indexer.collection.count()
        
        # Get sample metadata to determine unique sermons
        all_metadata = indexer.collection.get(limit=10000)['metadatas']
        
        unique_videos = set()
        segment_types = {}
        total_topics = set()
        
        for metadata in all_metadata:
            unique_videos.add(metadata.get('video_id', ''))
            
            seg_type = metadata.get('segment_type', 'unknown')
            segment_types[seg_type] = segment_types.get(seg_type, 0) + 1
            
            if metadata.get('topics'):
                topics = metadata['topics'].split(',')
                total_topics.update(t.strip() for t in topics if t.strip())
        
        stats = {
            'total_segments': count,
            'total_sermons': len(unique_videos),
            'segment_types': segment_types,
            'total_topics': len(total_topics)
        }
        
        return jsonify(stats), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sermon/format_response', methods=['POST'])
def format_sermon_response():
    """
    Format search results into a natural language response with links
    
    Expected JSON body:
    {
        "query": "What does Pastor Bob say about faith?",
        "results": [...],  # Results from search
        "format": "text" or "html"
    }
    """
    try:
        data = request.json
        query = data.get('query', '')
        results = data.get('results', [])
        format_type = data.get('format', 'text')
        
        if not results:
            response = "I couldn't find any specific sermons addressing that topic. You might want to try rephrasing your question."
        else:
            if format_type == 'html':
                response = f"<p>Based on Pastor Bob Kopeny's sermons, here's what he teaches about your question:</p>\n\n"
                
                for i, result in enumerate(results[:3], 1):  # Limit to top 3
                    response += f"""
<div class="sermon-result">
    <h4>{i}. From "{result['title']}" ({result['start_time']})</h4>
    <p>{result['text'][:200]}...</p>
    <a href="{result['timestamped_url']}" target="_blank" class="sermon-link">
        📹 Watch this segment ({result['start_time']} - {result['end_time']})
    </a>
    <p class="sermon-meta">Topics: {', '.join(result['topics'])}</p>
</div>
"""
                
                if len(results) > 3:
                    response += f"<p><em>Found {len(results) - 3} more related segments.</em></p>"
                    
            else:  # text format
                response = "Based on Pastor Bob Kopeny's sermons, here's what he teaches:\n\n"
                
                for i, result in enumerate(results[:3], 1):
                    response += f"{i}. From \"{result['title']}\" ({result['start_time']}):\n"
                    response += f"   {result['text'][:150]}...\n"
                    response += f"   📹 Watch: {result['timestamped_url']}\n\n"
                
                if len(results) > 3:
                    response += f"\n(Found {len(results) - 3} more related segments)"
        
        return jsonify({
            'formatted_response': response,
            'result_count': len(results)
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sermon/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        count = indexer.collection.count()
        return jsonify({
            'status': 'healthy',
            'segments_indexed': count
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

if __name__ == '__main__':
    print("=" * 50)
    print("Sermon Search API Server")
    print("=" * 50)
    print(f"Database segments: {indexer.collection.count()}")
    print("Starting server on http://localhost:5001")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5001, debug=True)