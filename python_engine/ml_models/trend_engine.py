import sys
import json
import os
import pandas as pd
import ta
import warnings

warnings.filterwarnings("ignore")

def run_trend_strategy(ticker, timeframe, dataset_path, strategy_type="sma"):
    file_path = os.path.join(dataset_path, f"{ticker}_{timeframe}.csv")

    try:
        if os.path.exists(file_path):
            df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        else:
            import yfinance as yf
            df = yf.download(ticker, period="2y", interval="1d")

        df = df.dropna()
        if df.empty:
            raise ValueError("Dataset empty")

        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else None

        times = df.index.strftime('%Y-%m-%d').tolist()
        prices = close.tolist()

        # ==========================================
        # 🔥 OVERLOAD STRATEGY (REAL ONE)
        # ==========================================
        if strategy_type == "overload":
            sma = ta.trend.sma_indicator(close, window=50)
            rsi = ta.momentum.rsi(close, window=14)

            output = {
                "times": times,
                "prices": prices,
                "sma": sma.fillna(0).tolist(),
                "rsi": rsi.fillna(0).tolist(),
                "volume": volume.fillna(0).tolist() if volume is not None else [],
                "metrics": {
                    "sma": float(sma.iloc[-1]),
                    "rsi": float(rsi.iloc[-1]),
                    "volume": float(volume.iloc[-1]) if volume is not None else 0,
                    "signal": "NEUTRAL"
                }
            }

            print(json.dumps(output))
            return

        # ==========================================
        # SMA STRATEGY
        # ==========================================
        if strategy_type == "sma":
            sma50 = ta.trend.sma_indicator(close, window=50)
            sma200 = ta.trend.sma_indicator(close, window=200)

            output = {
                "times": times,
                "prices": prices,
                "sma50": sma50.fillna(0).tolist(),
                "sma200": sma200.fillna(0).tolist(),
                "metrics": {
                    "metric1_name": "SMA 50",
                    "metric1_val": float(sma50.iloc[-1]),
                    "metric2_name": "SMA 200",
                    "metric2_val": float(sma200.iloc[-1]),
                    "signal": "BUY" if sma50.iloc[-1] > sma200.iloc[-1] else "SELL"
                }
            }

        # ==========================================
        # MACD STRATEGY
        # ==========================================
        elif strategy_type == "macd":
            macd = ta.trend.MACD(close)
            macd_line = macd.macd()
            signal_line = macd.macd_signal()

            output = {
                "times": times,
                "prices": prices,
                "macd": macd_line.fillna(0).tolist(),
                "signal_line": signal_line.fillna(0).tolist(),
                "metrics": {
                    "metric1_name": "MACD",
                    "metric1_val": float(macd_line.iloc[-1]),
                    "metric2_name": "Signal",
                    "metric2_val": float(signal_line.iloc[-1]),
                    "signal": "BUY" if macd_line.iloc[-1] > signal_line.iloc[-1] else "SELL"
                }
            }

        # ==========================================
        # PSAR STRATEGY
        # ==========================================
        elif strategy_type == "psar":
            psar = ta.trend.PSARIndicator(high, low, close).psar()

            output = {
                "times": times,
                "prices": prices,
                "psar": psar.fillna(0).tolist(),
                "metrics": {
                    "metric1_name": "PSAR",
                    "metric1_val": float(psar.iloc[-1]),
                    "metric2_name": "Price",
                    "metric2_val": float(close.iloc[-1]),
                    "signal": "BUY" if close.iloc[-1] > psar.iloc[-1] else "SELL"
                }
            }

        print(json.dumps(output))

    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    ticker = sys.argv[1]
    timeframe = sys.argv[2]
    dataset_path = sys.argv[3]
    strategy_type = sys.argv[4] if len(sys.argv) > 4 else "sma"

    run_trend_strategy(ticker, timeframe, dataset_path, strategy_type)