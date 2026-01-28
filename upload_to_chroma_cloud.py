#!/usr/bin/env python3
"""
Upload sermon data to Chroma Cloud
Extracts from local running API, uploads to cloud
"""

import os
import requests
import chromadb
from dotenv import load_dotenv

load_dotenv()

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY')
LOCAL_API = 'http://localhost:5001'
BATCH_SIZE = 100

def upload():
    print("Connecting to Chroma Cloud...")
    cloud_client = chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        database="APB",
        tenant="bobkopeny"
    )
    
    try:
        cloud_collection = cloud_client.get_collection("sermon_segments")
        existing = cloud_collection.count()
        print(f"Cloud collection exists with {existing} segments")
    except Exception as e:
        print(f"Creating new collection... ({e})")
        cloud_collection = cloud_client.create_collection(
            name="sermon_segments",
            metadata={"hnsw:space": "cosine"}
        )
        print("Created sermon_segments collection")
    
    stats = requests.get(f"{LOCAL_API}/api/stats").json()
    total = stats.get('sermon_segments', 0)
    print(f"Local API has {total} sermon segments")
    
    test_queries = [
        "sovereignty of God",
        "faith and trust",
        "forgiveness",
        "prayer",
        "marriage"
    ]
    
    print(f"\nUploading representative samples via search queries...")
    uploaded_ids = set()
    
    for query in test_queries:
        print(f"\n  Searching for: {query}")
        response = requests.post(
            f"{LOCAL_API}/api/sermon/search",
            json={"query": query, "n_results": 50}
        )
        
        results = response.json().get('results', [])
        print(f"    Found {len(results)} results")
        
        for r in results:
            doc_id = f"{r['video_id']}_{r['start_time']}"
            if doc_id not in uploaded_ids:
                try:
                    cloud_collection.add(
                        ids=[doc_id],
                        documents=[r['text']],
                        metadatas=[{
                            'title': r.get('title', ''),
                            'video_id': r.get('video_id', ''),
                            'url': r.get('url', ''),
                            'start_time': r.get('start_time', ''),
                            'end_time': r.get('end_time', ''),
                            'topics': ','.join(r.get('topics', []))
                        }]
                    )
                    uploaded_ids.add(doc_id)
                except Exception as e:
                    pass
        
        print(f"    Total uploaded so far: {len(uploaded_ids)}")
    
    print(f"\nUploaded {len(uploaded_ids)} unique segments to Chroma Cloud")
    print(f"Cloud collection count: {cloud_collection.count()}")

if __name__ == '__main__':
    upload()
