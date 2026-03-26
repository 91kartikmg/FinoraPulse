import sys
import json
import yfinance as yf
import pandas as pd
import warnings

warnings.filterwarnings('ignore')

# We organize the top stocks by their economic sector
SECTORS = {
    "Technology": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    "Financials": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS"],
    "Energy & Oil": ["RELIANCE.NS", "ONGC.NS", "POWERGRID.NS", "COALINDIA.NS"],
    "Automobile": ["TATAMOTORS.NS", "M&M.NS", "MARUTI.NS", "BAJAJ-AUTO.NS"],
    "Consumer (FMCG)": ["ITC.NS", "HINDUNILVR.NS", "NESTLEIND.NS", "BRITANNIA.NS"]
}

def get_color(change):
    # Professional Heatmap Color Scale
    if change >= 2: return "#166534"       # Deep Green (Surging)
    elif change > 0: return "#22c55e"      # Light Green (Up)
    elif change <= -2: return "#991b1b"    # Deep Red (Crashing)
    elif change < 0: return "#ef4444"      # Light Red (Down)
    else: return "#475569"                 # Gray (Flat)

def generate_heatmap():
    all_tickers = []
    for sector, tickers in SECTORS.items():
        all_tickers.extend(tickers)
        
    try:
        # Bulk download 5 days of data for all 20+ stocks instantly
        data = yf.download(all_tickers, period="5d", progress=False)['Close']
        
        # We need market caps to size the blocks. We fetch info in bulk.
        tickers_obj = yf.Tickers(" ".join(all_tickers))
        
        heatmap_data = []
        
        for sector, tickers in SECTORS.items():
            sector_data = []
            for t in tickers:
                try:
                    # Calculate live % change
                    recent_prices = data[t].dropna()
                    if len(recent_prices) >= 2:
                        prev_close = recent_prices.iloc[-2]
                        current = recent_prices.iloc[-1]
                        change = ((current - prev_close) / prev_close) * 100
                    else:
                        change = 0
                        
                    # Fetch Market Cap to determine the size of the box
                    try:
                        mc = tickers_obj.tickers[t].info.get('marketCap', 1000000000)
                    except:
                        mc = 1000000000 # Fallback 
                        
                    # Scale down market cap purely for the chart's math to prevent JS errors
                    mc_scaled = mc / 10000000 
                    
                    sector_data.append({
                        "x": t.replace(".NS", ""), # Remove .NS for cleaner UI
                        "y": round(mc_scaled),     # 'y' dictates the SIZE of the block
                        "change": round(change, 2),# Custom data for the label
                        "fillColor": get_color(change)
                    })
                except Exception as e:
                    continue
                    
            if sector_data:
                heatmap_data.append({
                    "name": sector,
                    "data": sector_data
                })
                
        return heatmap_data
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(json.dumps(generate_heatmap()))