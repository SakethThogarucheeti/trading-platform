import os

from dotenv import load_dotenv

from trading.broker.zerodha.kite_client import KiteClient

load_dotenv()

API_KEY = os.getenv("ZERODHA_API_KEY")
API_SECRET = os.getenv("ZERODHA_API_SECRET")

if API_KEY is None or API_SECRET is None:
    raise ValueError("Missing credentials")


client = KiteClient(API_KEY)

print("Login URL:")
print(client.login_url())

request_token = input("\nPaste request token: ")

session = client.generate_session(request_token, API_SECRET)

client.set_access_token(session["access_token"])

print(client.profile())
