import os
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

from trading.broker.zerodha.broker import ZerodhaBroker
from trading.broker.zerodha.kite_client import KiteClient

# Load environment variables
load_dotenv()

API_KEY = os.getenv("ZERODHA_API_KEY")
API_SECRET = os.getenv("ZERODHA_API_SECRET")

if API_KEY is None or API_SECRET is None:
    raise ValueError("Missing Zerodha credentials")


# -------- Login --------
client = KiteClient(API_KEY)

print("Login URL:")
print(client.login_url())

request_token = input("\nPaste request_token: ")

session = client.generate_session(request_token, API_SECRET)
client.set_access_token(session["access_token"])

print("\nLogin successful!")

print("\nProfile:")
print(client.profile())


# -------- Broker --------
broker = ZerodhaBroker(client)


# -------- Sample OHLC --------
tz = pytz.timezone("Asia/Kolkata")

end = datetime.now(tz)
start = end - timedelta(days=5)

df = broker.get_ohlc(
    symbol="INFY",
    interval="5minute",
    start=start,
    end=end,
)

print("\nSample candles:")
print(df.tail())
