import sys
import json
import yfinance as yf
import pandas as pd
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

def get_fundamentals(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        
        # --- 1. ASSET CLASSIFICATION ---
        if "-" in ticker_symbol: asset_type = "crypto"
        elif "=X" in ticker_symbol: asset_type = "forex"
        elif "=F" in ticker_symbol: asset_type = "commodity"
        elif "^" in ticker_symbol: asset_type = "index"
        else: asset_type = "stock"

        # --- 2. CATALYST ENGINE ---
        catalysts = []
        today = datetime.now()
        
        if asset_type == "stock":
            try:
                # Next Earnings
                if hasattr(stock, 'earnings_dates') and stock.earnings_dates is not None:
                    future_earnings = stock.earnings_dates[stock.earnings_dates.index > today]
                    if not future_earnings.empty:
                        next_earn_date = future_earnings.index[0].strftime("%b %d, %Y")
                        catalysts.append({"event": "Earnings Report", "date": next_earn_date, "type": "warning"})
            except: pass
            
            # Next Dividend
            ex_div_ts = info.get("exDividendDate", None)
            if ex_div_ts and ex_div_ts > today.timestamp():
                ex_div_date = datetime.fromtimestamp(ex_div_ts).strftime("%b %d, %Y")
                catalysts.append({"event": "Ex-Dividend Date", "date": ex_div_date, "type": "info"})
        
        elif asset_type == "crypto":
            catalysts.append({"event": "US CPI (Inflation) Data", "date": "Mid-Month", "type": "danger"})
            catalysts.append({"event": "FOMC Rate Decision", "date": "Next Fed Meeting", "type": "warning"})
            
        elif asset_type in ["forex", "commodity", "index"]:
            catalysts.append({"event": "US Non-Farm Payrolls", "date": "1st Friday", "type": "info"})
            catalysts.append({"event": "FOMC Rate Decision", "date": "Next Fed Meeting", "type": "warning"})

        if not catalysts:
            catalysts.append({"event": "No major events scheduled", "date": "-", "type": "neutral"})

        # --- 3. BASE DATA ROUTING ---
        data = {
            "asset_type": asset_type,
            "catalysts": catalysts,
            "marketCap": info.get('marketCap') or info.get('navPrice'),
            "pe_ratio": info.get('trailingPE') or info.get('forwardPE'),
            "bookValue": info.get('bookValue'),
            "dividendYield": info.get('dividendYield'),
            "roe": info.get('returnOnEquity'),
            "priceToBook": info.get('priceToBook'),
            "debtToEquity": info.get('debtToEquity'),
            "eps": info.get('trailingEps') or info.get('forwardEps'),
            
            "volume24Hr": info.get("volume24Hr", 0) or info.get("regularMarketVolume", 0), 
            "circulatingSupply": info.get("circulatingSupply", 0), 
            "previousClose": info.get("previousClose", "N/A"), 
            "open": info.get("open", "N/A"), 
            "dayLow": info.get("dayLow", "N/A"), 
            "dayHigh": info.get("dayHigh", "N/A"), 
            "volume": info.get("volume", "N/A") or info.get("regularMarketVolume", "N/A"),
            
            "historical": {"years": [], "pe": [], "market_cap": [], "roe": []},
            "income_stmt": {"years": [], "revenue": [], "net_income": []}
        }
        
        # --- 4. DEEP HISTORICAL DATA (Stocks Only) ---
        if asset_type == "stock":
            try:
                hist = stock.history(period="5y", interval="1mo")
                inc = stock.financials
                bs = stock.balance_sheet
                
                # A. Extract Annual Profit & Loss (Revenue vs Net Income)
                annual_data = {}
                if not inc.empty:
                    for d in sorted(inc.columns):
                        year_str = str(d.year)
                        
                        rev = 0
                        if 'Total Revenue' in inc.index and not pd.isna(inc.loc['Total Revenue', d]):
                            rev = inc.loc['Total Revenue', d]
                        elif 'Operating Revenue' in inc.index and not pd.isna(inc.loc['Operating Revenue', d]):
                            rev = inc.loc['Operating Revenue', d]
                            
                        ni = 0
                        if 'Net Income' in inc.index and not pd.isna(inc.loc['Net Income', d]):
                            ni = inc.loc['Net Income', d]
                            
                        data["income_stmt"]["years"].append(year_str)
                        data["income_stmt"]["revenue"].append(float(rev))
                        data["income_stmt"]["net_income"].append(float(ni))
                        
                        annual_data[d.year] = {'net_inc': ni}

                # B. Pre-calculate ROE and EPS
                shares = info.get('sharesOutstanding') or info.get('impliedSharesOutstanding')
                if not shares and info.get('marketCap') and info.get('currentPrice'):
                    shares = info.get('marketCap') / info.get('currentPrice')
                    
                if not bs.empty and shares:
                    for d in bs.columns:
                        y = d.year
                        if y in annual_data:
                            equity = 1
                            try:
                                if 'Stockholders Equity' in bs.index:
                                    equity = bs.loc['Stockholders Equity', d]
                                elif 'Total Equity Gross Minority Interest' in bs.index:
                                    equity = bs.loc['Total Equity Gross Minority Interest', d]
                                if pd.isna(equity) or equity == 0: equity = 1
                            except:
                                equity = 1
                                
                            net_inc = annual_data[y]['net_inc']
                            roe = (net_inc / equity) * 100 if equity else 0
                            eps = net_inc / shares if shares else 0.001
                            annual_data[y]['roe'] = roe
                            annual_data[y]['eps'] = eps

                # C. Extract Monthly P/E and Market Cap Trends
                if not hist.empty and shares:
                    hist = hist.dropna(subset=['Close'])
                    available_years = sorted(annual_data.keys())
                    
                    for date, row in hist.iterrows():
                        y = date.year
                        month_str = date.strftime('%b %Y')
                        price = row['Close']
                        mc = price * shares
                        
                        target_y = y
                        if available_years:
                            if y not in available_years:
                                past_years = [ay for ay in available_years if ay <= y]
                                target_y = past_years[-1] if past_years else available_years[0]
                            
                            eps = annual_data.get(target_y, {}).get('eps', 0.001)
                            roe = annual_data.get(target_y, {}).get('roe', 0)
                        else:
                            eps = info.get('trailingEps', 0.001) or 0.001
                            roe = (info.get('returnOnEquity', 0) or 0) * 100
                            
                        pe = price / eps if eps > 0 else 0
                        if pe > 500 or pe < -500: pe = 0

                        data["historical"]["years"].append(month_str)
                        data["historical"]["pe"].append(round(pe, 2))
                        data["historical"]["market_cap"].append(mc)
                        data["historical"]["roe"].append(round(roe, 2))
                        
            except Exception as e:
                pass 
            
        print(json.dumps(data))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        ticker = sys.argv[1].upper()
        get_fundamentals(ticker)
    else:
        print(json.dumps({"error": "No ticker provided"}))