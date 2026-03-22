import sys
import json
import requests
import datetime
import yfinance as yf

# World Bank API Indicators (Core Macro)
INDICATORS = {
    "gdp_total": "NY.GDP.MKTP.CD",       
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",   
    "inflation": "FP.CPI.TOTL.ZG",       
    "unemployment": "SL.UEM.TOTL.ZS"
}

CURRENCY_MAP = {
    "IN": "INR", "CN": "CNY", "JP": "JPY", "DE": "EUR",
    "GB": "GBP", "CA": "CAD", "AU": "AUD", "US": "USD"
}

BOND_MAP = {
    "US": "^TNX", "IN": "^IN10YT=RR", "CN": "CN10YT=RR", 
    "JP": "^JN10YT=RR", "DE": "^DE10YT=RR", "GB": "^UK10YT=RR"
}

# --- NEW: ADVANCED DETAILED EXPORTS & STOCK SCREENER DATA ---
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

def get_country_macro(country_code):
    country_code = country_code.upper()
    current_year = datetime.datetime.now().year
    date_range = f"{current_year-10}:{current_year}"
    base_url = "http://api.worldbank.org/v2/country"
    
    output = {
        "country": country_code,
        "history_years": [],
        "gdp_total_trend": [], 
        "gdp_trend": [],
        "inflation_trend": [],
        "unemployment_trend": [], 
        "currency_trend": [],
        "currency_pair": "",
        "bond_trend": [],
        "advanced_exports": [], # NEW
        "screener": []          # NEW
    }

    try:
        # 1. Fetch Core Macro (GDP, Inflation, Unemployment)
        for key in ["gdp_total", "gdp_growth", "inflation", "unemployment"]:
            url = f"{base_url}/{country_code}/indicator/{INDICATORS[key]}?format=json&date={date_range}"
            resp = requests.get(url).json()
            
            if len(resp) > 1:
                data_list = resp[1]
                values = []
                years = []
                for entry in reversed(data_list):
                    val = entry['value']
                    values.append(round(val, 2) if val is not None else 0)
                    years.append(entry['date'])
                
                if key == "gdp_total":
                    output["gdp_total_trend"] = values
                    output["history_years"] = years 
                elif key == "gdp_growth":
                    output["gdp_trend"] = values
                elif key == "inflation":
                    output["inflation_trend"] = values
                else:
                    output["unemployment_trend"] = values

        # 2. Currency & Bonds
        if len(output["history_years"]) > 0:
            currency_code = CURRENCY_MAP.get(country_code, "")
            if currency_code:
                ticker = f"USD{currency_code}=X" if country_code != "US" else "DX-Y.NYB"
                curr_data = yf.Ticker(ticker).history(period="15y")
                if not curr_data.empty:
                    curr_data['Year'] = curr_data.index.year
                    yearly_closes = curr_data.groupby('Year')['Close'].last().to_dict()
                    output["currency_trend"] = [round(yearly_closes.get(int(y), 0), 2) for y in output["history_years"]]
                    output["currency_pair"] = f"1 USD to {currency_code}" if country_code != "US" else "US Dollar Index (DXY)"

            bond_ticker = BOND_MAP.get(country_code, "")
            if bond_ticker:
                bond_data = yf.Ticker(bond_ticker).history(period="15y")
                if not bond_data.empty:
                    bond_data['Year'] = bond_data.index.year
                    yearly_closes = bond_data.groupby('Year')['Close'].last().to_dict()
                    output["bond_trend"] = [round(yearly_closes.get(int(y), 0), 2) for y in output["history_years"]]

        # --- 3. THE NEW ADVANCED SECTOR & SCREENER LOGIC ---
        # Default to US layout if country not in our advanced dictionary
        advanced_data = ADVANCED_EXPORTS.get(country_code, ADVANCED_EXPORTS["US"])
        output["advanced_exports"] = advanced_data

        screener_results = []
        
        # Loop through only the top 2 sectors to keep the API fast
        for sector_info in advanced_data[:2]:
            for stock_str in sector_info["stocks"]:
                name = stock_str.split("(")[0].strip()
                ticker = stock_str.split("(")[1].replace(")", "")
                
                try:
                    # Fetch live market data for the companies driving the GDP
                    tkr = yf.Ticker(ticker)
                    hist = tkr.history(period="2d")
                    if len(hist) >= 2:
                        prev = hist['Close'].iloc[0]
                        curr = hist['Close'].iloc[1]
                        change = ((curr - prev) / prev) * 100
                        
                        screener_results.append({
                            "sector": sector_info["sector"],
                            "company": name,
                            "ticker": ticker,
                            "price": round(curr, 2),
                            "change": round(change, 2)
                        })
                except:
                    continue
        
        output["screener"] = screener_results

    except Exception as e:
        output["error"] = str(e)

    print(json.dumps(output))

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "IN"
    get_country_macro(target)