# Debugging Sermon Links in Chat

## Issue
When asking about forgiveness or other topics via chat.html, the AI response doesn't include YouTube sermon links, even though:
1. The sermon search finds relevant segments
2. The API returns responses with links when tested directly
3. The sermon context is being added to the system message

## Investigation Results

### ✅ What's Working:
1. **Sermon Search**: Finding 3-5 relevant segments with good scores (0.7+)
2. **API Response**: When testing via curl, response includes YouTube links
3. **Context Addition**: Server adds 3000+ chars of sermon context to system message
4. **Relevance Filtering**: Only truly relevant segments are returned

### ⚠️ Potential Issues:
1. **Chat Display**: The formatMessageWithLinks function might not be catching all URL formats
2. **Response Truncation**: Chat might be cutting off response before links appear
3. **AI Compliance**: Despite explicit instructions, AI might not always include links

## Testing Steps

### 1. Test via API (Working)
```bash
curl -X POST http://localhost:3001/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "system", "content": "You are Pastor Bob Assistant."},
      {"role": "user", "content": "What does Bob teach about forgiveness?"}
    ],
    "model": "grok-3"
  }'
```
Result: Includes sermon titles and YouTube links

### 2. Test via Chat Interface
1. Open http://localhost:3001/chat.html
2. Type: "What does Bob teach about forgiveness?"
3. Open browser console (F12)
4. Check for: "Response contains YouTube links:" message

## Solutions Implemented

### 1. Enhanced URL Detection
Updated regex to handle URLs wrapped in parentheses:
```javascript
/\(?(https:\/\/(www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)(&amp;t=\d+s)?)\)?/g
```

### 2. Debug Logging
Added console logging to detect when YouTube links are in response:
```javascript
if (fullResponse.includes('youtube.com')) {
    console.log('Response contains YouTube links:', fullResponse);
}
```

### 3. Explicit Instructions
Server now provides MANDATORY instructions to include links:
- "YOU MUST INCLUDE ALL YouTube links exactly as provided"
- "REQUIRED LINK TO INCLUDE: [url]"
- "Your response is INCOMPLETE without sermon links"

## Quick Fix Workaround

If links still don't appear, try asking more specifically:
- "Give me YouTube links to Bob's sermons about forgiveness"
- "What sermons with timestamps does Bob have on forgiveness?"
- "Include YouTube links when telling me what Bob teaches about forgiveness"

## Next Steps

1. Monitor browser console for YouTube link detection
2. Check if response is being truncated (max_tokens limit)
3. Consider making sermon links a separate response section
4. Add fallback to always show found sermons below AI response