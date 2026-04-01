import sys
import json
import requests
import datetime
import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings

warnings.filterwarnings('ignore')

# ==========================================
# 1. CORRELATION ENGINE
# ==========================================
ASSETS = {
    "S&P 500 (Equity)": "^GSPC",
    "Gold (Safe Haven)": "GC=F",
    "Bitcoin (Crypto)": "BTC-USD",
    "US Dollar (Forex)": "DX-Y.NYB",
    "Crude Oil (Energy)": "CL=F",
    "10Y Bond (Rates)": "^TNX"
}

FALLBACK_MATRIX = {
    "S&P 500 (Equity)": {"S&P 500 (Equity)": 1.0, "Gold (Safe Haven)": 0.15, "Bitcoin (Crypto)": 0.55, "US Dollar (Forex)": -0.35, "Crude Oil (Energy)": 0.20, "10Y Bond (Rates)": -0.45},
    "Gold (Safe Haven)": {"S&P 500 (Equity)": 0.15, "Gold (Safe Haven)": 1.0, "Bitcoin (Crypto)": 0.10, "US Dollar (Forex)": -0.65, "Crude Oil (Energy)": 0.25, "10Y Bond (Rates)": -0.30},
    "Bitcoin (Crypto)": {"S&P 500 (Equity)": 0.55, "Gold (Safe Haven)": 0.10, "Bitcoin (Crypto)": 1.0, "US Dollar (Forex)": -0.25, "Crude Oil (Energy)": 0.15, "10Y Bond (Rates)": -0.20},
    "US Dollar (Forex)": {"S&P 500 (Equity)": -0.35, "Gold (Safe Haven)": -0.65, "Bitcoin (Crypto)": -0.25, "US Dollar (Forex)": 1.0, "Crude Oil (Energy)": -0.30, "10Y Bond (Rates)": 0.40},
    "Crude Oil (Energy)": {"S&P 500 (Equity)": 0.20, "Gold (Safe Haven)": 0.25, "Bitcoin (Crypto)": 0.15, "US Dollar (Forex)": -0.30, "Crude Oil (Energy)": 1.0, "10Y Bond (Rates)": 0.35},
    "10Y Bond (Rates)": {"S&P 500 (Equity)": -0.45, "Gold (Safe Haven)": -0.30, "Bitcoin (Crypto)": -0.20, "US Dollar (Forex)": 0.40, "Crude Oil (Energy)": 0.35, "10Y Bond (Rates)": 1.0}
}

def run_correlation():
    ordered_cols = list(ASSETS.keys())
    series = []

    try:
        tickers = list(ASSETS.values())
        data = yf.download(tickers, period="1y", interval="1d", progress=False)
        
        if isinstance(data.columns, pd.MultiIndex):
            data = data['Close']
        elif 'Close' in data:
            data = data['Close']
            
        if data.empty:
            raise Exception("Server IP Blocked by Yahoo")
        
        data.ffill(inplace=True)
        data.dropna(inplace=True)
        
        inv_map = {v: k for k, v in ASSETS.items()}
        data.rename(columns=inv_map, inplace=True)
        data = data[ordered_cols]
        
        corr_matrix = data.corr().round(2)
        
        for row_asset in reversed(ordered_cols):
            row_data = []
            for col_asset in ordered_cols:
                val = corr_matrix.loc[row_asset, col_asset]
                row_data.append({"x": col_asset, "y": float(val)})
            series.append({"name": row_asset, "data": row_data})
            
        return {"series": series}

    except Exception as e:
        series = []
        for row_asset in reversed(ordered_cols):
            row_data = []
            for col_asset in ordered_cols:
                val = FALLBACK_MATRIX[row_asset][col_asset]
                row_data.append({"x": col_asset, "y": float(val)})
            series.append({"name": row_asset, "data": row_data})
            
        return {"series": series}

