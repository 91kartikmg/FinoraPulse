import sys
import json
import os
import pandas as pd
import numpy as np
import ta
import warnings

warnings.filterwarnings("ignore")

def run_momentum_strategy(ticker, timeframe, dataset_path, strategy_type="bollinger"):
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
        
        buy_signal = pd.Series(False, index=df.index)
        sell_signal = pd.Series(False, index=df.index)
        metrics = {}

        # ========================================================
        # STRATEGY 1: BOLLINGER BAND MEAN REVERSION
        # ========================================================
        if strategy_type == "bollinger":
            bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            lower = bb.bollinger_lband()
            mid = bb.bollinger_mavg()
            upper = bb.bollinger_hband()
            
            # Buy: Price crashes below the lower band
            buy_signal = (close < lower) & (close.shift(1) >= lower.shift(1))
            # Sell: Price touches the middle moving average (Mean Reversion complete)
            sell_signal = (close > mid) & (close.shift(1) <= mid.shift(1))
            
            metrics = {
                "metric1_name": "Lower Band", "metric1_val": float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else 0,
                "metric2_name": "Middle Band", "metric2_val": float(mid.iloc[-1]) if not pd.isna(mid.iloc[-1]) else 0,
                "signal": "BUY" if close.iloc[-1] <= lower.iloc[-1] else "SELL" if close.iloc[-1] >= mid.iloc[-1] else "NEUTRAL"
            }
            
        # ========================================================
        # STRATEGY 2: STOCHASTIC OVERBOUGHT / OVERSOLD
        # ========================================================
        elif strategy_type == "stochastic":
            stoch = ta.momentum.StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
            k_line = stoch.stoch()
            d_line = stoch.stoch_signal()
            
            # Buy: %K drops below 20 (oversold) and crosses above %D
            buy_signal = (k_line < 20) & (k_line > d_line) & (k_line.shift(1) <= d_line.shift(1))
            # Sell: %K goes above 80 (overbought) and crosses below %D
            sell_signal = (k_line > 80) & (k_line < d_line) & (k_line.shift(1) >= d_line.shift(1))
            
            metrics = {
                "metric1_name": "Stochastic %K", "metric1_val": float(k_line.iloc[-1]) if not pd.isna(k_line.iloc[-1]) else 0,
                "metric2_name": "Stochastic %D", "metric2_val": float(d_line.iloc[-1]) if not pd.isna(d_line.iloc[-1]) else 0,
                "signal": "BUY" if k_line.iloc[-1] < 20 else "SELL" if k_line.iloc[-1] > 80 else "NEUTRAL"
            }

        # ========================================================
        # STRATEGY 3: RSI DIVERGENCE
        # ========================================================
        elif strategy_type == "rsi_div":
            rsi = ta.momentum.rsi(close, window=14)
            
            # Find rolling 20-day minimums to detect divergence
            rolling_min_close = close.rolling(20).min()
            rolling_min_rsi = rsi.rolling(20).min()
            
            rolling_max_close = close.rolling(20).max()
            rolling_max_rsi = rsi.rolling(20).max()
            
            # Buy (Bullish Div): Price hits 20-day low, but RSI is higher than its 20-day low (Momentum exhaustion)
            buy_signal = (close <= rolling_min_close) & (rsi > rolling_min_rsi + 3) & (rsi < 40)
            # Sell (Bearish Div): Price hits 20-day high, but RSI fails to hit a new high
            sell_signal = (close >= rolling_max_close) & (rsi < rolling_max_rsi - 3) & (rsi > 60)
            
            metrics = {
                "metric1_name": "Current RSI", "metric1_val": float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 0,
                "metric2_name": "20-Day RSI Low", "metric2_val": float(rolling_min_rsi.iloc[-1]) if not pd.isna(rolling_min_rsi.iloc[-1]) else 0,
                "signal": "BUY" if buy_signal.iloc[-1] else "SELL" if sell_signal.iloc[-1] else "NEUTRAL"
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
    strategy_type = sys.argv[4] if len(sys.argv) > 4 else "bollinger" 
    run_momentum_strategy(ticker, timeframe, dataset_path, strategy_type)