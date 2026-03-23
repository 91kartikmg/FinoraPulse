import sys
import json
import yfinance as yf
import pandas as pd
import warnings

warnings.filterwarnings('ignore')

# The 6 pillars of the global economy
ASSETS = {
    "S&P 500 (Equity)": "^GSPC",
    "Gold (Safe Haven)": "GC=F",
    "Bitcoin (Crypto)": "BTC-USD",
    "US Dollar (Forex)": "DX-Y.NYB",
    "Crude Oil (Energy)": "CL=F",
    "10Y Bond (Rates)": "^TNX"
}

def get_correlation():
    try:
        tickers = list(ASSETS.values())
        # Download 1 year of daily prices for all assets at once
        data = yf.download(tickers, period="1y", interval="1d", progress=False)['Close']
        
        # Forward-fill missing data (because crypto trades weekends, but stocks don't)
        data.fillna(method='ffill', inplace=True)
        data.dropna(inplace=True)
        
        # Rename columns to our clean names
        inv_map = {v: k for k, v in ASSETS.items()}
        data.rename(columns=inv_map, inplace=True)
        
        ordered_cols = list(ASSETS.keys())
        data = data[ordered_cols]
        
        # Calculate the mathematical correlation matrix
        corr_matrix = data.corr().round(2)
        
        # Format the data exactly how ApexCharts Heatmap expects it
        series = []
        for row_asset in reversed(ordered_cols):
            row_data = []
            for col_asset in ordered_cols:
                val = corr_matrix.loc[row_asset, col_asset]
                row_data.append({"x": col_asset, "y": float(val)})
            series.append({"name": row_asset, "data": row_data})
        
        return {"series": series}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(json.dumps(get_correlation()))