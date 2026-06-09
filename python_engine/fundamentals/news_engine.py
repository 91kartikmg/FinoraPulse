import yfinance as yf
import sys
import json
import re

# Lexicons for financial sentiment analysis
BULLISH_WORDS = {
    'up', 'gain', 'gains', 'gaining', 'buy', 'bull', 'bullish', 'growth', 'grow', 'growing', 
    'rise', 'rising', 'rose', 'rises', 'profit', 'profits', 'revenue', 'higher', 'highs', 'beat', 'beats',
    'positive', 'expansion', 'expand', 'success', 'successful', 'successes', 'record', 'high', 
    'outperform', 'overweight', 'long', 'upgrade', 'upgrades', 'upgraded', 'optimistic', 'opportunity',
    'catalyst', 'catalysts', 'surge', 'surges', 'surging', 'surged', 'breakout', 'rally', 'rallies', 
    'strong', 'stronger', 'recovery', 'recover', 'rebound', 'rebounds'
}

BEARISH_WORDS = {
    'down', 'loss', 'losses', 'losing', 'sell', 'bear', 'bearish', 'drop', 'drops', 'dropping', 'dropped',
    'fall', 'falling', 'fell', 'falls', 'decline', 'declines', 'declining', 'declined', 'negative', 
    'deficit', 'lower', 'lows', 'slip', 'slips', 'slipping', 'slipped', 'slump', 'slumps', 'slumping', 'slumped', 
    'plunge', 'plunges', 'plunging', 'plunged', 'miss', 'misses', 'missing', 'missed', 'crash', 'crashes', 'crashing', 
    'crashed', 'warning', 'warnings', 'warns', 'warned', 'risk', 'risks', 'risky', 'weak', 'weakness', 'weaknesses',
    'downgrade', 'downgrades', 'downgraded', 'pessimistic', 'short', 'underperform', 'selloff', 'selloffs',
    'debt', 'liabilities', 'inflation', 'pressure', 'pressures', 'concerns', 'slowdown', 'slows'
}

def clean_text(text):
    if not text:
        return []
    text = text.lower()
    # Replace non-alphabetic characters with spaces
    text = re.sub(r'[^a-zA-Z\s]', ' ', text)
    return text.split()

def safe_get(d, keys, default=None):
    if not isinstance(d, dict):
        return default
    curr = d
    for k in keys:
        if not isinstance(curr, dict):
            return default
        curr = curr.get(k)
        if curr is None:
            return default
    return curr

def analyze_sentiment(text):
    words = clean_text(text)
    pos_count = 0
    neg_count = 0
    matched_pos = []
    matched_neg = []
    
    for word in words:
        if word in BULLISH_WORDS:
            pos_count += 1
            matched_pos.append(word)
        elif word in BEARISH_WORDS:
            neg_count += 1
            matched_neg.append(word)
            
    total = pos_count + neg_count
    if total == 0:
        return 0.0, [], []
        
    score = (pos_count - neg_count) / total
    return score, matched_pos, matched_neg

def get_sentiment(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news:
            return {
                "overallSentiment": "Neutral",
                "sentimentScore": 0.0,
                "keyDrivers": [],
                "articles": []
            }
            
        articles = []
        total_score = 0.0
        all_pos_words = []
        all_neg_words = []
        
        for item in news:
            if not isinstance(item, dict):
                continue
                
            # Handle yfinance nested structure vs flat structure
            content = item.get('content')
            if not isinstance(content, dict):
                content = item
                
            title = content.get('title', '')
            if not title:
                title = item.get('title', '')
                
            summary = content.get('summary', '')
            if not summary:
                summary = item.get('summary', '')
                
            # Perform sentiment analysis on title + summary
            full_text = f"{title} {summary}".strip()
            score, pos, neg = analyze_sentiment(full_text)
            
            all_pos_words.extend(pos)
            all_neg_words.extend(neg)
            
            label = "Neutral"
            if score > 0.15:
                label = "Bullish"
            elif score < -0.15:
                label = "Bearish"
                
            # Extract publisher
            publisher = safe_get(content, ['provider', 'displayName'])
            if not publisher:
                publisher = item.get('publisher', 'Unknown')
                
            # Extract link
            link = safe_get(content, ['clickThroughUrl', 'url'])
            if not link:
                link = safe_get(content, ['canonicalUrl', 'url'])
            if not link:
                link = item.get('link', '#')
                
            # Extract date/time
            pub_time = content.get('pubDate')
            if not pub_time:
                pub_time = item.get('providerPublishTime', 0)
                
            articles.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "publishTime": pub_time,
                "score": round(score, 2),
                "sentiment": label
            })
            total_score += score
            
        avg_score = total_score / len(articles) if articles else 0.0
        
        overall = "Neutral"
        if avg_score > 0.1:
            overall = "Bullish"
        elif avg_score < -0.1:
            overall = "Bearish"
            
        # Get top unique driver words (excluding short words)
        drivers = list(set(all_pos_words + all_neg_words))
        drivers = [w for w in drivers if len(w) > 3][:6]
        
        return {
            "overallSentiment": overall,
            "sentimentScore": round(avg_score, 2),
            "keyDrivers": drivers,
            "articles": articles[:10]  # Limit to top 10 articles
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "overallSentiment": "Neutral",
            "sentimentScore": 0.0,
            "keyDrivers": [],
            "articles": []
        }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No ticker symbol provided."}))
        sys.exit(1)
        
    symbol = sys.argv[1].strip().upper()
    result = get_sentiment(symbol)
    print(json.dumps(result))
