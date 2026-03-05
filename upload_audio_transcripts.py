#!/usr/bin/env python3
"""
Upload 891 audio-only sermon transcripts to ChromaDB sermon_segments_v2.

These are pure prose transcripts from audio recordings (NOT YouTube videos).
They are added for agent context only — no video timestamps, no clip generation.

Metadata includes:
  - source_type: "audio_transcript" (distinguishes from video-sourced chunks)
  - video_id: "" (empty — no YouTube video)
  - title: Generated from filename (Bible book + chapter:verse range + date)
  - start_sec / end_sec / start_time: 0 (no timestamps)
  - timestamped_url / url: "" (no video URL)
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
BATCH_SIZE = 64
MAX_TEXT_LEN = 512
TARGET_WORDS = 400
MIN_CHUNK_WORDS = 50

TRANSCRIPT_DIR = '/Users/valorkopeny/Desktop/PastorBob_892_Transcripts'

BOOK_CODES = {
    'GEN': 'Genesis', 'EXO': 'Exodus', 'LEV': 'Leviticus', 'DEU': 'Deuteronomy',
    'JOE': 'Joel', 'JON': 'Jonah', 'JOB': 'Job', 'JOH': 'John',
    'JUE': 'Judges', 'JER': 'Jeremiah',
    'ACT': 'Acts', 'ROM': 'Romans', '1CO': '1 Corinthians', '2CO': '2 Corinthians',
    'GAL': 'Galatians', 'EPH': 'Ephesians', 'PHI': 'Philippians',
    'HEB': 'Hebrews', 'REV': 'Revelation', 'PSA': 'Psalms', 'PRO': 'Proverbs',
    'ECC': 'Ecclesiastes', 'SON': 'Song of Solomon', 'ISA': 'Isaiah',
    'EZE': 'Ezekiel', 'DAN': 'Daniel', 'HOS': 'Hosea', 'AMO': 'Amos',
    'OBA': 'Obadiah', 'MIC': 'Micah', 'NAH': 'Nahum', 'HAB': 'Habakkuk',
    'ZEP': 'Zephaniah', 'HAG': 'Haggai', 'ZEC': 'Zechariah', 'MAL': 'Malachi',
    'MAT': 'Matthew', 'LUK': 'Luke', 'LAM': 'Lamentations',
    '1PE': '1 Peter', '2PE': '2 Peter', '1JO': '1 John', '2JO': '2 John',
    '3JO': '3 John', '2TI': '2 Timothy', '1CH': '1 Chronicles', '2CH': '2 Chronicles',
    'TOP': 'Topical', 'XXX': 'Special Message',
}

def parse_filename(fname):
    base = fname.replace('.txt', '')
    parts = base.split('-')
    if len(parts) < 7:
        return {'title': 'Pastor Bob Sermon', 'date': '', 'book': '', 'ref': ''}

    date_str = parts[0]
    book_code = parts[2]
    book_name = BOOK_CODES.get(book_code, book_code)

    date_formatted = ''
    if date_str != '0000' and len(date_str) == 8:
        try:
            y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
            date_formatted = f"{m}/{d}/{y}"
        except:
            pass

    ch_start = parts[3] if len(parts) > 3 else ''
    vs_start = parts[4] if len(parts) > 4 else ''
    ch_end = parts[5] if len(parts) > 5 else ''
    vs_end = parts[6] if len(parts) > 6 else ''

    if book_code in ('XXX', 'TOP') or ch_start in ('XXX', 'xxx', '000'):
        ref = ''
        title = f"{book_name} ({date_formatted})" if date_formatted else book_name
    else:
        ch_s = ch_start.lstrip('0') or '0'
        vs_s = vs_start.lstrip('0') or '0'
        ch_e = ch_end.lstrip('0') or '0'
        vs_e = vs_end.lstrip('0') or '0'

        if vs_s.endswith('a') or vs_s.endswith('b'):
            vs_s_num = vs_s[:-1]
            vs_s_display = vs_s
        else:
            vs_s_num = vs_s
            vs_s_display = vs_s

        if vs_e.endswith('a') or vs_e.endswith('b'):
            vs_e_num = vs_e[:-1]
            vs_e_display = vs_e
        else:
            vs_e_num = vs_e
            vs_e_display = vs_e

        if ch_s == ch_e:
            ref = f"{ch_s}:{vs_s_display}-{vs_e_display}"
        else:
            ref = f"{ch_s}:{vs_s_display}-{ch_e}:{vs_e_display}"

        title = f"{book_name} {ref}"
        if date_formatted:
            title += f" ({date_formatted})"

    return {'title': title, 'date': date_formatted, 'book': book_name, 'ref': ref}

def chunk_text(text, target_words=TARGET_WORDS, min_words=MIN_CHUNK_WORDS):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = []
    current_count = 0

    for sent in sentences:
        words = sent.split()
        if not words:
            continue
        current.append(sent)
        current_count += len(words)

        if current_count >= target_words:
            chunks.append(' '.join(current))
            current = []
            current_count = 0

    if current and current_count >= min_words:
        chunks.append(' '.join(current))
    elif current and chunks:
        chunks[-1] += ' ' + ' '.join(current)

    return chunks

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

def process_transcripts(dry_run=False):
    files = sorted([
        f for f in os.listdir(TRANSCRIPT_DIR)
        if f.endswith('.txt') and os.path.getsize(os.path.join(TRANSCRIPT_DIR, f)) > 0
    ])
    print(f"Found {len(files)} non-empty transcript files")

    if not dry_run:
        client = get_client()
        collection = client.get_collection('sermon_segments_v2')
        existing_count = collection.count()
        print(f"Existing sermon_segments_v2 count: {existing_count}")
        model = get_embedder()

    total_chunks = 0
    skipped_files = 0
    all_batch_ids = []
    all_batch_docs = []
    all_batch_metas = []
    all_batch_embs = []
    start_time = time.time()

    for idx, fname in enumerate(files):
        filepath = os.path.join(TRANSCRIPT_DIR, fname)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read().strip()
        except UnicodeDecodeError:
            try:
                with open(filepath, 'r', encoding='latin-1') as f:
                    text = f.read().strip()
            except:
                skipped_files += 1
                continue

        if not text or len(text.split()) < 50:
            skipped_files += 1
            continue

        info = parse_filename(fname)
        chunks = chunk_text(text)

        if not chunks:
            skipped_files += 1
            continue

        if not dry_run:
            texts_to_embed = [c[:MAX_TEXT_LEN] for c in chunks]
            embs = encode_batch(model, texts_to_embed)

        for j, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"audio_{fname}_{j}".encode()).hexdigest()

            meta = {
                'video_id': '',
                'url': '',
                'timestamped_url': '',
                'title': info['title'],
                'start_time': '',
                'start_sec': 0.0,
                'end_sec': 0.0,
                'word_count': len(chunk.split()),
                'source_type': 'audio_transcript',
                'source_file': fname,
                'bible_book': info['book'],
                'sermon_date': info['date'],
            }

            all_batch_ids.append(doc_id)
            all_batch_docs.append(chunk[:MAX_TEXT_LEN])
            all_batch_metas.append(meta)
            if not dry_run:
                all_batch_embs.append(embs[j].tolist())
            total_chunks += 1

        if not dry_run and len(all_batch_ids) >= 200:
            collection.add(
                ids=all_batch_ids[:200],
                documents=all_batch_docs[:200],
                metadatas=all_batch_metas[:200],
                embeddings=all_batch_embs[:200]
            )
            all_batch_ids = all_batch_ids[200:]
            all_batch_docs = all_batch_docs[200:]
            all_batch_metas = all_batch_metas[200:]
            all_batch_embs = all_batch_embs[200:]
            print(f"  Uploaded batch, {total_chunks} chunks so far...")

        if (idx + 1) % 100 == 0:
            elapsed = time.time() - start_time
            print(f"  [{idx+1}/{len(files)}] {total_chunks} chunks, {elapsed:.0f}s")
            sys.stdout.flush()

    if not dry_run and all_batch_ids:
        while all_batch_ids:
            end = min(200, len(all_batch_ids))
            collection.add(
                ids=all_batch_ids[:end],
                documents=all_batch_docs[:end],
                metadatas=all_batch_metas[:end],
                embeddings=all_batch_embs[:end]
            )
            all_batch_ids = all_batch_ids[end:]
            all_batch_docs = all_batch_docs[end:]
            all_batch_metas = all_batch_metas[end:]
            all_batch_embs = all_batch_embs[end:]

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    if dry_run:
        print("DRY RUN COMPLETE")
    else:
        final_count = collection.count()
        print("UPLOAD COMPLETE")
        print(f"  Collection now has: {final_count} segments")
    print(f"  Files processed: {len(files) - skipped_files}")
    print(f"  Files skipped: {skipped_files}")
    print(f"  Total chunks created: {total_chunks}")
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"{'='*60}")

    return total_chunks

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Count chunks without uploading')
    args = parser.parse_args()

    process_transcripts(dry_run=args.dry_run)
