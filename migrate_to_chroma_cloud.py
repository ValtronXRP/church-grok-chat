#!/usr/bin/env python3
"""
Migrate local ChromaDB data to Chroma Cloud
Step 1: Export from local DB
Step 2: Import to Cloud
"""

import os
import json
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

load_dotenv()

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY')
LOCAL_DB_PATH = './sermon_vector_db'
EXPORT_FILE = './sermon_export.json'
BATCH_SIZE = 100

def export_local():
    """Export data from local ChromaDB to JSON file"""
    print("Connecting to local ChromaDB...")
    
    import chromadb as chroma_local
    from chromadb.config import Settings as LocalSettings
    
    os.environ.pop('CHROMA_API_KEY', None)
    os.environ.pop('CHROMA_TENANT', None)
    os.environ.pop('CHROMA_DATABASE', None)
    
    local_client = chroma_local.PersistentClient(
        path=LOCAL_DB_PATH,
        settings=LocalSettings(anonymized_telemetry=False)
    )
    
    local_collection = local_client.get_collection("sermon_segments")
    total_count = local_collection.count()
    print(f"Found {total_count} segments in local DB")
    
    print(f"\nExporting to {EXPORT_FILE}...")
    
    all_data = []
    offset = 0
    
    while offset < total_count:
        data = local_collection.get(
            limit=BATCH_SIZE,
            offset=offset,
            include=['documents', 'metadatas', 'embeddings']
        )
        
        if not data['ids']:
            break
        
        for i in range(len(data['ids'])):
            all_data.append({
                'id': data['ids'][i],
                'document': data['documents'][i],
                'metadata': data['metadatas'][i],
                'embedding': data['embeddings'][i] if data.get('embeddings') else None
            })
        
        offset += BATCH_SIZE
        
        if offset % 5000 == 0:
            print(f"  Exported {offset}/{total_count} ({100*offset//total_count}%)")
    
    with open(EXPORT_FILE, 'w') as f:
        json.dump(all_data, f)
    
    print(f"\nExported {len(all_data)} segments to {EXPORT_FILE}")
    return len(all_data)

def import_to_cloud():
    """Import from JSON file to Chroma Cloud"""
    print("\nLoading export file...")
    with open(EXPORT_FILE, 'r') as f:
        all_data = json.load(f)
    
    print(f"Loaded {len(all_data)} segments")
    
    print("\nConnecting to Chroma Cloud...")
    cloud_client = chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        database="APB",
        tenant="bobkopeny"
    )
    
    try:
        cloud_collection = cloud_client.get_collection("sermon_segments")
        existing = cloud_collection.count()
        print(f"Cloud collection exists with {existing} segments")
        if existing > 0:
            print("Collection not empty. Delete and recreate? (y/n)")
            return
    except Exception as e:
        print(f"Creating new collection... ({e})")
        cloud_collection = cloud_client.create_collection(
            name="sermon_segments",
            metadata={"hnsw:space": "cosine"}
        )
    
    print(f"\nUploading {len(all_data)} segments in batches of {BATCH_SIZE}...")
    
    for i in range(0, len(all_data), BATCH_SIZE):
        batch = all_data[i:i+BATCH_SIZE]
        
        ids = [item['id'] for item in batch]
        documents = [item['document'] for item in batch]
        metadatas = [item['metadata'] for item in batch]
        embeddings = [item['embedding'] for item in batch] if batch[0].get('embedding') else None
        
        if embeddings:
            cloud_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings
            )
        else:
            cloud_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
        
        if (i + BATCH_SIZE) % 5000 == 0:
            print(f"  Uploaded {i + BATCH_SIZE}/{len(all_data)} ({100*(i+BATCH_SIZE)//len(all_data)}%)")
    
    print(f"\nMigration complete!")
    print(f"Cloud collection count: {cloud_collection.count()}")

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'import':
        import_to_cloud()
    elif len(sys.argv) > 1 and sys.argv[1] == 'export':
        export_local()
    else:
        print("Usage:")
        print("  python migrate_to_chroma_cloud.py export  # Export local DB to JSON")
        print("  python migrate_to_chroma_cloud.py import  # Import JSON to Cloud")
