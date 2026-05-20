import sys
import json
import os
import pandas as pd
import numpy as np
import ta
import warnings

# Suppress pandas warnings for clean JSON output
warnings.filterwarnings("ignore")

def run_overload_strategy(ticker, timeframe, dataset_path):
    # 1. Point to the dataset already downloaded by the predict page
    file_path = os.path.join(dataset_path, f"{ticker}_{timeframe}.csv")
    
    try:
        # Load existing data. If missing, fall back to yfinance just in case
        if os.path.exists(file_path):
            df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        else:
            import yfinance as yf
            df = yf.download(ticker, period="1y", interval="1d" if timeframe=='1d' else timeframe)
            
        df = df.dropna()
        if df.empty:
            raise ValueError("Dataset is empty.")
            
        # Extract Close price as a 1D Series
        close_col = df['Close'].squeeze() if 'Close' in df.columns else df.iloc[:, 3].squeeze()
        
        # 2. Calculate the Overload Indicators
        sma_200 = ta.trend.sma_indicator(close_col, window=200)
        rsi = ta.momentum.rsi(close_col, window=14)
        bb = ta.volatility.BollingerBands(close=close_col, window=20, window_dev=2)
        bb_lower = bb.bollinger_lband()
        bb_upper = bb.bollinger_hband()
        macd = ta.trend.MACD(close=close_col)
        macd_line = macd.macd()
        macd_signal = macd.macd_signal()
        
        # 3. Consensus Rules (Slightly relaxed RSI thresholds so signals actually trigger on a 1y chart)
        # BUY: Trend UP + Oversold + Lower Volatility Band Hit + Momentum Reversing Up
        buy_signal = (close_col > sma_200) & (rsi < 40) & (close_col <= bb_lower) & (macd_line > macd_signal)
        
        # SELL: Overbought + Upper Volatility Band Hit + Momentum Reversing Down
        sell_signal = (rsi > 70) & (close_col >= bb_upper) & (macd_line < macd_signal)
        
        # 4. Prepare Data for Frontend Charting
        times = df.index.strftime('%Y-%m-%d').tolist()
        prices = close_col.tolist()
        
        signals = []
        for i in range(len(df)):
            if buy_signal.iloc[i]:
                signals.append({"x": times[i], "y": prices[i], "type": "BUY"})
            elif sell_signal.iloc[i]:
                signals.append({"x": times[i], "y": prices[i], "type": "SELL"})
                
        # Current Real-Time Metrics
        current_metrics = {
            "rsi": rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 0,
            "sma200": sma_200.iloc[-1] if not pd.isna(sma_200.iloc[-1]) else 0,
            "macd": macd_line.iloc[-1] if not pd.isna(macd_line.iloc[-1]) else 0,
            "signal": "BUY" if buy_signal.iloc[-1] else "SELL" if sell_signal.iloc[-1] else "NEUTRAL"
        }
        
        output = {
            "times": times,
            "prices": prices,
            "signals": signals,
            "metrics": current_metrics
        }
        
        # Print JSON securely to Node.js
        print(json.dumps(output))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    ticker = sys.argv[1]
    timeframe = sys.argv[2]
    dataset_path = sys.argv[3]
    run_overload_strategy(ticker, timeframe, dataset_path)