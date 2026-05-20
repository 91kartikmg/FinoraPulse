import sys
import json
import os
import pandas as pd
import numpy as np
import ta
import warnings

warnings.filterwarnings("ignore")

def run_volatility_strategy(ticker, timeframe, dataset_path, strategy_type="donchian"):
    file_path = os.path.join(dataset_path, f"{ticker}_{timeframe}.csv")
    
    try:
        if os.path.exists(file_path):
            df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        else:
            import yfinance as yf
            df = yf.download(ticker, period="2y", interval="1d" if timeframe=='1d' else timeframe)
            
        df = df.dropna()
        if df.empty:
            raise ValueError("Dataset is empty.")
            
        close = df['Close'].squeeze() if 'Close' in df.columns else df.iloc[:, 3].squeeze()
        high = df['High'].squeeze() if 'High' in df.columns else df.iloc[:, 1].squeeze()
        low = df['Low'].squeeze() if 'Low' in df.columns else df.iloc[:, 2].squeeze()
        volume = df['Volume'].squeeze() if 'Volume' in df.columns else df.iloc[:, 4].squeeze()
        
        buy_signal = pd.Series(False, index=df.index)
        sell_signal = pd.Series(False, index=df.index)
        metrics = {}

        # ========================================================
        # STRATEGY 1: DONCHIAN CHANNEL BREAKOUT (Turtle Strategy)
        # ========================================================
        if strategy_type == "donchian":
            # 20-day highs and lows
            dc_high = high.rolling(20).max()
            dc_low = low.rolling(20).min()
            
            # Buy: Price closes above the 20-day high of yesterday
            buy_signal = (close > dc_high.shift(1))
            # Sell: Price closes below the 20-day low of yesterday
            sell_signal = (close < dc_low.shift(1))
            
            metrics = {
                "metric1_name": "20-Day High (Resistance)", "metric1_val": float(dc_high.iloc[-1]) if not pd.isna(dc_high.iloc[-1]) else 0,
                "metric2_name": "20-Day Low (Support)", "metric2_val": float(dc_low.iloc[-1]) if not pd.isna(dc_low.iloc[-1]) else 0,
                "signal": "BUY (BREAKOUT)" if buy_signal.iloc[-1] else "SELL (BREAKOUT)" if sell_signal.iloc[-1] else "NEUTRAL"
            }

        # ========================================================
        # STRATEGY 2: THE VOLATILITY SQUEEZE (Bollinger Pinch)
        # ========================================================
        elif strategy_type == "squeeze":
            bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            bb_upper = bb.bollinger_hband()
            bb_lower = bb.bollinger_lband()
            bb_width = bb.bollinger_wband()
            
            # Identify a squeeze: Bollinger Band width is near its 50-day minimum (highly compressed)
            rolling_min_width = bb_width.rolling(50).min()
            is_squeeze = (bb_width <= rolling_min_width * 1.2) # Within 20% of the tightest it has been in 50 days
            
            # Buy: We were in a squeeze recently, and price explodes above the upper band
            buy_signal = is_squeeze.shift(1).rolling(3).max() & (close > bb_upper)
            # Sell: We were in a squeeze recently, and price dumps below the lower band
            sell_signal = is_squeeze.shift(1).rolling(3).max() & (close < bb_lower)
            
            metrics = {
                "metric1_name": "Bandwidth (Compression)", "metric1_val": float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 0,
                "metric2_name": "Squeeze Status", "metric2_val": 1 if is_squeeze.iloc[-1] else 0, # 1 for True, 0 for False
                "signal": "BUY (UP-SQUEEZE)" if buy_signal.iloc[-1] else "SELL (DOWN-SQUEEZE)" if sell_signal.iloc[-1] else "SQUEEZING" if is_squeeze.iloc[-1] else "NORMAL"
            }

        # ========================================================
        # STRATEGY 3: SUPPORT/RESISTANCE BREAKOUT WITH VOLUME
        # ========================================================
        elif strategy_type == "vol_breakout":
            res_level = close.rolling(20).max()
            sup_level = close.rolling(20).min()
            avg_vol = volume.rolling(20).mean()
            
            # Buy: Closes above 20d resistance WITH volume 1.5x higher than average
            buy_signal = (close > res_level.shift(1)) & (volume > avg_vol * 1.5)
            # Sell: Closes below 20d support WITH volume 1.5x higher than average
            sell_signal = (close < sup_level.shift(1)) & (volume > avg_vol * 1.5)
            
            metrics = {
                "metric1_name": "Current Vol vs Avg", "metric1_val": float(volume.iloc[-1] / avg_vol.iloc[-1]) if avg_vol.iloc[-1] > 0 else 0,
                "metric2_name": "Resistance Level", "metric2_val": float(res_level.iloc[-1]) if not pd.isna(res_level.iloc[-1]) else 0,
                "signal": "BUY (VOL BREAKOUT)" if buy_signal.iloc[-1] else "SELL (VOL BREAKDOWN)" if sell_signal.iloc[-1] else "NEUTRAL"
            }

        # 4. Prepare Data for Frontend Charting
        times = df.index.strftime('%Y-%m-%d').tolist()
        prices = close.tolist()
        
        signals = []
        for i in range(len(df)):
            if buy_signal.iloc[i]:
                signals.append({"x": times[i], "y": prices[i], "type": "BUY"})
            elif sell_signal.iloc[i]:
                signals.append({"x": times[i], "y": prices[i], "type": "SELL"})
                
        output = {
            "times": times,
            "prices": prices,
            "signals": signals,
            "metrics": metrics,
            "strategy": strategy_type
        }
        
        print(json.dumps(output))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    ticker = sys.argv[1]
    timeframe = sys.argv[2]
    dataset_path = sys.argv[3]
    strategy_type = sys.argv[4] if len(sys.argv) > 4 else "donchian" 
    run_volatility_strategy(ticker, timeframe, dataset_path, strategy_type)