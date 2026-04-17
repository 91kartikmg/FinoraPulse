import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report
import warnings

# New imports for DTW and Sequence Models
from tslearn.clustering import TimeSeriesKMeans
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping

warnings.filterwarnings('ignore')

# ---------------------------------------------------------
# 0. Generate Sample Data (Now with Volume)
# ---------------------------------------------------------
def get_sample_data():
    """Generates synthetic OHLCV data for demonstration."""
    np.random.seed(42)
    dates = pd.date_range(start='2020-01-01', periods=1000, freq='D')
    close_prices = np.cumsum(np.random.normal(0, 1, 1000)) + 100
    
    data = []
    for i in range(1000):
        c = close_prices[i]
        o = c + np.random.normal(0, 0.5)
        h = max(o, c) + abs(np.random.normal(0, 0.5))
        l = min(o, c) - abs(np.random.normal(0, 0.5))
        
        # Adding synthetic volume spikes
        base_vol = np.random.randint(1000, 5000)
        v = base_vol * (1 + abs(c - o)) # Higher volume on big body days
        
        data.append([dates[i], o, h, l, c, v])
        
    df = pd.DataFrame(data, columns=['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df.set_index('Datetime', inplace=True)
    return df

# ---------------------------------------------------------
# 1. Feature Engineering: Macro, Volume, & Risk-Adjusted Targets
# ---------------------------------------------------------
def extract_advanced_features(df):
    """Extracts structural components, macro context, and risk targets."""
    df = df.copy()
    
    # 1. Basic Shape Features
    df['Range'] = df['High'] - df['Low']
    df['Range'] = df['Range'].replace(0, 1e-5)
    df['Body'] = df['Close'] - df['Open']
    df['Upper_Shadow'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Shadow'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    
    df['Norm_Body'] = df['Body'] / df['Range']
    df['Norm_Upper'] = df['Upper_Shadow'] / df['Range']
    df['Norm_Lower'] = df['Lower_Shadow'] / df['Range']
    
    # 2. FEATURE 1: Volume & Conviction
    df['Rel_Volume'] = df['Volume'] / df['Volume'].rolling(20).mean()
    df['Vol_Weighted_Body'] = df['Norm_Body'] * df['Rel_Volume']
    
    # 3. FEATURE 2: Macro Context (Multi-Timeframe equivalent)
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['Macro_Trend'] = (df['Close'] - df['SMA_50']) / df['SMA_50']
    
    # 4. FEATURE 5: Risk-Adjusted Target (TP vs SL)
    # Define rules: Take Profit = +2%, Stop Loss = -1%, Max Lookahead = 10 days
    tp_pct = 0.02
    sl_pct = 0.01
    lookahead = 10
    
    targets = []
    prices = df['Close'].values
    highs = df['High'].values
    lows = df['Low'].values
    
    for i in range(len(df)):
        if i + lookahead >= len(df):
            targets.append(np.nan)
            continue
            
        entry_price = prices[i]
        tp_price = entry_price * (1 + tp_pct)
        sl_price = entry_price * (1 - sl_pct)
        
        hit_tp = False
        hit_sl = False
        
        # Look ahead 'n' days to see what hits first
        for j in range(1, lookahead + 1):
            if highs[i + j] >= tp_price:
                hit_tp = True
            if lows[i + j] <= sl_price:
                hit_sl = True
                
            if hit_tp and not hit_sl:
                targets.append(1) # Good trade
                break
            elif hit_sl and not hit_tp:
                targets.append(0) # Bad trade
                break
            elif hit_tp and hit_sl:
                # If both hit in same day, assume SL hit first for conservative modeling
                targets.append(0) 
                break
        else:
            # Neither hit within lookahead period
            targets.append(0)
            
    df['Risk_Adj_Target'] = targets
    
    df.dropna(inplace=True)
    return df

def group_individual_candles(df, num_clusters=5):
    """Groups similar individual candlesticks using enhanced features."""
    features = ['Norm_Body', 'Norm_Upper', 'Norm_Lower', 'Vol_Weighted_Body', 'Macro_Trend']
    
    kmeans = KMeans(n_clusters=num_clusters, random_state=42)
    df['Candle_Group'] = kmeans.fit_predict(df[features])
    return df

# ---------------------------------------------------------
# 2. FEATURE 3: Dynamic Time Warping (DTW) Pattern Grouping
# ---------------------------------------------------------
def group_patterns_dtw(df, window_size=5, num_pattern_clusters=10):
    """Uses DTW to cluster sequences of candles, ignoring slight timing variations."""
    features = ['Norm_Body', 'Norm_Upper', 'Norm_Lower', 'Vol_Weighted_Body', 'Macro_Trend']
    
    # Scale features for sequence modeling and DTW
    scaler = StandardScaler()
    scaled_data = scaler.fit_transform(df[features])
    
    pattern_sequences = []
    valid_indices = []
    
    # Create 3D arrays required for Time Series tasks: (samples, timesteps, features)
    for i in range(len(scaled_data) - window_size):
        window = scaled_data[i:i+window_size]
        pattern_sequences.append(window)
        valid_indices.append(df.index[i + window_size - 1])
        
    X_seq = np.array(pattern_sequences)
    
    print("Clustering patterns using DTW (this may take a moment)...")
    # Using TimeSeriesKMeans with DTW metric
    dtw_km = TimeSeriesKMeans(n_clusters=num_pattern_clusters, metric="dtw", max_iter=5, random_state=42)
    pattern_clusters = dtw_km.fit_predict(X_seq)
    
    df['Pattern_Group'] = np.nan
    df.loc[valid_indices, 'Pattern_Group'] = pattern_clusters
    
    df.dropna(inplace=True)
    return df, X_seq, valid_indices, scaler

# ---------------------------------------------------------
# 3. FEATURE 4: Predict Direction using Sequence Models (LSTM)
# ---------------------------------------------------------
def train_lstm_model(df, X_seq_full, valid_indices):
    """Trains an LSTM deep learning network on the sequential data."""
    print("--- DIRECTION PREDICTION: LSTM Model Metrics ---")
    
    # Filter X_seq to match the dataframe indices after dropna
    mask = [idx in df.index for idx in valid_indices]
    X = X_seq_full[mask]
    y = df['Risk_Adj_Target'].values
    
    # Split data chronologically (80% train, 20% test)
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    # Build LSTM Model
    model = Sequential()
    # Input shape: (window_size, num_features)
    model.add(LSTM(50, return_sequences=True, input_shape=(X_train.shape[1], X_train.shape[2])))
    model.add(Dropout(0.2))
    model.add(LSTM(20, return_sequences=False))
    model.add(Dropout(0.2))
    model.add(Dense(1, activation='sigmoid')) # Sigmoid for binary classification
    
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    
    # Train the model with early stopping to prevent overfitting
    early_stop = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
    
    print("Training LSTM network...")
    model.fit(X_train, y_train, epochs=30, batch_size=32, validation_split=0.1, 
              callbacks=[early_stop], verbose=0)
    
    # Evaluate
    predictions_prob = model.predict(X_test, verbose=0)
    predictions = (predictions_prob > 0.5).astype(int).flatten()
    
    accuracy = accuracy_score(y_test, predictions)
    
    print(f"LSTM Prediction Accuracy on Unseen Data: {accuracy * 100:.2f}%\n")
    print("Classification Report:")
    print(classification_report(y_test, predictions, target_names=['Hit SL/Timeout (0)', 'Hit TP (1)']))
    
    return model

# ---------------------------------------------------------
# Execution Pipeline
# ---------------------------------------------------------
if __name__ == "__main__":
    # 1. Load Data
    print("Generating data and extracting advanced features...")
    raw_data = get_sample_data()
    
    # 2. Extract Features (Includes Volume, Macro, and Risk-Targets)
    data_with_features = extract_advanced_features(raw_data)
    
    # 3. Group Individual Candles 
    grouped_candles_df = group_individual_candles(data_with_features, num_clusters=5)
    
    # 4. Group Patterns using DTW
    final_df, full_sequences, sequence_indices, scaler = group_patterns_dtw(
        grouped_candles_df, 
        window_size=5, 
        num_pattern_clusters=10
    )
    
    # 5. Train Predictive LSTM Model
    lstm_model = train_lstm_model(final_df, full_sequences, sequence_indices)