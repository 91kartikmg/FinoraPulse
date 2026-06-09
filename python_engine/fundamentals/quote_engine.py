import yfinance as yf
import sys
import json

def get_quotes(symbols):
    results = []
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            fast_info = ticker.fast_info
            
            price = fast_info.get('lastPrice', 0)
            prev_close = fast_info.get('previousClose', 0)
            change = price - prev_close if price and prev_close else 0
            change_pct = (change / prev_close) * 100 if prev_close else 0
            currency = fast_info.get('currency', 'USD')
            
            # Fetch name with fallback
            name = symbol
            try:
                # Retrieve info only if name is needed (info does a heavy network fetch)
                info = ticker.info
                name = info.get('longName', info.get('shortName', symbol))
            except:
                pass
            
            results.append({
                "symbol": symbol,
                "price": price,
                "change": change,
                "changePercent": change_pct,
                "name": name,
                "currency": currency
            })
        except Exception as e:
            # Append error entry so other symbols can succeed
            results.append({
                "symbol": symbol,
                "error": str(e)
            })
    return results

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No symbols provided."}))
        sys.exit(1)
        
    # Handle both space-separated and comma-separated arguments
    symbols = []
    for arg in sys.argv[1:]:
        symbols.extend([s.strip().upper() for s in arg.split(',') if s.strip()])
        
    quotes = get_quotes(symbols)
    print(json.dumps(quotes))
