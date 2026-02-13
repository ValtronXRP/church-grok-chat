#!/usr/bin/env python3
"""
Extract illustrations from Pastor Bob's sermons - VERSION 4
Uses SEGMENT_REQUIREMENTS.md rules:
- Complete sentences from beginning of story
- Specific multi-word topic tags for semantic search
- Worship/announcement filtering (no fixed skip)
- Chroma illustrations_v4 format

Processes: Json3 sermons 1-3, Jsons3 sermons 4, SERMONS_ZIP_05
Resumes from progress file.

Usage:
    python extract_illustrations_v4.py
"""

import os
import sys
import json
import re
import asyncio
import hashlib
import aiohttp
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.environ.get('XAI_API_KEY')
CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

JSON3_DIRS = [
    '/Users/valorkopeny/Desktop/Json3 sermons 1-3',
    '/Users/valorkopeny/Desktop/Jsons3 sermons 4',
]
BATCH5_DIR = '/Users/valorkopeny/Desktop/SERMONS_ZIP_05'

PROGRESS_FILE = './illustrations_v4_progress.json'
OUTPUT_FILE = './illustrations_v4_all.json'

EXTRACTION_PROMPT = """You are analyzing a sermon transcript from Pastor Bob Kopeny of Calvary Chapel.

Your job: find STORIES and ANECDOTES — real narratives Pastor Bob tells to illustrate a point. These are the moments listeners remember most.

WHAT COUNTS (only extract these):
1. PERSONAL STORIES: Pastor Bob shares something from his own life. "I remember when...", "Years ago, Becky and I...", "When my son Jesse was little..."
2. STORIES ABOUT OTHERS: Named or unnamed people. "There was a man who...", "A friend of mine...", "I heard about a woman who..."
3. HISTORICAL EVENTS: Real events from history used to make a point. "During World War II...", "When the Titanic sank..."
4. JOKES: Actual jokes or funny stories with a punchline
5. FAMOUS QUOTES: Only when Pastor Bob tells a story AROUND the quote (who said it, why, what happened)

WHAT DOES NOT COUNT (NEVER extract these):
- Bible teaching, exposition, or explanation of Scripture (this is NOT an illustration)
- Analogies or metaphors that are just a sentence or two ("It's like when you..." — too short, not a story)
- Hypothetical scenarios ("What if..." "Imagine..." — these are teaching devices, not stories)
- Lists of points or instructions
- Transitions, greetings, announcements, worship
- Paraphrasing Bible stories (David and Goliath, etc.)
- General theological explanations
- Short comparisons (pressing hands together, triangle analogy, etc.)

THE KEY TEST: Does it have CHARACTERS and EVENTS? A real illustration has people doing things — it's a narrative with a beginning, middle, and end. If it's just an idea or comparison, it's NOT an illustration.

EXPECT 2-5 ILLUSTRATIONS PER 3-MINUTE CHUNK. Most chunks will have 0-2. If you're finding more than 5, you're being too loose. Return [] if none found.

CRITICAL RULES:
- Text MUST start at the BEGINNING of the story (include the setup)
- Story MUST be COMPLETE — full narrative arc
- Minimum 3 sentences, maximum 15 sentences
- Clean up filler words but keep Pastor Bob's voice
- Topics must be SPECIFIC multi-word phrases matching user questions
  BAD: "faith", "life", "God", "salvation" (too generic)
  GOOD: "false teaching", "protecting young believers", "sharing faith with atheists", "dealing with grief after loss"

Transcript segment (video: {video_id}, starting at ~{start_time}):
---
{text}
---

For EACH illustration found, return:
{{
  "type": "personal_story|anecdote|historical|joke|quote",
  "summary": "1-2 sentence description of the story and what it teaches",
  "full_text": "Complete verbatim transcript of the story from beginning to end",
  "topics": ["specific multi-word topic 1", "specific topic 2", ...],
  "emotional_tone": "inspiring|sobering|funny|warm|convicting|hopeful|challenging|raw",
  "opening_phrase": "The exact first few words where the story begins"
}}

Return ONLY a JSON array. If no real stories found, return [].
Be VERY STRICT. Only NARRATIVES with characters and events. NOT teaching points."""


def parse_json3_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
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
                segments.append({'start_ms': start_ms, 'text': text})

    return video_id, segments


def parse_batch5_sermon(sermon):
    video_id = sermon.get('url', '').split('v=')[-1].split('&')[0] if 'youtube.com' in sermon.get('url', '') else None
    if not video_id:
        sid = sermon.get('id', '')
        video_id = sid.replace('youtube_', '') if sid.startswith('youtube_') else sid

    transcript = sermon.get('transcript', '')
    if not transcript or len(transcript) < 200:
        return None, None

    segments = []
    sentences = re.split(r'(?<=[.!?])\s+', transcript)
    current_ms = 0
    chunk_size = 50
    for i in range(0, len(sentences), chunk_size):
        chunk_text = ' '.join(sentences[i:i+chunk_size])
        if chunk_text.strip():
            segments.append({'start_ms': current_ms, 'text': chunk_text})
            current_ms += 180000

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
    phrase_lower = phrase.lower()[:50]
    for seg in chunk['seg_times']:
        if phrase_lower in seg['text'].lower():
            return seg['ms']
    for seg in chunk['seg_times']:
        words = phrase_lower.split()[:4]
        if all(w in seg['text'].lower() for w in words if len(w) > 3):
            return seg['ms']
    return chunk['start_ms']


