import sys
import json
import requests
import datetime
import yfinance as yf
import concurrent.futures  # The multi-threading engine
import warnings

warnings.filterwarnings('ignore')

# World Bank API Indicators (Core Macro)
INDICATORS = {
    "gdp_total": "NY.GDP.MKTP.CD",       
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",   
    "inflation": "FP.CPI.TOTL.ZG",       
    "unemployment": "SL.UEM.TOTL.ZS",
    "interest_rate": "FR.INR.LEND",      
    "debt_to_gdp": "GC.DOD.TOTL.GD.ZS"   
}

CURRENCY_MAP = {
    "IN": "INR", "CN": "CNY", "JP": "JPY", "DE": "EUR",
    "GB": "GBP", "CA": "CAD", "AU": "AUD", "US": "USD"
}

BOND_MAP = {
    "US": "^TNX", "IN": "^IN10YT=RR", "CN": "CN10YT=RR", 
    "JP": "^JN10YT=RR", "DE": "^DE10YT=RR", "GB": "^UK10YT=RR"
}

# --- ADVANCED DETAILED EXPORTS & STOCK SCREENER DATA ---
ADVANCED_EXPORTS = {
    "IN": [
        {"sector": "Engineering & Machinery", "pct": 27.3, "stocks": ["L&T (LT.NS)", "BHEL (BHEL.NS)"]},
        {"sector": "Refined Petroleum", "pct": 17.2, "stocks": ["RELIANCE (RELIANCE.NS)", "ONGC (ONGC.NS)"]},
        {"sector": "Gems & Jewellery", "pct": 6.5, "stocks": ["TITAN (TITAN.NS)"]},
        {"sector": "Pharmaceuticals", "pct": 5.5, "stocks": ["SUN PHARMA (SUNPHARMA.NS)", "DR REDDYS (DRREDDY.NS)"]},
        {"sector": "Electronic Goods", "pct": 4.5, "stocks": ["DIXON TECH (DIXON.NS)"]}
    ],
    "US": [
        {"sector": "Mineral Fuels & Oil", "pct": 15.5, "stocks": ["EXXONMOBIL (XOM)", "CHEVRON (CVX)"]},
        {"sector": "Nuclear & Machinery", "pct": 12.2, "stocks": ["CATERPILLAR (CAT)", "DEERE (DE)"]},
        {"sector": "Electrical & Tech", "pct": 10.4, "stocks": ["APPLE (AAPL)", "NVIDIA (NVDA)"]},
        {"sector": "Vehicles & Auto", "pct": 7.0, "stocks": ["TESLA (TSLA)", "FORD (F)"]},
        {"sector": "Aerospace & Defense", "pct": 6.5, "stocks": ["BOEING (BA)", "LOCKHEED (LMT)"]}
    ],
    "JP": [
        {"sector": "Vehicles & Auto", "pct": 21.0, "stocks": ["TOYOTA (TM)", "HONDA (HMC)"]},
        {"sector": "Machinery & Computers", "pct": 19.5, "stocks": ["KOMATSU (KMTUY)"]},
        {"sector": "Electrical Equipment", "pct": 14.8, "stocks": ["SONY (SONY)", "PANASONIC (PCRFY)"]},
        {"sector": "Optical & Medical", "pct": 6.2, "stocks": ["CANON (CAJ)"]}
    ]
}

# ==========================================
# ⚡ WORKER FUNCTIONS (Run simultaneously)
# ==========================================

def fetch_wb_indicator(key, country_code, date_range):
    """Worker to fetch a single World Bank Indicator"""
    try:
        url = f"http://api.worldbank.org/v2/country/{country_code}/indicator/{INDICATORS[key]}?format=json&date={date_range}"
        resp = requests.get(url, timeout=5).json()
        if len(resp) > 1:
            data_list = resp[1]
            values, years = [], []
            for entry in reversed(data_list):
                val = entry['value']
                values.append(round(val, 2) if val is not None else 0)
                years.append(entry['date'])
            return key, years, values
    except Exception:
        pass
    return key, [], []

