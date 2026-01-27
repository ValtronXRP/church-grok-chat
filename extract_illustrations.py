#!/usr/bin/env python3
"""
Extract illustrations from Pastor Bob's sermons and create a vector database.

For each illustration:
- Write the exact text he says (short quote, 1-3 sentences)
- List all topics it could apply to
- Add tone: comforting, funny, raw, challenging, warm, sobering
- Note timestamp and video URL
- Output as JSON

Usage:
    python extract_illustrations.py --input "/path/to/json3/files" --output "./illustrations"
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import timedelta
import asyncio
import aiohttp

# Try to use OpenAI/xAI for analysis
XAI_API_KEY = os.environ.get('XAI_API_KEY')

def parse_json3_file(filepath):
    """Parse a YouTube JSON3 transcript file and extract text with timestamps."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None, None
    
    # Extract video ID from filename
    video_id = Path(filepath).stem.replace('.en', '')
    
    # Extract events (transcript segments)
    events = data.get('events', [])
    
    segments = []
    for event in events:
        if 'segs' in event:
            start_ms = event.get('tStartMs', 0)
            text_parts = []
            for seg in event['segs']:
                if 'utf8' in seg:
                    text_parts.append(seg['utf8'])
            
            text = ''.join(text_parts).strip()
            if text and text not in ['[Music]', '[Applause]', '\n']:
                segments.append({
                    'start_ms': start_ms,
                    'start_time': format_timestamp(start_ms),
                    'text': text
                })
    
    return video_id, segments

def format_timestamp(ms):
    """Convert milliseconds to HH:MM:SS format."""
    seconds = ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

def combine_segments_into_chunks(segments, chunk_duration_ms=60000):
    """Combine segments into larger chunks for analysis (default 1 minute)."""
    if not segments:
        return []
    
    chunks = []
    current_chunk = {
        'start_ms': segments[0]['start_ms'],
        'start_time': segments[0]['start_time'],
        'texts': []
    }
    
    for seg in segments:
        # If this segment is beyond our chunk window, start new chunk
        if seg['start_ms'] - current_chunk['start_ms'] > chunk_duration_ms and current_chunk['texts']:
            current_chunk['text'] = ' '.join(current_chunk['texts'])
            chunks.append(current_chunk)
            current_chunk = {
                'start_ms': seg['start_ms'],
                'start_time': seg['start_time'],
                'texts': []
            }
        
        current_chunk['texts'].append(seg['text'])
    
    # Don't forget last chunk
    if current_chunk['texts']:
        current_chunk['text'] = ' '.join(current_chunk['texts'])
        chunks.append(current_chunk)
    
    return chunks

def find_illustration_markers(text):
    """Look for phrases that typically introduce illustrations/stories."""
    markers = [
        r"let me tell you",
        r"i remember when",
        r"remember when",
        r"there was a (man|woman|boy|girl|guy|lady|person)",
        r"i was (talking|speaking|praying|thinking)",
        r"years ago",
        r"one time",
        r"story about",
        r"reminds me of",
        r"picture this",
        r"imagine",
        r"for example",
        r"illustration",
        r"my (wife|son|daughter|friend|dad|mom|father|mother)",
        r"becky (and i|told me|said)",
        r"when i was (young|a kid|growing up)",
        r"c\.s\. lewis",
        r"someone once said",
        r"(funny|true) story",
    ]
    
    text_lower = text.lower()
    for marker in markers:
        if re.search(marker, text_lower):
            return True
    return False

