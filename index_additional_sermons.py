#!/usr/bin/env python3
"""
Index additional sermon data from Jsons3 sermons 4 and SERMONS_ZIP_05 into ChromaDB
"""

import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sermon_indexer import SermonIndexer

def main():
    indexer = SermonIndexer(db_path="./sermon_vector_db")
    
    print(f"Current collection size: {indexer.collection.count()} segments")
    print("="*60)
    
    # Index Jsons3 sermons 4
    sermons_4_dir = "/Users/valorkopeny/Desktop/Jsons3 sermons 4"
    if os.path.exists(sermons_4_dir):
        print(f"\nIndexing from: {sermons_4_dir}")
        transcript_files = list(Path(sermons_4_dir).glob("*.json3"))
        print(f"Found {len(transcript_files)} transcript files")
        
        for i, transcript_file in enumerate(transcript_files, 1):
            print(f"\n[{i}/{len(transcript_files)}] ", end="")
            try:
                indexer.index_sermon(str(transcript_file))
            except Exception as e:
                print(f"  Error: {e}")
    else:
        print(f"Directory not found: {sermons_4_dir}")
    
    print("\n" + "="*60)
    print(f"Collection size after Jsons3 sermons 4: {indexer.collection.count()} segments")
    print("="*60)
    
    # Index SERMONS_ZIP_05 batch files (pre-processed JSON)
    sermons_05_dir = "/Users/valorkopeny/Desktop/SERMONS_ZIP_05"
    if os.path.exists(sermons_05_dir):
        print(f"\nIndexing batch files from: {sermons_05_dir}")
        import json
        
        batch_files = list(Path(sermons_05_dir).glob("SERMONS_BATCH_*.json"))
        print(f"Found {len(batch_files)} batch files")
        
        for batch_file in batch_files:
            print(f"\nProcessing: {batch_file.name}")
            try:
                with open(batch_file, 'r') as f:
                    sermons = json.load(f)
                
                print(f"  Contains {len(sermons)} sermons")
                
                # These are metadata files, we need to check if they have associated json3 files
                # or if they contain segment data directly
                if sermons and isinstance(sermons, list):
                    sample = sermons[0]
                    print(f"  Sample keys: {list(sample.keys())[:5]}")
                    
                    # If they have 'url' they're metadata - find corresponding json3 files
                    if 'url' in sample:
                        print(f"  These are metadata entries, looking for corresponding transcripts...")
                        # Extract video IDs and check if we have transcripts
                        import re
                        for sermon in sermons:
                            url = sermon.get('url', '')
                            match = re.search(r'v=([^&]+)', url)
                            if match:
                                video_id = match.group(1)
                                # Check multiple possible locations for transcript
                                possible_paths = [
                                    f"/Users/valorkopeny/Desktop/Json3 sermons 1-3/{video_id}.en.json3",
                                    f"/Users/valorkopeny/Desktop/Jsons3 sermons 4/{video_id}.en.json3",
                                ]
                                for path in possible_paths:
                                    if os.path.exists(path):
                                        # Already indexed or will be indexed from transcript dirs
                                        break
            except Exception as e:
                print(f"  Error: {e}")
    else:
        print(f"Directory not found: {sermons_05_dir}")
    
    print("\n" + "="*60)
    print(f"FINAL collection size: {indexer.collection.count()} segments")
    print("="*60)

if __name__ == "__main__":
    main()
