# Smart Quote Selection for Sermon References

## Update Summary
Modified the sermon integration to intelligently choose when to use direct quotes vs. summaries based on content type.

## Quote Selection Rules

### 1. **Stories/Illustrations** → DIRECT QUOTES
When Pastor Bob tells a story or gives an illustration, use direct quotes to preserve the narrative.

**Indicators:**
- "remember when"
- "story"
- "once"
- "example"
- "illustration"
- "let me tell you"

**Example Output:**
> Pastor Bob Kopeny illustrates this with a story: "I remember when I was young and my father told me..."

### 2. **Scripture Reading** → SUMMARIZE
When Pastor Bob is reading scripture, summarize the teaching point rather than quoting the Bible verses verbatim.

**Indicators:**
- "verse"
- "scripture says"
- "bible says"
- "chapter"
- "reads"
- "it says"

**Example Output:**
> Pastor Bob Kopeny teaches from Romans 8 that nothing can separate us from God's love (watch at 15:32).

### 3. **Teaching Points** → PARAPHRASE
For general teaching, paraphrase the key concepts in clear, concise language.

**Example Output:**
> Pastor Bob Kopeny emphasizes that faith requires action, not just belief (see sermon at 22:15).

## Implementation Details

### server.js Changes:
```javascript
// Detect content type
if (text_lower.includes('remember when') || 
    text_lower.includes('story') || ...) {
  contentType = 'STORY/ILLUSTRATION';
}
else if (text_lower.includes('verse') || 
         text_lower.includes('scripture says') || ...) {
  contentType = 'SCRIPTURE/TEACHING';
}

// Apply different instructions
if (contentType === 'STORY/ILLUSTRATION') {
  context += 'QUOTE THIS DIRECTLY (it's a story)';
} else {
  context += 'SUMMARIZE THIS TEACHING (don't quote verbatim)';
}
```

## Benefits
1. **More Natural Responses**: Avoids awkward verbatim scripture readings
2. **Preserves Stories**: Keeps the narrative power of illustrations
3. **Clearer Teaching**: Focuses on what Pastor Bob teaches, not just what he reads
4. **Better User Experience**: More conversational and engaging responses

## Example Response Pattern

**Before:**
> "According to Pastor Bob Kopeny in his sermon 'Faith Journey' at 15:32, 'Now verse 28 says and we know that all things work together for good to them that love God to them who are the called according to his purpose verse 29 for whom he did foreknow...'"

**After:**
> "According to Pastor Bob Kopeny in his sermon 'Faith Journey' at 15:32, he teaches that Romans 8:28-29 assures us that God works all things for good for those who love Him. Watch the full explanation here: [link]"

## Testing
Open http://localhost:3001/chat.html and try:
- "What does Pastor Bob say about faith?" - Should summarize teaching
- "Does Pastor Bob have any stories about forgiveness?" - Should quote stories directly
- "How does Pastor Bob explain John 3:16?" - Should summarize, not quote scripture verbatim