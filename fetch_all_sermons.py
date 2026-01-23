#!/usr/bin/env python3
"""
Fetch all sermon data from the running API and save to JSON
"""
import requests
import json

# Topics to search for
topics = [
    "faith", "love", "forgiveness", "prayer", "hope", 
    "salvation", "grace", "healing", "wisdom", "peace",
    "joy", "patience", "kindness", "purpose", "worship",
    "sin", "repentance", "mercy", "truth", "spirit",
    "jesus", "christ", "god", "holy", "bible",
    "church", "community", "service", "giving", "blessing"
]

all_sermons = {}  # Use dict to avoid duplicates

print("Fetching sermon data from API...")

for topic in topics:
    print(f"Fetching {topic}...")
    try:
        response = requests.post(
            "http://localhost:5001/api/sermon/search",
            json={"query": topic, "n_results": 50}
        )
        data = response.json()
        
        for result in data.get("results", []):
            # Create unique key
            key = f"{result['video_id']}_{result['start_time']}"
            
            # Store sermon data
            if key not in all_sermons:
                all_sermons[key] = {
                    "text": result["text"],
                    "title": result["title"],
                    "video_id": result["video_id"],
                    "start_time": result["start_time"],
                    "url": result["url"],
                    "topics": [topic]
                }
            else:
                # Add topic if not already there
                if topic not in all_sermons[key]["topics"]:
                    all_sermons[key]["topics"].append(topic)
    except Exception as e:
        print(f"Error fetching {topic}: {e}")

# Convert to list
sermon_list = list(all_sermons.values())
print(f"\nTotal unique sermon segments: {len(sermon_list)}")

# Save to JSON
with open("sermons_static.json", "w") as f:
    json.dump(sermon_list, f, separators=(',', ':'))

file_size_mb = len(json.dumps(sermon_list, separators=(',', ':'))) / 1024 / 1024
print(f"Saved to sermons_static.json ({file_size_mb:.2f} MB)")