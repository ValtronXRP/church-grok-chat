"""
Sermon Indexer - Processes YouTube sermon transcripts and builds a searchable vector database
with timestamp-based segment linking for Pastor Bob Kopeny's teachings.
"""

import json
import os
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import hashlib
from dataclasses import dataclass
from pathlib import Path

# For vector database - using ChromaDB for local persistence
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

# For better text processing
import nltk
from nltk.tokenize import sent_tokenize
import numpy as np

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

@dataclass
class SermonSegment:
    """Represents a segment of a sermon with timestamp and content"""
    video_id: str
    title: str
    url: str
    text: str
    start_time_ms: int
    end_time_ms: int
    start_time_formatted: str  # "HH:MM:SS"
    segment_type: str  # "illustration", "scripture", "teaching", "prayer", etc.
    topics: List[str]
    scripture_references: List[str]
    
    def get_timestamped_url(self) -> str:
        """Generate YouTube URL with timestamp"""
        seconds = self.start_time_ms // 1000
        return f"{self.url}&t={seconds}s"

class SermonIndexer:
    def __init__(self, db_path: str = "./sermon_vector_db"):
        """Initialize the sermon indexer with ChromaDB"""
        self.db_path = db_path
        
        # Initialize ChromaDB client with persistence
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True
            )
        )
        
        # Create or get collection for sermon segments
        # Using OpenAI embeddings for better semantic search
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        
        try:
            self.collection = self.client.get_collection(
                name="sermon_segments",
                embedding_function=self.embedding_fn
            )
            print(f"Loaded existing collection with {self.collection.count()} segments")
        except:
            self.collection = self.client.create_collection(
                name="sermon_segments",
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"}
            )
            print("Created new sermon_segments collection")
    
    def parse_json3_transcript(self, file_path: str) -> Tuple[str, str, List[Dict]]:
        """Parse a json3 transcript file and extract segments with timestamps"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract video ID from filename
        filename = os.path.basename(file_path)
        video_id = filename.replace('.en.json3', '')
        
        # Build YouTube URL
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        segments = []
        
        if 'events' in data:
            current_text = ""
            current_start_ms = 0
            
            for event in data.get('events', []):
                if 'segs' in event:
                    # Extract text from segments
                    event_text = ""
                    for seg in event['segs']:
                        if 'utf8' in seg:
                            event_text += seg['utf8']
                    
                    # Clean up the text
                    event_text = event_text.strip()
                    
                    if event_text and event_text != '\n':
                        start_ms = event.get('tStartMs', 0)
                        duration_ms = event.get('dDurationMs', 0)
                        
                        segments.append({
                            'text': event_text,
                            'start_ms': start_ms,
                            'end_ms': start_ms + duration_ms
                        })
        
        return video_id, url, segments
    
    def parse_sermon_metadata(self, metadata_file: str) -> Dict[str, Dict]:
        """Parse the sermon metadata JSON files"""
        with open(metadata_file, 'r', encoding='utf-8') as f:
            sermons = json.load(f)
        
        # Create lookup by video ID
        metadata_lookup = {}
        for sermon in sermons:
            if 'url' in sermon:
                # Extract video ID from URL
                video_id_match = re.search(r'v=([^&]+)', sermon['url'])
                if video_id_match:
                    video_id = video_id_match.group(1)
                    metadata_lookup[video_id] = sermon
        
        return metadata_lookup
    
    def classify_segment_type(self, text: str) -> str:
        """Classify the type of content in a segment"""
        text_lower = text.lower()
        
        # Classification patterns
        if any(phrase in text_lower for phrase in ['let me tell you a story', 'i remember when', 'there was a', 'once upon']):
            return "illustration"
        elif any(phrase in text_lower for phrase in ['turn to', 'scripture says', 'the bible says', 'verse', 'chapter']):
            return "scripture"
        elif any(phrase in text_lower for phrase in ['let us pray', 'father god', 'lord we', 'amen']):
            return "prayer"
        elif any(phrase in text_lower for phrase in ['what does this mean', 'the point is', 'god is telling us']):
            return "teaching"
        else:
            return "general"
    
    def extract_scripture_references(self, text: str) -> List[str]:
        """Extract scripture references from text"""
        references = []
        
        # Common scripture patterns
        patterns = [
            r'\b([1-3]?\s?[A-Za-z]+)\s+(\d+):(\d+(?:-\d+)?)',  # "John 3:16" or "1 Corinthians 13:4-7"
            r'\b([A-Za-z]+)\s+chapter\s+(\d+)',  # "Genesis chapter 1"
            r'\b([1-3]?\s?[A-Za-z]+)\s+(\d+)',  # "Romans 8"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    reference = ' '.join(match)
                else:
                    reference = match
                references.append(reference.strip())
        
        return list(set(references))  # Remove duplicates
    
    def extract_topics(self, text: str) -> List[str]:
        """Extract key topics from the text"""
        topics = []
        
        # Topic keywords mapping
        topic_keywords = {
            "faith": ["faith", "believe", "trust", "belief"],
            "prayer": ["pray", "prayer", "praying", "intercession"],
            "love": ["love", "loving", "beloved", "charity"],
            "salvation": ["salvation", "saved", "redemption", "born again"],
            "forgiveness": ["forgive", "forgiveness", "mercy", "pardon"],
            "healing": ["heal", "healing", "healed", "restore"],
            "worship": ["worship", "praise", "glorify", "honor"],
            "sin": ["sin", "transgression", "iniquity", "repent"],
            "grace": ["grace", "gracious", "unmerited favor"],
            "hope": ["hope", "hopeful", "expectation"],
            "peace": ["peace", "peaceful", "tranquility"],
            "joy": ["joy", "joyful", "rejoice", "gladness"],
            "wisdom": ["wisdom", "wise", "understanding", "discernment"],
            "holy spirit": ["holy spirit", "spirit of god", "comforter"],
            "jesus": ["jesus", "christ", "messiah", "savior"],
            "heaven": ["heaven", "eternal life", "paradise"],
            "discipleship": ["disciple", "follower", "discipleship"],
            "church": ["church", "congregation", "fellowship", "body of christ"],
            "family": ["family", "marriage", "children", "parenting"],
            "service": ["serve", "service", "ministry", "helping"]
        }
        
        text_lower = text.lower()
        for topic, keywords in topic_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                topics.append(topic)
        
        return topics
    
    def create_semantic_chunks(self, segments: List[Dict], chunk_duration_ms: int = 30000) -> List[Dict]:
        """Group segments into semantic chunks of approximately chunk_duration_ms"""
        chunks = []
        current_chunk = {
            'text': '',
            'start_ms': 0,
            'end_ms': 0,
            'segments': []
        }
        
        for segment in segments:
            # Start new chunk if current is too long or empty
            if not current_chunk['text']:
                current_chunk['start_ms'] = segment['start_ms']
            
            current_chunk['text'] += ' ' + segment['text']
            current_chunk['end_ms'] = segment['end_ms']
            current_chunk['segments'].append(segment)
            
            # Check if we should start a new chunk
            duration = current_chunk['end_ms'] - current_chunk['start_ms']
            
            # Create chunk if it's long enough or if text seems complete
            if duration >= chunk_duration_ms or segment['text'].endswith('.'):
                if len(current_chunk['text'].strip()) > 50:  # Minimum text length
                    chunks.append(current_chunk.copy())
                    current_chunk = {
                        'text': '',
                        'start_ms': 0,
                        'end_ms': 0,
                        'segments': []
                    }
        
        # Add remaining chunk
        if current_chunk['text'].strip():
            chunks.append(current_chunk)
        
        return chunks
    
    def format_time(self, ms: int) -> str:
        """Convert milliseconds to HH:MM:SS format"""
        seconds = ms // 1000
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    
    def index_sermon(self, transcript_file: str, metadata: Optional[Dict] = None):
        """Index a single sermon transcript"""
        print(f"Processing: {transcript_file}")
        
        # Parse transcript
        video_id, url, segments = self.parse_json3_transcript(transcript_file)
        
        if not segments:
            print(f"  No segments found in {transcript_file}")
            return
        
        # Get title from metadata or filename
        title = "Unknown Sermon"
        date = ""
        scripture = ""
        
        if metadata and video_id in metadata:
            sermon_info = metadata[video_id]
            title = sermon_info.get('title', title)
            date = sermon_info.get('date', '')
            scripture = sermon_info.get('scripture', '')
        
        # Create semantic chunks
        chunks = self.create_semantic_chunks(segments, chunk_duration_ms=30000)  # 30-second chunks
        
        print(f"  Found {len(chunks)} semantic chunks")
        
        # Index each chunk
        for i, chunk in enumerate(chunks):
            # Create unique ID for this segment
            segment_id = hashlib.md5(f"{video_id}_{chunk['start_ms']}".encode()).hexdigest()
            
            # Extract features
            segment_type = self.classify_segment_type(chunk['text'])
            topics = self.extract_topics(chunk['text'])
            scripture_refs = self.extract_scripture_references(chunk['text'])
            
            # Format timestamps
            start_time_formatted = self.format_time(chunk['start_ms'])
            end_time_formatted = self.format_time(chunk['end_ms'])
            
            # Create metadata
            metadata_dict = {
                'video_id': video_id,
                'title': title,
                'url': url,
                'date': date,
                'scripture': scripture,
                'start_ms': chunk['start_ms'],
                'end_ms': chunk['end_ms'],
                'start_time': start_time_formatted,
                'end_time': end_time_formatted,
                'segment_type': segment_type,
                'topics': ','.join(topics),
                'scripture_refs': ','.join(scripture_refs),
                'segment_number': i + 1,
                'total_segments': len(chunks)
            }
            
            # Add to vector database
            self.collection.add(
                documents=[chunk['text']],
                metadatas=[metadata_dict],
                ids=[segment_id]
            )
        
        print(f"  ✓ Indexed {len(chunks)} segments from {title}")
    
    def index_all_sermons(self, transcript_dir: str, metadata_dir: str):
        """Index all sermons from directories"""
        # Load all metadata files
        all_metadata = {}
        
        print("Loading metadata...")
        for metadata_file in Path(metadata_dir).glob("SERMONS_BATCH_*.json"):
            batch_metadata = self.parse_sermon_metadata(str(metadata_file))
            all_metadata.update(batch_metadata)
        
        print(f"Loaded metadata for {len(all_metadata)} sermons")
        
        # Process all transcript files
        transcript_files = list(Path(transcript_dir).glob("*.json3"))
        print(f"Found {len(transcript_files)} transcript files")
        
        for i, transcript_file in enumerate(transcript_files, 1):
            print(f"\n[{i}/{len(transcript_files)}] ", end="")
            try:
                self.index_sermon(str(transcript_file), all_metadata)
            except Exception as e:
                print(f"  ✗ Error processing {transcript_file}: {e}")
        
        print(f"\n✅ Indexing complete! Total segments: {self.collection.count()}")
    
    def search(self, query: str, n_results: int = 5, filter_dict: Optional[Dict] = None) -> List[Dict]:
        """Search for relevant sermon segments"""
        where_clause = {}
        
        # Add filters if provided
        if filter_dict:
            if 'topics' in filter_dict:
                where_clause['topics'] = {"$contains": filter_dict['topics']}
            if 'segment_type' in filter_dict:
                where_clause['segment_type'] = filter_dict['segment_type']
            if 'scripture' in filter_dict:
                where_clause['scripture_refs'] = {"$contains": filter_dict['scripture']}
        
        # Perform semantic search
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause if where_clause else None
        )
        
        # Format results
        formatted_results = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i]
                
                # Generate timestamped URL
                seconds = metadata['start_ms'] // 1000
                timestamped_url = f"{metadata['url']}&t={seconds}s"
                
                formatted_results.append({
                    'text': doc,
                    'title': metadata['title'],
                    'video_id': metadata['video_id'],
                    'url': metadata['url'],
                    'timestamped_url': timestamped_url,
                    'start_time': metadata['start_time'],
                    'end_time': metadata['end_time'],
                    'segment_type': metadata['segment_type'],
                    'topics': metadata['topics'].split(',') if metadata['topics'] else [],
                    'scripture_refs': metadata['scripture_refs'].split(',') if metadata['scripture_refs'] else [],
                    'date': metadata.get('date', ''),
                    'distance': results['distances'][0][i] if 'distances' in results else 0
                })
        
        return formatted_results

def main():
    """Main function to index sermons"""
    # Initialize indexer
    indexer = SermonIndexer(db_path="./sermon_vector_db")
    
    # Index all sermons
    transcript_dir = "/Users/valorkopeny/Desktop/Json3 sermons 1-3"
    metadata_dir = "/Users/valorkopeny/Desktop/SERMONS_ZIP_05"
    
    print("Starting sermon indexing...")
    print("=" * 50)
    
    indexer.index_all_sermons(transcript_dir, metadata_dir)
    
    print("\n" + "=" * 50)
    print("Testing search functionality...")
    
    # Test search
    test_queries = [
        "What does Pastor Bob say about faith?",
        "Tell me about prayer",
        "Scripture about love",
        "How to overcome fear"
    ]
    
    for query in test_queries:
        print(f"\n🔍 Query: {query}")
        results = indexer.search(query, n_results=3)
        
        for i, result in enumerate(results, 1):
            print(f"\n  Result {i}:")
            print(f"    📹 {result['title']}")
            print(f"    ⏰ {result['start_time']} - {result['end_time']}")
            print(f"    🔗 {result['timestamped_url']}")
            print(f"    📝 {result['text'][:150]}...")
            print(f"    🏷️ Topics: {', '.join(result['topics'])}")

if __name__ == "__main__":
    main()