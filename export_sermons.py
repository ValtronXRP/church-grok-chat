#!/usr/bin/env python3
"""
Export sermons from ChromaDB to JSON for Node.js integration
"""
import json
import chromadb
from chromadb.config import Settings

# Initialize ChromaDB client
client = chromadb.PersistentClient(
    path="sermon_vector_db",
    settings=Settings(anonymized_telemetry=False)
)

# Get the collection
collection = client.get_collection("sermons")

# Get all documents
print("Fetching all sermons from database...")
results = collection.get(
    include=["metadatas", "documents"]
)

# Prepare export data
sermons = []
for i in range(len(results['ids'])):
    sermon = {
        'id': results['ids'][i],
        'text': results['documents'][i],
        'metadata': results['metadatas'][i]
    }
    sermons.append(sermon)

print(f"Found {len(sermons)} sermon segments")

# Export to JSON
output_file = "sermons_data.json"
with open(output_file, 'w') as f:
    json.dump(sermons, f, indent=2)

print(f"Exported to {output_file}")
print(f"File size: {len(json.dumps(sermons)) / 1024 / 1024:.2f} MB")