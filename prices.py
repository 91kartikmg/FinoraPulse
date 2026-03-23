import sys
import json
import yfinance as yf
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings('ignore')

# --- HOMEPAGE ASSETS ---
ASSETS = {
    "STOCKS": ["NVDA", "AAPL", "TSLA", "MSFT", "GOOGL", "AMZN", "META", "AMD"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD", "ADA-USD"],
    "FOREX": ["EURUSD=X", "GBPUSD=X", "JPY=X", "AUDUSD=X"],
    "METALS": ["GC=F", "SI=F"]
}

# --- FETCH SINGLE TICKER (Used by both) ---
def fetch_data(ticker, is_homepage=False):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[-2]
            curr_price = hist['Close'].iloc[-1]
            change = ((curr_price - prev_close) / prev_close) * 100
        elif len(hist) == 1:
            curr_price = hist['Close'].iloc[-1]
            change = 0.0
        else:
            return ticker, None

        if is_homepage:
            display_name = ticker.replace("-USD", "").replace("=X", "")
            if ticker == "GC=F": display_name = "GOLD"
            elif ticker == "SI=F": display_name = "SILVER"
            return ticker, {"symbol": display_name, "original_ticker": ticker, "price": float(curr_price), "change": round(float(change), 2)}
        else:
            return ticker, {"price": float(curr_price), "change": float(change)}
            
    except Exception:
        return ticker, None

# --- HEADER LOGIC ---
def get_header_data(tickers_string):
    tickers = tickers_string.split(',')
    result = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_data, t, False) for t in tickers]
        for future in futures:
            ticker, data = future.result()
            if data:
                result[ticker] = data
    return result

# --- HOMEPAGE LOGIC ---
def get_category_performance(ticker_list):
    data = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_data, t, True) for t in ticker_list]
        for future in futures:
            ticker, res = future.result()
            if res:
                data.append(res)
    return sorted(data, key=lambda x: x['change'], reverse=True)

def get_homepage_data():
    return {
        "stocks": get_category_performance(ASSETS["STOCKS"])[:3],
        "crypto": get_category_performance(ASSETS["CRYPTO"])[:3],
        "forex": get_category_performance(ASSETS["FOREX"])[:2],
        "metals": get_category_performance(ASSETS["METALS"])
    }

# --- MAIN CONTROLLER ---
def main():
    if len(sys.argv) < 2:
        print(json.dumps({}))
        return

    command = sys.argv[1]

    if command == "TOP_MOVERS":
        # Node asked for homepage cards
        print(json.dumps(get_homepage_data()))
    else:
        # Node asked for header tickers
        print(json.dumps(get_header_data(command)))

    sys.stdout.flush()

if __name__ == "__main__":
    main()