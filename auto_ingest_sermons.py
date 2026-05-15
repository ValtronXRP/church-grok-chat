#!/usr/bin/env python3
import os, sys, json, hashlib, time, re, logging, html
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import requests
import chromadb
from sentence_transformers import SentenceTransformer
import numpy as np

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')

PLAYLIST_ID = 'PLEgYquYMZK-S5hMVvpeGJ4U-R627ZIQ94'
EMBEDDING_MODEL = 'sentence-transformers/all-mpnet-base-v2'
COLLECTION_NAME = 'sermon_segments_v2'
MAX_TEXT_LEN = 512
BATCH_SIZE = 64
TARGET_WORDS = 400

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', SCRIPT_DIR)
TITLE_MAP_PATH = os.path.join(SCRIPT_DIR, 'video_title_map.json')
SKIP_LIST_PATH = os.path.join(SCRIPT_DIR, 'ingest_skip_list.json')
INGEST_HISTORY_PATH = os.path.join(DATA_DIR, 'ingest_history.json')

SKIP_PATTERNS = re.compile(
    r'^\[?(music|applause|laughter|silence|foreign)\]?$|'
    r'^[\s\n]*$|'
    r'^\[?\d+:\d+:\d+\]?$',
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

def load_title_map():
    if os.path.exists(TITLE_MAP_PATH):
        with open(TITLE_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_title_map(title_map):
    with open(TITLE_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(title_map, f, indent=2, ensure_ascii=False)

def load_skip_list():
    if os.path.exists(SKIP_LIST_PATH):
        with open(SKIP_LIST_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_skip_list(skip_list):
    with open(SKIP_LIST_PATH, 'w', encoding='utf-8') as f:
        json.dump(skip_list, f, indent=2, ensure_ascii=False)

def load_ingest_history():
    if os.path.exists(INGEST_HISTORY_PATH):
        with open(INGEST_HISTORY_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_ingest_report(report):
    history = load_ingest_history()
    history.append(report)
    history = history[-200:]
    with open(INGEST_HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def fetch_playlist_videos(full_scan=False):
    if full_scan:
        try:
            import yt_dlp
            logger.info("Full scan: using yt-dlp to extract entire playlist...")
            ydl_opts = {'extract_flat': True, 'quiet': True, 'no_warnings': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f'https://youtube.com/playlist?list={PLAYLIST_ID}', download=False)
                entries = info.get('entries', [])
            unique = {}
            for e in entries:
                if not e:
                    continue
                vid = e.get('id', '')
                title = e.get('title', '') or ''
                if vid and title and vid not in unique:
                    unique[vid] = title
            logger.info(f"Full scan: found {len(unique)} videos in playlist")
            return unique
        except ImportError:
            logger.warning("yt-dlp not installed, falling back to page scrape")
        except Exception as e:
            logger.warning(f"yt-dlp extraction failed: {e}, falling back to page scrape")

    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    url = 'https://www.youtube.com/playlist?list=' + PLAYLIST_ID
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    pairs = re.findall(
        r'"title":\{"runs":\[\{"text":"([^"]+)"\}\].*?"videoId":"([a-zA-Z0-9_-]{11})"',
        r.text
    )
    unique = {}
    for raw_title, vid in pairs:
        if vid not in unique:
            decoded = html.unescape(raw_title.encode().decode('unicode_escape', errors='replace'))
            unique[vid] = decoded
    logger.info(f"Fetched {len(unique)} videos from playlist (first page)")
    return unique

def _fetch_transcript_page_scrape(video_id):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    page_url = f'https://www.youtube.com/watch?v={video_id}'
    r = requests.get(page_url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None

    match = re.search(r'"captionTracks":\s*(\[.*?\])', r.text)
    if not match:
        return None

    tracks_str = match.group(1).replace('\\u0026', '&')
    try:
        tracks = json.loads(tracks_str)
    except json.JSONDecodeError:
        return None

    en_track = None
    for t in tracks:
        if t.get('languageCode') == 'en':
            en_track = t
            break
    if not en_track and tracks:
        en_track = tracks[0]
    if not en_track:
        return None

    base_url = en_track.get('baseUrl', '')
    if not base_url:
        return None

    sub_url = base_url + '&fmt=json3'
    sr = requests.get(sub_url, headers=headers, timeout=30)
    if sr.status_code != 200:
        return None

    data = sr.json()
    events = data.get('events', [])
    segments = []
    for e in events:
        segs = e.get('segs', [])
        if not segs:
            continue
        text = ' '.join(s.get('utf8', '') for s in segs).strip()
        if not text or SKIP_PATTERNS.match(text):
            continue
        start_ms = e.get('tStartMs', 0)
        segments.append({
            'text': text,
            'start_ms': int(start_ms),
            'start_sec': float(start_ms) / 1000.0
        })
    return segments if segments else None

def fetch_transcript(video_id, retries=2, delay=15):
    from youtube_transcript_api import YouTubeTranscriptApi
    for attempt in range(retries + 1):
        try:
            ytt = YouTubeTranscriptApi()
            transcript = ytt.fetch(video_id, languages=['en'])
            snippets = list(transcript)
            segments = []
            for s in snippets:
                text = s.text.strip()
                if not text or SKIP_PATTERNS.match(text):
                    continue
                segments.append({
                    'text': text,
                    'start_ms': int(s.start * 1000),
                    'start_sec': float(s.start)
                })
            return segments
        except Exception as e:
            err_str = str(e)
            if 'disabled' in err_str.lower():
                logger.warning(f"  Subtitles disabled for {video_id}")
                return None
            if 'blocking' in err_str.lower() or 'ip' in err_str.lower():
                if attempt < retries:
                    wait = delay * (attempt + 1)
                    logger.info(f"  API rate limited on {video_id}, trying fallback after {wait}s...")
                    time.sleep(wait)
                    continue
                logger.info(f"  API blocked, trying page scrape fallback for {video_id}...")
                result = _fetch_transcript_page_scrape(video_id)
                if result:
                    logger.info(f"  Page scrape fallback succeeded for {video_id}")
                    return result
                logger.warning(f"  Both methods failed for {video_id} (IP rate limited)")
                return 'RATE_LIMITED'
            logger.warning(f"Could not fetch transcript for {video_id}: {e}")
            return None
    return None

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
    is_apb = bool(re.search(r'ask pastor bob', title, re.IGNORECASE))
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

def get_existing_video_ids(collection):
    try:
        count = collection.count()
        if count == 0:
            return set()
        all_ids = set()
        offset = 0
        page_size = 200
        while offset < count:
            batch = collection.get(limit=page_size, offset=offset, include=['metadatas'])
            for meta in batch['metadatas']:
                vid = meta.get('video_id', '')
                if vid:
                    all_ids.add(vid)
            if len(batch['ids']) < page_size:
                break
            offset += page_size
        logger.info(f"Found {len(all_ids)} unique video IDs already in ChromaDB")
        return all_ids
    except Exception as e:
        logger.warning(f"Could not fetch existing video IDs from collection: {e}")
        return set()

def ingest_new_sermons(dry_run=False, full_scan=False):
    logger.info("=== Auto-Ingest: Checking for new sermons ===")

    title_map = load_title_map()
    skip_list = load_skip_list()
    playlist_videos = fetch_playlist_videos(full_scan=full_scan)

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={'description': 'Pastor Bob sermons - enriched with titles + timestamps', 'hnsw:space': 'cosine'}
    )
    db_video_ids = get_existing_video_ids(collection)

    synced = 0
    for vid in db_video_ids:
        if vid and vid not in title_map:
            title_map[vid] = playlist_videos.get(vid, 'Sermon')
            synced += 1
    if synced > 0:
        save_title_map(title_map)
        logger.info(f"Synced {synced} video IDs from ChromaDB into title_map")

    new_videos = {}
    for vid, title in playlist_videos.items():
        if vid not in title_map and vid not in skip_list and vid not in db_video_ids:
            new_videos[vid] = title

    if not new_videos:
        logger.info("No new sermons found. Database is up to date.")
        import datetime
        report = {
            'timestamp': int(time.time() * 1000),
            'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'up_to_date',
            'sermons_ingested': [],
            'sermons_skipped': 0,
            'total_chunks_added': 0,
            'collection_total': None,
            'playlist_total': len(playlist_videos),
            'trigger': 'scheduled' if 'AUTO_INGEST' in os.environ else 'manual'
        }
        try:
            save_ingest_report(report)
        except Exception:
            pass
        return 0

    logger.info(f"Found {len(new_videos)} new sermons to ingest:")
    for vid, title in new_videos.items():
        logger.info(f"  {vid}: {title}")

    if dry_run:
        logger.info("DRY RUN — not uploading.")
        return len(new_videos)

    model = get_embedder()
    existing_count = collection.count()
    logger.info(f"Collection '{COLLECTION_NAME}' has {existing_count} existing chunks")

    total_new_chunks = 0
    successfully_ingested = []

    processed_count = 0
    for vid, title in new_videos.items():
        if processed_count > 0:
            wait = 30 if processed_count % 5 != 0 else 120
            logger.info(f"  Pausing {wait}s to avoid rate limits ({processed_count} processed so far)...")
            time.sleep(wait)

        logger.info(f"Processing: {title} ({vid})")

        segments = fetch_transcript(vid)
        if segments == 'RATE_LIMITED':
            logger.warning(f"  YouTube rate limit hit. Saving progress and stopping.")
            break
        if not segments:
            logger.warning(f"  Skipping {vid} — no transcript available")
            skip_list[vid] = title
            save_skip_list(skip_list)
            processed_count += 1
            continue

        logger.info(f"  Got {len(segments)} transcript segments")

        youtube_url = f'https://www.youtube.com/watch?v={vid}'
        chunks = chunk_segments(segments, vid, youtube_url, title)

        if not chunks:
            logger.warning(f"  Skipping {vid} — no chunks after processing")
            continue

        logger.info(f"  Created {len(chunks)} chunks, embedding...")

        texts = [c['text'][:MAX_TEXT_LEN] for c in chunks]
        embs = encode_batch(model, texts)

        batch_ids = []
        batch_docs = []
        batch_metas = []
        batch_embs = []

        for j, c in enumerate(chunks):
            doc_id = hashlib.md5(f"{c['video_id']}_{c['start_sec']}_{j}".encode()).hexdigest()
            clip_url = f"https://www.youtube.com/watch?v={c['video_id']}&t={int(c['start_sec'])}s"

            batch_ids.append(doc_id)
            batch_docs.append(c['text'][:MAX_TEXT_LEN])
            batch_metas.append({
                'video_id': c['video_id'],
                'url': c['youtube_url'],
                'timestamped_url': clip_url,
                'title': title if title else 'Sermon',
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
            logger.info(f"  Uploaded {len(batch_ids)} chunks for {vid}")
            successfully_ingested.append((vid, title))
        except Exception as e:
            logger.error(f"  Failed to upload chunks for {vid}: {e}")
            processed_count += 1
            continue
        processed_count += 1

    if successfully_ingested:
        for vid, title in successfully_ingested:
            title_map[vid] = title
        save_title_map(title_map)
        logger.info(f"Updated video_title_map.json with {len(successfully_ingested)} new entries (total: {len(title_map)})")

    final_count = collection.count()
    logger.info(f"=== Ingest complete: {total_new_chunks} new chunks added. Collection now has {final_count} chunks ===")

    import datetime
    report = {
        'timestamp': int(time.time() * 1000),
        'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'success',
        'sermons_ingested': [{'video_id': v, 'title': t} for v, t in successfully_ingested],
        'sermons_skipped': len(skip_list) - len(load_skip_list()) + len([v for v in new_videos if v in skip_list]),
        'total_chunks_added': total_new_chunks,
        'collection_total': final_count,
        'playlist_total': len(playlist_videos),
        'trigger': 'scheduled' if 'AUTO_INGEST' in os.environ else 'manual'
    }
    try:
        save_ingest_report(report)
        logger.info(f"Ingest report saved to {INGEST_HISTORY_PATH}")
    except Exception as e:
        logger.warning(f"Failed to save ingest report: {e}")

    return len(successfully_ingested)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Auto-ingest new sermons from YouTube playlist into ChromaDB')
    parser.add_argument('--dry-run', action='store_true', help='List new sermons without uploading')
    parser.add_argument('--full-scan', action='store_true', help='Paginate through entire playlist (not just first page)')
    args = parser.parse_args()
    count = ingest_new_sermons(dry_run=args.dry_run, full_scan=args.full_scan)
    if count > 0:
        logger.info(f"Done! Ingested {count} new sermons.")
    else:
        logger.info("No new sermons to ingest.")