async def analyze_chunk_with_ai(chunk_text, video_id, start_time, start_ms):
    """Use AI to analyze a text chunk and extract illustrations."""
    if not XAI_API_KEY:
        return []
    
    prompt = f"""Analyze this sermon transcript segment from Pastor Bob Kopeny. 
Look for illustrations, stories, personal anecdotes, quotes, or examples he uses to make a point.

For EACH illustration found, extract:
1. A short title (2-5 words)
2. The exact key quote (1-3 sentences, cleaned up for clarity while keeping his voice)
3. All topics it could apply to (be generous - list 5-10 topics)
4. The tone (one of: comforting, funny, raw, challenging, warm, sobering, inspiring, convicting)

IMPORTANT: 
- Only extract ACTUAL illustrations/stories/examples, not general teaching
- Clean up filler words but keep Pastor Bob's natural speaking style
- If no illustrations are found, return empty array []

Transcript segment:
---
{chunk_text}
---

Return ONLY a JSON array (no markdown, no explanation):
[
  {{
    "illustration": "Title Here",
    "text": "The cleaned up quote...",
    "topics": ["topic1", "topic2", ...],
    "tone": "tone_here"
  }}
]

If no illustrations found, return: []"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.x.ai/v1/chat/completions',
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {XAI_API_KEY}'
                },
                json={
                    'model': 'grok-3',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.3,
                    'max_tokens': 1500
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data['choices'][0]['message']['content'].strip()
                    
                    # Parse JSON from response
                    # Handle potential markdown wrapping
                    if content.startswith('```'):
                        content = re.sub(r'^```json?\n?', '', content)
                        content = re.sub(r'\n?```$', '', content)
                    
                    illustrations = json.loads(content)
                    
                    # Add video info to each illustration
                    for ill in illustrations:
                        ill['timestamp'] = start_time
                        ill['video_url'] = f"https://www.youtube.com/watch?v={video_id}&t={start_ms // 1000}s"
                        ill['video_id'] = video_id
                    
                    return illustrations
                else:
                    print(f"API error: {response.status}")
                    return []
    except Exception as e:
        print(f"Error calling AI: {e}")
        return []

async def process_sermon_file(filepath, output_dir):
    """Process a single sermon JSON3 file and extract illustrations."""
    print(f"Processing: {Path(filepath).name}")
    
    video_id, segments = parse_json3_file(filepath)
    if not segments:
        print(f"  No segments found")
        return []
    
    print(f"  Found {len(segments)} segments")
    
    # Combine into larger chunks for analysis
    chunks = combine_segments_into_chunks(segments, chunk_duration_ms=90000)  # 90 second chunks
    print(f"  Created {len(chunks)} chunks for analysis")
    
    all_illustrations = []
    
    # First pass: find chunks that might contain illustrations (faster)
    candidate_chunks = []
    for chunk in chunks:
        if find_illustration_markers(chunk['text']):
            candidate_chunks.append(chunk)
    
    print(f"  Found {len(candidate_chunks)} candidate chunks with illustration markers")
    
    # Analyze candidate chunks with AI
    for i, chunk in enumerate(candidate_chunks):
        print(f"    Analyzing chunk {i+1}/{len(candidate_chunks)}...")
        illustrations = await analyze_chunk_with_ai(
            chunk['text'], 
            video_id, 
            chunk['start_time'],
            chunk['start_ms']
        )
        if illustrations:
            print(f"      Found {len(illustrations)} illustration(s)")
            all_illustrations.extend(illustrations)
        
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)
    
    return all_illustrations

async def main():
    parser = argparse.ArgumentParser(description='Extract illustrations from sermon transcripts')
    parser.add_argument('--input', '-i', required=True, help='Input directory with JSON3 files')
    parser.add_argument('--output', '-o', default='./illustrations', help='Output directory')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit number of files to process (0=all)')
    args = parser.parse_args()
    
    if not XAI_API_KEY:
        print("ERROR: XAI_API_KEY environment variable not set")
        print("Set it with: export XAI_API_KEY='your-key-here'")
        sys.exit(1)
    
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all JSON3 files
    json3_files = list(input_dir.glob('*.json3'))
    print(f"Found {len(json3_files)} JSON3 files")
    
    if args.limit > 0:
        json3_files = json3_files[:args.limit]
        print(f"Processing first {args.limit} files")
    
    all_illustrations = []
    
    for filepath in json3_files:
        illustrations = await process_sermon_file(filepath, output_dir)
        all_illustrations.extend(illustrations)
        
        # Save progress periodically
        if len(all_illustrations) > 0 and len(all_illustrations) % 10 == 0:
            progress_file = output_dir / 'illustrations_progress.json'
            with open(progress_file, 'w') as f:
                json.dump(all_illustrations, f, indent=2)
    
    # Save final results
    output_file = output_dir / 'illustrations.json'
    with open(output_file, 'w') as f:
        json.dump(all_illustrations, f, indent=2)
    
    print(f"\n{'='*50}")
    print(f"COMPLETE: Found {len(all_illustrations)} total illustrations")
    print(f"Saved to: {output_file}")
    
    # Print summary by topic
    topic_counts = {}
    for ill in all_illustrations:
        for topic in ill.get('topics', []):
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    
    print(f"\nTop topics:")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {topic}: {count}")

if __name__ == '__main__':
    asyncio.run(main())
