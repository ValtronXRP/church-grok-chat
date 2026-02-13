#!/usr/bin/env python3
"""
Rebuild all Chroma collections with high-quality embeddings (all-mpnet-base-v2)
and add church website content.

Collections created:
  - sermon_segments_v2: 72K sermon chunks with mpnet embeddings
  - illustrations_v5: illustrations with mpnet embeddings
  - church_website: cc-ea.org website content
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

# ============================================
# SERMON PARSING (from build_ask_pastor_bob_db.py)
# ============================================
SKIP_PATTERNS = re.compile(
    r'^\[?(music|applause|laughter|silence|foreign)\]?$|'
    r'^[\s\n]*$|'
    r'^\[?\d+:\d+:\d+\]?$',
    re.IGNORECASE
)

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
    return {
        'video_id': video_id,
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}',
        'title': '',
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
        'title': item.get('title', ''),
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

# ============================================
# CHURCH WEBSITE CONTENT
# ============================================
CHURCH_WEBSITE_CONTENT = [
    {
        "page": "Homepage",
        "url": "https://www.cc-ea.org",
        "text": "Calvary Chapel East Anaheim (CCEA) is located at 5605 E. La Palma Ave, Anaheim, CA 92807. Phone: (714) 695-9650. Email: info@cc-ea.org. Office Hours: Tuesday-Friday, 9am-5pm. The church offers live streaming services on YouTube, a mobile app for iOS and Android, and various online resources."
    },
    {
        "page": "About Us",
        "url": "https://www.cc-ea.org/about",
        "text": "Calvary Chapel East Anaheim was founded in 1985 by Pastor Bob Kopeny in Placentia. It started with 15 Christians meeting in a living room. The church moved to East Anaheim in 2002. Currently has over 2,000 weekly service attendees. Their mission is to bring people to a saving knowledge of Jesus Christ and then to assist each one to grow to maturity as a healthy believer in Christ. Pastor Bob Kopeny was originally a full-time police officer before feeling called to church ministry. He leads the church with his wife Becky."
    },
    {
        "page": "Service Times",
        "url": "https://www.cc-ea.org/services",
        "text": "Calvary Chapel East Anaheim service times: Sunday at 9:00 AM, Sunday at 11:00 AM, Wednesday at 7:00 PM. Location: 5605 E. La Palma Ave, Anaheim, CA 92807. Services are also live streamed on YouTube."
    },
    {
        "page": "Statement of Faith",
        "url": "https://www.cc-ea.org/statement-of-faith",
        "text": "Calvary Chapel East Anaheim Statement of Faith: We believe in one triune God existing in three persons - Father, Son, and Holy Spirit. We believe Scripture is fully inspired and infallible - the Word of God is the foundation upon which this church operates. We believe in Jesus Christ's full deity and humanity, virgin birth, sinless life, atoning death, bodily resurrection, and second coming. We believe the Holy Spirit indwells every believer from the moment of salvation. We believe salvation is by grace through faith in Jesus Christ alone. We practice baptism by immersion and communion. We believe in a Pre-Tribulation Rapture of the church. We affirm marriage as between one man and one woman. We hold a pro-life position. Every pastor, pastoral assistant, board member, employee, member or volunteer shall affirm agreement with this Statement of Faith."
    },
    {
        "page": "Ministries",
        "url": "https://www.cc-ea.org/ministries",
        "text": "Calvary Chapel East Anaheim Ministries: Adventure Kids - children's ministry on Sunday mornings and Wednesday nights at CCEA Adventure Lodge. Youth Ministry - Jr. high and high school on Sunday mornings and Wednesday nights. Young Adults (Ignited) - ages 18-25 on Thursday nights in the high school room. Women's Ministry - Monday nights and Thursday mornings in CCEA Sanctuary. Men's Ministry - Friday mornings at Solid Rock Cafe. Spanish Ministry - Sunday mornings and Wednesday nights. Devoted Ministry - ages 25-45 on Tuesday nights. Additional ministries: Community Groups, DivorceCare, GriefShare, Homeschool, Royal Rangers, Prayer Team, Sports, Veterans Ministry, Widows Ministry."
    },
    {
        "page": "Missions",
        "url": "https://www.cc-ea.org/missions",
        "text": "Calvary Chapel East Anaheim Missions: The church believes in raising up and sending missionaries from their own body. Local reach teams include: Sazdonoff Family (Breakn Truth Ministries - using breakdancing to reach youth) and Connected Blessings (food distribution in downtown Anaheim). Long-term missionaries: Costa Rica (Victor and Nichol Mejia), Ireland (Tebbe family), Japan (Vicente and Janine Alvarado), Romania (Jejeran Family), Mission Aviation Fellowship. Short-term missions include regular trips to Mexico working with an orphanage, and opportunities in Cuba, Ireland, Costa Rica, and Romania."
    },
    {
        "page": "Registrations & Events",
        "url": "https://www.cc-ea.org/registrations",
        "text": "Calvary Chapel East Anaheim upcoming events and registrations: Ask Pastor Bob - Online Q&A series on YouTube. Galilean Wedding - February 6, 2026, 6-9 PM, $30 per person. History of Roman Catholicism Lecture - February 28th. Newcomers Dinner - March 15th at 1 PM. Church Camp 2026 at Sugar Pine Christian Camp. Youth Spring Retreat - March 27-29. Men's Breakfast with Mark Spence - February 28th. Prince of Egypt Crew Registration. Living Waters 20-week Group. School of Discipleship Winter Session. Reel Disciples Fishing Trip - February 7th. Alaska Cruise Prophecy Conference - July 3-10, 2026."
    },
    {
        "page": "Giving",
        "url": "https://www.cc-ea.org/give",
        "text": "Calvary Chapel East Anaheim giving options: By mail to 5605 E. La Palma Ave, Anaheim, CA 92807. Text to Give: text 'CCEA' to 77977. Online offerings for General Offering/Tithe at pushpay.com/g/cceastanaheim. Missions Giving at pushpay.com/g/cceamissions."
    },
    {
        "page": "Resources",
        "url": "https://www.cc-ea.org/resources",
        "text": "Calvary Chapel East Anaheim resources: Wedding Application, Church Directory, Prayer Request Form, Event Photos, Church Calendar, Pastor Bob's Resources, Sermon Outline, Calvary Chapel Magazine, Missions Newsletter, Worship Lyrics, Lust: Is There Victory resource, House of Refuge, CHEA (Christian Homeschool Education Association), Community Groups, Christian Understanding of Disability, Home Bible Studies, Homeschool Group, Crisis Pregnancy Resources."
    },
    {
        "page": "Contact",
        "url": "https://www.cc-ea.org/contact-us",
        "text": "Calvary Chapel East Anaheim contact information: Address: 5605 E. La Palma Ave, Anaheim, CA 92807. Phone: (714) 695-9650. Email: info@cc-ea.org. Office Hours: Tuesday-Friday, 9am-5pm. Social media: Facebook and Instagram. Contact form available on website."
    }
]

# ============================================
# MAIN REBUILD
# ============================================
def rebuild_sermons(client, model):
    print("\n" + "="*60)
    print("REBUILDING SERMON SEGMENTS WITH MPNET EMBEDDINGS")
    print("="*60)

    try:
        client.delete_collection('sermon_segments_v2')
        print("Deleted old sermon_segments_v2")
    except:
        pass

    collection = client.create_collection(
        name='sermon_segments_v2',
        metadata={'description': 'Pastor Bob sermons - mpnet embeddings', 'hnsw:space': 'cosine'}
    )
    print("Created sermon_segments_v2")

    sources = discover_all_sermons()
    print(f"Discovered {len(sources)} sermon sources")

    total_chunks = 0
    total_uploaded = 0
    batch_ids, batch_docs, batch_metas, batch_embs = [], [], [], []
    start_time = time.time()
    skipped_sources = 0

    for idx, (source_type, source_data) in enumerate(sources):
        try:
            if source_type == 'json3':
                sermon = parse_json3_file(source_data)
                if not sermon or not sermon['segments']:
                    skipped_sources += 1
                    continue
                chunks = chunk_segments(
                    sermon['segments'], sermon['video_id'],
                    sermon['youtube_url'], sermon['title'], sermon['source_file']
                )
                if not chunks:
                    skipped_sources += 1
                    continue

                texts = [c['text'][:MAX_TEXT_LEN] for c in chunks]
                embs = encode_batch(model, texts)

                for j, c in enumerate(chunks):
                    doc_id = hashlib.md5(f"{c['video_id']}_{c['start_sec']}_{total_chunks}".encode()).hexdigest()
                    clip_url = f"https://www.youtube.com/watch?v={c['video_id']}&t={int(c['start_sec'])}s"
                    batch_ids.append(doc_id)
                    batch_docs.append(c['text'][:MAX_TEXT_LEN])
                    batch_metas.append({
                        'video_id': c['video_id'],
                        'url': c['youtube_url'],
                        'timestamped_url': clip_url,
                        'title': c.get('title', '') or 'Sermon',
                        'start_time': f"{c['start_sec'] // 60:.0f}:{c['start_sec'] % 60:02.0f}",
                        'start_sec': c['start_sec'],
                        'end_sec': c['end_sec'],
                        'word_count': c['word_count'],
                    })
                    batch_embs.append(embs[j].tolist())
                    total_chunks += 1

            elif source_type == 'batch_file':
                with open(source_data, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                for item in items:
                    sermon = parse_batch_sermon(item, os.path.basename(source_data))
                    if not sermon or not sermon['segments']:
                        continue
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
                        batch_ids.append(doc_id)
                        batch_docs.append(c['text'][:MAX_TEXT_LEN])
                        batch_metas.append({
                            'video_id': c['video_id'],
                            'url': c['youtube_url'],
                            'timestamped_url': clip_url,
                            'title': c.get('title', '') or 'Sermon',
                            'start_time': f"{c['start_sec'] // 60:.0f}:{c['start_sec'] % 60:02.0f}",
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

        if (idx + 1) % 25 == 0:
            elapsed = time.time() - start_time
            print(f"  [{idx+1}/{len(sources)}] {total_chunks} chunks, {total_uploaded} uploaded, {elapsed:.0f}s")
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
    print(f"\nSERMONS DONE: {total_chunks} chunks, {total_uploaded} uploaded, {count} in collection, {elapsed:.0f}s")
    print(f"Skipped {skipped_sources} empty sources")
    return count


def rebuild_illustrations(client, model):
    print("\n" + "="*60)
    print("REBUILDING ILLUSTRATIONS WITH MPNET EMBEDDINGS")
    print("="*60)

    ill_files = [
        './illustrations_v4_all.json',
        './illustrations_v2/illustrations_v2.json',
        './illustrations/illustrations.json',
    ]
    illustrations = []
    for f in ill_files:
        if os.path.exists(f):
            try:
                with open(f, 'r') as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        illustrations.extend(data)
                        print(f"Loaded {len(data)} illustrations from {f}")
            except Exception as e:
                print(f"Error loading {f}: {e}")

    if not illustrations:
        print("No illustrations found, skipping")
        return 0

    try:
        client.delete_collection('illustrations_v5')
        print("Deleted old illustrations_v5")
    except:
        pass

    collection = client.create_collection(
        name='illustrations_v5',
        metadata={'description': 'Pastor Bob illustrations - mpnet embeddings', 'hnsw:space': 'cosine'}
    )

    ids, docs, metas, embs_list = [], [], [], []
    texts_to_embed = []
    for i, ill in enumerate(illustrations):
        full_text = ill.get('full_text', ill.get('text', ''))
        if not full_text:
            continue
        doc_id = hashlib.md5(f"{ill.get('video_id', '')}_{ill.get('illustration_timestamp', '')}_{i}".encode()).hexdigest()
        ids.append(doc_id)
        docs.append(full_text[:MAX_TEXT_LEN])
        texts_to_embed.append(full_text[:MAX_TEXT_LEN])
        metas.append({
            'type': ill.get('type', ''),
            'summary': ill.get('summary', ''),
            'topics': ','.join(ill.get('topics', [])) if isinstance(ill.get('topics'), list) else str(ill.get('topics', '')),
            'emotional_tone': ill.get('emotional_tone', ''),
            'timestamp': ill.get('illustration_timestamp', ''),
            'youtube_url': ill.get('youtube_url', ''),
            'video_id': ill.get('video_id', '')
        })

    if not ids:
        print("No valid illustrations to embed")
        return 0

    embs = encode_batch(model, texts_to_embed)
    embs_list = embs.tolist()

    for s in range(0, len(ids), 200):
        e = min(s + 200, len(ids))
        collection.add(
            ids=ids[s:e],
            documents=docs[s:e],
            metadatas=metas[s:e],
            embeddings=embs_list[s:e]
        )

    count = collection.count()
    print(f"ILLUSTRATIONS DONE: {len(ids)} uploaded, {count} in collection")
    return count


def rebuild_website(client, model):
    print("\n" + "="*60)
    print("BUILDING CHURCH WEBSITE COLLECTION")
    print("="*60)

    try:
        client.delete_collection('church_website')
        print("Deleted old church_website")
    except:
        pass

    collection = client.create_collection(
        name='church_website',
        metadata={'description': 'cc-ea.org website content - mpnet embeddings', 'hnsw:space': 'cosine'}
    )

    ids, docs, metas, embs_list = [], [], [], []
    texts_to_embed = []

    for i, page in enumerate(CHURCH_WEBSITE_CONTENT):
        doc_id = hashlib.md5(f"website_{page['page']}_{i}".encode()).hexdigest()
        ids.append(doc_id)
        docs.append(page['text'])
        texts_to_embed.append(page['text'])
        metas.append({
            'page': page['page'],
            'url': page['url'],
            'source': 'cc-ea.org'
        })

    embs = encode_batch(model, texts_to_embed)
    embs_list = embs.tolist()

    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embs_list)
    count = collection.count()
    print(f"WEBSITE DONE: {len(ids)} pages, {count} in collection")
    return count


def test_queries(client, model):
    print("\n" + "="*60)
    print("TESTING QUERIES")
    print("="*60)

    sermon_col = client.get_collection('sermon_segments_v2')
    website_col = client.get_collection('church_website')

    queries = [
        "What does Pastor Bob teach about the baptism of the Holy Spirit?",
        "How was Pastor Bob saved?",
        "How did Bob meet Becky?",
        "What are the service times?",
        "How can I register for church camp?",
        "What does the Bible say about forgiveness?",
        "What is the church's statement of faith?",
        "How can I share my faith with someone?",
    ]

    for q in queries:
        emb = model.encode([q], normalize_embeddings=True).tolist()

        print(f"\nQ: {q}")

        results = sermon_col.query(query_embeddings=emb, n_results=3, include=['metadatas', 'documents', 'distances'])
        if results['ids'][0]:
            for i in range(len(results['ids'][0])):
                d = results['distances'][0][i]
                m = results['metadatas'][0][i]
                t = results['documents'][0][i][:100]
                print(f"  SERMON: dist={d:.3f} | {m.get('title','')} | {t}...")

        results = website_col.query(query_embeddings=emb, n_results=2, include=['metadatas', 'documents', 'distances'])
        if results['ids'][0]:
            for i in range(len(results['ids'][0])):
                d = results['distances'][0][i]
                m = results['metadatas'][0][i]
                t = results['documents'][0][i][:100]
                print(f"  WEBSITE: dist={d:.3f} | {m.get('page','')} | {t}...")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sermons', action='store_true', help='Rebuild sermon embeddings')
    parser.add_argument('--illustrations', action='store_true', help='Rebuild illustration embeddings')
    parser.add_argument('--website', action='store_true', help='Rebuild website content')
    parser.add_argument('--test', action='store_true', help='Test queries')
    parser.add_argument('--all', action='store_true', help='Rebuild everything')
    args = parser.parse_args()

    if not any([args.sermons, args.illustrations, args.website, args.test, args.all]):
        args.all = True

    client = get_client()
    model = get_embedder()

    if args.all or args.sermons:
        rebuild_sermons(client, model)
    if args.all or args.illustrations:
        rebuild_illustrations(client, model)
    if args.all or args.website:
        rebuild_website(client, model)
    if args.all or args.test:
        test_queries(client, model)

    print("\n" + "="*60)
    print("ALL DONE")
    print("="*60)
