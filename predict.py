import sys
import json
import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import pytz
from xgboost import XGBRegressor
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# --- 1. DYNAMIC CONFIGURATION ---
TICKER = sys.argv[1] if len(sys.argv) > 1 else "SBIN.NS"
TIMEFRAME = sys.argv[2] if len(sys.argv) > 2 else "1h" # Defaulted to 1h
SAVE_DIR = sys.argv[3] if len(sys.argv) > 3 else "."

is_crypto_or_forex = "-" in TICKER or "=X" in TICKER

TF_MAP = {
    "1h": {"interval": "1h", "period": "2y", "steps": 4, "sleep": 300},    # 4 hours ahead
    "90m": {"interval": "90m", "period": "2y", "steps": 4, "sleep": 300},  # 6 hours ahead
    "1d": {"interval": "1d", "period": "max", "steps": 5, "sleep": 3600},  # 5 days ahead
    "1wk": {"interval": "1wk", "period": "max", "steps": 4, "sleep": 3600} # 4 weeks ahead
}

# Fallback in case of unexpected timeframe
CONFIG = TF_MAP.get(TIMEFRAME, TF_MAP["1h"])
INTERVAL = CONFIG["interval"]
PERIOD = CONFIG["period"]
STEPS = CONFIG["steps"]
LOG_FREQ_SECONDS = CONFIG["sleep"]

CSV_FILE = os.path.join(SAVE_DIR, f"data_{TICKER}_{INTERVAL}.csv")

# 🌟 GLOBALS FOR PREDICTION LOCK
cached_candle_time = None
cached_velocities = []

def get_market_data():
    stock = yf.Ticker(TICKER)
    if not os.path.exists(CSV_FILE):
        df = stock.history(period=PERIOD, interval=INTERVAL)
    else:
        new_df = stock.history(period="5d", interval=INTERVAL)
        try:
            old_df = pd.read_csv(CSV_FILE, index_col=0)
            old_df.index = pd.to_datetime(old_df.index, utc=True)
            if not new_df.empty:
                new_df.index = pd.to_datetime(new_df.index, utc=True)
                combined_df = pd.concat([old_df, new_df])
                combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                df = combined_df
            else:
                df = old_df
        except:
            df = new_df 

    if df is None or df.empty: 
        print(f"[DATASET ENGINE] ⚠️ Warning: yfinance returned empty data for {TICKER}", file=sys.stderr)
        return None

    df.index = pd.to_datetime(df.index, utc=True)
    ist = pytz.timezone('Asia/Kolkata')
    df.index = df.index.tz_convert(ist)
    
    try:
        df.to_csv(CSV_FILE)
    except Exception as e:
        pass

    return df

