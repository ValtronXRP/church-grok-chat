#!/usr/bin/env python3
"""
Full migration from local ChromaDB to Chroma Cloud
"""

import sqlite3
import chromadb

DB_PATH = './sermon_vector_db/chroma.sqlite3'
BATCH_SIZE = 100

CHROMA_API_KEY = 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd'
CHROMA_TENANT = '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b'
CHROMA_DATABASE = 'APB'

def get_documents():
    """Extract all documents with metadata from local ChromaDB SQLite"""
    conn = sqlite3.connect(DB_PATH)
    
    print("Counting records...")
    cursor = conn.execute("SELECT COUNT(*) FROM embeddings")
    total = cursor.fetchone()[0]
    print(f"Total embeddings: {total}")
    
    print("\nExtracting documents with metadata...")
    
    cursor = conn.execute("""
        SELECT 
            e.id,
            e.embedding_id,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'chroma:document') as document,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'title') as title,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'video_id') as video_id,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'url') as url,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'start_time') as start_time,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'end_time') as end_time,
            (SELECT int_value FROM embedding_metadata WHERE id = e.id AND key = 'start_ms') as start_ms,
            (SELECT int_value FROM embedding_metadata WHERE id = e.id AND key = 'end_ms') as end_ms,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'topics') as topics,
            (SELECT string_value FROM embedding_metadata WHERE id = e.id AND key = 'segment_type') as segment_type
        FROM embeddings e
    """)
    
    docs = []
    count = 0
    for row in cursor:
        (row_id, embedding_id, document, title, video_id, url, 
         start_time, end_time, start_ms, end_ms, topics, segment_type) = row
        
        if document:
            docs.append({
                'id': embedding_id,
                'document': document,
                'metadata': {
                    'title': title or 'Unknown Sermon',
                    'video_id': video_id or '',
                    'url': url or '',
                    'start_time': start_time or '',
                    'end_time': end_time or '',
                    'start_ms': start_ms or 0,
                    'end_ms': end_ms or 0,
                    'topics': topics or '',
                    'segment_type': segment_type or 'general'
                }
            })
        
        count += 1
        if count % 20000 == 0:
            print(f"  Processed {count}/{total} rows...")
    
    conn.close()
    return docs

def upload_to_cloud(docs, start_index=73000):
    """Upload documents to Chroma Cloud, starting from index"""
    print(f"\nConnecting to Chroma Cloud...")
    client = chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )
    
    try:
        collection = client.get_collection("sermon_segments")
        existing = collection.count()
        print(f"Collection exists with {existing} items")
    except:
        collection = client.create_collection(
            "sermon_segments",
            metadata={"hnsw:space": "cosine"}
        )
        print("Created new collection")
    
    docs_to_upload = docs[start_index:]
    print(f"\nUploading {len(docs_to_upload)} documents starting from index {start_index}...")
    
    uploaded = 0
    skipped = 0
    for i in range(0, len(docs_to_upload), BATCH_SIZE):
        batch = docs_to_upload[i:i+BATCH_SIZE]
        
        ids = [d['id'] for d in batch]
        documents = [d['document'] for d in batch]
        metadatas = [d['metadata'] for d in batch]
        
        try:
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            uploaded += len(batch)
            
            if uploaded % 1000 == 0:
                print(f"  Uploaded {uploaded}/{len(docs_to_upload)} ({100*uploaded//len(docs_to_upload)}%)")
        except Exception as e:
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
                skipped += len(batch)
            else:
                print(f"  Error at batch {i}: {e}")
    
    final_count = collection.count()
    print(f"\nDone! Uploaded {uploaded}, skipped {skipped}")
    print(f"Cloud collection count: {final_count}")

def main():
    print("=" * 50)
    print("ChromaDB Migration to Cloud")
    print("=" * 50)
    
    docs = get_documents()
    print(f"\nExtracted {len(docs)} documents from local DB")
    
    if docs:
        print(f"\nSample: {docs[0]['metadata']['title']} - {docs[0]['document'][:100]}...")
        upload_to_cloud(docs)

if __name__ == '__main__':
    main()
