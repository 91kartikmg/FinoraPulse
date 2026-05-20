import yfinance as yf
import matplotlib.pyplot as plt
import pandas as pd

# 1. Get Real Stock Data (No API Key Required)
ticker = "AAPL"  # Change to any stock (TSLA, NVDA, etc.)
df = yf.download(ticker, start="2024-01-01", end="2024-04-01")

# 2. Setup the Chart
fig, ax = plt.subplots(figsize=(12, 8))

# 3. Create the Candlestick Logic
# We iterate through the data to draw each candle manually
width = 0.6  # Width of the candle body
width2 = 0.05 # Width of the wick

for i in range(len(df)):
    # Get OHLC values
    open_p, high_p, low_p, close_p = df.iloc[i][['Open', 'High', 'Low', 'Close']]
    
    # Choose color based on price action
    color = 'green' if close_p >= open_p else 'red'
    
    # Draw the Wick (High to Low)
    ax.vlines(i, low_p, high_p, color='black', linewidth=1)
    
    # Draw the Body (Open to Close)
    height = abs(close_p - open_p)
    bottom = min(open_p, close_p)
    ax.bar(i, height, bottom=bottom, color=color, width=width, edgecolor='black')

# --- YOUR GOAL: DRAWING LINES ---
# Example: Manual Support and Resistance Lines
# You can change these numbers to match what YOU see on the chart
resistance_level = 195.0
support_level = 170.0

ax.axhline(y=resistance_level, color='blue', linestyle='--', label=f'Resistance: {resistance_level}')
ax.axhline(y=support_level, color='orange', linestyle='--', label=f'Support: {support_level}')

# Example: A manual Trendline (connecting two arbitrary points)
# Points format: [x1, x2], [y1, y2]
ax.plot([5, 25], [180, 190], color='purple', linewidth=2, label="My Pattern Line")

# 4. Final Formatting
ax.set_title(f"{ticker} Daily Candlestick Chart - Manual Analysis")
ax.set_ylabel("Price (USD)")
ax.set_xlabel("Trading Days")
ax.legend()
plt.grid(True, alpha=0.3)
plt.show()