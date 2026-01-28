#!/usr/bin/env python3
"""
Extract illustrations from Pastor Bob's sermons - VERSION 3
Improved filtering: stories, anecdotes, quotes WITH context.
Excludes general teaching segments.

Processes all JSON3 transcript dirs, tracks progress per video,
uploads results to Chroma Cloud illustrations collection.

Usage:
    python extract_illustrations_v3.py
"""

import os
import sys
import json
import re
import asyncio
import aiohttp
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.environ.get('XAI_API_KEY')
CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

TRANSCRIPT_DIRS = [
    '/Users/valorkopeny/Desktop/Json3 sermons 1-3',
    '/Users/valorkopeny/Desktop/Jsons3 sermons 4',
]

PROGRESS_FILE = './illustrations_v3_progress.json'
OUTPUT_FILE = './illustrations_v3.json'

EXTRACTION_PROMPT = """You are analyzing a sermon transcript from Pastor Bob Kopeny.
Find ILLUSTRATIONS - these are specific types of content that bring a teaching point to life:

WHAT COUNTS as an illustration:
1. PERSONAL STORIES: "I remember when...", "Years ago, Becky and I...", "My son Jesse once..."
2. BIBLICAL STORIES RETOLD: Pastor Bob retelling a Bible story in his own words with detail
3. REAL-WORLD EXAMPLES: "There was a man who...", "I heard about a family that..."
4. ANALOGIES/METAPHORS: Extended comparisons that paint a picture
5. QUOTES WITH CONTEXT: Famous quotes where Pastor Bob explains WHY the quote matters and connects it to his point
6. HISTORICAL REFERENCES: Real events used to illustrate a spiritual truth

WHAT DOES NOT COUNT (EXCLUDE these):
- General verse-by-verse teaching/exposition
- Doctrinal explanations without a story element
- Lists of instructions or commands
- Transitions between topics
- Greetings, announcements, prayer requests

Transcript segment:
---
{text}
---

For EACH illustration found, return:
{{
  "title": "Short descriptive title (3-6 words)",
  "text": "The FULL illustration text - include enough context to understand the story/point (3-8 sentences). Clean up filler words but keep Pastor Bob's voice.",
  "type": "story|biblical_retelling|analogy|quote|historical|example",
  "context": "What point Pastor Bob is making with this illustration (1 sentence)",
  "opening_phrase": "The exact opening words where the illustration begins",
  "topics": ["topic1", "topic2", ...],
  "tone": "comforting|funny|raw|challenging|warm|sobering|inspiring|convicting"
}}

Return ONLY a JSON array. If no true illustrations found, return [].
Be STRICT - only include genuine stories, examples, quotes-with-context, or analogies.
Do NOT include general teaching passages."""

def parse_json3_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return None, None

    video_id = Path(filepath).stem.replace('.en', '')
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
                    'text': text
                })

    return video_id, segments

def format_timestamp(ms):
    seconds = ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

def combine_into_chunks(segments, chunk_ms=180000):
    if not segments:
        return []
    chunks = []
    current = {'start_ms': segments[0]['start_ms'], 'texts': [], 'seg_times': []}

    for seg in segments:
        if seg['start_ms'] - current['start_ms'] > chunk_ms and current['texts']:
            current['text'] = ' '.join(current['texts'])
            chunks.append(current)
            current = {'start_ms': seg['start_ms'], 'texts': [], 'seg_times': []}
        current['texts'].append(seg['text'])
        current['seg_times'].append({'ms': seg['start_ms'], 'text': seg['text']})

    if current['texts']:
        current['text'] = ' '.join(current['texts'])
        chunks.append(current)

    return chunks

def find_timestamp_for_phrase(chunk, phrase):
    if not phrase or not chunk.get('seg_times'):
        return chunk['start_ms']
    phrase_lower = phrase.lower()[:40]
    for seg in chunk['seg_times']:
        if phrase_lower in seg['text'].lower():
            return seg['ms']
    return chunk['start_ms']