def is_worship_or_announcement(text):
    text_lower = text.lower()
    worship_phrases = [
        'let\'s worship', 'let\'s sing', 'worship team', 'praise team',
        'la la la', 'hallelujah hallelujah', 'glory glory',
        'this sunday', 'next week', 'sign up', 'registration',
        'potluck', 'women\'s ministry', 'men\'s breakfast',
        'welcome to calvary', 'glad you\'re here', 'welcome back'
    ]
    worship_count = sum(1 for p in worship_phrases if p in text_lower)
    if worship_count >= 2:
        return True
    music_count = text_lower.count('[music]') + text_lower.count('[applause]')
    if music_count > 5:
        return True
    return False


async def analyze_chunk(chunk_text, video_id, start_time, semaphore, retry=0):
    if is_worship_or_announcement(chunk_text):
        return []

    async with semaphore:
        try:
            prompt = EXTRACTION_PROMPT.format(
                text=chunk_text[:4000],
                video_id=video_id,
                start_time=start_time
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://api.x.ai/v1/chat/completions',
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {XAI_API_KEY}'
                    },
                    json={
                        'model': 'grok-3-mini-fast',
                        'messages': [{'role': 'user', 'content': prompt}],
                        'temperature': 0.15,
                        'max_tokens': 3000
                    },
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        content = data['choices'][0]['message']['content'].strip()
                        if content.startswith('```'):
                            content = re.sub(r'^```json?\n?', '', content)
                            content = re.sub(r'\n?```$', '', content)
                        result = json.loads(content)
                        if isinstance(result, list):
                            return result
                        return []
                    elif response.status == 429:
                        wait = min(2 ** retry * 2, 30)
                        print(f"    Rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                        if retry < 5:
                            return await analyze_chunk(chunk_text, video_id, start_time, semaphore, retry + 1)
                    else:
                        err = await response.text()
                        print(f"    API error {response.status}: {err[:100]}")
        except json.JSONDecodeError as e:
            print(f"    JSON parse error: {e}")
        except Exception as e:
            if retry < 3:
                await asyncio.sleep(2)
                return await analyze_chunk(chunk_text, video_id, start_time, semaphore, retry + 1)
            print(f"    Error: {e}")
    return []


def format_illustration(ill, chunk, video_id):
    if not isinstance(ill, dict):
        return None
    full_text = ill.get('full_text', ill.get('text', ''))
    if not full_text or len(full_text) < 50:
        return None

    opening = ill.get('opening_phrase', '')
    start_ms = find_timestamp_for_phrase(chunk, opening)
    timestamp = format_timestamp(start_ms)
    start_seconds = start_ms // 1000

    topics = ill.get('topics', [])
    if isinstance(topics, str):
        topics = [t.strip() for t in topics.split(',')]

    return {
        'type': ill.get('type', 'illustration'),
        'summary': ill.get('summary', ill.get('title', '')),
        'full_text': full_text,
        'topics': topics,
        'emotional_tone': ill.get('emotional_tone', ill.get('tone', 'inspiring')),
        'illustration_timestamp': timestamp,
        'youtube_url': f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}s",
        'video_id': video_id
    }


async def process_video(video_id, segments, semaphore):
    chunks = combine_into_chunks(segments, chunk_ms=180000)
    illustrations = []

    for i in range(0, len(chunks), 3):
        batch = chunks[i:i+3]
        tasks = [
            analyze_chunk(c['text'], video_id, format_timestamp(c['start_ms']), semaphore)
            for c in batch
        ]
        results = await asyncio.gather(*tasks)

        for chunk, chunk_results in zip(batch, results):
            if not chunk_results:
                continue
            for ill in chunk_results:
                formatted = format_illustration(ill, chunk, video_id)
                if formatted:
                    illustrations.append(formatted)

        await asyncio.sleep(0.2)

    return illustrations


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'processed_videos': [], 'illustrations': [], 'stats': {'total_videos': 0, 'total_illustrations': 0}}


def save_progress(progress):
    progress['stats'] = {
        'total_videos': len(progress['processed_videos']),
        'total_illustrations': len(progress['illustrations'])
    }
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