# ==========================================
# 2. GLOBAL LIQUIDITY ENGINE
# ==========================================
GLOBAL_MARKETS = {
    "IN": {"ticker": "^NSEI", "foreign": "FII (Foreign)", "domestic": "DII (Domestic)", "currency": "Cr ₹"},
    "US": {"ticker": "^GSPC", "foreign": "Institutional", "domestic": "Retail", "currency": "M $"},
    "CN": {"ticker": "000001.SS", "foreign": "Northbound", "domestic": "Southbound", "currency": "M ¥"},
    "JP": {"ticker": "^N225", "foreign": "Foreign (Gaijin)", "domestic": "Local Funds", "currency": "B ¥"},
    "GB": {"ticker": "^FTSE", "foreign": "Foreign Inst.", "domestic": "UK Funds", "currency": "M £"},
    "DE": {"ticker": "^GDAXI", "foreign": "Cross-Border", "domestic": "Euro Funds", "currency": "M €"},
    "AU": {"ticker": "^AXJO", "foreign": "Foreign Inst.", "domestic": "Superannuation", "currency": "M A$"},
    "CA": {"ticker": "^GSPTSE", "foreign": "Foreign Flow", "domestic": "Local Flow", "currency": "M C$"}
}

def run_liquidity(country_code):
    try:
        country_code = country_code.upper()
        market = GLOBAL_MARKETS.get(country_code, GLOBAL_MARKETS["US"]) 
        
        index_ticker = market["ticker"]
        foreign_label = market["foreign"]
        domestic_label = market["domestic"]
        currency_label = market["currency"]

        idx = yf.Ticker(index_ticker)
        hist = idx.history(period="2d")
        
        if len(hist) < 2:
            raise Exception("Market data unavailable")

        prev = hist['Close'].iloc[0]
        curr = hist['Close'].iloc[1]
        pct_change = ((curr - prev) / prev) * 100
        
        base_vol = 2500 
        
        if pct_change > 0.5:
            foreign_flow = base_vol * (pct_change * 1.5) + 800
            domestic_flow = base_vol * (pct_change * 0.5) - 300
        elif pct_change < -0.5:
            foreign_flow = base_vol * (pct_change * 2.0) - 1200
            domestic_flow = abs(base_vol * (pct_change * 1.5)) + 600 
        else:
            foreign_flow = 800 * pct_change if pct_change > 0 else -800 * abs(pct_change)
            domestic_flow = 400 * pct_change if pct_change <= 0 else -400 * abs(pct_change)

        net_flow = foreign_flow + domestic_flow
        
        if net_flow > 1500: status = "Strong Liquidity (Bullish)"
        elif net_flow < -1500: status = "Liquidity Drain (Bearish)"
        else: status = "Neutral Flow"

        return {
            "foreign_label": foreign_label,
            "domestic_label": domestic_label,
            "currency": currency_label,
            "foreign_val": round(foreign_flow, 2),
            "domestic_val": round(domestic_flow, 2),
            "net": round(net_flow, 2),
            "status": status
        }

    except Exception as e:
        return {
            "foreign_label": "Foreign Flow", "domestic_label": "Local Flow", "currency": "Units",
            "foreign_val": 1245.50, "domestic_val": -450.75, "net": 794.75, "status": "Estimated Flow"
        }

