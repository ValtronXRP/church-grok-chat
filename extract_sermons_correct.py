#!/usr/bin/env python3
"""
Extract sermon data from ChromaDB to a deployable JSON format
"""
import json
import sqlite3

# Connect to ChromaDB SQLite database
db_path = "sermon_vector_db/chroma.sqlite3"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("Extracting sermon data from ChromaDB...")

# Get documents with metadata
cursor.execute("""
    SELECT 
        efs.string_value as document,
        eq.id,
        eq.metadata
    FROM embedding_fulltext_search efs
    JOIN embeddings_queue eq ON eq.id = efs.id
    WHERE eq.metadata IS NOT NULL
    LIMIT 10000
""")

sermons = []
for row in cursor.fetchall():
    document, doc_id, metadata_json = row
    
    if document and metadata_json:
        try:
            metadata = json.loads(metadata_json)
            sermon_entry = {
                "id": doc_id,
                "text": document,
                "title": metadata.get("title", "Unknown"),
                "video_id": metadata.get("video_id", ""),
                "start_time": metadata.get("start_time", 0),
                "url": metadata.get("url", ""),
                "sermon_number": metadata.get("sermon_number", 0)
            }
            
            # Only add if it has meaningful content
            if len(document) > 50 and metadata.get("video_id"):
                sermons.append(sermon_entry)
        except Exception as e:
            continue

print(f"Found {len(sermons)} valid sermon segments")

# Sort by sermon number and start time
sermons.sort(key=lambda x: (x.get("sermon_number", 0), x.get("start_time", 0)))

# Save to JSON (compact format to reduce size)
output_file = "sermon_data.json"
with open(output_file, 'w') as f:
    json.dump(sermons, f, separators=(',', ':'))

file_size_mb = len(json.dumps(sermons, separators=(',', ':'))) / 1024 / 1024
print(f"Saved to {output_file} ({file_size_mb:.2f} MB)")

conn.close()