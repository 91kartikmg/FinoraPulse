import sys
import json
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import warnings

warnings.filterwarnings('ignore')

def get_sentiment(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        news = stock.news
        
        if not news:
            print(json.dumps({"error": "No recent news found for this ticker."}))
            return
            
        analyzer = SentimentIntensityAnalyzer()
        articles = []
        total_compound = 0
        
        # Analyze the top 10 most recent articles
        for item in news[:10]:
            title = item.get('title', '')
            link = item.get('link', '')
            publisher = item.get('publisher', 'News Source')
            
            # NLP calculates score from -1.0 (Extreme Fear) to +1.0 (Extreme Greed)
            score = analyzer.polarity_scores(title)
            compound = score['compound']
            total_compound += compound
            
            # Tag individual articles
            if compound >= 0.05: tag = "Bullish"
            elif compound <= -0.05: tag = "Bearish"
            else: tag = "Neutral"
            
            articles.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "sentiment": round(compound, 2),
                "tag": tag
            })
            
        avg_sentiment = total_compound / len(articles) if articles else 0
        
        # Map the -1 to 1 score into a 0 to 100 "Fear & Greed Index"
        fear_greed_score = int(((avg_sentiment + 1) / 2) * 100)
        
        if fear_greed_score >= 65: 
            state = "Extreme Greed"
            color = "#22c55e"
        elif fear_greed_score >= 55: 
            state = "Greed"
            color = "#86efac"
        elif fear_greed_score <= 35: 
            state = "Extreme Fear"
            color = "#ef4444"
        elif fear_greed_score <= 45: 
            state = "Fear"
            color = "#fca5a5"
        else: 
            state = "Neutral"
            color = "#f59e0b"

        print(json.dumps({
            "score": fear_greed_score,
            "state": state,
            "color": color,
            "articles": articles
        }))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "RELIANCE.NS"
    get_sentiment(ticker)