# ==========================================
# 3. HEATMAP ENGINE
# ==========================================
GLOBAL_SECTORS = {
    # 1. UNITED STATES
    "US": {
        "Technology": ["AAPL", "MSFT", "NVDA", "GOOGL", "META"],
        "Financials": ["JPM", "BAC", "WFC", "C", "GS"],
        "Energy & Oil": ["XOM", "CVX", "COP", "SLB", "EOG"],
        "Automobile": ["TSLA", "F", "GM", "TM", "HMC"],
        "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK"],
        "Consumer": ["PG", "KO", "PEP", "WMT", "COST"]
    },
    
    # 2. INDIA (NSE)
    "IN": {
        "Technology": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
        "Financials": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS"],
        "Energy & Oil": ["RELIANCE.NS", "ONGC.NS", "POWERGRID.NS", "COALINDIA.NS", "NTPC.NS"],
        "Automobile": ["TATAMOTORS.NS", "M&M.NS", "MARUTI.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS"],
        "Healthcare": ["SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS", "APOLLOHOSP.NS"],
        "Consumer (FMCG)": ["ITC.NS", "HINDUNILVR.NS", "NESTLEIND.NS", "BRITANNIA.NS", "TITAN.NS"]
    },
    
    # 3. CHINA (US ADRs & HK Listings for API Reliability)
    "CN": { 
        "Technology": ["BABA", "0700.HK", "JD", "BIDU", "NTES"],
        "Financials": ["0939.HK", "1398.HK", "2318.HK", "3968.HK", "03988.HK"],
        "Energy & Oil": ["0857.HK", "0386.HK", "0883.HK", "PTR", "SNP"],
        "Automobile": ["NIO", "XPEV", "LI", "1211.HK", "0175.HK"],
        "Consumer": ["PDD", "YUMC", "BILI", "TME", "ZTO"]
    },

    # 4. JAPAN (Tokyo Stock Exchange)
    "JP": {
        "Technology": ["9984.T", "6758.T", "8035.T", "6861.T", "6981.T"], # Softbank, Sony, Tokyo Electron
        "Financials": ["8306.T", "8316.T", "8411.T", "8766.T", "8591.T"], # MUFG, SMFG
        "Energy & Trading": ["8058.T", "8031.T", "8001.T", "5020.T", "1605.T"], # Mitsubishi, Mitsui, Eneos
        "Automobile": ["7203.T", "7267.T", "7269.T", "7201.T", "7270.T"], # Toyota, Honda, Nissan
        "Healthcare": ["4502.T", "4568.T", "4519.T", "4523.T", "4507.T"] # Takeda, Daiichi
    },

    # 5. UNITED KINGDOM (London Stock Exchange)
    "GB": {
        "Technology/Data": ["EXPN.L", "REL.L", "SGE.L", "HLMA.L", "INF.L"],
        "Financials": ["HSBA.L", "BARC.L", "LLOY.L", "LSEG.L", "NWG.L"],
        "Energy & Oil": ["SHEL.L", "BP.L", "NG.L", "SSE.L", "CNA.L"],
        "Healthcare": ["AZN.L", "GSK.L", "HLN.L", "SN.L", "HIK.L"],
        "Consumer & Auto": ["ULVR.L", "BATS.L", "DGE.L", "RR.L", "AML.L"] # Includes Rolls Royce & Aston Martin
    },

    # 6. GERMANY (Xetra)
    "DE": {
        "Technology": ["SAP.DE", "IFX.DE", "SY1.DE", "NEM.DE", "BEI.DE"],
        "Financials": ["ALV.DE", "MUV2.DE", "DBK.DE", "CBK.DE", "TKA.DE"],
        "Energy & Utilities": ["ENR.DE", "EOAN.DE", "RWE.DE", "SIE.DE", "SHL.DE"],
        "Automobile": ["BMW.DE", "VOW3.DE", "MBG.DE", "PAH3.DE", "CON.DE"], # BMW, VW, Mercedes, Porsche
        "Healthcare": ["BAYN.DE", "MRK.DE", "SRT3.DE", "FME.DE", "QIA.DE"]
    },

    # 7. FRANCE (Euronext Paris)
    "FR": {
        "Technology": ["CAP.PA", "DSY.PA", "STM.PA", "WLN.PA", "SO.PA"],
        "Financials": ["BNP.PA", "GLE.PA", "ACA.PA", "CS.PA", "CNP.PA"],
        "Energy & Oil": ["TTE.PA", "ENGI.PA", "LR.PA", "VK.PA", "GTT.PA"], # TotalEnergies
        "Automobile & Aero": ["STE.PA", "RNO.PA", "AIR.PA", "SAF.PA", "HO.PA"], # Stellantis, Renault, Airbus
        "Luxury & Consumer": ["MC.PA", "RMS.PA", "CDI.PA", "OR.PA", "KER.PA"] # LVMH, Hermes, L'Oreal
    },

    # 8. CANADA (Toronto Stock Exchange)
    "CA": {
        "Technology": ["SHOP.TO", "CSU.TO", "CGI.TO", "OTEX.TO", "KXS.TO"],
        "Financials": ["RY.TO", "TD.TO", "BMO.TO", "BNS.TO", "CM.TO"],
        "Energy & Oil": ["CNQ.TO", "SU.TO", "TRP.TO", "ENB.TO", "CVE.TO"],
        "Mining & Materials": ["GOLD.TO", "NTR.TO", "TECK-B.TO", "FNV.TO", "WPM.TO"],
        "Automobile & Rail": ["MG.TO", "BRP.TO", "CNR.TO", "CP.TO", "NFI.TO"] # Magna Int.
    },

    # 9. AUSTRALIA (ASX)
    "AU": {
        "Technology": ["WTC.AX", "XRO.AX", "CPU.AX", "REA.AX", "NXT.AX"],
        "Financials": ["CBA.AX", "WBC.AX", "NAB.AX", "ANZ.AX", "MQG.AX"],
        "Energy & Mining": ["BHP.AX", "RIO.AX", "WDS.AX", "FMG.AX", "STO.AX"],
        "Healthcare": ["CSL.AX", "SHL.AX", "COH.AX", "RHC.AX", "FPH.AX"],
        "Consumer": ["WOW.AX", "WES.AX", "COL.AX", "TLC.AX", "ALL.AX"]
    },

    # 10. SOUTH KOREA (KOSPI)
    "KR": {
        "Technology": ["005930.KS", "000660.KS", "035420.KS", "035720.KS", "018260.KS"], # Samsung, SK Hynix
        "Financials": ["105560.KS", "055550.KS", "086790.KS", "316140.KS", "024110.KS"],
        "Energy & Chem": ["096770.KS", "051910.KS", "010950.KS", "011780.KS", "267250.KS"],
        "Automobile": ["005380.KS", "000270.KS", "012330.KS", "005385.KS", "028670.KS"], # Hyundai, Kia
        "Consumer & Bio": ["207940.KS", "068270.KS", "051900.KS", "090430.KS", "028260.KS"]
    },

    # 11. BRAZIL (B3)
    "BR": {
        "Technology & FinTech": ["TOTVS3.SA", "PAGS", "STNE", "LWSA3.SA", "CIEL3.SA"],
        "Financials": ["ITUB4.SA", "BBDC4.SA", "BBAS3.SA", "B3SA3.SA", "SANB11.SA"],
        "Energy & Oil": ["PETR4.SA", "PETR3.SA", "PRIO3.SA", "UGPA3.SA", "VBBR3.SA"], # Petrobras
        "Mining & Materials": ["VALE3.SA", "GGBR4.SA", "CSNA3.SA", "SUZB3.SA", "KLBN11.SA"],
        "Consumer & Auto": ["WEGE3.SA", "RENT3.SA", "RADL3.SA", "LREN3.SA", "MGLU3.SA"]
    },

    # 12. ITALY (Borsa Italiana)
    "IT": {
        "Technology & Eng": ["STMMI.MI", "PRY.MI", "LEO.MI", "REC.MI", "BAMI.MI"],
        "Financials": ["ISP.MI", "UCG.MI", "G.MI", "MB.MI", "PST.MI"],
        "Energy & Utilities": ["ENEL.MI", "ENI.MI", "SRG.MI", "TRN.MI", "TEN.MI"],
        "Automobile": ["RACE.MI", "PMA.MI", "STLA.MI", "PIA.MI", "BRE.MI"], # Ferrari, Pirelli
        "Consumer": ["CPR.MI", "MONC.MI", "AMP.MI", "DIA.MI", "IG.MI"]
    },

    # 13. SPAIN (Bolsa de Madrid)
    "ES": {
        "Technology": ["AMADEUS.MC", "CLNX.MC", "IND.MC", "CABK.MC", "AENA.MC"],
        "Financials": ["SAN.MC", "BBVA.MC", "CABK.MC", "SAB.MC", "BKT.MC"],
        "Energy & Utilities": ["IBE.MC", "REP.MC", "NTGY.MC", "ELE.MC", "ENG.MC"], # Iberdrola, Repsol
        "Automobile & Ind": ["CIE.MC", "ACS.MC", "FER.MC", "AENA.MC", "IAG.MC"],
        "Consumer": ["ITX.MC", "GRF.MC", "MEL.MC", "VIS.MC", "FDR.MC"] # Inditex (Zara)
    },

    # 14. NETHERLANDS (Euronext Amsterdam)
    "NL": {
        "Technology": ["ASML.AS", "ADYEN.AS", "ASM.AS", "BESI.AS", "TKWY.AS"], # ASML, Adyen
        "Financials": ["INGA.AS", "ABN.AS", "NN.AS", "ASRN.AS", "KPN.AS"],
        "Energy & Chemicals": ["SHELL.AS", "AKZA.AS", "DSM.AS", "OCI.AS", "VOPA.AS"],
        "Automobile & Ind": ["STLA.AS", "RAND.AS", "MT.AS", "IMCD.AS", "BOS.AS"], # Stellantis HQ
        "Consumer": ["HEIA.AS", "AD.AS", "UNA.AS", "URW.AS", "JDEP.AS"]
    },

    # 15. SWITZERLAND (SIX Swiss Exchange)
    "CH": {
        "Technology": ["LOGN.SW", "SOON.SW", "AMS.SW", "TEMN.SW", "UHR.SW"], # Logitech
        "Financials": ["UBSG.SW", "ZURN.SW", "SREN.SW", "SLHN.SW", "JULIUS.SW"], # UBS
        "Healthcare (Massive)": ["NOVN.SW", "ROG.SW", "LONN.SW", "ALC.SW", "SON.SW"], # Novartis, Roche
        "Industrials": ["ABBN.SW", "SIKA.SW", "GEBN.SW", "HOLN.SW", "KNIN.SW"],
        "Consumer": ["NESN.SW", "CFR.SW", "UHR.SW", "LINDT.SW", "GIVN.SW"] # Nestle
    },

    # 16. TAIWAN (TWSE)
    "TW": {
        "Technology": ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW"], # TSMC, Foxconn, MediaTek
        "Financials": ["2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW"],
        "Energy & Plastics": ["6505.TW", "1301.TW", "1303.TW", "1326.TW", "2002.TW"],
        "Automobile & Parts": ["2201.TW", "2207.TW", "2105.TW", "1536.TW", "2227.TW"],
        "Telecom": ["2412.TW", "3045.TW", "4904.TW", "2603.TW", "2609.TW"]
    },

    # 17. SOUTH AFRICA (JSE)
    "ZA": {
        "Technology & Comms": ["NPN.JO", "PRX.JO", "MTN.JO", "VOD.JO", "TKG.JO"], # Naspers
        "Financials": ["FSR.JO", "SBK.JO", "ABG.JO", "NED.JO", "DSY.JO"],
        "Energy & Mining": ["SOL.JO", "AGL.JO", "GFI.JO", "SSW.JO", "ANG.JO"], # Sasol, Gold Fields
        "Retail & Consumer": ["SHP.JO", "WHL.JO", "BVT.JO", "CPI.JO", "MRP.JO"],
        "Healthcare": ["DSC.JO", "NTC.JO", "MEI.JO", "LHC.JO", "APN.JO"]
    },

    # 18. SWEDEN (Nasdaq Stockholm)
    "SE": {
        "Technology": ["ERIC-B.ST", "HEXA-B.ST", "SINCH.ST", "ENTRA.ST", "NOKIA.ST"], # Ericsson
        "Financials": ["SEB-A.ST", "SHB-A.ST", "SWEDA.ST", "EQT.ST", "INVE-B.ST"],
        "Automobile & Ind": ["VOLV-B.ST", "ASSA-B.ST", "ATCO-A.ST", "EPI-A.ST", "ALFA.ST"], # Volvo
        "Healthcare": ["AZN.ST", "GETI-B.ST", "SOBI.ST", "VITR.ST", "ELEK-B.ST"],
        "Consumer": ["HM-B.ST", "EVO.ST", "TELIA.ST", "SWMA.ST", "KINV-B.ST"]
    },

    # 19. SINGAPORE (SGX)
    "SG": {
        "Technology": ["V03.SI", "U96.SI", "F34.SI", "A17U.SI", "Z74.SI"],
        "Financials": ["D05.SI", "O39.SI", "U11.SI", "S68.SI", "C38U.SI"], # DBS, OCBC, UOB
        "Energy & Offshore": ["S51.SI", "C52.SI", "T39.SI", "BS6.SI", "CJLU.SI"],
        "Industrials & Trans": ["C6L.SI", "S63.SI", "C31.SI", "U96.SI", "BQC.SI"], # Singtel, Airlines
        "Real Estate": ["A17U.SI", "ME8U.SI", "N2IU.SI", "M44U.SI", "C38U.SI"]
    },

    # 20. INDONESIA (IDX)
    "ID": {
        "Technology": ["GOTO.JK", "EMTK.JK", "BUKA.JK", "DCII.JK", "MTEL.JK"],
        "Financials": ["BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "ARTO.JK"], # BCA, BRI
        "Energy & Mining": ["BYAN.JK", "ADRO.JK", "PGAS.JK", "PTBA.JK", "MEDC.JK"],
        "Automobile": ["ASII.JK", "AUTO.JK", "IMAS.JK", "SMSM.JK", "MASA.JK"], # Astra Int.
        "Consumer": ["ICBP.JK", "UNVR.JK", "INDF.JK", "CPIN.JK", "KLBF.JK"]
    },

    # 21. SAUDI ARABIA (Tadawul)
    "SA": {
        "Technology & Comms": ["7010.SR", "7020.SR", "7030.SR", "7203.SR", "7110.SR"], # STC
        "Financials": ["1120.SR", "1180.SR", "1060.SR", "1050.SR", "1080.SR"], # Al Rajhi
        "Energy & Oil": ["2222.SR", "2380.SR", "2010.SR", "2020.SR", "2030.SR"], # Saudi Aramco
        "Basic Materials": ["2010.SR", "2020.SR", "2250.SR", "2060.SR", "2280.SR"], # SABIC
        "Healthcare": ["4002.SR", "4004.SR", "4005.SR", "4007.SR", "4013.SR"]
    },

    # 22. TURKEY (Borsa Istanbul)
    "TR": {
        "Technology & Defense": ["ASELS.IS", "LOGO.IS", "ARDYZ.IS", "KFEIN.IS", "MIATK.IS"],
        "Financials": ["GARAN.IS", "AKBNK.IS", "YKBNK.IS", "ISCTR.IS", "SAHOL.IS"],
        "Energy & Oil": ["TUPRS.IS", "ENJSA.IS", "ODAS.IS", "AKENR.IS", "GWIND.IS"],
        "Automobile": ["TOASO.IS", "FROTO.IS", "DOAS.IS", "KARSN.IS", "OTKAR.IS"], # Tofas, Ford Otosan
        "Consumer & Air": ["THYAO.IS", "PGSUS.IS", "BIMAS.IS", "CCOLA.IS", "MGROS.IS"] # Turkish Airlines
    },

    # 23. POLAND (Warsaw Stock Exchange)
    "PL": {
        "Technology (Gaming)": ["CDR.WA", "11B.WA", "TEN.WA", "PLW.WA", "PCF.WA"], # CD Projekt
        "Financials": ["PKO.WA", "PEO.WA", "PZU.WA", "SPL.WA", "MBK.WA"],
        "Energy & Resources": ["PKN.WA", "PGE.WA", "KGH.WA", "JSW.WA", "TPE.WA"], # PKN Orlen, KGHM
        "Consumer": ["LPP.WA", "DNP.WA", "ALE.WA", "CCC.WA", "ACP.WA"],
        "Telecom": ["OPL.WA", "CPS.WA", "NET.WA", "CMR.WA", "ATC.WA"]
    },

    # 24. MEXICO (BMV)
    "MX": {
        "Telecommunications": ["AMXL.MX", "TLEVISACPO.MX", "MEGACOA.MX", "AXTELCPO.MX", "TVEA.MX"], # America Movil
        "Financials": ["GFNORTEO.MX", "BBAJIOO.MX", "GENTERA.MX", "Q.MX", "GCARSOA1.MX"],
        "Materials & Mining": ["GMEXICOB.MX", "CEMEXCPO.MX", "PENOLES.MX", "ORBIA.MX", "GCC.MX"], # Cemex
        "Consumer & Food": ["WALMEX.MX", "FEMSAUBD.MX", "BIMBOA.MX", "GRUMAB.MX", "AC.MX"],
        "Industrials & Infra": ["ASURB.MX", "GAPB.MX", "OMAB.MX", "ALFAA.MX", "PINFRA.MX"]
    },

    # 25. HONG KONG (Hang Seng Local View)
    "HK": {
        "Technology": ["0700.HK", "3690.HK", "1810.HK", "9988.HK", "9618.HK"], # Tencent, Meituan, Xiaomi
        "Financials": ["0005.HK", "1299.HK", "0388.HK", "2318.HK", "0011.HK"], # HSBC, AIA
        "Energy & Resources": ["0883.HK", "0386.HK", "0857.HK", "0267.HK", "1088.HK"],
        "Automobile": ["1211.HK", "0175.HK", "2015.HK", "2333.HK", "0425.HK"], # BYD, Geely
        "Real Estate": ["0016.HK", "0001.HK", "0823.HK", "1109.HK", "1113.HK"] # Sun Hung Kai, CK Asset
    }
}

