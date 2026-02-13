import os
import json
import time
import tempfile
import requests
import chromadb
from dotenv import load_dotenv

load_dotenv()

MANAGEMENT_KEY = os.environ["XAI_MANAGEMENT_KEY"]
COLLECTION_ID = os.environ["XAI_COLLECTION_ID"]
CHROMA_API_KEY = os.environ["CHROMA_API_KEY"]
CHROMA_TENANT = os.environ["CHROMA_TENANT"]
CHROMA_DATABASE = os.environ["CHROMA_DATABASE"]

UPLOAD_URL = f"https://management-api.x.ai/v1/collections/{COLLECTION_ID}/documents"
HEADERS = {"Authorization": f"Bearer {MANAGEMENT_KEY}"}

BATCH_SIZE = 100
MAX_RETRIES = 3

def get_chroma_segments():
    client = chromadb.HttpClient(
        host="api.trychroma.com",
        port=443,
        ssl=True,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE,
        headers={"x-chroma-token": CHROMA_API_KEY}
    )
    collection = client.get_collection("sermon_segments_v2")
    total = collection.count()
    print(f"Total segments in ChromaDB: {total}")

    all_docs = []
    batch = 250
    for offset in range(0, total, batch):
        results = collection.get(
            offset=offset,
            limit=batch,
            include=["documents", "metadatas"]
        )
        for i, doc_id in enumerate(results["ids"]):
            text = results["documents"][i] if results["documents"] else ""
            meta = results["metadatas"][i] if results["metadatas"] else {}
            if text and len(text.strip()) > 50:
                all_docs.append({
                    "id": doc_id,
                    "text": text.strip(),
                    "title": meta.get("title", "Sermon"),
                    "video_id": meta.get("video_id", ""),
                    "start_time": str(meta.get("start_time", "")),
                    "url": meta.get("timestamped_url", meta.get("url", ""))
                })
        print(f"  Fetched {min(offset + batch, total)}/{total} from ChromaDB ({len(all_docs)} valid)")

    print(f"\nTotal valid segments to upload: {len(all_docs)}")
    return all_docs

def upload_segment(seg, idx):
    for attempt in range(MAX_RETRIES):
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(seg["text"])
                tmp_path = f.name

            fields_json = json.dumps({
                "title": seg["title"][:200],
                "video_id": seg["video_id"],
                "start_time": seg["start_time"],
                "url": seg["url"][:500]
            })

            with open(tmp_path, 'rb') as f:
                resp = requests.post(
                    UPLOAD_URL,
                    headers=HEADERS,
                    files={"data": (f"segment_{idx:06d}.txt", f, "text/plain")},
                    data={
                        "name": f"segment_{idx:06d}.txt",
                        "content_type": "text/plain",
                        "fields": fields_json
                    },
                    timeout=30
                )

            os.unlink(tmp_path)

            if resp.status_code == 200:
                return True
            elif resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  Upload error {resp.status_code}: {resp.text[:200]}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2)
        except Exception as e:
            print(f"  Exception: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
    return False

def main():
    print("=" * 60)
    print("Upload Pastor Bob Sermons to xAI Collection")
    print("=" * 60)
    print(f"Collection: {COLLECTION_ID}")
    print()

    segments = get_chroma_segments()
    if not segments:
        print("No segments found!")
        return

    uploaded = 0
    failed = 0
    start_time = time.time()

    for i, seg in enumerate(segments):
        if upload_segment(seg, i):
            uploaded += 1
        else:
            failed += 1

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed * 60
            eta = (len(segments) - i - 1) / (rate / 60) if rate > 0 else 0
            print(f"  [{i+1}/{len(segments)}] uploaded={uploaded} failed={failed} rate={rate:.0f}/min ETA={eta/60:.1f}min")

        if (i + 1) % 200 == 0:
            time.sleep(1)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"DONE: {uploaded} uploaded, {failed} failed in {elapsed/60:.1f} minutes")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
