#!/usr/bin/env python3
"""Export ChromaDB data directly from SQLite to JSON"""

import sqlite3
import json
import struct

DB_PATH = './sermon_vector_db/chroma.sqlite3'
OUTPUT_FILE = './sermon_export.json'

def decode_embedding(blob):
    """Decode binary embedding to list of floats"""
    if not blob:
        return None
    n_floats = len(blob) // 4
    return list(struct.unpack(f'{n_floats}f', blob))

def export():
    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    
    print("Getting segments...")
    cursor = conn.execute("""
        SELECT 
            e.id,
            em.string_value as document,
            e.embedding
        FROM embeddings e
        LEFT JOIN embedding_metadata em ON e.id = em.id AND em.key = 'chroma:document'
        LIMIT 5
    """)
    
    sample = cursor.fetchall()
    print(f"Sample: {len(sample)} rows")
    for row in sample[:2]:
        print(f"  ID: {row[0][:20]}..., Doc length: {len(row[1]) if row[1] else 0}")
    
    print("\nExporting all data...")
    
    cursor = conn.execute("""
        SELECT 
            e.id,
            group_concat(CASE WHEN em.key = 'chroma:document' THEN em.string_value END) as document,
            e.embedding
        FROM embeddings e
        LEFT JOIN embedding_metadata em ON e.id = em.id
        GROUP BY e.id
    """)
    
    all_data = []
    count = 0
    
    for row in cursor:
        doc_id, document, embedding_blob = row
        
        metadata_cursor = conn.execute("""
            SELECT key, string_value, int_value, float_value
            FROM embedding_metadata
            WHERE id = ? AND key != 'chroma:document'
        """, (doc_id,))
        
        metadata = {}
        for meta_row in metadata_cursor:
            key, str_val, int_val, float_val = meta_row
            if str_val is not None:
                metadata[key] = str_val
            elif int_val is not None:
                metadata[key] = int_val
            elif float_val is not None:
                metadata[key] = float_val
        
        all_data.append({
            'id': doc_id,
            'document': document,
            'metadata': metadata,
            'embedding': decode_embedding(embedding_blob)
        })
        
        count += 1
        if count % 10000 == 0:
            print(f"  Processed {count} rows...")
    
    conn.close()
    
    print(f"\nWriting {len(all_data)} records to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_data, f)
    
    print(f"Done! File size: {len(open(OUTPUT_FILE).read()) / 1024 / 1024:.1f} MB")

if __name__ == '__main__':
    export()
