#!/usr/bin/env python3
"""
Rebuild sermon_segments_v2 in ChromaDB with ENRICHED metadata.
Every segment gets a proper sermon title, accurate timestamps, and YouTube URLs.

Uses video_title_map.json (728 titles from batch files + YouTube oEmbed).
Same chunking logic as rebuild_embeddings.py but with title enrichment.
"""

import os, json, hashlib, time, sys, re
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import chromadb
from sentence_transformers import SentenceTransformer
import numpy as np

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

EMBEDDING_MODEL = 'sentence-transformers/all-mpnet-base-v2'
EMBEDDING_DIM = 768
BATCH_SIZE = 64
MAX_TEXT_LEN = 512

JSON3_DIRS = [
    '/Users/valorkopeny/Desktop/Json3 sermons 1-3',
    '/Users/valorkopeny/Desktop/Jsons3 sermons 4',
]
BATCH_DIR = '/Users/valorkopeny/Desktop/SERMONS_ZIP_05'

with open(os.path.join(os.path.dirname(__file__), 'video_title_map.json')) as f:
    TITLE_MAP = json.load(f)
print(f"Loaded {len(TITLE_MAP)} sermon titles from video_title_map.json")

SKIP_PATTERNS = re.compile(
    r'^\[?(music|applause|laughter|silence|foreign)\]?$|'
    r'^[\s\n]*$|'
    r'^\[?\d+:\d+:\d+\]?$',
    re.IGNORECASE
)

def get_client():
    return chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )

def get_embedder():
    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
    print(f"Model loaded. Dim: {model.get_sentence_embedding_dimension()}")
    return model

def encode_batch(model, texts, batch_size=BATCH_SIZE):
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        embs = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        all_embs.append(embs)
    return np.vstack(all_embs) if all_embs else np.array([])

def format_timestamp(seconds):
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

def parse_json3_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    events = data.get('events', [])
    segments = []
    for ev in events:
        if 'segs' not in ev:
            continue
        text = ''.join(s.get('utf8', '') for s in ev['segs']).strip()
        if not text or SKIP_PATTERNS.match(text):
            continue
        start_ms = ev.get('tStartMs', 0)
        segments.append({
            'text': text,
            'start_ms': int(start_ms),
            'start_sec': int(start_ms) / 1000.0
        })
    video_id = os.path.basename(filepath).replace('.en.json3', '')
    title = TITLE_MAP.get(video_id, '')
    return {
        'video_id': video_id,
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}',
        'title': title,
        'source_file': os.path.basename(filepath),
        'segments': segments
    }

def parse_batch_sermon(item, batch_file):
    transcript = item.get('transcript', '')
    if not transcript:
        return None
    video_id = item.get('video_id', '')
    if not video_id:
        url = item.get('url', '')
        if 'v=' in url:
            video_id = url.split('v=')[1].split('&')[0]
    title = TITLE_MAP.get(video_id, '') or item.get('title', '')
    segments = []
    ts_pattern = re.compile(r'\[(\d+):(\d+):(\d+)\]')
    parts = ts_pattern.split(transcript)
    i = 0
    while i < len(parts):
        if i + 3 < len(parts):
            try:
                h, m, s = int(parts[i+1]), int(parts[i+2]), int(parts[i+3])
                start_sec = h * 3600 + m * 60 + s
                text = parts[i].strip()
                if text and not SKIP_PATTERNS.match(text):
                    segments.append({
                        'text': text,
                        'start_ms': start_sec * 1000,
                        'start_sec': float(start_sec)
                    })
                i += 4
            except (ValueError, IndexError):
                i += 1
        else:
            text = parts[i].strip()
            if text and not SKIP_PATTERNS.match(text):
                segments.append({
                    'text': text,
                    'start_ms': 0,
                    'start_sec': 0.0
                })
            i += 1
    return {
        'video_id': video_id,
        'youtube_url': item.get('url', f'https://www.youtube.com/watch?v={video_id}'),
        'title': title,
        'source_file': batch_file,
        'segments': segments
    }

