#!/usr/bin/env python3
"""
Ask Pastor Bob — Vector Database Builder

Processes all sermon transcripts (json3 captions + batch JSON), filters non-teaching
content, chunks into semantically coherent segments, embeds with sentence-transformers,
and stores in FAISS for semantic search.

Usage:
    python build_ask_pastor_bob_db.py --limit 50    # test run on 50 files
    python build_ask_pastor_bob_db.py                # full run
    python build_ask_pastor_bob_db.py --query "How do I share my faith?"
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("apb")

SERMON_DIRS = [
    "/Users/valorkopeny/Desktop/Json3 sermons 1-3",
    "/Users/valorkopeny/Desktop/Jsons3 sermons 4",
]
BATCH_DIR = "/Users/valorkopeny/Desktop/SERMONS_ZIP_05"
VDB_PATH = "./ask-pastor-bob-vdb"
COLLECTION_NAME = "sermon_chunks"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_MIN_WORDS = 80
CHUNK_MAX_WORDS = 450
CHUNK_TARGET_WORDS = 250
CHECKPOINT_EVERY = 50

WORSHIP_KEYWORDS = re.compile(
    r"\b(welcome|good morning|good evening|let'?s stand|worship team|"
    r"announcements?|offering|next week|thank you for coming|"
    r"let'?s worship|sing with me|praise team|band|"
    r"sign up|event|potluck|ladies'? group|men'?s group|"
    r"youth group event|vacation bible school)\b",
    re.IGNORECASE,
)

TEACHING_STARTS = re.compile(
    r"\b(turn with me to|open your bibles?|today we'?re (going to |gonna )?look|"
    r"let'?s pray|father god|lord we|the lord spoke|"
    r"in (genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|"
    r"samuel|kings|chronicles|ezra|nehemiah|esther|job|psalms?|proverbs?|"
    r"ecclesiastes|song of solomon|isaiah|jeremiah|lamentations|ezekiel|daniel|"
    r"hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|"
    r"zechariah|malachi|matthew|mark|luke|john|acts|romans|corinthians|"
    r"galatians|ephesians|philippians|colossians|thessalonians|timothy|titus|"
    r"philemon|hebrews|james|peter|jude|revelation) \d)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

def parse_json3_file(filepath: str) -> Optional[dict]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning("Failed to parse %s: %s", filepath, e)
        return None

    video_id = Path(filepath).stem.replace(".en", "")
    events = data.get("events", [])
    segments = []
    for ev in events:
        if "segs" not in ev:
            continue
        start_ms = ev.get("tStartMs", 0)
        text_parts = []
        for seg in ev["segs"]:
            t = seg.get("utf8", "")
            if t:
                text_parts.append(t)
        text = "".join(text_parts).strip()
        if text and text not in ("[Music]", "[Applause]", "\n"):
            segments.append({
                "text": text,
                "start_sec": start_ms / 1000.0,
            })

    if not segments:
        return None

    return {
        "video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": "",
        "source_file": os.path.basename(filepath),
        "segments": segments,
    }


def parse_batch_sermon(item: dict, source_file: str) -> Optional[dict]:
    transcript = item.get("transcript", "")
    if not transcript or len(transcript) < 200:
        return None

    url = item.get("url", "")
    video_id_match = re.search(r"v=([a-zA-Z0-9_-]+)", url)
    video_id = video_id_match.group(1) if video_id_match else item.get("id", "").replace("youtube_", "")

    ts_pattern = re.compile(r"\[(\d+:\d{2}:\d{2})\]\s*")
    parts = ts_pattern.split(transcript)

    segments = []
    i = 1
    while i < len(parts) - 1:
        ts_str = parts[i]
        text = parts[i + 1].strip()
        h, m, s = ts_str.split(":")
        start_sec = int(h) * 3600 + int(m) * 60 + int(s)
        if text:
            segments.append({"text": text, "start_sec": float(start_sec)})
        i += 2

    if not segments and transcript:
        segments = [{"text": transcript, "start_sec": 0.0}]

    if not segments:
        return None

    return {
        "video_id": video_id,
        "youtube_url": url or f"https://www.youtube.com/watch?v={video_id}",
        "title": item.get("title", ""),
        "source_file": source_file,
        "segments": segments,
    }


def discover_sermons(limit: Optional[int] = None) -> list:
    all_sermons = []

    for dir_path in SERMON_DIRS:
        if not os.path.exists(dir_path):
            log.warning("Directory not found: %s", dir_path)
            continue
        files = sorted(Path(dir_path).glob("*.json3"))
        for fp in files:
            all_sermons.append(("json3", str(fp)))

    if os.path.exists(BATCH_DIR):
        for bp in sorted(Path(BATCH_DIR).glob("*.json")):
            all_sermons.append(("batch_file", str(bp)))

    log.info("Discovered %d total sermon sources", len(all_sermons))

    if limit:
        all_sermons = all_sermons[:limit]
        log.info("Limited to %d sermons (test mode)", limit)

    return all_sermons


# ---------------------------------------------------------------------------
# FILTERING
# ---------------------------------------------------------------------------

def is_worship_or_announcement(text: str) -> bool:
    if len(text.split()) < 3:
        return True
    return bool(WORSHIP_KEYWORDS.search(text))


def find_teaching_start(segments: list) -> int:
    for i, seg in enumerate(segments):
        if TEACHING_STARTS.search(seg["text"]):
            return max(0, i - 1)
        if seg["start_sec"] > 120 and len(seg["text"].split()) > 15:
            if not is_worship_or_announcement(seg["text"]):
                return i
    for i, seg in enumerate(segments):
        if seg["start_sec"] > 60 and len(seg["text"].split()) > 10:
            return i
    return 0


def filter_segments(segments: list) -> list:
    if not segments:
        return []

    start_idx = find_teaching_start(segments)
    teaching = segments[start_idx:]

    end_idx = len(teaching)
    for i in range(len(teaching) - 1, max(len(teaching) - 20, -1), -1):
        text_lower = teaching[i]["text"].lower()
        if any(kw in text_lower for kw in [
            "thank you for coming", "dismissed", "have a great week",
            "see you next", "god bless you all", "let's close in prayer"
        ]):
            end_idx = i
            break

    filtered = []
    for seg in teaching[:end_idx]:
        if seg["text"].strip() in ("[Music]", "[Applause]"):
            continue
        if is_worship_or_announcement(seg["text"]) and len(seg["text"].split()) < 12:
            continue
        filtered.append(seg)

    return filtered


# ---------------------------------------------------------------------------
# CHUNKING
# ---------------------------------------------------------------------------

def chunk_segments(segments: list, video_id: str, youtube_url: str,
                   title: str, source_file: str) -> list:
    if not segments:
        return []

    total_words = sum(len(s["text"].split()) for s in segments)
    if total_words < CHUNK_MIN_WORDS:
        return []

    chunks = []
    current_texts = []
    current_words = 0
    chunk_start_sec = segments[0]["start_sec"]
    chunk_end_sec = segments[0]["start_sec"]

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        wc = len(text.split())

        if current_words + wc > CHUNK_MAX_WORDS and current_words >= CHUNK_MIN_WORDS:
            chunk_text = " ".join(current_texts)
            chunks.append({
                "text": chunk_text,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "title": title,
                "source_file": source_file,
                "start_sec": int(chunk_start_sec),
                "end_sec": int(chunk_end_sec),
                "word_count": current_words,
            })
            current_texts = []
            current_words = 0
            chunk_start_sec = seg["start_sec"]

        current_texts.append(text)
        current_words += wc
        chunk_end_sec = seg["start_sec"]

    if current_texts and current_words >= CHUNK_MIN_WORDS // 2:
        chunk_text = " ".join(current_texts)
        chunks.append({
            "text": chunk_text,
            "video_id": video_id,
            "youtube_url": youtube_url,
            "title": title,
            "source_file": source_file,
            "start_sec": int(chunk_start_sec),
            "end_sec": int(chunk_end_sec),
            "word_count": current_words,
        })

    return chunks


def _char_to_time(char_pos: int, time_map: list) -> float:
    if not time_map:
        return 0.0
    for tm in time_map:
        if tm["char_start"] <= char_pos <= tm["char_end"]:
            return tm["start_sec"]
    if char_pos <= time_map[0]["char_start"]:
        return time_map[0]["start_sec"]
    return time_map[-1]["start_sec"]


# ---------------------------------------------------------------------------
# EMBEDDING + STORAGE
# ---------------------------------------------------------------------------

_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        import torch
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model: %s", EMBED_MODEL)
        _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
        log.info("Model loaded (CPU)")
    return _embedder


class FaissStore:
    def __init__(self, path: str, dim: int = 384, reset: bool = False):
        import faiss as _faiss
        self._faiss = _faiss
        self.path = path
        self.index_file = os.path.join(path, "index.faiss")
        self.meta_file = os.path.join(path, "metadata.json")
        self.dim = dim
        self.documents = []
        self.metadatas = []

        os.makedirs(path, exist_ok=True)

        if not reset and os.path.exists(self.index_file) and os.path.exists(self.meta_file):
            self.index = _faiss.read_index(self.index_file)
            with open(self.meta_file, "r") as f:
                saved = json.load(f)
            self.documents = saved.get("documents", [])
            self.metadatas = saved.get("metadatas", [])
            log.info("Loaded FAISS index: %d vectors", self.index.ntotal)
        else:
            self.index = _faiss.IndexFlatIP(dim)
            log.info("Created new FAISS index (dim=%d)", dim)

    def add(self, embeddings: np.ndarray, documents: list, metadatas: list):
        self.index.add(embeddings.astype(np.float32))
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def save(self):
        self._faiss.write_index(self.index, self.index_file)
        with open(self.meta_file, "w") as f:
            json.dump({"documents": self.documents, "metadatas": self.metadatas}, f)
        log.info("Saved FAISS index: %d vectors", self.index.ntotal)

    def count(self):
        return self.index.ntotal

    def search(self, query_embedding: np.ndarray, k: int = 20):
        scores, indices = self.index.search(query_embedding.reshape(1, -1).astype(np.float32), k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.documents):
                continue
            results.append({
                "document": self.documents[idx],
                "metadata": self.metadatas[idx],
                "score": float(score),
            })
        return results


def init_store(reset: bool = False):
    embedder = get_embedder()
    dim = embedder.get_sentence_embedding_dimension()
    store = FaissStore(VDB_PATH, dim=dim, reset=reset)
    return store


def embed_and_store(chunks: list, store: FaissStore, batch_size: int = 8):
    if not chunks:
        return 0

    embedder = get_embedder()
    texts = [c["text"][:1500] for c in chunks]
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        emb = embedder.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        all_embeddings.append(emb)
    embeddings = np.vstack(all_embeddings)

    documents = []
    metadatas = []

    for chunk in chunks:
        clip_url = f"https://www.youtube.com/watch?v={chunk['video_id']}&t={chunk['start_sec']}s"
        documents.append(chunk["text"])
        metadatas.append({
            "video_id": chunk["video_id"],
            "youtube_url": chunk["youtube_url"],
            "clip_url": clip_url,
            "title": chunk.get("title", ""),
            "source_file": chunk["source_file"],
            "start_sec": chunk["start_sec"],
            "end_sec": chunk["end_sec"],
            "word_count": chunk["word_count"],
        })

    store.add(np.array(embeddings), documents, metadatas)
    return len(documents)


# ---------------------------------------------------------------------------
# RETRIEVAL + RE-RANKING
# ---------------------------------------------------------------------------

SYNONYM_MAP = {
    "share": ["witness", "evangelize", "tell others", "testimony", "gospel"],
    "forgive": ["forgiveness", "pardon", "let go", "reconcile", "mercy"],
    "church": ["fellowship", "congregation", "body of christ", "gathering"],
    "afraid": ["fear", "scared", "anxious", "worry", "terrified"],
    "death": ["dying", "die", "funeral", "heaven", "eternity", "afterlife"],
    "marriage": ["husband", "wife", "spouse", "wedding", "divorce"],
    "pray": ["prayer", "praying", "intercede", "petition"],
    "sin": ["sinful", "transgression", "iniquity", "disobedience", "temptation"],
    "faith": ["believe", "trust", "confidence", "assurance"],
    "hope": ["hopeful", "hopeless", "despair", "encouragement"],
    "love": ["loving", "compassion", "charity", "kindness"],
    "false": ["deception", "deceive", "counterfeit", "heresy", "heretical"],
    "teach": ["teaching", "doctrine", "instruct", "lesson"],
    "suffer": ["suffering", "pain", "trial", "tribulation", "hardship"],
    "salvation": ["saved", "born again", "redemption", "redeemed"],
    "baptism": ["baptize", "baptized", "water baptism"],
    "money": ["finances", "tithe", "tithing", "giving", "stewardship"],
    "anxiety": ["anxious", "worry", "worried", "stress", "fear"],
    "grief": ["grieving", "loss", "mourning", "bereavement", "sorrow"],
    "children": ["kids", "parenting", "raising", "family"],
}


def expand_query_keywords(query: str) -> list:
    words = re.findall(r"\b[a-z]+\b", query.lower())
    stop = {"what", "does", "how", "can", "the", "and", "for", "with", "that",
            "this", "from", "have", "more", "when", "why", "who", "about",
            "pastor", "bob", "say", "says", "tell", "bible", "according"}
    keywords = [w for w in words if w not in stop and len(w) > 2]
    expanded = set(keywords)
    for kw in keywords:
        if kw in SYNONYM_MAP:
            expanded.update(SYNONYM_MAP[kw])
        for root, syns in SYNONYM_MAP.items():
            if kw.startswith(root[:4]) or root.startswith(kw[:4]):
                expanded.add(root)
                expanded.update(syns)
    return list(expanded)


def keyword_boost(text: str, keywords: list) -> float:
    text_lower = text.lower()
    matches = sum(1 for kw in keywords if kw in text_lower)
    if not keywords:
        return 0.0
    return min(matches / max(len(keywords), 1), 1.0)


def answer_question(query: str, top_k: int = 5, threshold: float = 0.68,
                    store: FaissStore = None) -> dict:
    if store is None:
        embedder = get_embedder()
        store = FaissStore(VDB_PATH, dim=embedder.get_sentence_embedding_dimension())

    embedder = get_embedder()
    q_emb = embedder.encode([query], normalize_embeddings=True)

    fetch_k = max(top_k * 4, 20)
    results = store.search(q_emb, k=fetch_k)

    if not results:
        return {"answer_text": "No relevant teachings found.", "clips": []}

    keywords = expand_query_keywords(query)
    candidates = []

    for r in results:
        doc = r["document"]
        meta = r["metadata"]
        cosine_sim = r["score"]

        kw_score = keyword_boost(doc, keywords)
        meta_kw = keyword_boost(
            " ".join([meta.get("title", ""), doc[:200]]),
            keywords
        )
        composite = 0.75 * cosine_sim + 0.15 * kw_score + 0.10 * meta_kw

        if composite >= threshold:
            candidates.append({
                "text": doc,
                "meta": meta,
                "score": round(composite, 4),
                "cosine_sim": round(cosine_sim, 4),
            })

    candidates.sort(key=lambda x: -x["score"])
    top = candidates[:top_k]

    clips = []
    for c in top:
        m = c["meta"]
        clips.append({
            "url": m.get("clip_url", m.get("youtube_url", "")),
            "start": m.get("start_sec", 0),
            "end": m.get("end_sec", None),
            "snippet": c["text"][:250],
            "title": m.get("title", ""),
            "score": c["score"],
        })

    if clips:
        answer_text = (
            f"Based on Pastor Bob's sermons, here are {len(clips)} relevant "
            f"teaching segment(s) on this topic. "
            f"The strongest match (score {clips[0]['score']:.2f}) is from "
            f"\"{clips[0]['title'] or 'a sermon'}\"."
        )
    else:
        answer_text = "I don't have a specific teaching from Pastor Bob on that exact topic."

    return {"answer_text": answer_text, "clips": clips}


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def build_database(limit: Optional[int] = None, reset: bool = False):
    sermons = discover_sermons(limit)
    store = init_store(reset)

    existing = store.count()
    if existing > 0 and not reset:
        log.info("Store already has %d vectors. Use --reset to rebuild.", existing)

    total_chunks = 0
    total_skipped = 0
    processed = 0
    sample_chunks = []

    expanded = []
    for source_type, source_data in sermons:
        if source_type == "batch_file":
            try:
                items = json.load(open(source_data, "r", encoding="utf-8"))
                for item in items:
                    expanded.append(("batch", (item, os.path.basename(source_data))))
            except Exception as e:
                log.warning("Failed to load %s: %s", source_data, e)
        else:
            expanded.append((source_type, source_data))

    log.info("Expanded to %d individual sermons", len(expanded))

    pbar = tqdm(expanded, desc="Processing sermons", unit="sermon")
    for source_type, source_data in pbar:
        try:
            if source_type == "json3":
                sermon = parse_json3_file(source_data)
            else:
                item, batch_file = source_data
                sermon = parse_batch_sermon(item, batch_file)

            if not sermon:
                total_skipped += 1
                continue

            filtered = filter_segments(sermon["segments"])
            if not filtered:
                total_skipped += 1
                continue

            chunks = chunk_segments(
                filtered,
                sermon["video_id"],
                sermon["youtube_url"],
                sermon["title"],
                sermon["source_file"],
            )

            if chunks:
                stored = embed_and_store(chunks, store)
                total_chunks += stored
                if len(sample_chunks) < 10:
                    sample_chunks.append(chunks[0])

            processed += 1
            pbar.set_postfix(chunks=total_chunks, skip=total_skipped)

            if processed % CHECKPOINT_EVERY == 0:
                store.save()
                log.info("Checkpoint: %d sermons, %d chunks, %d skipped",
                         processed, total_chunks, total_skipped)

        except Exception as e:
            log.warning("Error processing sermon: %s", e)
            total_skipped += 1

    log.info("=" * 60)
    store.save()
    log.info("DONE: %d sermons processed, %d chunks stored, %d skipped",
             processed, total_chunks, total_skipped)
    log.info("Store total: %d vectors", store.count())

    if sample_chunks:
        log.info("\n--- SAMPLE CHUNKS (first %d) ---", len(sample_chunks))
        for i, sc in enumerate(sample_chunks):
            log.info(
                "\n[Sample %d] video=%s start=%ds words=%d\n  %s",
                i + 1, sc["video_id"], sc["start_sec"], sc["word_count"],
                sc["text"][:300] + "..."
            )

    return total_chunks


def main():
    parser = argparse.ArgumentParser(description="Build Ask Pastor Bob vector database")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only N sermons (test mode)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete and rebuild the collection from scratch")
    parser.add_argument("--query", type=str, default=None,
                        help="Run a search query against the database")
    parser.add_argument("--top-k", type=int, default=5,
                        help="Number of results to return for queries")
    parser.add_argument("--threshold", type=float, default=0.40,
                        help="Minimum composite score threshold")
    args = parser.parse_args()

    if args.query:
        log.info("Querying: %s", args.query)
        result = answer_question(args.query, top_k=args.top_k, threshold=args.threshold)
        print(f"\n{result['answer_text']}\n")
        for i, clip in enumerate(result["clips"]):
            print(f"  {i+1}. [score={clip['score']:.3f}] {clip['title'] or 'Sermon'}")
            print(f"     {clip['url']}")
            print(f"     \"{clip['snippet'][:150]}...\"")
            print()
        if not result["clips"]:
            print("  No results above threshold.")
        return

    start_time = time.time()
    total = build_database(limit=args.limit, reset=args.reset)
    elapsed = time.time() - start_time
    log.info("Total time: %.1f seconds (%.1f chunks/sec)", elapsed,
             total / max(elapsed, 1))


if __name__ == "__main__":
    main()
