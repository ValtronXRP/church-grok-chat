#!/usr/bin/env python3
"""
Extract sermon data from ChromaDB to a simple JSON format
This will create a lightweight, deployable sermon database
"""
import json
import sqlite3
import pickle

# Connect to ChromaDB SQLite database
db_path = "sermon_vector_db/chroma.sqlite3"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("Extracting sermon data from ChromaDB...")

# Get all documents with their metadata
cursor.execute("""
    SELECT 
        embeddings_queue.seq_id,
        embeddings_queue.operation,
        embeddings_queue.metadata,
        embeddings_queue.document
    FROM embeddings_queue
    WHERE operation = 1
    ORDER BY seq_id
    LIMIT 5000
""")

sermons = []
for row in cursor.fetchall():
    seq_id, operation, metadata_json, document = row
    
    if document and metadata_json:
        try:
            metadata = json.loads(metadata_json)
            sermon_entry = {
                "text": document,
                "title": metadata.get("title", "Unknown"),
                "video_id": metadata.get("video_id", ""),
                "start_time": metadata.get("start_time", 0),
                "url": metadata.get("url", ""),
                "sermon_number": metadata.get("sermon_number", 0)
            }
            sermons.append(sermon_entry)
        except:
            continue

print(f"Found {len(sermons)} sermon segments")

# Save to JSON
output_file = "sermon_data.json"
with open(output_file, 'w') as f:
    json.dump(sermons, f, separators=(',', ':'))

file_size_mb = len(json.dumps(sermons)) / 1024 / 1024
print(f"Saved to {output_file} ({file_size_mb:.2f} MB)")

conn.close()