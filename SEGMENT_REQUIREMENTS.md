# APB Segment Extraction & Search Requirements

## Master Rules for All Segments (Illustrations + Future Sermon Segments)

### 1. Content Must Be Pastor Bob's Teachings Only
- All segments must come directly from Pastor Bob Kopeny's sermon transcripts
- Exclude worship portions (singing, worship leader speaking, band playing)
- Exclude announcements, event promotions, greetings by other staff
- Exclude any portions where someone other than Pastor Bob is speaking (worship pastor, guest speakers unless Pastor Bob is quoting them within his sermon)
- If the speaker changes mid-transcript, only extract Pastor Bob's portions

### 2. No Hallucinations Policy
- Agents must ONLY summarize or reference content that exists in the actual transcript data
- If no relevant segment exists for a user's question, the agent must say it doesn't have specific teaching from Pastor Bob on that topic
- Agents may summarize transcript text but must NOT invent theological positions, stories, or stances not present in the data
- Never fabricate quotes, illustrations, or doctrinal positions
- The agent should say: "I don't have a specific sermon from Pastor Bob on that topic" rather than guess

### 3. Complete Sentences and Thoughts
- Every segment must start at the beginning of a sentence (never mid-sentence)
- Every segment must end at the end of a sentence (never cut off mid-thought)
- Illustrations/stories must be complete from beginning to end (full narrative arc)
- No fragments, no trailing off, no "..." endings

### 4. Semantic Search Relevance Requirements
- Search results must be topically relevant to the user's question
- The key concepts/keywords of the question must be addressed in the returned segments
- Results about tangentially related topics should be filtered out
- Example: "Why is going to church important?" should return segments about church attendance, fellowship, community, body of Christ — NOT segments that merely mention the word "church" in an unrelated context
- Relevance scoring must weigh: topic tags > summary match > full text match
- Distance threshold: discard results with low semantic similarity even if keyword matches exist

---

## Illustration Segment Requirements

### What to Extract
- Illustrations (analogies, metaphors, real-world examples used to make a point)
- Personal stories (Pastor Bob's own life experiences)
- Anecdotes (stories about other people, named or unnamed)
- Jokes (humorous stories used to make a point or lighten the mood)
- Hypothetical scenarios ("Imagine if...", "What if...")
- Quotes from famous/historical persons (not Bible quotes)

### What NOT to Extract
- Bible stories, parables, or direct Scripture quotations (these belong in sermon segments)
- Worship lyrics or song references
- Announcements or event details
- Generic transitional phrases
- Reading of Scripture passages (the Bible text itself)

### Schema per Illustration
```json
{
  "type": "illustration | personal_story | anecdote | joke | hypothetical | quote",
  "summary": "1-2 sentence description of the illustration",
  "full_text": "Complete verbatim transcript of the illustration from start to finish",
  "topics": ["3-8 specific topic tags that describe what this illustration teaches about"],
  "emotional_tone": "inspiring | sobering | funny | warm | convicting | hopeful | etc.",
  "illustration_timestamp": "MM:SS or HH:MM:SS format",
  "youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDSs",
  "video_id": "YouTube video ID",
  "teaching_point": "What theological or life principle does this illustration support?"
}
```

### Topic Tag Requirements
- Tags must be SPECIFIC to what the illustration teaches, not generic
- Bad tags: "faith", "life", "God" (too broad)
- Good tags: "church attendance", "importance of fellowship", "isolation dangers", "body of Christ"
- Tags should match the kinds of questions users would ask
- Example questions users ask: "Why should I go to church?", "How do I forgive someone?", "What does Pastor Bob say about marriage?"
- Tags should bridge from the illustration's content to the user's likely question phrasing

---

## Future Sermon Segment Requirements

### What to Extract
- Teaching portions where Pastor Bob explains a concept, doctrine, or principle
- Scripture exposition (Pastor Bob explaining what a Bible passage means)
- Practical application (Pastor Bob telling the congregation how to apply a teaching)
- Doctrinal positions (Pastor Bob's stance on theological topics)
- Counseling-style advice (Pastor Bob addressing common life situations)

### What NOT to Extract
- Worship/singing portions
- Announcements and event promotions
- Greetings by non-Pastor-Bob speakers
- Purely transitional phrases ("Let's turn to...", "Now let's look at...")
- Reading Scripture without commentary (just the Bible text being read aloud)

### Schema per Sermon Segment
```json
{
  "segment_text": "Complete verbatim transcript of the teaching segment",
  "summary": "1-2 sentence summary of what Pastor Bob teaches in this segment",
  "main_topic": "Primary topic of this segment",
  "topics": ["3-8 specific topic tags"],
  "scriptures": ["Book Chapter:Verse references discussed"],
  "questions_answered": ["What user questions does this segment answer?"],
  "timestamp": "MM:SS or HH:MM:SS",
  "youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDSs",
  "video_id": "YouTube video ID",
  "sermon_title": "Title of the sermon if known"
}
```

---

## Search & Retrieval Rules

### Query Processing
1. Extract key topic words from user question (remove stop words)
2. Search Chroma Cloud with semantic embedding similarity
3. Post-filter results by keyword relevance scoring:
   - Topic tag match: highest weight
   - Summary match: medium weight
   - Full text match: lower weight
4. Discard results below relevance threshold
5. Return only segments that actually address the question's core topic

### Response Rules for Agents (Voice + Text)
1. First priority: Use ONLY data from Pastor Bob's actual transcripts
2. Summarize transcript content naturally — don't read verbatim unless quoting
3. If no relevant segment exists: "I don't have a specific teaching from Pastor Bob on that exact topic"
4. NEVER invent stories, theological positions, or doctrinal stances
5. NEVER attribute a position to Pastor Bob that isn't explicitly in the transcript data
6. Both voice and text agents must return the same search results for the same query
7. Include YouTube clip links so users can watch Pastor Bob teach it himself

### Worship/Announcement Detection
To filter out non-sermon content from transcripts:
- Skip segments in the first 5-10 minutes that contain: greetings, "welcome to", event announcements, "this week", "next Sunday"
- Skip segments with repeated phrases typical of worship: "hallelujah", "praise", singing patterns
- Skip segments where the speaker is clearly not Pastor Bob (different voice/name mentioned)
- Focus on the main teaching body of each sermon (typically starts 10-15 minutes in)