async def analyze_chunk(chunk_text, semaphore, retry=0):
    async with semaphore:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://api.x.ai/v1/chat/completions',
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {XAI_API_KEY}'
                    },
                    json={
                        'model': 'grok-3-mini-fast',
                        'messages': [{'role': 'user', 'content': EXTRACTION_PROMPT.format(text=chunk_text[:3000])}],
                        'temperature': 0.2,
                        'max_tokens': 2000
                    },
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        content = data['choices'][0]['message']['content'].strip()
                        if content.startswith('```'):
                            content = re.sub(r'^```json?\n?', '', content)
                            content = re.sub(r'\n?```$', '', content)
                        return json.loads(content)
                    elif response.status == 429:
                        wait = min(2 ** retry * 2, 30)
                        await asyncio.sleep(wait)
                        if retry < 5:
                            return await analyze_chunk(chunk_text, semaphore, retry + 1)
                    else:
                        err = await response.text()
                        print(f"    API error {response.status}: {err[:80]}")
        except json.JSONDecodeError:
            pass
        except Exception as e:
            if retry < 3:
                await asyncio.sleep(2)
                return await analyze_chunk(chunk_text, semaphore, retry + 1)
    return []

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'processed_videos': [], 'illustrations': []}

def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)

async def process_video(filepath, semaphore):
    video_id, segments = parse_json3_file(filepath)
    if not segments:
        return video_id, []

    chunks = combine_into_chunks(segments, chunk_ms=180000)

    illustrations = []
    for i in range(0, len(chunks), 3):
        batch = chunks[i:i+3]
        tasks = [analyze_chunk(c['text'], semaphore) for c in batch]
        results = await asyncio.gather(*tasks)

        for chunk, chunk_results in zip(batch, results):
            if not chunk_results:
                continue
            for ill in chunk_results:
                if not isinstance(ill, dict):
                    continue
                opening = ill.get('opening_phrase', '')
                start_ms = find_timestamp_for_phrase(chunk, opening)
                ill['timestamp'] = format_timestamp(start_ms)
                ill['video_url'] = f"https://www.youtube.com/watch?v={video_id}&t={start_ms // 1000}s"
                ill['video_id'] = video_id
                if 'opening_phrase' in ill:
                    del ill['opening_phrase']
                illustrations.append(ill)

        await asyncio.sleep(0.3)

    return video_id, illustrations

async def main():
    if not XAI_API_KEY:
        print("ERROR: XAI_API_KEY not set")
        sys.exit(1)

    progress = load_progress()
    processed_videos = set(progress.get('processed_videos', []))
    all_illustrations = progress.get('illustrations', [])

    print(f"Loaded progress: {len(processed_videos)} videos done, {len(all_illustrations)} illustrations")

    all_files = []
    for dir_path in TRANSCRIPT_DIRS:
        if os.path.exists(dir_path):
            files = list(Path(dir_path).glob('*.json3'))
            all_files.extend(files)
            print(f"Found {len(files)} files in {dir_path}")

    remaining = [f for f in all_files if Path(f).stem.replace('.en', '') not in processed_videos]
    print(f"Total files: {len(all_files)}, Remaining: {len(remaining)}")

    if not remaining:
        print("All videos processed!")
    else:
        semaphore = asyncio.Semaphore(3)

        for i, filepath in enumerate(remaining):
            video_id = Path(filepath).stem.replace('.en', '')
            print(f"\n[{i+1}/{len(remaining)}] Processing {video_id}...")

            video_id, illustrations = await process_video(filepath, semaphore)

            if illustrations:
                print(f"  Found {len(illustrations)} illustrations")
                all_illustrations.extend(illustrations)
            else:
                print(f"  No illustrations found")

            processed_videos.add(video_id)
            progress['processed_videos'] = list(processed_videos)
            progress['illustrations'] = all_illustrations

            if (i + 1) % 5 == 0:
                save_progress(progress)
                print(f"  [Progress saved: {len(all_illustrations)} total illustrations]")

        save_progress(progress)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_illustrations, f, indent=2)

    print(f"\n{'='*50}")
    print(f"TOTAL: {len(all_illustrations)} illustrations from {len(processed_videos)} videos")

    type_counts = {}
    for ill in all_illustrations:
        t = ill.get('type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"Types: {type_counts}")

    topic_counts = {}
    for ill in all_illustrations:
        for topic in ill.get('topics', []):
            topic_counts[topic.lower()] = topic_counts.get(topic.lower(), 0) + 1
    print(f"\nTop 20 topics:")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {topic}: {count}")

if __name__ == '__main__':
    asyncio.run(main())
