import yfinance as yf
import sys
import json
import pandas as pd
import os

def get_live_data(ticker, dataset_path, timeframe="1d"):
    try:
        df = None
        
        # 1. SMART IMPORT
        if dataset_path:
            file_1 = os.path.join(dataset_path, f"data_{ticker}_{timeframe}.csv")
            file_2 = os.path.join(dataset_path, f"data_{ticker}.csv")
            file_3 = os.path.join(dataset_path, f"{ticker}_{timeframe}.csv")
            file_4 = os.path.join(dataset_path, f"{ticker}.csv")
            if os.path.exists(file_1):
                df = pd.read_csv(file_1, index_col=0, parse_dates=True)
            elif timeframe == "1d" and os.path.exists(file_2):
                df = pd.read_csv(file_2, index_col=0, parse_dates=True)
            elif os.path.exists(file_3):
                df = pd.read_csv(file_3, index_col=0, parse_dates=True)
            elif timeframe == "1d" and os.path.exists(file_4):
                df = pd.read_csv(file_4, index_col=0, parse_dates=True)
        
        # 2. FALLBACK
        if df is None or df.empty:
            stock = yf.Ticker(ticker)
            period = "2y" if timeframe == "1wk" else "1y"
            df = stock.history(period=period, interval=timeframe)
            if df is None or df.empty:
                return {"error": f"No data found for {ticker}."}

        # --- Base Indicators ---
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        
        df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()

        # Rolling VWAP 20
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['VWAP'] = (df['Typical_Price'] * df['Volume']).rolling(window=20).sum() / df['Volume'].rolling(window=20).sum()

        df['BB_Mid'] = df['SMA_20']
        df['BB_Std'] = df['Close'].rolling(window=20).std()
        df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * 2)
        df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * 2)

        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))

        ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD_Line'] = ema_12 - ema_26
        df['MACD_Signal'] = df['MACD_Line'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD_Line'] - df['MACD_Signal']

        # --- Ichimoku Cloud ---
        high_9 = df['High'].rolling(window=9).max()
        low_9 = df['Low'].rolling(window=9).min()
        df['Tenkan'] = (high_9 + low_9) / 2
        high_26 = df['High'].rolling(window=26).max()
        low_26 = df['Low'].rolling(window=26).min()
        df['Kijun'] = (high_26 + low_26) / 2
        df['Senkou_A'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
        high_52 = df['High'].rolling(window=52).max()
        low_52 = df['Low'].rolling(window=52).min()
        df['Senkou_B'] = ((high_52 + low_52) / 2).shift(26)

        # --- Oscillators (Stoch, ATR, AO) ---
        low_14 = df['Low'].rolling(window=14).min()
        high_14 = df['High'].rolling(window=14).max()
        df['Stoch_K'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
        df['Stoch_D'] = df['Stoch_K'].rolling(window=3).mean()

        prev_close = df['Close'].shift(1)
        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - prev_close).abs()
        tr3 = (df['Low'] - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()

        median_price = (df['High'] + df['Low']) / 2
        df['AO'] = median_price.rolling(window=5).mean() - median_price.rolling(window=34).mean()

        # --- SuperTrend ---
        multiplier = 3
        df['Basic_UB'] = median_price + (multiplier * df['ATR'])
        df['Basic_LB'] = median_price - (multiplier * df['ATR'])
        df['Final_UB'] = 0.0
        df['Final_LB'] = 0.0
        df['SuperTrend'] = 0.0
        df['SuperTrend_Dir'] = 1

        for i in range(1, len(df)):
            if pd.isna(df['Basic_UB'].iloc[i]): continue
            
            curr_ub = df['Basic_UB'].iloc[i]
            prev_f_ub = df['Final_UB'].iloc[i-1]
            prev_close_val = df['Close'].iloc[i-1]
            df.loc[df.index[i], 'Final_UB'] = curr_ub if curr_ub < prev_f_ub or prev_close_val > prev_f_ub else prev_f_ub
            
            curr_lb = df['Basic_LB'].iloc[i]
            prev_f_lb = df['Final_LB'].iloc[i-1]
            df.loc[df.index[i], 'Final_LB'] = curr_lb if curr_lb > prev_f_lb or prev_close_val < prev_f_lb else prev_f_lb
            
            prev_st = df['SuperTrend'].iloc[i-1]
            curr_close = df['Close'].iloc[i]
            
            if prev_st == prev_f_ub and curr_close <= df['Final_UB'].iloc[i]:
                df.loc[df.index[i], 'SuperTrend_Dir'] = -1
            elif prev_st == prev_f_ub and curr_close > df['Final_UB'].iloc[i]:
                df.loc[df.index[i], 'SuperTrend_Dir'] = 1
            elif prev_st == prev_f_lb and curr_close >= df['Final_LB'].iloc[i]:
                df.loc[df.index[i], 'SuperTrend_Dir'] = 1
            elif prev_st == prev_f_lb and curr_close < df['Final_LB'].iloc[i]:
                df.loc[df.index[i], 'SuperTrend_Dir'] = -1
                
            df.loc[df.index[i], 'SuperTrend'] = df['Final_LB'].iloc[i] if df['SuperTrend_Dir'].iloc[i] == 1 else df['Final_UB'].iloc[i]

        # --- Parabolic SAR ---
        length = len(df)
        psar = df['Close'].copy()
        psar_dir = [1] * length 
        af, ep = 0.02, df['High'].iloc[0]
        
        for i in range(1, length):
            prev_psar = psar.iloc[i-1]
            if psar_dir[i-1] == 1: 
                psar.iloc[i] = prev_psar + af * (ep - prev_psar)
                psar.iloc[i] = min(psar.iloc[i], df['Low'].iloc[i-1], df['Low'].iloc[max(0, i-2)])
                if df['Low'].iloc[i] < psar.iloc[i]:
                    psar_dir[i], psar.iloc[i], af, ep = -1, ep, 0.02, df['Low'].iloc[i]
                else:
                    psar_dir[i] = 1
                    if df['High'].iloc[i] > ep:
                        ep, af = df['High'].iloc[i], min(af + 0.02, 0.2)
            else: 
                psar.iloc[i] = prev_psar + af * (ep - prev_psar)
                psar.iloc[i] = max(psar.iloc[i], df['High'].iloc[i-1], df['High'].iloc[max(0, i-2)])
                if df['High'].iloc[i] > psar.iloc[i]:
                    psar_dir[i], psar.iloc[i], af, ep = 1, ep, 0.02, df['High'].iloc[i]
                else:
                    psar_dir[i] = -1
                    if df['Low'].iloc[i] < ep:
                        ep, af = df['Low'].iloc[i], min(af + 0.02, 0.2)
        df['PSAR'] = psar

        # --- Trend Phase Detection ---
        df['Phase'] = 'Sideways'
        df.loc[(df['Close'] > df['SMA_20']) & (df['SMA_20'] > df['SMA_50']), 'Phase'] = 'Uptrend'
        df.loc[(df['Close'] < df['SMA_20']) & (df['SMA_20'] < df['SMA_50']), 'Phase'] = 'Downtrend'

        # --- Pivot Logic for Patterns & Trendlines ---
        order = 5 
        highs, lows = df['High'].tolist(), df['Low'].tolist()
        pivot_highs = [(i, highs[i]) for i in range(order, len(df) - order) if highs[i] == max(highs[i-order:i+order+1])]
        pivot_lows = [(i, lows[i]) for i in range(order, len(df) - order) if lows[i] == min(lows[i-order:i+order+1])]
        
        # Auto Trendline (Now includes explicit Sideways detection)
        trendline = None
        if df['Phase'].iloc[-1] == 'Uptrend' and len(pivot_lows) >= 2:
            idx1, idx2 = pivot_lows[-2][0], pivot_lows[-1][0]
            slope = (lows[idx2] - lows[idx1]) / (idx2 - idx1)
            trendline = {"type": "uptrend", "x1": idx1, "y1": lows[idx1], "x2": len(df)-1, "y2": lows[idx2] + slope * (len(df)-1-idx2)}
        elif df['Phase'].iloc[-1] == 'Downtrend' and len(pivot_highs) >= 2:
            idx1, idx2 = pivot_highs[-2][0], pivot_highs[-1][0]
            slope = (highs[idx2] - highs[idx1]) / (idx2 - idx1)
            trendline = {"type": "downtrend", "x1": idx1, "y1": highs[idx1], "x2": len(df)-1, "y2": highs[idx2] + slope * (len(df)-1-idx2)}
        elif df['Phase'].iloc[-1] == 'Sideways' and len(pivot_lows) >= 2:
            idx1, idx2 = pivot_lows[-2][0], pivot_lows[-1][0]
            # Flat line connecting the recent sideways low
            trendline = {"type": "sideways", "x1": idx1, "y1": lows[idx2], "x2": len(df)-1, "y2": lows[idx2]}

        # Geometric Patterns & Channels
        patterns = []
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
            ph1, ph2 = pivot_highs[-2], pivot_highs[-1]
            pl1, pl2 = pivot_lows[-2], pivot_lows[-1]
            
            slope_high = (ph2[1] - ph1[1]) / (ph2[0] - ph1[0]) if ph2[0] != ph1[0] else 0
            slope_low = (pl2[1] - pl1[1]) / (pl2[0] - pl1[0]) if pl2[0] != pl1[0] else 0
            end_idx = len(df) - 1
            
            proj_high = ph2[1] + slope_high * (end_idx - ph2[0])
            proj_low = pl2[1] + slope_low * (end_idx - pl2[0])
            
            # --- NEW: Check if lines are Parallel (Channels) vs Converging (Wedges/Triangles) ---
            slope_diff = abs(slope_high - slope_low)
            is_parallel = slope_diff <= 0.015 # Mathematical tolerance for parallel lines
            
            pattern_name = "Formation"
            
            if is_parallel:
                if slope_high > 0.005:
                    pattern_name = "Ascending Channel (Bullish)"
                elif slope_high < -0.005:
                    pattern_name = "Descending Channel (Bearish)"
                else:
                    pattern_name = "Sideways Channel"
            else:
                if slope_high < -0.005 and slope_low < -0.005:
                    pattern_name = "Falling Wedge (Bullish)"
                elif slope_high > 0.005 and slope_low > 0.005:
                    pattern_name = "Rising Wedge (Bearish)"
                elif abs(slope_high) <= 0.005 and slope_low > 0.005:
                    pattern_name = "Ascending Triangle (Bullish)"
                elif slope_high < -0.005 and abs(slope_low) <= 0.005:
                    pattern_name = "Descending Triangle (Bearish)"
                elif slope_high < -0.005 and slope_low > 0.005:
                    pattern_name = "Symmetrical Triangle"
                
            patterns.append({
                "name": pattern_name,
                "upper": {"x1": ph1[0], "y1": ph1[1], "x2": end_idx, "y2": proj_high},
                "lower": {"x1": pl1[0], "y1": pl1[1], "x2": end_idx, "y2": proj_low}
            })

        # Auto Support & Resistance
        sr_order = 15 
        res_levels = [highs[i] for i in range(sr_order, len(df)-sr_order) if highs[i] == max(highs[i-sr_order:i+sr_order+1])]
        sup_levels = [lows[i] for i in range(sr_order, len(df)-sr_order) if lows[i] == min(lows[i-sr_order:i+sr_order+1])]
        
        def filter_levels(levels, threshold=0.015):
            filtered = []
            for l in sorted(levels, reverse=True):
                if not any(abs(l - f)/f < threshold for f in filtered): filtered.append(l)
            return filtered[:3] 
            
        sr_levels = {
            "resistance": filter_levels(res_levels),
            "support": filter_levels(sup_levels)
        }

        # Daily Pivot Points
        last_h = highs[-2] if len(highs) > 1 else highs[-1]
        last_l = lows[-2] if len(lows) > 1 else lows[-1]
        last_c = df['Close'].iloc[-2] if len(df) > 1 else df['Close'].iloc[-1]
        pivot_p = (last_h + last_l + last_c) / 3
        pivot_points = {
            "P": pivot_p,
            "R1": (2 * pivot_p) - last_l,
            "R2": pivot_p + (last_h - last_l),
            "S1": (2 * pivot_p) - last_h,
            "S2": pivot_p - (last_h - last_l)
        }

        # --- Micro Candlestick Pattern Detection (Context-Aware) ---
        def get_candle_signal(i):
            if i < 5: return None
            
            O0, C0, H0, L0 = df['Open'].iloc[i-2], df['Close'].iloc[i-2], df['High'].iloc[i-2], df['Low'].iloc[i-2]
            O1, C1, H1, L1 = df['Open'].iloc[i-1], df['Close'].iloc[i-1], df['High'].iloc[i-1], df['Low'].iloc[i-1]
            O2, C2, H2, L2 = df['Open'].iloc[i], df['Close'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i]
            
            is_bullish_trend = df['Phase'].iloc[i] == 'Downtrend'
            is_bearish_trend = df['Phase'].iloc[i] == 'Uptrend'
            
            body = abs(C2 - O2)
            upper_wick, lower_wick = H2 - max(O2, C2), min(O2, C2) - L2
            is_green, is_red = C2 > O2, C2 < O2
            prev_is_red, prev_is_green = C1 < O1, C1 > O1
            
            avg_body = sum(abs(df['Close'].iloc[j] - df['Open'].iloc[j]) for j in range(i-5, i)) / 5
            avg_body = avg_body if avg_body > 0 else 0.001

            if is_bullish_trend:
                if lower_wick >= 2.5 * body and upper_wick <= 0.2 * body and body > 0: return {"name": "Hammer", "signal": "bullish"}
                if prev_is_red and is_green and C2 > O1 and O2 <= C1 and body > avg_body * 1.2: return {"name": "Bullish Engulfing", "signal": "bullish"}
                if C0 < O0 and abs(C0-O0) > avg_body and abs(C1-O1) <= (H1-L1)*0.3 and is_green and C2 > (O0 + C0)/2: return {"name": "Morning Star", "signal": "bullish"}

            if is_bearish_trend:
                if upper_wick >= 2.5 * body and lower_wick <= 0.2 * body and body > 0: return {"name": "Shooting Star", "signal": "bearish"}
                if prev_is_green and is_red and C2 < O1 and O2 >= C1 and body > avg_body * 1.2: return {"name": "Bearish Engulfing", "signal": "bearish"}
                if C0 > O0 and abs(C0-O0) > avg_body and abs(C1-O1) <= (H1-L1)*0.3 and is_red and C2 < (O0 + C0)/2: return {"name": "Evening Star", "signal": "bearish"}
                    
            return None

        def get_fvg(i):
            if i < 2: return None
            if df['Low'].iloc[i] > df['High'].iloc[i-2]:
                return {"type": "bullish", "top": df['Low'].iloc[i], "bottom": df['High'].iloc[i-2]}
            elif df['High'].iloc[i] < df['Low'].iloc[i-2]:
                return {"type": "bearish", "top": df['Low'].iloc[i-2], "bottom": df['High'].iloc[i]}
            return None

        # --- Format Output Data ---
        data = []
        for i in range(len(df)):
            row = df.iloc[i]
            c_signal = get_candle_signal(i)
            fvg = get_fvg(i)

            candle_obj = {
                "date": df.index[i].strftime('%d %b'),
                "open": round(float(row['Open']), 2), "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2), "close": round(float(row['Close']), 2),
                "volume": int(row['Volume']), "phase": row['Phase'],
                "sma20": round(float(row['SMA_20']), 2) if pd.notna(row['SMA_20']) else None,
                "sma50": round(float(row['SMA_50']), 2) if pd.notna(row['SMA_50']) else None,
                "ema9": round(float(row['EMA_9']), 2) if pd.notna(row['EMA_9']) else None,
                "ema21": round(float(row['EMA_21']), 2) if pd.notna(row['EMA_21']) else None,
                "vwap": round(float(row['VWAP']), 2) if pd.notna(row['VWAP']) else None,
                "bbUpper": round(float(row['BB_Upper']), 2) if pd.notna(row['BB_Upper']) else None,
                "bbLower": round(float(row['BB_Lower']), 2) if pd.notna(row['BB_Lower']) else None,
                "rsi": round(float(row['RSI_14']), 2) if pd.notna(row['RSI_14']) else None,
                "macd": round(float(row['MACD_Line']), 2) if pd.notna(row['MACD_Line']) else None,
                "signal": round(float(row['MACD_Signal']), 2) if pd.notna(row['MACD_Signal']) else None,
                "hist": round(float(row['MACD_Hist']), 2) if pd.notna(row['MACD_Hist']) else None,
                "psar": round(float(row['PSAR']), 2) if pd.notna(row['PSAR']) else None,
                "ichimoku": {
                    "tenkan": round(float(row['Tenkan']), 2) if pd.notna(row['Tenkan']) else None,
                    "kijun": round(float(row['Kijun']), 2) if pd.notna(row['Kijun']) else None,
                    "senkou_a": round(float(row['Senkou_A']), 2) if pd.notna(row['Senkou_A']) else None,
                    "senkou_b": round(float(row['Senkou_B']), 2) if pd.notna(row['Senkou_B']) else None
                },
                "stoch": {
                    "k": round(float(row['Stoch_K']), 2) if pd.notna(row['Stoch_K']) else None,
                    "d": round(float(row['Stoch_D']), 2) if pd.notna(row['Stoch_D']) else None
                },
                "atr": round(float(row['ATR']), 2) if pd.notna(row['ATR']) else None,
                "ao": round(float(row['AO']), 2) if pd.notna(row['AO']) else None,
                "supertrend": {
                    "val": round(float(row['SuperTrend']), 2) if pd.notna(row['SuperTrend']) and row['SuperTrend'] != 0 else None,
                    "dir": int(row['SuperTrend_Dir'])
                }
            }
            if c_signal: candle_obj["candle_signal"] = c_signal
            if fvg: candle_obj["fvg"] = fvg
            data.append(candle_obj)
            
        # Attach meta-data to last candle
        if data:
            if patterns: data[-1]["patterns"] = patterns
            if trendline: data[-1]["trendline"] = trendline
            data[-1]["sr_levels"] = sr_levels
            data[-1]["pivot_points"] = pivot_points
            
        return data
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    ticker_val = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    dataset_path_val = sys.argv[2] if len(sys.argv) > 2 else None
    timeframe_val = sys.argv[3] if len(sys.argv) > 3 else "1d"
    print(json.dumps(get_live_data(ticker_val, dataset_path_val, timeframe_val)))