#!/usr/bin/env python3
"""
Export sermons using the existing sermon indexer
"""
import sys
import json
from sermon_indexer import SermonIndexer

# Initialize the indexer with existing database
indexer = SermonIndexer(db_path="./sermon_vector_db")

print("Searching for all sermons...")

# Common topics to search for
topics = [
    "faith", "love", "forgiveness", "prayer", "hope", 
    "salvation", "grace", "healing", "wisdom", "peace",
    "joy", "patience", "kindness", "purpose", "worship"
]

all_sermons = []
seen_ids = set()

for topic in topics:
    print(f"Searching for {topic}...")
    results = indexer.search(topic, n_results=100)
    
    for result in results:
        # Create a unique ID
        segment_id = f"{result['video_id']}_{result['start_time']}"
        
        if segment_id not in seen_ids:
            seen_ids.add(segment_id)
            all_sermons.append({
                "id": segment_id,
                "text": result["text"],
                "title": result["title"],
                "video_id": result["video_id"],
                "start_time": result["start_time"],
                "url": result["url"],
                "topics": [topic]  # Simple topic assignment
            })

print(f"Found {len(all_sermons)} unique sermon segments")

# Save to JSON
with open("sermons_export.json", "w") as f:
    json.dump(all_sermons, f, separators=(',', ':'))

file_size_mb = len(json.dumps(all_sermons, separators=(',', ':'))) / 1024 / 1024
print(f"Exported to sermons_export.json ({file_size_mb:.2f} MB)")