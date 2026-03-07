#!/usr/bin/env python3
"""
Scrapes cc-ea.org public pages and upserts them into the 'church_website' ChromaDB collection.
Run manually or via cron/scheduler for hourly updates.

Usage:
  python3 scrape_church_website.py
"""

import os, sys, time, json, hashlib, re
import requests
from bs4 import BeautifulSoup
import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_API_KEY = os.environ.get('CHROMA_API_KEY', 'ck-Ci7fQVMx8Q6nENxr8daGNYYNj22wmTazd9hXkAPWNVPd')
CHROMA_TENANT = os.environ.get('CHROMA_TENANT', '4b12a7c7-2fb4-4edc-9b6e-c2a77305136b')
CHROMA_DATABASE = os.environ.get('CHROMA_DATABASE', 'APB')
EMBEDDING_MODEL = 'sentence-transformers/all-mpnet-base-v2'

PAGES = [
    ('/', 'Home'),
    ('/about-us', 'About Us'),
    ('/about-us/contact-us', 'Contact Us'),
    ('/about-us/statement-of-faith', 'Statement of Faith'),
    ('/give', 'Give'),
    ('/ministries-2', 'Ministries'),
    ('/missions', 'Missions'),
    ('/new-here', 'New Here'),
    ('/registrations', 'Registrations'),
    ('/resources', 'Resources'),
    ('/resources/christian-understanding-of-disability', 'Christian Understanding of Disability'),
    ('/resources/crisis-pregnancy', 'Crisis Pregnancy'),
    ('/resources/home-bible-studies', 'Home Bible Studies'),
    ('/resources/pastor-bob-s-resources', "Pastor Bob's Resources"),
    ('/resources/wedding-application2', 'Wedding Application'),
    ('/service-times-and-location', 'Service Times and Location'),
    ('/services', 'Services'),
    ('/services/live', 'Live Stream'),
    ('/volunteer', 'Volunteer'),
    ('/highlights', 'Highlights'),
]

EXTERNAL_PAGES = [
    ('https://www.cceacommunity.org/', 'Community Groups'),
    ('https://www.cceayouth.com', 'Youth Ministry'),
    ('https://www.cceaignited.com', 'Young Adults (Ignited)'),
    ('https://www.cceachildrens.com', "Children's Ministry (Adventure Kids)"),
    ('https://www.cceachildrens.com/childrens-church', "Children's Church - Sunday Mornings"),
    ('https://www.cceachildrens.com/level-up-wed-nights', "Level Up - Wednesday Nights (Kids)"),
    ('https://www.cceachildrens.com/royalrangersmpactgirls', "MPact Girls & Royal Rangers"),
    ('https://www.cceahomeschool.com', 'CCEA Homeschool Community'),
]

NAV_LINES = {
    'Home', 'About Us', 'Contact Us', 'Statement of Faith', 'Services', 'Live',
    'Recent Sermons', 'Older Sermon Archive', 'Ministries', 'Missions',
    'Registrations', 'Resources', 'COMMUNITY GROUPS', 'Calendar',
    'Christian Understanding of Disability', 'Event Photos', 'Home Bible Studies',
    "Pastor Bob's Resources", 'Prayer Request', 'Homeschool Group', 'Sermon Outline',
    'WORSHIP LYRICS', 'Crisis Pregnancy', 'Wedding Application', 'Give', 'Volunteer',
    'Follow Us', 'Subscribe', 'Church Calendar', "pastor bob's study tools",
    'prayer request', 'church information',
}

FOOTER_SEQUENCE = ['Follow Us', 'Subscribe']


SKIP_LINK_TEXTS = {
    'home', 'about us', 'contact us', 'statement of faith', 'services', 'live',
    'recent sermons', 'older sermon archive', 'ministries', 'missions',
    'registrations', 'resources', 'calendar', 'event photos', 'home bible studies',
    "pastor bob's resources", 'prayer request', 'homeschool group', 'sermon outline',
    'worship lyrics', 'crisis pregnancy', 'wedding application', 'give', 'volunteer',
    'follow us', 'subscribe', 'church calendar', "pastor bob's study tools",
    'prayer request', 'church information',
}

def _inline_links(soup):
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        if not text or len(text) < 3:
            continue
        if text.lower() in SKIP_LINK_TEXTS:
            continue
        if href.startswith('#') or href.startswith('javascript') or href.startswith('tel:'):
            continue
        if href.startswith('/'):
            href = f'https://cc-ea.org{href}'
        if href.startswith('http'):
            a.replace_with(f'[{text}]({href})')