def chunk_segments(segments, video_id, youtube_url, title, source_file, target_words=400, overlap_words=50):
    chunks = []
    current_texts = []
    current_word_count = 0
    current_start_sec = 0

    for seg in segments:
        words = seg['text'].split()
        if not current_texts:
            current_start_sec = seg['start_sec']
        current_texts.append(seg['text'])
        current_word_count += len(words)

        if current_word_count >= target_words:
            chunk_text = ' '.join(current_texts)
            end_sec = seg['start_sec']
            chunks.append({
                'text': chunk_text,
                'video_id': video_id,
                'youtube_url': youtube_url,
                'title': title,
                'source_file': source_file,
                'start_sec': current_start_sec,
                'end_sec': end_sec,
                'word_count': len(chunk_text.split())
            })
            overlap_text = ' '.join(current_texts[-2:]) if len(current_texts) >= 2 else ''
            overlap_wc = len(overlap_text.split()) if overlap_text else 0
            if overlap_wc > 0:
                current_texts = [overlap_text]
                current_word_count = overlap_wc
            else:
                current_texts = []
                current_word_count = 0

    if current_texts and current_word_count >= 50:
        chunk_text = ' '.join(current_texts)
        chunks.append({
            'text': chunk_text,
            'video_id': video_id,
            'youtube_url': youtube_url,
            'title': title,
            'source_file': source_file,
            'start_sec': current_start_sec,
            'end_sec': segments[-1]['start_sec'] if segments else current_start_sec,
            'word_count': len(chunk_text.split())
        })
    return chunks

def discover_all_sermons():
    all_sermons = []
    for d in JSON3_DIRS:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname.endswith('.json3'):
                all_sermons.append(('json3', os.path.join(d, fname)))
    if os.path.isdir(BATCH_DIR):
        for fname in sorted(os.listdir(BATCH_DIR)):
            if fname.startswith('SERMONS_BATCH_') and fname.endswith('.json'):
                all_sermons.append(('batch_file', os.path.join(BATCH_DIR, fname)))
    return all_sermons

def rebuild_sermons(client, model):
    print("\n" + "="*60)
    print("REBUILDING sermon_segments_v2 WITH ENRICHED METADATA")
    print("="*60)

    try:
        client.delete_collection('sermon_segments_v2')
        print("Deleted old sermon_segments_v2")
    except:
        pass

    collection = client.create_collection(
        name='sermon_segments_v2',
        metadata={'description': 'Pastor Bob sermons - enriched with titles + timestamps', 'hnsw:space': 'cosine'}
    )
    print("Created sermon_segments_v2")

    sources = discover_all_sermons()
    print(f"Discovered {len(sources)} sermon sources")

    total_chunks = 0
    total_uploaded = 0
    titled_chunks = 0
    untitled_chunks = 0
    batch_ids, batch_docs, batch_metas, batch_embs = [], [], [], []
    start_time = time.time()
    skipped_sources = 0

    for idx, (source_type, source_data) in enumerate(sources):
        try:
            sermons_to_process = []

            if source_type == 'json3':
                sermon = parse_json3_file(source_data)
                if sermon and sermon['segments']:
                    sermons_to_process.append(sermon)
                else:
                    skipped_sources += 1
                    continue

            elif source_type == 'batch_file':
                with open(source_data, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                for item in items:
                    sermon = parse_batch_sermon(item, os.path.basename(source_data))
                    if sermon and sermon['segments']:
                        sermons_to_process.append(sermon)

            for sermon in sermons_to_process:
                chunks = chunk_segments(
                    sermon['segments'], sermon['video_id'],
                    sermon['youtube_url'], sermon['title'], sermon['source_file']
                )
                if not chunks:
                    continue

                texts = [c['text'][:MAX_TEXT_LEN] for c in chunks]
                embs = encode_batch(model, texts)

                for j, c in enumerate(chunks):
                    doc_id = hashlib.md5(f"{c['video_id']}_{c['start_sec']}_{total_chunks}".encode()).hexdigest()
                    clip_url = f"https://www.youtube.com/watch?v={c['video_id']}&t={int(c['start_sec'])}s"
                    title = c.get('title', '')

                    if title:
                        titled_chunks += 1
                    else:
                        title = 'Sermon'
                        untitled_chunks += 1

                    batch_ids.append(doc_id)
                    batch_docs.append(c['text'][:MAX_TEXT_LEN])
                    batch_metas.append({
                        'video_id': c['video_id'],
                        'url': c['youtube_url'],
                        'timestamped_url': clip_url,
                        'title': title,
                        'start_time': format_timestamp(c['start_sec']),
                        'start_sec': c['start_sec'],
                        'end_sec': c['end_sec'],
                        'word_count': c['word_count'],
                    })
                    batch_embs.append(embs[j].tolist())
                    total_chunks += 1

            if len(batch_ids) >= 200:
                collection.add(
                    ids=batch_ids[:200],
                    documents=batch_docs[:200],
                    metadatas=batch_metas[:200],
                    embeddings=batch_embs[:200]
                )
                total_uploaded += 200
                batch_ids = batch_ids[200:]
                batch_docs = batch_docs[200:]
                batch_metas = batch_metas[200:]
                batch_embs = batch_embs[200:]

        except Exception as e:
            print(f"  Error processing source {idx}: {e}")
            import traceback
            traceback.print_exc()

        if (idx + 1) % 25 == 0:
            elapsed = time.time() - start_time
            print(f"  [{idx+1}/{len(sources)}] {total_chunks} chunks ({titled_chunks} titled, {untitled_chunks} untitled), {total_uploaded} uploaded, {elapsed:.0f}s")
            sys.stdout.flush()

    while batch_ids:
        end = min(200, len(batch_ids))
        collection.add(
            ids=batch_ids[:end],
            documents=batch_docs[:end],
            metadatas=batch_metas[:end],
            embeddings=batch_embs[:end]
        )
        total_uploaded += end
        batch_ids = batch_ids[end:]
        batch_docs = batch_docs[end:]
        batch_metas = batch_metas[end:]
        batch_embs = batch_embs[end:]

    elapsed = time.time() - start_time
    count = collection.count()
    print(f"\n{'='*60}")
    print(f"ENRICHED REBUILD COMPLETE")
    print(f"  Total chunks: {total_chunks}")
    print(f"  With titles: {titled_chunks}")
    print(f"  Without titles: {untitled_chunks}")
    print(f"  Uploaded: {total_uploaded}")
    print(f"  In collection: {count}")
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"{'='*60}")
    return count