def get_color(change):
    if change >= 2: return "#166534"       
    elif change > 0: return "#22c55e"      
    elif change <= -2: return "#991b1b"    
    elif change < 0: return "#ef4444"      
    else: return "#475569"                 

def run_heatmap(country_code="US"):
    country_code = country_code.upper()
    
    # 1. Grab ONLY the requested country's sectors (Defaults to US)
    sectors = GLOBAL_SECTORS.get(country_code, GLOBAL_SECTORS["US"])
    
    # 2. Extract all unique tickers for the API call
    all_tickers = []
    for sector, tickers in sectors.items():
        all_tickers.extend(tickers)
        
    try:
        # Fetch data for all tickers at once
        data = yf.download(all_tickers, period="5d", progress=False)['Close']
        tickers_obj = yf.Tickers(" ".join(all_tickers))
        heatmap_data = []
        
        # Loop through the country's dictionary
        for sector, tickers in sectors.items():
            sector_data = []
            for t in tickers:
                try:
                    recent_prices = data[t].dropna()
                    if len(recent_prices) >= 2:
                        prev_close = recent_prices.iloc[-2]
                        current = recent_prices.iloc[-1]
                        change = ((current - prev_close) / prev_close) * 100
                    else:
                        change = 0
                        
                    try:
                        mc = tickers_obj.tickers[t].info.get('marketCap', 1000000000)
                    except:
                        mc = 1000000000 
                        
                    mc_scaled = mc / 10000000 
                    
                    sector_data.append({
                        "x": t.replace(".NS", ""), 
                        "y": round(mc_scaled),     
                        "change": round(change, 2),
                        "fillColor": get_color(change)
                    })
                except Exception:
                    continue
                    
            if sector_data:
                heatmap_data.append({"name": sector, "data": sector_data})
                
        return heatmap_data
    except Exception as e:
        return {"error": str(e)}
