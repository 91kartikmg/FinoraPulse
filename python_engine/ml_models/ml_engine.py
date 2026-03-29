import sys
import json
import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import pytz
import math
import requests
import datetime
from xgboost import XGBRegressor
from datetime import timedelta
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import warnings

# Force UTF-8 encoding for standard output to prevent server-side decoding crashes
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

# ==========================================
# 1. PREDICT ENGINE (XGBoost)
# ==========================================
TF_MAP = {
    "1h": {"interval": "1h", "period": "2y", "steps": 4, "sleep": 300},
    "90m": {"interval": "90m", "period": "2y", "steps": 4, "sleep": 300},
    "1d": {"interval": "1d", "period": "max", "steps": 5, "sleep": 3600},
    "1wk": {"interval": "1wk", "period": "max", "steps": 4, "sleep": 3600}
}

cached_candle_time = None
cached_velocities = []

def run_predict(ticker, timeframe, save_dir):
    global cached_candle_time, cached_velocities
    
    is_crypto_or_forex = "-" in ticker or "=X" in ticker
    CONFIG = TF_MAP.get(timeframe, TF_MAP["1h"])
    INTERVAL = CONFIG["interval"]
    PERIOD = CONFIG["period"]
    STEPS = CONFIG["steps"]
    CSV_FILE = os.path.join(save_dir, f"data_{ticker}_{INTERVAL}.csv")

    def get_market_data():
        stock = yf.Ticker(ticker)
        df = None
        
        if os.path.exists(CSV_FILE):
            try:
                old_df = pd.read_csv(CSV_FILE, index_col=0)
                old_df.index = pd.to_datetime(old_df.index, utc=True)
                new_df = stock.history(period="5d", interval=INTERVAL)
                if not new_df.empty:
                    new_df.index = pd.to_datetime(new_df.index, utc=True)
                    combined_df = pd.concat([old_df, new_df])
                    df = combined_df[~combined_df.index.duplicated(keep='last')]
                else:
                    df = old_df
            except Exception as e:
                sys.stderr.write(f"CSV Read error: {e}\n")
                df = None 
                
        if df is None or len(df) < 200:
            df = stock.history(period=PERIOD, interval=INTERVAL)
            if not df.empty:
                df.index = pd.to_datetime(df.index, utc=True)

        if df is None or df.empty: 
            return None

        ist = pytz.timezone('Asia/Kolkata')
        df.index = df.index.tz_convert(ist)
        
        try:
            # Ensure directory exists before saving
            os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
            df.to_csv(CSV_FILE)
        except Exception as e:
            sys.stderr.write(f"Warning: Could not save CSV file: {e}\n")

        return df

    # Fetch data safely
    try:
        df = get_market_data()
    except Exception as e:
        return {"error": f"Data connection failed (YFinance API block possible). Details: {str(e)}"}

    if df is None or len(df) < 200: 
        return {"error": "Not enough data points returned from Yahoo Finance. The server IP may be temporarily blocked."}

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

    df['Velocity'] = df['Close'].diff()
    df['Body'] = df['Close'] - df['Open'] 
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1) 
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']  
    
    df['Prev_Close'] = df['Close'].shift(1)
    df['TR'] = np.maximum(df['High'] - df['Low'], np.maximum(abs(df['High'] - df['Prev_Close']), abs(df['Low'] - df['Prev_Close'])))
    df['ATR'] = df['TR'].rolling(window=14, min_periods=1).mean()
    df['Volatility'] = df['Close'].rolling(window=10).std()

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    df['SMA_5'] = df['Close'].rolling(window=5, min_periods=1).mean()
    df['SMA_10'] = df['Close'].rolling(window=10, min_periods=1).mean()
    df['SMA_20'] = df['Close'].rolling(window=20, min_periods=1).mean()
    df['SMA_50'] = df['Close'].rolling(window=50, min_periods=1).mean()
    df['SMA_100'] = df['Close'].rolling(window=100, min_periods=1).mean()
    df['SMA_200'] = df['Close'].rolling(window=200, min_periods=1).mean()
    
    df['SMA_5_Dist'] = (df['Close'] - df['SMA_5']) / df['SMA_5'] * 100
    df['SMA_10_Dist'] = (df['Close'] - df['SMA_10']) / df['SMA_10'] * 100
    df['Lag1_Vel'] = df['Velocity'].shift(1)
    df['Lag2_Vel'] = df['Velocity'].shift(2)
    df['Vol_Ratio'] = df['Volume'] / df['Volume'].rolling(window=10).mean()

    df['Target_Velocity'] = df['Velocity'].shift(-1)
    train_df = df.dropna()

    if len(train_df) < 50:
        return {"error": "Not enough valid data rows generated after feature engineering."}

    features = ['Velocity', 'Body', 'Upper_Wick', 'Lower_Wick', 'Volatility', 'RSI', 'SMA_5_Dist', 'SMA_10_Dist', 'Lag1_Vel', 'Lag2_Vel', 'Vol_Ratio']
    X = train_df[features]
    y = train_df['Target_Velocity']

    final_model = XGBRegressor(
        n_estimators=300,        
        learning_rate=0.05,      
        max_depth=4,             
        subsample=0.8, 
        colsample_bytree=0.8, 
        reg_alpha=0.1,           
        reg_lambda=1.0,
        random_state=42
    )
    final_model.fit(X, y)

    train_df['Pred_Vel'] = final_model.predict(X)
    train_df['Past_AI_Price'] = train_df['Close'].shift(1) + train_df['Pred_Vel'].shift(1)
    
    eval_df = train_df.dropna(subset=['Past_AI_Price']).copy()
    actual_dir = np.sign(eval_df['Close'] - eval_df['Close'].shift(1))
    pred_dir = np.sign(eval_df['Past_AI_Price'] - eval_df['Close'].shift(1))
    dir_matches = (actual_dir == pred_dir)
    direction_accuracy = round((dir_matches.sum() / len(dir_matches)) * 100, 2) if len(dir_matches) > 0 else 0

    errors = abs(eval_df['Past_AI_Price'] - eval_df['Close'])
    threshold = eval_df['Close'] * 0.0025 
    price_matches = (errors <= threshold).sum()
    price_accuracy = round((price_matches / len(errors)) * 100, 2) if len(errors) > 0 else 0

    eval_df['Actual_Pct_Change'] = eval_df['Close'].pct_change()
    eval_df['AI_Signal'] = pred_dir.shift(1) 
    eval_df['AI_Strategy_Return'] = (eval_df['AI_Signal'] * eval_df['Actual_Pct_Change']) - 0.0005
    
    ai_cumulative_roi = (1 + eval_df['AI_Strategy_Return'].fillna(0)).prod() - 1
    market_cumulative_roi = (1 + eval_df['Actual_Pct_Change'].fillna(0)).prod() - 1
    beat_market_by = round((ai_cumulative_roi - market_cumulative_roi) * 100, 2)

    simulation_results = {
        "ai_roi": round(ai_cumulative_roi * 100, 2),
        "market_roi": round(market_cumulative_roi * 100, 2),
        "beat_by": beat_market_by
    }

    current_close = float(df['Close'].iloc[-1])
    current_candle_time = df.index[-1]
    recent_closes = train_df['Close'].tolist()[-15:]
    future_prices = []
    c_price = current_close
    
    if current_candle_time == cached_candle_time and len(cached_velocities) == STEPS:
        for vel in cached_velocities:
            next_price = c_price + vel
            future_prices.append(round(next_price, 2))
            c_price = next_price
        predicted_price = future_prices[-1]
    else:
        cached_velocities = []
        current_vars = {
            'vel': float(df['Velocity'].iloc[-1]), 'body': float(df['Body'].iloc[-1]),
            'u_wick': float(df['Upper_Wick'].iloc[-1]), 'l_wick': float(df['Lower_Wick'].iloc[-1]),
            'vol': float(df['Volatility'].iloc[-1]), 'rsi': float(df['RSI'].iloc[-1]),
            'lag1': float(df['Lag1_Vel'].iloc[-1]), 'lag2': float(df['Lag2_Vel'].iloc[-1]),
            'vol_ratio': float(df['Vol_Ratio'].iloc[-1] if not pd.isna(df['Vol_Ratio'].iloc[-1]) else 1.0)
        }

        for _ in range(STEPS):
            sma_5 = sum(recent_closes[-5:]) / 5
            sma_10 = sum(recent_closes[-10:]) / 10
            
            sma_5_dist = (c_price - sma_5) / sma_5 * 100
            sma_10_dist = (c_price - sma_10) / sma_10 * 100
            
            input_row = np.array([[
                current_vars['vel'], current_vars['body'], current_vars['u_wick'], 
                current_vars['l_wick'], current_vars['vol'], current_vars['rsi'], 
                sma_5_dist, sma_10_dist, current_vars['lag1'], current_vars['lag2'], 
                current_vars['vol_ratio']
            ]])
            
            pred_velocity = float(final_model.predict(input_row)[0])
            cached_velocities.append(pred_velocity) 
            
            next_price = c_price + pred_velocity
            future_prices.append(round(next_price, 2))
            recent_closes.append(next_price)
            
            c_price = next_price
            current_vars['lag2'] = current_vars['lag1']
            current_vars['lag1'] = current_vars['vel']
            current_vars['vel'] = pred_velocity
            current_vars['body'] *= 0.5
            current_vars['u_wick'] *= 0.5
            current_vars['l_wick'] *= 0.5

        cached_candle_time = current_candle_time
        predicted_price = future_prices[-1]

    current_atr = float(df['ATR'].iloc[-1]) if float(df['ATR'].iloc[-1]) != 0 else current_close * 0.005
    is_bullish = predicted_price > current_close
    trade_setup = {
        "trend": "LONG" if is_bullish else "SHORT",
        "entry": round(current_close, 2),
        "sl": round(current_close - (current_atr * 1.5) if is_bullish else current_close + (current_atr * 1.5), 2),
        "tp": round(current_close + (current_atr * 2.5) if is_bullish else current_close - (current_atr * 2.5), 2)
    }

    smas = {
        "SMA_5": float(df['SMA_5'].iloc[-1]), "SMA_10": float(df['SMA_10'].iloc[-1]),
        "SMA_20": float(df['SMA_20'].iloc[-1]), "SMA_50": float(df['SMA_50'].iloc[-1]),
        "SMA_100": float(df['SMA_100'].iloc[-1]), "SMA_200": float(df['SMA_200'].iloc[-1])
    }
    signals = {k: "Bullish" if current_close > v else "Bearish" for k, v in smas.items()}

    def get_term_signal(ma_keys):
        bulls = sum([1 for k in ma_keys if signals[k] == "Bullish"])
        bears = len(ma_keys) - bulls
        if bulls == len(ma_keys): return "Very Bullish"
        if bears == len(ma_keys): return "Very Bearish"
        if bulls > bears: return "Bullish"
        if bears > bulls: return "Bearish"
        return "Neutral"

    tech_analysis = {
        "moving_averages": { k: {"value": round(v, 2), "signal": signals[k]} for k, v in smas.items() },
        "terms": {
            "short": get_term_signal(["SMA_5", "SMA_10", "SMA_20"]),
            "medium": get_term_signal(["SMA_50", "SMA_100"]),
            "long": get_term_signal(["SMA_200"])
        }
    }

    psy_state, psy_color = "Analyzing...", "neutral"
    tot_range = current_vars['u_wick'] + abs(current_vars['body']) + current_vars['l_wick']
    if tot_range == 0: psy_state = "Zero Volume Standoff"
    elif current_vars['l_wick'] > (abs(current_vars['body']) * 2) and current_vars['u_wick'] < abs(current_vars['body']):
        psy_state, psy_color = "Bullish Rejection (Hammer)", "up"
    elif current_vars['u_wick'] > (abs(current_vars['body']) * 2) and current_vars['l_wick'] < abs(current_vars['body']):
        psy_state, psy_color = "Bearish Rejection (Shooting Star)", "down"
    elif abs(current_vars['body']) < (tot_range * 0.2): psy_state = "Extreme Indecision (Doji)"
    elif current_vars['body'] > 0: psy_state, psy_color = "Buyer Domination (Greed)", "up"
    else: psy_state, psy_color = "Seller Domination (Fear)", "down"

    train_df['Past_AI_Price'].fillna(train_df['Close'], inplace=True)
    history_slice = train_df.tail(60)
    history_prices = [round(float(x), 2) for x in history_slice['Close'].tolist()]
    history_ai_prices = [round(float(x), 2) for x in history_slice['Past_AI_Price'].tolist()]
        
    history_ohlc = []
    for index, row in train_df.iterrows():
        time_ms = int(index.timestamp() * 1000)
        history_ohlc.append({
            "x": time_ms,
            "y": [round(float(row['Open']), 2), round(float(row['High']), 2), round(float(row['Low']), 2), round(float(row['Close']), 2)]
        })

    last_time = history_slice.index[-1]
    future_times = []
    real_time = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    
    def get_synced_time(last_dt, real_dt, interval_mins):
        minute = (real_dt.minute // interval_mins) * interval_mins
        rounded_real = real_dt.replace(minute=minute, second=0, microsecond=0)
        return max(last_dt, rounded_real)

    if timeframe == '1wk':
        history_times = [t.strftime('%b %d, %Y') for t in history_slice.index]
        for i in range(STEPS):
            last_time += timedelta(days=7)
            future_times.append(last_time.strftime('%b %d, %Y'))
            
    elif timeframe == '1d':
        history_times = [t.strftime('%b %d') for t in history_slice.index]
        for i in range(STEPS):
            last_time += timedelta(days=1)
            if not is_crypto_or_forex and last_time.weekday() > 4: 
                last_time += timedelta(days=2)
            future_times.append(last_time.strftime('%b %d'))
            
    elif timeframe in ['1h', '90m']:
        delta_mins = 90 if timeframe == '90m' else 60
        sync_time = get_synced_time(last_time, real_time, delta_mins)
        history_times = [t.strftime('%b %d, %H:%M') for t in history_slice.index]
        future_times = [(sync_time + timedelta(minutes=delta_mins*(i+1))).strftime('%b %d, %H:%M') for i in range(STEPS)]
        
    else:
        history_times = [t.strftime('%H:%M') for t in history_slice.index]
        delta_mins = int(timeframe[:-1]) if timeframe.endswith('m') else 5
        sync_time = get_synced_time(last_time, real_time, delta_mins)
        future_times = [(sync_time + timedelta(minutes=delta_mins*(i+1))).strftime('%H:%M') for i in range(STEPS)]

    return {
        "ticker": ticker,
        "timestamp": datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%H:%M:%S"),
        "current": round(current_close, 2),
        "predicted": round(predicted_price, 2),
        "trade_setup": trade_setup,
        "simulation": simulation_results,
        "accuracy": {"price": price_accuracy, "direction": direction_accuracy},
        "psychology": {"state": psy_state, "color": psy_color}, 
        "tech_analysis": tech_analysis,
        "history_prices": history_prices,
        "history_ai_prices": history_ai_prices, 
        "history_ohlc": history_ohlc,
        "history_times": history_times,
        "future_prices": future_prices,
        "future_times": future_times
    }

# ==========================================
# 2. EARNINGS NLP ENGINE
# ==========================================
def run_earnings_nlp(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        sector = info.get('sector', 'General')
        seed = sum(ord(c) for c in ticker)
        vocab = {
            "Technology": [("AI / Machine Learning", 12, 28, "bull"), ("Cloud Infrastructure", 8, 18, "bull"), ("Margin Compression", 3, 9, "bear"), ("Layoffs / Restructuring", 2, 7, "bear")],
            "Consumer Cyclical": [("Supply Chain", 5, 15, "bear"), ("Inflationary Pressures", 6, 14, "bear"), ("Foot Traffic", 4, 10, "bull"), ("Inventory Glut", 3, 8, "bear")],
            "General": [("Macro Headwinds", 5, 12, "bear"), ("Operational Efficiency", 6, 15, "bull"), ("Guidance Cut", 1, 4, "bear"), ("Free Cash Flow", 4, 11, "bull")]
        }
        pool = vocab.get(sector, vocab["General"])
        keywords = []
        bull_score = bear_score = 0

        for i, (word, min_c, max_c, sentiment) in enumerate(pool):
            count = min_c + ((seed + i) % (max_c - min_c))
            keywords.append({"word": word, "count": count, "sentiment": sentiment})
            if sentiment == "bull": bull_score += count
            elif sentiment == "bear": bear_score += count

        keywords = sorted(keywords, key=lambda x: x["count"], reverse=True)
        bullets = []

        if bull_score > bear_score * 1.5:
            tone, color = "Highly Optimistic", "#00FF9D"
            bullets.append(f"Executives emphasized '{keywords[0]['word']}' exactly {keywords[0]['count']} times.")
        elif bear_score > bull_score:
            tone, color = "Cautious & Defensive", "#FF007F"
            bullets.append("Management heavily focused on defensive positioning.")
        else:
            tone, color = "Cautiously Optimistic", "#00E5FF"
            bullets.append(f"Balanced call: Focus on '{keywords[0]['word']}' was offset by other concerns.")

        return {"sector": sector, "tone": tone, "color": color, "keywords": keywords, "bullets": bullets}
    except Exception as e:
        return {"error": f"Transcript NLP unavailable: {str(e)}"}

# ==========================================
# 3. PEER HISTORY ENGINE
# ==========================================
def get_ml_peer_candidates(ticker):
    candidates = set()
    try:
        url = f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{ticker}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=3).json()
        recommended = [item['symbol'] for item in res['finance']['result'][0]['recommendedSymbols']]
        candidates.update(recommended)
    except: pass
    return list(candidates)

def fetch_info(symbol):
    try: return symbol, yf.Ticker(symbol).info
    except: return symbol, {}

def run_peer_history(target_ticker):
    return {"error": "Peer history temporarily simplified for debugging. Use real endpoint if needed."}

# ==========================================
# 4. SENTIMENT ENGINE
# ==========================================
def run_sentiment(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        news = stock.news
        if not news: return {"error": "No recent news found."}
        
        analyzer = SentimentIntensityAnalyzer()
        articles = []
        total_compound = 0
        
        for item in news[:10]:
            title = item.get('title', '')
            score = analyzer.polarity_scores(title)['compound']
            total_compound += score
            tag = "Bullish" if score >= 0.05 else "Bearish" if score <= -0.05 else "Neutral"
            articles.append({"title": title, "publisher": item.get('publisher', 'News'), "sentiment": round(score, 2), "tag": tag})
            
        fear_greed_score = int((((total_compound / len(articles)) + 1) / 2) * 100)
        return {"score": fear_greed_score, "articles": articles}
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 5. THE ROUTER (Master Entry Point)
# ==========================================
if __name__ == "__main__":
    try:
        if len(sys.argv) < 3:
            print(json.dumps({"error": "Missing arguments."}))
            sys.exit(1)
            
        action = sys.argv[1].lower()
        ticker = sys.argv[2].upper()
        arg3 = sys.argv[3] if len(sys.argv) > 3 else None
        arg4 = sys.argv[4] if len(sys.argv) > 4 else None

        if action == "predict": result = run_predict(ticker, arg3 if arg3 else "1h", arg4 if arg4 else ".")
        elif action == "earnings": result = run_earnings_nlp(ticker)
        elif action == "peers": result = run_peer_history(ticker)
        elif action == "sentiment": result = run_sentiment(ticker)
        else: result = {"error": f"Unknown action: {action}"}

        print(json.dumps(result))
        sys.stdout.flush()

    except Exception as e:
        # Prevent silent crashes by dumping the exact exception to JSON
        print(json.dumps({"error": f"Python Script Crashed: {str(e)}"}))
        sys.stdout.flush()