def test_queries(client, model):
    print("\n" + "="*60)
    print("TESTING ENRICHED QUERIES")
    print("="*60)

    col = client.get_collection('sermon_segments_v2')

    queries = [
        "What does Pastor Bob teach about the baptism of the Holy Spirit?",
        "How was Pastor Bob saved?",
        "What does the Bible say about forgiveness?",
        "What does Pastor Bob teach about prayer?",
        "What does Pastor Bob teach about marriage?",
    ]

    for q in queries:
        emb = model.encode([q], normalize_embeddings=True).tolist()
        results = col.query(query_embeddings=emb, n_results=5, include=['metadatas', 'documents', 'distances'])
        print(f"\nQ: {q}")
        if results['ids'][0]:
            for i in range(len(results['ids'][0])):
                d = results['distances'][0][i]
                m = results['metadatas'][0][i]
                t = results['documents'][0][i][:80]
                print(f"  [{i+1}] dist={d:.3f} | \"{m.get('title','')}\" @ {m.get('start_time','')} | vid={m.get('video_id','')} | {t}...")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-only', action='store_true', help='Only test queries, do not rebuild')
    parser.add_argument('--dry-run', action='store_true', help='Parse and count but do not upload')
    args = parser.parse_args()

    client = get_client()
    model = get_embedder()

    if args.test_only:
        test_queries(client, model)
    elif args.dry_run:
        sources = discover_all_sermons()
        print(f"Discovered {len(sources)} sermon sources")
        total = 0
        titled = 0
        for idx, (source_type, source_data) in enumerate(sources):
            if source_type == 'json3':
                sermon = parse_json3_file(source_data)
                if sermon and sermon['segments']:
                    chunks = chunk_segments(sermon['segments'], sermon['video_id'], sermon['youtube_url'], sermon['title'], sermon['source_file'])
                    for c in chunks:
                        total += 1
                        if c.get('title'):
                            titled += 1
            elif source_type == 'batch_file':
                with open(source_data, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                for item in items:
                    sermon = parse_batch_sermon(item, os.path.basename(source_data))
                    if sermon and sermon['segments']:
                        chunks = chunk_segments(sermon['segments'], sermon['video_id'], sermon['youtube_url'], sermon['title'], sermon['source_file'])
                        for c in chunks:
                            total += 1
                            if c.get('title'):
                                titled += 1
            if (idx+1) % 50 == 0:
                print(f"  [{idx+1}/{len(sources)}] {total} chunks so far ({titled} titled)")
        print(f"\nDRY RUN: {total} total chunks, {titled} titled ({titled*100//max(total,1)}%)")
    else:
        rebuild_sermons(client, model)
        test_queries(client, model)