# ==========================================
# 4. MACRO EXPLORER ENGINE
# ==========================================
INDICATORS = {
    "gdp_total": "NY.GDP.MKTP.CD",       
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",   
    "inflation": "FP.CPI.TOTL.ZG",       
    "unemployment": "SL.UEM.TOTL.ZS",
    "interest_rate": "FR.INR.LEND",      
    "debt_to_gdp": "GC.DOD.TOTL.GD.ZS"   
}

CURRENCY_MAP = {"IN": "INR", "CN": "CNY", "JP": "JPY", "DE": "EUR", "GB": "GBP", "CA": "CAD", "AU": "AUD", "US": "USD"}
BOND_MAP = {"US": "^TNX", "IN": "^IN10YT=RR", "CN": "CN10YT=RR", "JP": "^JN10YT=RR", "DE": "^DE10YT=RR", "GB": "^UK10YT=RR"}

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

def fetch_wb_indicator(key, country_code, date_range):
    """Worker to fetch World Bank Indicators in a single bulk page"""
    try:
        # ADDED per_page=1000 so the API doesn't cut off our data!
        url = f"http://api.worldbank.org/v2/country/{country_code}/indicator/{INDICATORS[key]}?format=json&date={date_range}&per_page=1000"
        resp = requests.get(url, timeout=10).json()
        
        if len(resp) > 1 and resp[1] is not None:
            data_list = resp[1]
            values, years = [], []
            for entry in reversed(data_list):
                val = entry['value']
                values.append(round(val, 2) if val is not None else 0)
                years.append(entry['date'])
            return key, years, values
    except Exception as e:
        sys.stderr.write(f"WB Error {key}: {str(e)}\n")
    return key, [], []

