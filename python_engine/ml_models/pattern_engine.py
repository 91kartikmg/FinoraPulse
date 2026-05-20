import sys
import json
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema

def get_patterns(ticker, timeframe):
    df = yf.download(ticker, period="6mo", interval=timeframe)
    if df.empty: return {"error": "No data"}

    # Convert to line for simplicity
    prices = df['Close'].values
    dates = df.index.strftime('%Y-%m-%d').tolist()
    
    # 1. Detect Pivot Points (Peaks and Troughs)
    n = 5 # neighborhood
    peaks = argrelextrema(prices, np.greater, order=n)[0]
    troughs = argrelextrema(prices, np.less, order=n)[0]

    patterns = []
    
    # Example Logic: Double Top Detection
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        price_diff = abs(prices[p1] - prices[p2]) / prices[p1]
        if price_diff < 0.02: # 2% variation allowed
            patterns.append({
                "label": "DOUBLE TOP",
                "color": "red",
                "points": [
                    {"x": dates[p1], "y": float(prices[p1])},
                    {"x": dates[p2], "y": float(prices[p2])}
                ]
            })

    # Example Logic: Symmetrical Triangle (Connecting last 2 peaks/troughs)
    if len(peaks) >= 2 and len(troughs) >= 2:
        patterns.append({
            "label": "SYMMETRICAL TRIANGLE",
            "color": "red",
            "points": [
                {"x": dates[peaks[-2]], "y": float(prices[peaks[-2]])},
                {"x": dates[peaks[-1]], "y": float(prices[peaks[-1]])},
                {"x": dates[troughs[-1]], "y": float(prices[troughs[-1]])},
                {"x": dates[peaks[-2]], "y": float(prices[peaks[-2]])}
            ]
        })

    return {
        "ticker": ticker,
        "actual_price": [{"time": d, "value": float(p)} for d, p in zip(dates, prices)],
        "detected_patterns": patterns,
        "signal": "STRONG BUY" if prices[-1] > prices[peaks[-1]] else "NEUTRAL"
    }

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(json.dumps(get_patterns(ticker, "1d")))