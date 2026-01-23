/**
 * Simple sermon search module that works with static JSON data
 * This can be deployed with the main app to Railway
 */

const fs = require('fs');
const path = require('path');

class SermonSearch {
  constructor() {
    this.sermons = [];
    this.loadSermons();
  }

  loadSermons() {
    try {
      const dataPath = path.join(__dirname, 'sermons_static.json');
      const data = fs.readFileSync(dataPath, 'utf8');
      this.sermons = JSON.parse(data);
      console.log(`Loaded ${this.sermons.length} sermon segments`);
    } catch (error) {
      console.error('Failed to load sermon data:', error);
      this.sermons = [];
    }
  }

  /**
   * Calculate relevance score between query and text
   */
  calculateRelevance(query, text, topics = []) {
    const queryLower = query.toLowerCase();
    const textLower = text.toLowerCase();
    
    // Direct word matching
    const queryWords = queryLower.split(/\s+/);
    let wordMatches = 0;
    
    for (const word of queryWords) {
      if (word.length > 3 && textLower.includes(word)) {
        wordMatches++;
      }
    }
    
    // Topic matching
    const topicScore = topics.some(t => queryLower.includes(t.toLowerCase())) ? 0.3 : 0;
    
    // Calculate final score
    const wordScore = wordMatches / queryWords.length;
    return Math.min(1.0, wordScore + topicScore);
  }

  /**
   * Search for relevant sermon segments
   */
  search(query, nResults = 3) {
    if (!query || this.sermons.length === 0) {
      return [];
    }

    // Score all sermons
    const scored = this.sermons.map(sermon => {
      const score = this.calculateRelevance(
        query, 
        sermon.text, 
        sermon.topics || []
      );
      
      return {
        ...sermon,
        relevance_score: score
      };
    });

    // Filter and sort by relevance
    const relevant = scored
      .filter(s => s.relevance_score > 0.2)
      .sort((a, b) => b.relevance_score - a.relevance_score)
      .slice(0, nResults);

    // Format results
    return relevant.map(sermon => ({
      text: sermon.text,
      title: sermon.title,
      video_id: sermon.video_id,
      start_time: sermon.start_time,
      url: sermon.url,
      relevance_score: sermon.relevance_score,
      timestamped_url: `${sermon.url}&t=${this.timeToSeconds(sermon.start_time)}s`
    }));
  }

  /**
   * Convert time string to seconds
   */
  timeToSeconds(timeStr) {
    if (!timeStr) return 0;
    const parts = timeStr.split(':').map(Number);
    
    if (parts.length === 3) {
      return parts[0] * 3600 + parts[1] * 60 + parts[2];
    } else if (parts.length === 2) {
      return parts[0] * 60 + parts[1];
    }
    return 0;
  }
}

module.exports = SermonSearch;