def fetch_yf_yearly(ticker_type, ticker_symbol, history_years):
    """Worker to fetch historical yearly data for Bonds/Currency"""
    try:
        if not ticker_symbol: return ticker_type, []
        data = yf.Ticker(ticker_symbol).history(period="15y")
        if not data.empty:
            data['Year'] = data.index.year
            yearly_closes = data.groupby('Year')['Close'].last().to_dict()
            return ticker_type, [round(yearly_closes.get(int(y), 0), 2) for y in history_years]
    except Exception:
        pass
    return ticker_type, []

def fetch_screener_stock(sector, name, ticker):
    """Worker to fetch live data for a single screener stock"""
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if len(hist) >= 2:
            prev = hist['Close'].iloc[0]
            curr = hist['Close'].iloc[1]
            change = ((curr - prev) / prev) * 100
            return {
                "sector": sector, "company": name, "ticker": ticker,
                "price": round(curr, 2), "change": round(change, 2)
            }
    except Exception:
        pass
    return None

# ==========================================
# 🚀 MAIN MULTI-THREADED ENGINE
# ==========================================

def get_country_macro(country_code):
    country_code = country_code.upper()
    current_year = datetime.datetime.now().year
    date_range = f"{current_year-10}:{current_year}"
    
    output = {
        "country": country_code, "history_years": [],
        "gdp_total_trend": [], "gdp_trend": [], "inflation_trend": [],
        "unemployment_trend": [], "currency_trend": [], "currency_pair": "",
        "bond_trend": [], "advanced_exports": [], "screener": [],
        "interest_rate_trend": [], "debt_trend": [] 
    }

    try:
        # --- THREAD POOL 1: WORLD BANK DATA ---
        # Fires 6 requests to the World Bank at the exact same time
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fetch_wb_indicator, key, country_code, date_range): key for key in INDICATORS.keys()}
            
            for future in concurrent.futures.as_completed(futures):
                key, years, values = future.result()
                
                # Sync the master timeline based on whoever returned the longest list
                if len(years) > len(output["history_years"]): 
                    output["history_years"] = years 
                
                # Map data back to output dictionary
                if key == "gdp_total": output["gdp_total_trend"] = values
                elif key == "gdp_growth": output["gdp_trend"] = values
                elif key == "inflation": output["inflation_trend"] = values
                elif key == "unemployment": output["unemployment_trend"] = values
                elif key == "interest_rate": output["interest_rate_trend"] = values
                elif key == "debt_to_gdp": output["debt_trend"] = values

        # --- THREAD POOL 2: YAHOO FINANCE HISTORICAL (Bonds/Currency) ---
        if len(output["history_years"]) > 0:
            currency_code = CURRENCY_MAP.get(country_code, "")
            curr_ticker = f"USD{currency_code}=X" if country_code != "US" else "DX-Y.NYB"
            bond_ticker = BOND_MAP.get(country_code, "")
            
            output["currency_pair"] = f"1 USD to {currency_code}" if country_code != "US" else "US Dollar Index (DXY)"

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(fetch_yf_yearly, "currency", curr_ticker, output["history_years"])
                f2 = executor.submit(fetch_yf_yearly, "bond", bond_ticker, output["history_years"])
                
                for future in concurrent.futures.as_completed([f1, f2]):
                    t_type, vals = future.result()
                    if t_type == "currency": output["currency_trend"] = vals
                    if t_type == "bond": output["bond_trend"] = vals

        # --- THREAD POOL 3: YAHOO FINANCE LIVE SCREENER ---
        advanced_data = ADVANCED_EXPORTS.get(country_code, ADVANCED_EXPORTS["US"])
        output["advanced_exports"] = advanced_data

        screener_results = []
        screener_futures = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Dispatch workers for all stocks in the top 2 sectors
            for sector_info in advanced_data[:2]:
                for stock_str in sector_info["stocks"]:
                    name = stock_str.split("(")[0].strip()
                    ticker = stock_str.split("(")[1].replace(")", "")
                    screener_futures.append(executor.submit(fetch_screener_stock, sector_info["sector"], name, ticker))
            
            # Collect results as they finish
            for future in concurrent.futures.as_completed(screener_futures):
                result = future.result()
                if result:
                    screener_results.append(result)

        output["screener"] = screener_results

    except Exception as e:
        output["error"] = str(e)

    print(json.dumps(output))

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "IN"
    get_country_macro(target)