async def upload_to_chroma(illustrations):
    try:
        import chromadb
        client = chromadb.CloudClient(
            api_key=CHROMA_API_KEY,
            tenant=CHROMA_TENANT,
            database=CHROMA_DATABASE
        )

        try:
            client.delete_collection('illustrations_v4')
            print("Deleted old illustrations_v4")
        except Exception:
            pass

        collection = client.create_collection(
            name='illustrations_v4',
            metadata={'description': 'Pastor Bob illustrations - v4 extraction'}
        )

        batch_size = 50
        total_uploaded = 0

        for i in range(0, len(illustrations), batch_size):
            batch = illustrations[i:i+batch_size]
            ids, documents, metadatas = [], [], []

            for j, ill in enumerate(batch):
                doc_id = hashlib.md5(
                    f"{ill['video_id']}_{ill['illustration_timestamp']}_{i+j}".encode()
                ).hexdigest()
                ids.append(doc_id)
                documents.append(ill['full_text'])
                metadatas.append({
                    'type': ill.get('type', 'illustration'),
                    'summary': ill.get('summary', '')[:500],
                    'topics': ','.join(ill.get('topics', [])),
                    'emotional_tone': ill.get('emotional_tone', ''),
                    'timestamp': ill.get('illustration_timestamp', ''),
                    'youtube_url': ill.get('youtube_url', ''),
                    'video_id': ill.get('video_id', '')
                })

            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            total_uploaded += len(batch)
            print(f"  Uploaded {total_uploaded}/{len(illustrations)}")

        print(f"Chroma upload complete: {collection.count()} illustrations in illustrations_v4")
        return True
    except Exception as e:
        print(f"Chroma upload error: {e}")
        return False


async def main():
    if not XAI_API_KEY:
        print("ERROR: XAI_API_KEY not set in .env")
        sys.exit(1)

    progress = load_progress()
    processed_videos = set(progress.get('processed_videos', []))
    all_illustrations = progress.get('illustrations', [])

    print(f"Progress: {len(processed_videos)} videos done, {len(all_illustrations)} illustrations")
    print("="*60)

    all_sources = []

    for dir_path in JSON3_DIRS:
        if os.path.exists(dir_path):
            files = sorted(Path(dir_path).glob('*.json3'))
            for f in files:
                vid = f.stem.replace('.en', '')
                if vid not in processed_videos:
                    all_sources.append(('json3', str(f), vid))
            print(f"  {dir_path}: {len(files)} total, {sum(1 for s in all_sources if s[0]=='json3')} remaining")

    if os.path.exists(BATCH5_DIR):
        batch5_count = 0
        for batch_file in sorted(Path(BATCH5_DIR).glob('*.json')):
            with open(batch_file) as f:
                sermons = json.load(f)
            for sermon in sermons:
                vid_url = sermon.get('url', '')
                vid = vid_url.split('v=')[-1].split('&')[0] if 'v=' in vid_url else sermon.get('id', '').replace('youtube_', '')
                if vid and vid not in processed_videos and sermon.get('transcript'):
                    all_sources.append(('batch5', json.dumps(sermon), vid))
                    batch5_count += 1
        print(f"  SERMONS_ZIP_05: {batch5_count} remaining with transcripts")

    print(f"\nTotal remaining: {len(all_sources)} videos")
    print("="*60)

    if not all_sources:
        print("All videos processed!")
    else:
        semaphore = asyncio.Semaphore(3)

        for i, (source_type, source_data, video_id) in enumerate(all_sources):
            print(f"\n[{i+1}/{len(all_sources)}] Processing {video_id}...")

            if source_type == 'json3':
                _, segments = parse_json3_file(source_data)
            else:
                sermon = json.loads(source_data)
                _, segments = parse_batch5_sermon(sermon)

            if not segments:
                print(f"  No segments found, skipping")
                processed_videos.add(video_id)
                continue

            illustrations = await process_video(video_id, segments, semaphore)

            if illustrations:
                print(f"  Found {len(illustrations)} illustrations")
                for ill in illustrations[:2]:
                    print(f"    - [{ill['type']}] {ill['summary'][:80]}...")
                all_illustrations.extend(illustrations)
            else:
                print(f"  No illustrations found")

            processed_videos.add(video_id)
            progress['processed_videos'] = list(processed_videos)
            progress['illustrations'] = all_illustrations

            if (i + 1) % 5 == 0:
                save_progress(progress)
                print(f"  [Saved: {len(processed_videos)} videos, {len(all_illustrations)} illustrations]")

        save_progress(progress)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_illustrations, f, indent=2)

    print(f"\n{'='*60}")
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
    print(f"\nTop 30 topics:")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:30]:
        print(f"  {topic}: {count}")

    print(f"\nUpload to Chroma Cloud? ({len(all_illustrations)} illustrations)")
    upload = input("Type 'yes' to upload: ").strip().lower()
    if upload == 'yes':
        await upload_to_chroma(all_illustrations)
    else:
        print("Skipped upload. Run separately or re-run script.")


if __name__ == '__main__':
    asyncio.run(main())
