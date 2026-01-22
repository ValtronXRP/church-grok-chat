#!/usr/bin/env python3
"""
Test script to verify improved sermon relevance filtering
"""

import requests
import json

def test_sermon_search(query, description):
    """Test the sermon search API with a specific query"""
    print(f"\n{'='*60}")
    print(f"Testing: {description}")
    print(f"Query: '{query}'")
    print('='*60)
    
    try:
        response = requests.post('http://localhost:5001/api/sermon/search', 
            json={
                'query': query,
                'n_results': 5
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nFound {data['count']} relevant sermon segments:")
            
            for i, result in enumerate(data['results'], 1):
                print(f"\n{i}. {result['title']} ({result['start_time']})")
                print(f"   Relevance Score: {result.get('relevance_score', 'N/A'):.2f}")
                print(f"   Distance: {result['distance']:.3f}")
                print(f"   Text preview: {result['text'][:150]}...")
                print(f"   Link: {result['timestamped_url']}")
                
                # Check if forgiveness is actually discussed
                text_lower = result['text'].lower()
                forgiveness_words = ['forgive', 'forgiven', 'forgiving', 'forgiveness', 'unforgiveness']
                word_count = sum(1 for word in forgiveness_words if word in text_lower)
                print(f"   Forgiveness word count: {word_count}")
                
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Error: {e}")

# Test queries
print("TESTING IMPROVED RELEVANCE FILTERING")
print("=====================================")

# Test 1: Direct forgiveness query
test_sermon_search(
    "What does Pastor Bob teach about forgiveness?",
    "Direct forgiveness teaching query"
)

# Test 2: Check that abstract connections are filtered out
test_sermon_search(
    "forgiveness",
    "Simple forgiveness keyword"
)

# Test 3: More specific forgiveness query
test_sermon_search(
    "How does Pastor Bob Kopeny say we should forgive others?",
    "Specific forgiveness action query"
)

# Test 4: Query that shouldn't match if only abstract
test_sermon_search(
    "Does Pastor Bob teach about forgiveness in relationships?",
    "Forgiveness in specific context"
)

print("\n" + "="*60)
print("RELEVANCE FILTERING TEST COMPLETE")
print("="*60)
print("\nKey checks:")
print("1. Results should have relevance scores >= 0.5")
print("2. Each segment should mention forgiveness multiple times")
print("3. Segments should contain substantive teaching, not just passing mentions")
print("4. No abstract or tangential connections should appear")