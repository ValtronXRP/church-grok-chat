"""
Standalone Sermon API Server
Simplified version for cloud deployment
"""
import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import numpy as np
from sentence_transformers import SentenceTransformer
import sqlite3
import pickle

app = Flask(__name__)
CORS(app)

# Initialize embedding model
model = SentenceTransformer('all-MiniLM-L6-v2')

# Use environment variable for database path
DB_PATH = os.environ.get('SERMON_DB_PATH', 'sermon_vector_db/chroma.sqlite3')

def search_sermons_direct(query, n_results=3):
    """Direct SQLite search without ChromaDB"""
    try:
        # Generate query embedding
        query_embedding = model.encode([query])[0]
        
        # Connect to SQLite database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get all embeddings from database
        cursor.execute("""
            SELECT id, embedding 
            FROM embeddings 
            LIMIT 1000
        """)
        
        results = []
        for row_id, embedding_blob in cursor.fetchall():
            # Deserialize embedding
            embedding = pickle.loads(embedding_blob)
            
            # Calculate cosine similarity
            similarity = np.dot(query_embedding, embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(embedding)
            )
            
            results.append((row_id, similarity))
        
        # Sort by similarity and get top results
        results.sort(key=lambda x: x[1], reverse=True)
        top_ids = [r[0] for r in results[:n_results]]
        
        # Get metadata for top results
        final_results = []
        for doc_id in top_ids:
            cursor.execute("""
                SELECT document, metadata 
                FROM documents 
                WHERE id = ?
            """, (doc_id,))
            
            row = cursor.fetchone()
            if row:
                document, metadata = row
                metadata = json.loads(metadata) if metadata else {}
                final_results.append({
                    'text': document,
                    'title': metadata.get('title', 'Unknown'),
                    'video_id': metadata.get('video_id', ''),
                    'start_time': metadata.get('start_time', 0),
                    'url': metadata.get('url', '')
                })
        
        conn.close()
        return final_results
        
    except Exception as e:
        print(f"Search error: {e}")
        return []

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'})

@app.route('/api/sermon/search', methods=['POST'])
def search():
    data = request.json
    query = data.get('query', '')
    n_results = data.get('n_results', 3)
    
    results = search_sermons_direct(query, n_results)
    
    return jsonify({'results': results})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)