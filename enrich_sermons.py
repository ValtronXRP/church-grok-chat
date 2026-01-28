#!/usr/bin/env python3
"""
Enrich sermon segments with AI-generated metadata.
Uses Grok to analyze each segment and extract:
- Main theological concept/topic
- Key questions this segment answers
- Scripture references
- Searchable keywords
- Segment classification (teaching, story, application, exhortation, etc.)

Processes segments from Chroma Cloud in batches, enriches them,
then updates the metadata back in Chroma Cloud.
"""

import os
import sys
import json
import re
import asyncio
import aiohttp
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.environ.get('XAI_API_KEY')
CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

BATCH_SIZE = 10
PROGRESS_FILE = './enrich_sermons_progress.json'

ANALYSIS_PROMPT = """Analyze this sermon transcript segment from Pastor Bob Kopeny.

Segment text:
---
{text}
---

Sermon title: {title}

Provide a JSON object with:
1. "main_topic": The PRIMARY theological/life concept (1-3 words, e.g., "forgiveness", "God's sovereignty", "marriage", "prayer life")
2. "questions_answered": Array of 2-4 questions a person might ask that this segment answers (e.g., ["How should Christians handle anger?", "What does the Bible say about forgiving others?"])
3. "keywords": Array of 8-15 searchable keywords/phrases found IN the actual text (theological terms, life situations, emotions, actions mentioned)
4. "scriptures": Array of scripture references mentioned (e.g., ["Romans 8:28", "John 3:16"]) or empty array if none
5. "segment_type": One of: "teaching" (doctrinal explanation), "story" (personal anecdote or illustration), "application" (practical life advice), "exhortation" (encouragement/challenge), "exposition" (verse-by-verse), "prayer" (praying), "worship" (praise/worship related)
6. "summary": One sentence summary of what Pastor Bob is saying (max 30 words)

IMPORTANT:
- Keywords must reflect what's ACTUALLY in the text, not assumed
- Questions should be natural questions a church member would ask
- Be specific with topics (not just "faith" but "faith during trials" if that's what it's about)

Return ONLY valid JSON, no markdown:"""

async def analyze_segment(text, title, semaphore, retry_count=0):
    if not text or len(text.strip()) < 50:
        return None

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
                        'messages': [{'role': 'user', 'content': ANALYSIS_PROMPT.format(text=text[:2000], title=title)}],
                        'temperature': 0.2,
                        'max_tokens': 800
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        content = data['choices'][0]['message']['content'].strip()
                        if content.startswith('```'):
                            content = re.sub(r'^```json?\n?', '', content)
                            content = re.sub(r'\n?```$', '', content)
                        return json.loads(content)
                    elif response.status == 429:
                        wait = min(2 ** retry_count * 2, 30)
                        print(f"  Rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                        if retry_count < 5:
                            return await analyze_segment(text, title, semaphore, retry_count + 1)
                    else:
                        error_text = await response.text()
                        print(f"  API error {response.status}: {error_text[:100]}")
        except json.JSONDecodeError as e:
            print(f"  JSON parse error: {e}")
        except Exception as e:
            print(f"  Error: {e}")
            if retry_count < 3:
                await asyncio.sleep(2)
                return await analyze_segment(text, title, semaphore, retry_count + 1)
    return None

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'processed_ids': [], 'enriched_count': 0, 'offset': 0}

def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)

async def main():
    if not XAI_API_KEY:
        print("ERROR: XAI_API_KEY not set")
        sys.exit(1)

    import chromadb
    print("Connecting to Chroma Cloud...")
    client = chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )

    collection = client.get_collection("sermon_segments")
    total = collection.count()
    print(f"Collection has {total} segments")

    progress = load_progress()
    offset = progress.get('offset', 0)
    enriched_total = progress.get('enriched_count', 0)
    processed_set = set(progress.get('processed_ids', []))

    print(f"Resuming from offset {offset}, {enriched_total} already enriched")

    semaphore = asyncio.Semaphore(5)
    fetch_batch = 50

    while offset < total:
        print(f"\n--- Fetching batch at offset {offset}/{total} ---")
        try:
            results = collection.get(
                limit=fetch_batch,
                offset=offset,
                include=['documents', 'metadatas']
            )
        except Exception as e:
            print(f"Fetch error: {e}")
            offset += fetch_batch
            continue

        if not results['ids']:
            break

        segments_to_analyze = []
        for i, doc_id in enumerate(results['ids']):
            if doc_id in processed_set:
                continue
            meta = results['metadatas'][i]
            if meta.get('main_topic'):
                processed_set.add(doc_id)
                continue
            segments_to_analyze.append({
                'id': doc_id,
                'text': results['documents'][i],
                'title': meta.get('title', 'Unknown Sermon'),
                'metadata': meta
            })

        if not segments_to_analyze:
            offset += fetch_batch
            continue

        print(f"  Analyzing {len(segments_to_analyze)} segments...")

        for batch_start in range(0, len(segments_to_analyze), BATCH_SIZE):
            batch = segments_to_analyze[batch_start:batch_start + BATCH_SIZE]

            tasks = [
                analyze_segment(seg['text'], seg['title'], semaphore)
                for seg in batch
            ]
            results_ai = await asyncio.gather(*tasks)

            ids_to_update = []
            metas_to_update = []

            for seg, enrichment in zip(batch, results_ai):
                if enrichment:
                    updated_meta = dict(seg['metadata'])
                    updated_meta['main_topic'] = enrichment.get('main_topic', '')
                    updated_meta['questions_answered'] = json.dumps(enrichment.get('questions_answered', []))
                    updated_meta['keywords'] = ','.join(enrichment.get('keywords', []))
                    updated_meta['scriptures'] = ','.join(enrichment.get('scriptures', []))
                    updated_meta['segment_type'] = enrichment.get('segment_type', 'teaching')
                    updated_meta['summary'] = enrichment.get('summary', '')
                    updated_meta['topics'] = enrichment.get('main_topic', updated_meta.get('topics', ''))

                    ids_to_update.append(seg['id'])
                    metas_to_update.append(updated_meta)
                    processed_set.add(seg['id'])
                    enriched_total += 1

            if ids_to_update:
                try:
                    collection.update(
                        ids=ids_to_update,
                        metadatas=metas_to_update
                    )
                    print(f"  Updated {len(ids_to_update)} segments (total enriched: {enriched_total})")
                except Exception as e:
                    print(f"  Update error: {e}")

            await asyncio.sleep(0.2)

        offset += fetch_batch
        progress['offset'] = offset
        progress['enriched_count'] = enriched_total
        progress['processed_ids'] = list(processed_set)[-10000:]
        save_progress(progress)

    print(f"\n{'='*50}")
    print(f"COMPLETE: Enriched {enriched_total} segments total")

if __name__ == '__main__':
    asyncio.run(main())
