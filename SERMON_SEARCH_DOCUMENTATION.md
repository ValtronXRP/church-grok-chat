# Sermon Search System Documentation

## Overview
This system indexes Pastor Bob Kopeny's YouTube sermons and creates a searchable vector database that automatically provides timestamped video links to relevant sermon segments when users ask questions.

## Key Features
- **Automatic Sermon Indexing**: Processes YouTube transcript files with timestamps
- **Semantic Search**: Finds relevant sermon segments based on meaning, not just keywords
- **Timestamped Links**: Generates direct YouTube links to specific moments in sermons
- **Topic Classification**: Identifies illustrations, scripture references, teachings, and prayers
- **Scripture Extraction**: Automatically detects and indexes Bible references
- **Integrated with Chat**: Automatically enriches chat responses with relevant sermons

## System Architecture

### Components

1. **sermon_indexer.py**
   - Processes JSON3 transcript files from YouTube
   - Extracts timestamped segments (30-second chunks)
   - Classifies content types (illustration, scripture, teaching, prayer)
   - Extracts topics and scripture references
   - Stores in ChromaDB vector database

2. **sermon_api.py**
   - REST API server (runs on port 5001)
   - Provides search endpoints
   - Formats responses with video links
   - Returns health status and statistics

3. **server.js Integration**
   - Automatically searches sermons for user questions
   - Adds sermon context to AI responses
   - Ensures Pastor Bob Kopeny's actual teachings are referenced

## Setup Instructions

### 1. Install Dependencies
```bash
# Make setup script executable
chmod +x setup_sermons.sh

# Run setup
./setup_sermons.sh
```

### 2. Index Sermons (One-Time Setup)
```bash
# Activate virtual environment
source venv_sermon/bin/activate

# Run indexer
python sermon_indexer.py
```

This will:
- Load sermon metadata from `/Users/valorkopeny/Desktop/SERMONS_ZIP_05/`
- Process transcripts from `/Users/valorkopeny/Desktop/Json3 sermons 1-3/`
- Create vector database in `./sermon_vector_db/`
- Index approximately 600+ sermons with timestamps

### 3. Start Sermon API Server
```bash
# In a new terminal
source venv_sermon/bin/activate
python sermon_api.py
```

The API will be available at `http://localhost:5001`

### 4. Restart Main Server
```bash
# Restart the main Node.js server to enable sermon search
npm start
```

## How It Works

### When a User Asks a Question:

1. **Query Processing**
   - User asks: "What does Pastor Bob say about faith?"
   - System searches sermon database for relevant segments

2. **Segment Matching**
   - Finds 3 most relevant 30-second segments
   - Each segment includes:
     - Transcript text
     - Video title and date
     - Exact timestamp
     - YouTube link with timestamp

3. **Response Enhancement**
   - Sermon context added to system prompt
   - AI references specific sermons in response
   - Includes clickable links to video segments

4. **Example Response**:
   ```
   According to Pastor Bob Kopeny in his sermon "Walking in Faith" 
   (watch at 15:32), faith is not just believing but acting on 
   what God has promised...
   
   📹 Watch: https://youtube.com/watch?v=VIDEO_ID&t=932s
   ```

## API Endpoints

### POST /api/sermon/search
Search for relevant sermon segments
```json
{
  "query": "What about prayer?",
  "n_results": 5,
  "filters": {
    "topics": "prayer",
    "segment_type": "teaching"
  }
}
```

### GET /api/sermon/topics
Get all available topics

### GET /api/sermon/stats
Get database statistics

### GET /api/sermon/health
Check if service is running

## Data Structure

### Sermon Segment
```javascript
{
  "video_id": "oogU6KGl8Os",
  "title": "When God Made Heaven",
  "url": "https://youtube.com/watch?v=oogU6KGl8Os",
  "timestamped_url": "https://youtube.com/watch?v=oogU6KGl8Os&t=932s",
  "text": "Sermon transcript text...",
  "start_time": "15:32",
  "end_time": "16:02",
  "segment_type": "teaching",
  "topics": ["faith", "trust"],
  "scripture_refs": ["Hebrews 11:1"]
}
```

## Content Classification

### Segment Types
- **illustration**: Stories and examples
- **scripture**: Bible reading and references  
- **teaching**: Explanations and applications
- **prayer**: Prayer segments
- **general**: Other content

### Topics Detected
- Faith, Prayer, Love, Salvation
- Forgiveness, Healing, Worship
- Sin, Grace, Hope, Peace, Joy
- Wisdom, Holy Spirit, Jesus
- Heaven, Discipleship, Church
- Family, Service, and more...

## Integration with Chat/Voice

The system automatically:
1. Searches sermons when users ask questions
2. Adds sermon context to the AI's knowledge
3. Ensures responses reference actual sermons
4. Provides clickable timestamps to videos

## Troubleshooting

### If sermons aren't being found:
1. Check sermon API is running: `curl http://localhost:5001/api/sermon/health`
2. Verify database exists: Check `./sermon_vector_db/` directory
3. Re-index if needed: `python sermon_indexer.py`

### If links aren't working:
- Ensure YouTube video IDs match between metadata and transcripts
- Check that timestamps are being generated correctly

### Performance Tips:
- Index runs once and persists data
- API caches frequently accessed segments
- Limit search to 3-5 results for faster responses

## Future Enhancements

Potential improvements:
- Add sermon series grouping
- Include sermon dates in search
- Create topic-based playlists
- Add favorite sermons feature
- Export sermon quotes with citations
- Generate sermon summaries
- Track which segments are most helpful

## Files and Directories

```
/Users/valorkopeny/Desktop/church-grok-chat/
├── sermon_indexer.py      # Indexing script
├── sermon_api.py           # API server
├── requirements_sermon.txt # Python dependencies
├── setup_sermons.sh        # Setup script
├── sermon_vector_db/       # Vector database (created)
└── server.js               # Enhanced with sermon search

/Users/valorkopeny/Desktop/
├── SERMONS_ZIP_05/         # Sermon metadata JSON files
└── Json3 sermons 1-3/      # YouTube transcripts with timestamps
```

## Important Notes

1. **First-Time Setup**: Indexing all sermons takes 10-15 minutes
2. **Storage**: Vector database uses ~200MB disk space
3. **Memory**: API server uses ~500MB RAM
4. **Updates**: Re-run indexer to add new sermons
5. **Backup**: Keep copies of original transcript files

## Support

For issues or questions:
1. Check this documentation
2. Review error logs in terminal
3. Verify all services are running
4. Ensure sermon data files are accessible

The system is designed to make Pastor Bob Kopeny's teachings easily searchable and accessible through natural conversation!