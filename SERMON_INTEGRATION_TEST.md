# Sermon Integration Testing Guide

## Quick Start Testing

### 1. Start All Services

```bash
# Terminal 1: Start Node.js server
npm start

# Terminal 2: Setup and index sermons (first time only)
chmod +x setup_sermons.sh
./setup_sermons.sh
source venv_sermon/bin/activate
python sermon_indexer.py

# Terminal 3: Start sermon API
source venv_sermon/bin/activate
python sermon_api.py

# Terminal 4: Restart voice agent
pkill -f agent_smart.py
source venv311/bin/activate
python agent_smart.py
```

### 2. Open Chat Interface
Navigate to: http://localhost:3001/chat.html

### 3. Test Text Chat with Sermon Search

Try these test questions in the text chat:
- "What does Pastor Bob say about faith?"
- "How does Bob teach about prayer?"
- "What scripture does Pastor Kopeny use about love?"

**Expected Results:**
- Response includes specific sermon references
- YouTube links appear as green clickable buttons (📹 Watch at XX:XX)
- Sermon titles highlighted in blue
- Scripture references highlighted in yellow

### 4. Test Voice Chat with Sermon Search

Click the microphone button and ask:
- "What does Pastor Bob teach about forgiveness?"
- "Tell me about Bob's sermon on healing"

**Expected Results:**
- Voice agent speaks the answer
- Chat shows both question and answer transcripts
- Sermon references appear in chat as system messages
- YouTube links are clickable

### 5. Test Name Correction

Try misspelling the name:
- Type: "What does Pastor Bob Copeny say about faith?"
- Expected: Response uses correct spelling "Kopeny"

### 6. Verify Features

✅ **Text Chat Features:**
- Sermon segments automatically searched
- YouTube timestamps as clickable links
- Formatted sermon references
- Scripture highlighting

✅ **Voice Agent Features:**
- Searches sermons when answering
- References specific sermons by name
- Provides timestamps
- Shows links in chat transcript

✅ **Visual Indicators:**
- 📹 Green video link buttons
- Blue sermon title references  
- Yellow scripture verses
- System messages for sermon references

### Common Issues & Solutions

**Sermons not appearing:**
- Check sermon API is running: `curl http://localhost:5001/api/sermon/health`
- Verify indexing completed: Should show "Total segments: XXXX"
- Check server.js logs for "Found X relevant sermon segments"

**Links not clickable:**
- Refresh the page (Ctrl+F5)
- Check browser console for errors
- Verify formatMessageWithLinks function is working

**Voice agent not referencing sermons:**
- Restart agent_smart.py
- Check agent logs for "Found X relevant sermon segments"
- Verify aiohttp is installed: `pip install aiohttp`

### Test Successful When:

1. **Text questions** return responses with:
   - Specific sermon titles
   - Clickable YouTube timestamps
   - "According to Pastor Bob Kopeny..." phrases

2. **Voice questions** result in:
   - Agent speaking sermon references
   - Chat showing transcript with links
   - Sermon references as system messages

3. **All links** are:
   - Properly formatted with timestamps
   - Opening YouTube at correct moment
   - Visually distinct (green buttons)

### Sample Working Response:

```
According to Pastor Bob Kopeny in his sermon "Walking by Faith" (15:32), 
faith is not just believing but acting on God's promises...

📹 Watch at 15:32
https://youtube.com/watch?v=VIDEO_ID&t=932s

Pastor Bob Kopeny also teaches in "Trust in the Lord" (8:45) that...

📹 Watch at 8:45  
https://youtube.com/watch?v=VIDEO_ID&t=525s
```

## Success! 🎉
If all tests pass, your sermon integration is working perfectly!