def run_prediction():
    global cached_candle_time, cached_velocities
    
    df = get_market_data()
    if df is None or len(df) < 200: 
        return {"error": "Waiting for enough data points..."}

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

    # --- 2. ADVANCED FEATURE ENGINEERING & MOVING AVERAGES ---
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

    features = ['Velocity', 'Body', 'Upper_Wick', 'Lower_Wick', 'Volatility', 'RSI', 'SMA_5_Dist', 'SMA_10_Dist', 'Lag1_Vel', 'Lag2_Vel', 'Vol_Ratio']
    X = train_df[features]
    y = train_df['Target_Velocity']

    # --- 3. TRAIN THE AI MODEL ---
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

    # --- 4. BACKTESTING ---
    train_df['Pred_Vel'] = final_model.predict(X)
    train_df['Past_AI_Price'] = train_df['Close'].shift(1) + train_df['Pred_Vel'].shift(1)
    
    # --- 5. CALCULATE ACCURACY & PnL SIMULATOR ---
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

    # --- 6. LIVE MULTI-STEP PREDICTION (WITH LOCKING) ---
    current_close = float(df['Close'].iloc[-1])
    current_candle_time = df.index[-1]
    recent_closes = train_df['Close'].tolist()[-15:]
    future_prices = []
    
    c_price = current_close
    
    # 🌟 If we are in the same candle interval, reuse the locked trend shape
    if current_candle_time == cached_candle_time and len(cached_velocities) == STEPS:
        for vel in cached_velocities:
            next_price = c_price + vel
            future_prices.append(round(next_price, 2))
            c_price = next_price
        predicted_price = future_prices[-1]
    
    # 🌟 Otherwise, new candle closed! Calculate new trajectory and lock it.
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
            cached_velocities.append(pred_velocity) # Save velocity to lock
            
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

    # --- 7. SMART TRADE SETUP ENGINE ---
    current_atr = float(df['ATR'].iloc[-1]) if float(df['ATR'].iloc[-1]) != 0 else current_close * 0.005
    is_bullish = predicted_price > current_close
    trade_setup = {
        "trend": "LONG" if is_bullish else "SHORT",
        "entry": round(current_close, 2),
        "sl": round(current_close - (current_atr * 1.5) if is_bullish else current_close + (current_atr * 1.5), 2),
        "tp": round(current_close + (current_atr * 2.5) if is_bullish else current_close - (current_atr * 2.5), 2)
    }

    # --- 8. TECHNICAL ANALYSIS & MOVING AVERAGES ---
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

    # --- 9. LIVE CANDLESTICK PSYCHOLOGY ---
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

    # --- 10. TIMELINE FORMATTING & OHLC DATA ---
    train_df['Past_AI_Price'].fillna(train_df['Close'], inplace=True)
    
    history_slice = train_df.tail(60)
    history_prices = [round(float(x), 2) for x in history_slice['Close'].tolist()]
    history_ai_prices = [round(float(x), 2) for x in history_slice['Past_AI_Price'].tolist()]
        
    history_ohlc = []
    for index, row in train_df.iterrows():
        time_ms = int(index.timestamp() * 1000)
        history_ohlc.append({
            "x": time_ms,
            "y": [
                round(float(row['Open']), 2),
                round(float(row['High']), 2),
                round(float(row['Low']), 2),
                round(float(row['Close']), 2)
            ]
        })

    last_time = history_slice.index[-1]
    future_times = []
    
    real_time = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    def get_synced_time(last_dt, real_dt, interval_mins):
        minute = (real_dt.minute // interval_mins) * interval_mins
        rounded_real = real_dt.replace(minute=minute, second=0, microsecond=0)
        return max(last_dt, rounded_real)

    # 🌟 NEW TIMEFRAME LOGIC (1wk, 1d, 90m, 1h)
    if TIMEFRAME == '1wk':
        history_times = [t.strftime('%b %d, %Y') for t in history_slice.index]
        for i in range(STEPS):
            last_time += timedelta(days=7)
            future_times.append(last_time.strftime('%b %d, %Y'))

    elif TIMEFRAME == '1d':
        history_times = [t.strftime('%b %d') for t in history_slice.index]
        for i in range(STEPS):
            last_time += timedelta(days=1)
            # Skip weekends for NSE
            if not is_crypto_or_forex and last_time.weekday() > 4: 
                last_time += timedelta(days=2)
            future_times.append(last_time.strftime('%b %d'))

    elif TIMEFRAME in ['1h', '90m']:
        delta_mins = 90 if TIMEFRAME == '90m' else 60
        sync_time = get_synced_time(last_time, real_time, delta_mins)
        history_times = [t.strftime('%b %d, %H:%M') for t in history_slice.index]
        future_times = [(sync_time + timedelta(minutes=delta_mins*(i+1))).strftime('%b %d, %H:%M') for i in range(STEPS)]
        
    else:
        history_times = [t.strftime('%H:%M') for t in history_slice.index]
        if TIMEFRAME.endswith('m'):
            delta_mins = int(TIMEFRAME[:-1]) 
        else:
            delta_mins = 5
            
        sync_time = get_synced_time(last_time, real_time, delta_mins)
        future_times = [(sync_time + timedelta(minutes=delta_mins*(i+1))).strftime('%H:%M') for i in range(STEPS)]

    return {
        "ticker": TICKER,
        "timestamp": datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%H:%M:%S"),
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

if __name__ == "__main__":
    while True:
        try:
            stats = run_prediction()
            print(json.dumps(stats))
            sys.stdout.flush() 
            time.sleep(LOG_FREQ_SECONDS)
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            time.sleep(10)