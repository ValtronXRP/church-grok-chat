#!/usr/bin/env python3
import os, sys, json, hashlib, re, logging, time
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import chromadb
from sentence_transformers import SentenceTransformer
import numpy as np

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

EMBEDDING_MODEL = 'sentence-transformers/all-mpnet-base-v2'
COLLECTION_NAME = 'sermon_segments_v2'
MAX_TEXT_LEN = 512
BATCH_SIZE = 64
TARGET_WORDS = 400

TITLE_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_title_map.json')

SKIP_PATTERNS = re.compile(
    r'^\[?(music|applause|laughter|silence|foreign)\]?$|'
    r'^[\s\n]*$',
    re.IGNORECASE
)
WORSHIP_INDICATORS = re.compile(
    r'\b(hallelujah|praise the lord|worship with us|let\'?s worship|'
    r'sing with us|worship team|let\'?s stand|stand together|'
    r'♪|🎵|🎶)\b',
    re.IGNORECASE
)
SERMON_START_INDICATORS = re.compile(
    r'\b(turn in your bibles?|open your bibles?|'
    r'let\'?s pray|father god|heavenly father|'
    r'lord we come|chapter \d|verse \d|'
    r'genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|'
    r'samuel|kings|chronicles|ezra|nehemiah|esther|job|psalm|proverbs|'
    r'ecclesiastes|song of solomon|isaiah|jeremiah|lamentations|ezekiel|'
    r'daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|'
    r'zephaniah|haggai|zechariah|malachi|'
    r'matthew|mark|luke|john|acts|romans|corinthians|galatians|'
    r'ephesians|philippians|colossians|thessalonians|timothy|titus|'
    r'philemon|hebrews|james|peter|jude|revelation)\b',
    re.IGNORECASE
)

_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _embedder = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
        logger.info(f"Model loaded. Dim: {_embedder.get_sentence_embedding_dimension()}")
    return _embedder

def get_chroma_client():
    return chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )

def encode_batch(model, texts):
    all_embs = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i+BATCH_SIZE]
        embs = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        all_embs.append(embs)
    return np.vstack(all_embs) if all_embs else np.array([])

def format_timestamp(seconds):
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

def parse_timestamp(ts_str):
    parts = ts_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0

def load_title_map():
    if os.path.exists(TITLE_MAP_PATH):
        with open(TITLE_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_title_map(title_map):
    with open(TITLE_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(title_map, f, indent=2, ensure_ascii=False)

def parse_json_transcript_file(file_path):
    with open(file_path, 'rb') as f:
        raw = f.read()
    text = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').decode('utf-8', errors='replace')
    data = json.loads(text)

    sermons = []
    for item in data:
        transcript = item.get('transcript', '').strip()
        if not transcript or len(transcript.split()) < 50:
            continue
        if transcript.endswith('---'):
            transcript = transcript[:-3].strip()

        video_id = item.get('id', '')
        title = item.get('title', '')
        if not title:
            continue

        words = transcript.split()
        segments = []
        for i in range(0, len(words), 20):
            chunk_words = words[i:i+20]
            text = ' '.join(chunk_words)
            if SKIP_PATTERNS.match(text):
                continue
            segments.append({
                'text': text,
                'start_ms': 0,
                'start_sec': 0.0
            })

        if segments:
            sermons.append({
                'title': title,
                'video_id': video_id,
                'segments': segments,
                'flat_text': True
            })

    return sermons

def parse_transcript_file(file_path):
    if file_path.endswith('.json'):
        return parse_json_transcript_file(file_path)

    if file_path.endswith('.rtf'):
        import subprocess
        result = subprocess.run(['textutil', '-convert', 'txt', '-stdout', file_path],
                              capture_output=True, text=True)
        content = result.stdout
    else:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

    sermons = []
    blocks = re.split(r'\n---\n', content)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        title_match = re.search(r'^#\s*Video Title:\s*(.+)', block, re.MULTILINE)
        vid_match = re.search(r'^#\s*Video ID:\s*(\S+)', block, re.MULTILINE)

        if not title_match:
            continue

        title = title_match.group(1).strip()
        video_id = vid_match.group(1).strip() if vid_match else None

        segments = []
        for line in block.split('\n'):
            ts_match = re.match(r'^\[(\d+:\d+(?::\d+)?)\]\s*(.+)', line.strip())
            if ts_match:
                ts_str = ts_match.group(1)
                text = ts_match.group(2).strip()
                if text and not SKIP_PATTERNS.match(text):
                    start_sec = parse_timestamp(ts_str)
                    segments.append({
                        'text': text,
                        'start_ms': start_sec * 1000,
                        'start_sec': float(start_sec)
                    })

        if segments:
            sermons.append({
                'title': title,
                'video_id': video_id,
                'segments': segments
            })

    return sermons

def find_sermon_start(segments):
    for i, seg in enumerate(segments):
        if seg['start_sec'] < 30:
            continue
        window_text = ' '.join(s['text'] for s in segments[max(0,i-2):i+3])
        if SERMON_START_INDICATORS.search(window_text):
            return max(0, i - 2)
    for i, seg in enumerate(segments):
        if seg['start_sec'] >= 120:
            return i
    return 0

def chunk_segments(segments, video_id, youtube_url, title, target_words=TARGET_WORDS):
    is_apb = bool(re.search(r'ask pastor bob|APB', title, re.IGNORECASE))
    if not is_apb:
        sermon_start_idx = find_sermon_start(segments)
        if sermon_start_idx > 0:
            segments = segments[sermon_start_idx:]

    chunks = []
    current_texts = []
    current_word_count = 0
    current_start_sec = 0
    next_start_sec = None

    for seg in segments:
        words = seg['text'].split()
        if not current_texts:
            current_start_sec = next_start_sec if next_start_sec is not None else seg['start_sec']
            next_start_sec = None
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
                'start_sec': current_start_sec,
                'end_sec': end_sec,
                'word_count': len(chunk_text.split())
            })
            next_start_sec = seg['start_sec']
            current_texts = []
            current_word_count = 0

    if current_texts and current_word_count >= 50:
        chunk_text = ' '.join(current_texts)
        chunks.append({
            'text': chunk_text,
            'video_id': video_id,
            'youtube_url': youtube_url,
            'title': title,
            'start_sec': current_start_sec,
            'end_sec': segments[-1]['start_sec'] if segments else current_start_sec,
            'word_count': len(chunk_text.split())
        })
    return chunks