def scrape_page(session, path, name):
    url = f'https://cc-ea.org{path}'
    r = session.get(url, timeout=15, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script', 'style', 'link', 'meta', 'noscript']):
        tag.decompose()
    _inline_links(soup)
    text = soup.get_text(separator='\n', strip=True)
    all_lines = []
    for l in text.split('\n'):
        l = l.strip()
        if l:
            all_lines.append(l)

    nav_end = 0
    for i, l in enumerate(all_lines):
        if l == 'Volunteer':
            nav_end = i + 1
            break

    footer_start = len(all_lines)
    for i in range(len(all_lines) - 1):
        if all_lines[i] == 'Follow Us' and i + 1 < len(all_lines) and all_lines[i + 1] == 'Subscribe':
            footer_start = i
            break

    body_lines = all_lines[nav_end:footer_start]

    lines = []
    seen = set()
    for l in body_lines:
        if len(l) < 3 or l in seen:
            continue
        if l.endswith('- ccea'):
            continue
        seen.add(l)
        lines.append(l)
    content = '\n'.join(lines)
    return {'page': name, 'url': url, 'path': path, 'content': content}


def scrape_external_page(session, url, name):
    r = session.get(url, timeout=15, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script', 'style', 'link', 'meta', 'noscript']):
        tag.decompose()
    _inline_links(soup)
    text = soup.get_text(separator='\n', strip=True)
    lines = []
    seen = set()
    for l in text.split('\n'):
        l = l.strip()
        if l and len(l) >= 3 and l not in seen:
            seen.add(l)
            lines.append(l)
    content = '\n'.join(lines[:100])
    return {'page': name, 'url': url, 'path': '', 'content': content}


def chunk_page(page_data, max_chunk=800):
    content = page_data['content']
    if len(content) <= max_chunk:
        return [page_data]
    
    paragraphs = content.split('\n')
    chunks = []
    current = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > max_chunk and current:
            chunks.append({
                'page': page_data['page'],
                'url': page_data['url'],
                'path': page_data['path'],
                'content': '\n'.join(current),
                'chunk_index': len(chunks),
            })
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        chunks.append({
            'page': page_data['page'],
            'url': page_data['url'],
            'path': page_data['path'],
            'content': '\n'.join(current),
            'chunk_index': len(chunks),
        })
    return chunks


def main():
    print("=== Church Website Scraper ===")
    print(f"Scraping {len(PAGES)} pages from cc-ea.org...")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.5',
    })

    all_chunks = []
    for i, (path, name) in enumerate(PAGES):
        if i > 0:
            time.sleep(2)
        try:
            page = scrape_page(session, path, name)
            chunks = chunk_page(page)
            all_chunks.extend(chunks)
            print(f"  OK: {name} -> {len(chunks)} chunk(s) ({len(page['content'])} chars)")
        except Exception as e:
            print(f"  FAIL: {name} - {e}")

    for ext_url, ext_name in EXTERNAL_PAGES:
        time.sleep(2)
        try:
            page = scrape_external_page(session, ext_url, ext_name)
            chunks = chunk_page(page)
            all_chunks.extend(chunks)
            print(f"  OK: {ext_name} (external) -> {len(chunks)} chunk(s) ({len(page['content'])} chars)")
        except Exception as e:
            print(f"  FAIL: {ext_name} (external) - {e}")

    print(f"\nTotal chunks: {len(all_chunks)}")
    if not all_chunks:
        print("No content scraped, exiting.")
        return

    print("\nLoading embedding model...")
    embedder = SentenceTransformer(EMBEDDING_MODEL, device='cpu')

    print("Connecting to Chroma Cloud...")
    client = chromadb.CloudClient(
        api_key=CHROMA_API_KEY,
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE
    )

    try:
        client.delete_collection('church_website')
        print("Deleted old church_website collection")
    except:
        pass

    collection = client.get_or_create_collection(
        name='church_website',
        metadata={'description': 'Calvary Chapel East Anaheim website pages from cc-ea.org'}
    )

    ids = []
    documents = []
    metadatas = []
    embeddings = []

    print("Generating embeddings...")
    for chunk in all_chunks:
        chunk_id = hashlib.md5(f"{chunk['url']}_{chunk.get('chunk_index', 0)}".encode()).hexdigest()
        ids.append(chunk_id)
        documents.append(chunk['content'])
        metadatas.append({
            'page': chunk['page'],
            'url': chunk['url'],
            'path': chunk['path'],
            'chunk_index': chunk.get('chunk_index', 0),
            'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        })

    embs = embedder.encode(documents, normalize_embeddings=True, show_progress_bar=True)
    embeddings = embs.tolist()

    print(f"Upserting {len(ids)} chunks to church_website collection...")
    batch_size = 50
    for i in range(0, len(ids), batch_size):
        batch_end = min(i + batch_size, len(ids))
        collection.upsert(
            ids=ids[i:batch_end],
            documents=documents[i:batch_end],
            metadatas=metadatas[i:batch_end],
            embeddings=embeddings[i:batch_end],
        )
        print(f"  Upserted batch {i//batch_size + 1} ({batch_end}/{len(ids)})")

    final_count = collection.count()
    print(f"\nDone! church_website collection now has {final_count} documents.")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == '__main__':
    main()
