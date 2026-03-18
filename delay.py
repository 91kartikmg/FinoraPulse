import yfinance as yf
import time
from datetime import datetime
import pytz
import random

def monitor_tcs_final():
    ticker_symbol = "TCS.NS"
    local_tz = pytz.timezone("Asia/Kolkata")
    
    # Define the ticker ONCE outside the loop to reduce overhead
    tcs = yf.Ticker(ticker_symbol)
    
    print(f"--- TCS NSE Monitor (Resilient Mode) ---")
    
    while True:
        try:
            # period="1d" is fine, but sometimes "2d" is more stable for 1m data
            data = tcs.history(period="1d", interval="1m")

            if data is not None and not data.empty:
                last_candle_time = data.index[-1].to_pydatetime()
                current_price = data['Close'].iloc[-1]
                
                now_ist = datetime.now(local_tz)
                delay = now_ist - last_candle_time
                delay_minutes = delay.total_seconds() / 60

                print(f"[{now_ist.strftime('%H:%M:%S')}] ₹{current_price:.2f} | Lag: {delay_minutes:.1f}m")
            else:
                print(f"[{datetime.now(local_tz).strftime('%H:%M:%S')}] Data is empty. NSE might be in a cooling period.")

        except Exception as e:
            print(f"[{datetime.now(local_tz).strftime('%H:%M:%S')}] Connection Error: {e}")
            # If it fails, wait a bit longer before trying again
            time.sleep(10)

        # Wait ~60 seconds with a tiny bit of randomness to avoid bot detection
        wait_time = 60 + random.randint(1, 5)
        time.sleep(wait_time)

if __name__ == "__main__":
    monitor_tcs_final()