def ingest_transcript_files(file_paths, dry_run=False):
    logger.info("=== Transcript File Ingest: Starting ===")

    title_map = load_title_map()
    all_sermons = []

    for fp in file_paths:
        logger.info(f"Parsing: {fp}")
        sermons = parse_transcript_file(fp)
        logger.info(f"  Found {len(sermons)} sermons")
        all_sermons.extend(sermons)

    new_sermons = []
    for s in all_sermons:
        vid = s.get('video_id')
        if vid and vid in title_map:
            continue
        new_sermons.append(s)

    logger.info(f"Total: {len(all_sermons)} sermons parsed, {len(new_sermons)} new (not in title_map)")

    if not new_sermons:
        logger.info("No new sermons to ingest.")
        return 0

    if dry_run:
        for s in new_sermons:
            logger.info(f"  [DRY RUN] {s.get('video_id', 'NO_VID')}: {s['title']} ({len(s['segments'])} segments)")
        return len(new_sermons)

    model = get_embedder()
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={'description': 'Pastor Bob sermons - enriched with titles + timestamps', 'hnsw:space': 'cosine'}
    )
    existing_count = collection.count()
    logger.info(f"Collection '{COLLECTION_NAME}' has {existing_count} existing chunks")

    total_new_chunks = 0
    successfully_ingested = []

    for idx, sermon in enumerate(new_sermons):
        title = sermon['title']
        video_id = sermon.get('video_id') or hashlib.md5(title.encode()).hexdigest()[:11]
        segments = sermon['segments']

        youtube_url = f'https://www.youtube.com/watch?v={video_id}' if sermon.get('video_id') else ''
        chunks = chunk_segments(segments, video_id, youtube_url, title)

        if not chunks:
            logger.warning(f"  Skipping {title} — no chunks after processing")
            continue

        logger.info(f"  [{idx+1}/{len(new_sermons)}] {title}: {len(chunks)} chunks")

        texts = [c['text'][:MAX_TEXT_LEN] for c in chunks]
        embs = encode_batch(model, texts)

        batch_ids = []
        batch_docs = []
        batch_metas = []
        batch_embs = []

        for j, c in enumerate(chunks):
            doc_id = hashlib.md5(f"{c['video_id']}_{c['start_sec']}_{existing_count + total_new_chunks}".encode()).hexdigest()

            clip_url = ''
            if c['youtube_url']:
                clip_url = f"https://www.youtube.com/watch?v={c['video_id']}&t={int(c['start_sec'])}s"

            batch_ids.append(doc_id)
            batch_docs.append(c['text'][:MAX_TEXT_LEN])
            batch_metas.append({
                'video_id': c['video_id'],
                'url': c['youtube_url'] or '',
                'timestamped_url': clip_url,
                'title': title,
                'start_time': format_timestamp(c['start_sec']),
                'start_sec': c['start_sec'],
                'end_sec': c['end_sec'],
                'word_count': c['word_count'],
            })
            batch_embs.append(embs[j].tolist())
            total_new_chunks += 1

        try:
            for i in range(0, len(batch_ids), 200):
                end = min(i + 200, len(batch_ids))
                collection.add(
                    ids=batch_ids[i:end],
                    documents=batch_docs[i:end],
                    metadatas=batch_metas[i:end],
                    embeddings=batch_embs[i:end]
                )
            successfully_ingested.append((video_id, title))
        except Exception as e:
            logger.error(f"  Failed to upload chunks for {title}: {e}")
            continue

    if successfully_ingested:
        for vid, title in successfully_ingested:
            title_map[vid] = title
        save_title_map(title_map)
        logger.info(f"Updated video_title_map.json with {len(successfully_ingested)} new entries (total: {len(title_map)})")

    final_count = collection.count()
    logger.info(f"=== Ingest complete: {total_new_chunks} new chunks added. Collection now has {final_count} chunks ===")
    return len(successfully_ingested)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest sermon transcripts from text/RTF files into ChromaDB')
    parser.add_argument('files', nargs='+', help='Transcript file paths')
    parser.add_argument('--dry-run', action='store_true', help='List sermons without uploading')
    args = parser.parse_args()
    count = ingest_transcript_files(args.files, dry_run=args.dry_run)
    logger.info(f"Done! Ingested {count} sermons.")
