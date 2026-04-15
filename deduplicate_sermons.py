#!/usr/bin/env python3
"""
One-time (and ongoing) deduplication script for ChromaDB sermon_segments_v2 collection.

Identifies duplicate chunks by (video_id, start_sec) and removes all but one copy.
Uses the deterministic ID format: md5(f"{video_id}_{start_sec:.3f}") as the canonical ID.
If a canonical ID exists, non-canonical duplicates are deleted. If no canonical ID exists,
the lexicographically smallest existing ID is kept.

Usage:
    python deduplicate_sermons.py              # dry run — reports duplicates, no deletions
    python deduplicate_sermons.py --execute    # actually delete duplicates
    python deduplicate_sermons.py --collection illustrations_v5 --execute
"""

import os, sys, json, hashlib, logging, argparse
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import chromadb

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

CHROMA_API_KEY  = os.environ.get('CHROMA_API_KEY',  'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT   = os.environ.get('CHROMA_TENANT',   '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')
PAGE_SIZE = 1000
DELETE_BATCH = 100


def get_chroma_client():
    return chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )


def canonical_id(video_id: str, start_sec: float) -> str:
    return hashlib.md5(f"{video_id}_{start_sec:.3f}".encode()).hexdigest()


def deduplicate(collection_name: str, execute: bool = False):
    logger.info(f"=== Deduplication: {collection_name} ===")
    logger.info(f"Mode: {'EXECUTE (will delete)' if execute else 'DRY RUN (no deletions)'}")

    client = get_chroma_client()
    collection = client.get_collection(name=collection_name)
    total = collection.count()
    logger.info(f"Total records: {total}")

    # Fetch all records in pages
    # key = (video_id, start_sec_rounded_to_3dp)  value = list of doc IDs
    groups: dict[tuple, list[str]] = {}
    offset = 0
    fetched = 0

    while offset < total:
        batch = collection.get(
            limit=PAGE_SIZE,
            offset=offset,
            include=['metadatas']
        )
        ids = batch['ids']
        metas = batch['metadatas']
        for doc_id, meta in zip(ids, metas):
            vid = meta.get('video_id', '')
            start = meta.get('start_sec', None)
            if not vid or start is None:
                continue
            key = (vid, round(float(start), 3))
            groups.setdefault(key, []).append(doc_id)
        fetched += len(ids)
        offset += PAGE_SIZE
        logger.info(f"  Fetched {fetched}/{total} records...")
        if len(ids) < PAGE_SIZE:
            break

    # Find duplicates
    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    logger.info(f"Found {len(duplicate_groups)} (video_id, start_sec) keys with duplicates")

    ids_to_delete = []
    for (vid, start), doc_ids in duplicate_groups.items():
        # Prefer the canonical deterministic ID if it exists among duplicates
        canon = canonical_id(vid, start)
        if canon in doc_ids:
            keep = canon
        else:
            keep = sorted(doc_ids)[0]  # keep lexicographically smallest as tiebreaker
        to_remove = [d for d in doc_ids if d != keep]
        ids_to_delete.extend(to_remove)

    logger.info(f"Records to delete: {len(ids_to_delete)}")
    logger.info(f"Records to keep:   {total - len(ids_to_delete)}")

    if not ids_to_delete:
        logger.info("No duplicates found — collection is clean!")
        return 0

    if not execute:
        logger.info("DRY RUN complete. Re-run with --execute to delete duplicates.")
        # Show a sample
        sample = list(duplicate_groups.items())[:5]
        for (vid, start), doc_ids in sample:
            logger.info(f"  Example duplicate: video={vid} start={start}s → {len(doc_ids)} copies")
        return len(ids_to_delete)

    # Delete in batches
    deleted = 0
    for i in range(0, len(ids_to_delete), DELETE_BATCH):
        batch = ids_to_delete[i:i + DELETE_BATCH]
        collection.delete(ids=batch)
        deleted += len(batch)
        logger.info(f"  Deleted {deleted}/{len(ids_to_delete)}...")

    final_count = collection.count()
    logger.info(f"=== Done: deleted {deleted} duplicates. Collection now has {final_count} records ===")
    return deleted


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Deduplicate ChromaDB sermon chunks')
    parser.add_argument('--execute', action='store_true',
                        help='Actually delete duplicates (default is dry-run)')
    parser.add_argument('--collection', default='sermon_segments_v2',
                        help='Collection name (default: sermon_segments_v2)')
    args = parser.parse_args()

    count = deduplicate(args.collection, execute=args.execute)
    if not args.execute:
        print(f"\nDRY RUN: would remove {count} duplicate records.")
        print("Re-run with --execute to apply.")
    else:
        print(f"\nRemoved {count} duplicate records.")
