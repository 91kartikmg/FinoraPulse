import yfinance as yf
import json
import sys
import time

# Assets to monitor
ASSETS = {
    "STOCKS": ["NVDA", "AAPL", "TSLA", "MSFT", "GOOGL", "AMZN", "META", "AMD"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD", "ADA-USD"],
    "FOREX": ["EURUSD=X", "GBPUSD=X", "JPY=X", "AUDUSD=X"],
    "METALS": ["GC=F", "SI=F"] # Gold and Silver
}

def get_performance(ticker_list):
    data = []
    for ticker in ticker_list:
        try:
            t = yf.Ticker(ticker)
            # Fetching 2 days of data to calculate percentage change
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                curr_price = hist['Close'].iloc[-1]
                change = ((curr_price - prev_close) / prev_close) * 100
                
                # Clean up display name
                display_name = ticker.replace("-USD", "").replace("=X", "").replace("=F", " (Gold/Silver)")
                if ticker == "GC=F": display_name = "GOLD"
                if ticker == "SI=F": display_name = "SILVER"

                data.append({
                    "symbol": display_name,
                    "original_ticker": ticker, # Keep this for the URL link
                    "price": round(curr_price, 2),
                    "change": round(change, 2)
                })
        except Exception as e:
            continue
    
    # Sort by highest performance
    return sorted(data, key=lambda x: x['change'], reverse=True)

def main():
    while True:
        results = {
            "stocks": get_performance(ASSETS["STOCKS"])[:3], # Top 3
            "crypto": get_performance(ASSETS["CRYPTO"])[:3], # Top 3
            "forex": get_performance(ASSETS["FOREX"])[:2],
            "metals": get_performance(ASSETS["METALS"])
        }
        # Print as single JSON line for Node.js to read
        print(json.dumps(results))
        sys.stdout.flush()
        time.sleep(30) # Update every 30 seconds

if __name__ == "__main__":
    main()