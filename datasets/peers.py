import yfinance as yf
import sys
import json
import pandas as pd

def get_peers_comparison(ticker_symbol):
    try:
        main_stock = yf.Ticker(ticker_symbol)
        info = main_stock.info
        
        # 1. Identify Sector and Industry
        sector = info.get('sector')
        industry = info.get('industry')
        
        # Note: yfinance doesn't have a direct ".get_peers()" for all regions.
        # For Indian stocks, we often compare within a predefined list or search by industry.
        # This example uses a sector-based logic.
        
        # For demo purposes, we define a peer list if it's a major IT stock (like your image)
        # In production, you would use an API like Finnhub or FMP to get exact peers.
        peer_list = [ticker_symbol]
        if "Software" in industry or sector == "Technology":
            peer_list = ["TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS"]
        
        comparison_data = []
        
        for symbol in peer_list:
            t = yf.Ticker(symbol)
            s_info = t.info
            
            comparison_data.append({
                "symbol": symbol.replace(".NS", ""),
                "name": s_info.get('shortName', symbol),
                "marketCap": s_info.get('marketCap', 0),
                "pe": s_info.get('forwardPE', 0),
                "roce": s_info.get('returnOnAssets', 0) * 100 if s_info.get('returnOnAssets') else 0,
                "divYield": s_info.get('dividendYield', 0) * 100 if s_info.get('dividendYield') else 0,
                "currentPrice": s_info.get('currentPrice', 0)
            })

        # Sort by Market Cap
        comparison_data = sorted(comparison_data, key=lambda x: x['marketCap'], reverse=True)
        
        print(json.dumps(comparison_data))

    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TCS.NS"
    get_peers_comparison(ticker)