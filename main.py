import time
import json
import requests
import pandas as pd
import numpy as np
from web3 import Web3
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
import logging
from secrets import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALCHEMY_API_KEY

# === Logging ===
logging.basicConfig(filename='logs.csv', level=logging.INFO, format='%(message)s')

# === RPC ===
RPC_URL = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
web3 = Web3(Web3.HTTPProvider(RPC_URL))

# === Token Contracts ===
TOKENS = {
    "USDT": "0xc2132D05D31c914A87C6611C10748AEb04B58e8F",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "MATIC": "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0",
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    "MKR": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"
}

DEX_LINKS = {
    "sushi": "https://www.sushi.com",
    "uniswap": "https://app.uniswap.org",
    "1inch": "https://1inch.io"
}

# === Telegram ===
def send_message(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

# === Load Historical Data for ML ===
def load_historical():
    with open("historical.json") as f:
        data = json.load(f)
    rows = []
    for d in data:
        profit = d["profit_range"]
        avg_profit = (
            float(profit.split("-")[0])
            if "-" not in profit
            else np.mean([float(p) for p in profit.split("-")])
        )
        rows.append({
            "pair": d["pair"],
            "platform": d["platform"],
            "profit": avg_profit
        })
    return pd.DataFrame(rows)

# === Train simple model ===
def train_model(df):
    df["platform_code"] = df["platform"].astype("category").cat.codes
    df["target"] = df["profit"].apply(lambda x: 1 if x > 1.5 else 0)
    X = df[["platform_code"]]
    y = df["target"]
    model = RandomForestClassifier()
    model.fit(X, y)
    return model

# === Predict profitability ===
def is_profitable(platform: str, model):
    platform_code = pd.Series([platform]).astype("category").cat.codes[0]
    return bool(model.predict([[platform_code]])[0])

# === Simulated price fetch (replace with actual DEX price fetch later) ===
def fetch_price(pair_name):
    # TODO: Replace with real contract queries
    price = np.random.uniform(0.98, 1.05)
    volume = np.random.uniform(1000, 100000)
    return price, volume

# === Main monitoring loop ===
def monitor_loop(model):
    token_pairs = [
        ("USDT", "FRAX"),
        ("USDT", "LDO"),
        ("USDT", "SAND"),
        ("USDT", "GMT"),
        ("USDT", "LINK"),
        ("USDT", "wstETH"),
        ("USDT", "AAVE"),
        ("USDT", "MKR")
    ]

    send_message("🤖 Бот запущен. Мониторинг сделок в реальном времени!")

    while True:
        now = datetime.utcnow().strftime("%H:%M")
        for base, quote in token_pairs:
            for dex in DEX_LINKS:
                price, volume = fetch_price(f"{base}->{quote}")
                if volume < 5000:
                    continue

                # Dummy profit estimation
                profit = round((price - 1.0) * 100, 2)
                if abs(profit) < 1.5:
                    continue

                if not is_profitable(DEX_LINKS[dex], model):
                    continue

                msg = f"""
📉{base}->{quote}->{base}📈
TIMING: 4 MIN⌛️
TIME FOR START: {now}
TIME FOR SELL: {datetime.utcnow().strftime('%H:%M')}
PROFIT: {profit:.2f}% 💸
PLATFORMS:📊
{DEX_LINKS[dex]}
                """
                send_message(msg.strip())

                # Log the deal
                logging.info(f"{datetime.utcnow()},{base}->{quote},{DEX_LINKS[dex]},{price:.4f},{volume:.0f},{profit:.2f}")

        time.sleep(30)  # 30 сек. интервал

# === Main ===
if __name__ == "__main__":
    historical_df = load_historical()
    model = train_model(historical_df)
    monitor_loop(model)
    