def fetch_yf_yearly(ticker_type, ticker_symbol, history_years):
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
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if len(hist) >= 2:
            prev = hist['Close'].iloc[0]
            curr = hist['Close'].iloc[1]
            change = ((curr - prev) / prev) * 100
            return {"sector": sector, "company": name, "ticker": ticker, "price": round(curr, 2), "change": round(change, 2)}
    except Exception:
        pass
    return None

def run_macro_explorer(country_code):
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fetch_wb_indicator, key, country_code, date_range): key for key in INDICATORS.keys()}
            
            for future in concurrent.futures.as_completed(futures):
                key, years, values = future.result()
                if len(years) > len(output["history_years"]): 
                    output["history_years"] = years 
                
                if key == "gdp_total": output["gdp_total_trend"] = values
                elif key == "gdp_growth": output["gdp_trend"] = values
                elif key == "inflation": output["inflation_trend"] = values
                elif key == "unemployment": output["unemployment_trend"] = values
                elif key == "interest_rate": output["interest_rate_trend"] = values
                elif key == "debt_to_gdp": output["debt_trend"] = values

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

        advanced_data = ADVANCED_EXPORTS.get(country_code, ADVANCED_EXPORTS["US"])
        output["advanced_exports"] = advanced_data

        screener_results = []
        screener_futures = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for sector_info in advanced_data[:2]:
                for stock_str in sector_info["stocks"]:
                    name = stock_str.split("(")[0].strip()
                    ticker = stock_str.split("(")[1].replace(")", "")
                    screener_futures.append(executor.submit(fetch_screener_stock, sector_info["sector"], name, ticker))
            
            for future in concurrent.futures.as_completed(screener_futures):
                result = future.result()
                if result:
                    screener_results.append(result)

        output["screener"] = screener_results

    except Exception as e:
        output["error"] = str(e)

    return output

# ==========================================
# 5. THE ROUTER (Master Entry Point)
# ==========================================
if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "Missing action argument"}))
            sys.exit(1)
            
        action = sys.argv[1].lower()
        
        # Some actions require a country code as the second argument
        arg2 = sys.argv[2] if len(sys.argv) > 2 else "US"
        
        result = {}

        if action == "correlation":
            result = run_correlation()
        elif action == "liquidity":
            result = run_liquidity(arg2)
        elif action == "heatmap":
            result = run_heatmap(arg2)
        elif action == "macro":
            result = run_macro_explorer(arg2)
        else:
            result = {"error": f"Unknown action: {action}"}

        print(json.dumps(result))
        sys.stdout.flush()

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.stdout.flush()