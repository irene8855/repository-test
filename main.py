import requests
import pandas as pd
from datetime import datetime, timedelta

symbol = "FRAXUSDT"
granularity = 60  # 1 минута

# Интервал: 5 минут до сделки (15:06 — 15:11, 22 июля 2025)
end_time = datetime(2025, 7, 22, 15, 11)
start_time = end_time - timedelta(minutes=5)

start = int(start_time.timestamp() * 1000)
end = int(end_time.timestamp() * 1000)

url = f"https://api.bitget.com/api/spot/v1/market/candles?symbol={symbol}&granularity={granularity}&startTime={start}&endTime={end}"

response = requests.get(url)
data = response.json()

df = pd.DataFrame(data["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
df = df.sort_values("timestamp")
df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)

print(df)
