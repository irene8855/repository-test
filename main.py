import os
import time
import pandas as pd
import numpy as np
import requests
import joblib
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestRegressor

# === ENVIRONMENT ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLYGON_API_URL = os.getenv("POLYGON_API_URL")

# === TOKEN ADDRESSES ===
TOKEN_PAIRS = {
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LDO": "0xc3d688b66703497daa19211eedff47f25384cdc3",
    "wstETH": "0x7f39c581f595b53c5cb5bb2d7205e62b578e1e7c",
    "BET": "0x5C3e1e1C38691eD7476A35a266fEb3cE5A770c44",
    "GMT": "0xe3c408BD53c31C085a1746AF401A4042954ff740",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "EMT": "0x3ea8ea4237344c9931214796d9417af1a1180770"
}

# === DEX LINKS ===
DEX_LINKS = {
    "sushi": "https://www.sushi.com/swap?from=USDT&to={token}",
    "uniswap": "https://app.uniswap.org/#/swap?inputCurrency=USDT&outputCurrency={token}",
    "1inch": "https://app.1inch.io/#/137/swap/USDT/{token}"
}

# === TELEGRAM ===
def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except Exception as e:
        print(f"Telegram error: {e}")

# === MODEL ===
def load_or_train_model():
    if os.path.exists("model.pkl"):
        return joblib.load("model.pkl")

    df = pd.read_csv("historical.csv")
    df["avg_profit"] = (df["profit_low"] + df["profit_high"]) / 2
    features = pd.get_dummies(df[["pair", "timing", "platform"]])
    model = RandomForestRegressor(n_estimators=100)
    model.fit(features, df["avg_profit"])
    joblib.dump(model, "model.pkl")
    return model

# === PREDICT BEST TRADE ===
def predict_best_trade(model):
    df = pd.read_csv("historical.csv")
    options = []

    for token in TOKEN_PAIRS.keys():
        for timing in [3, 4]:
            for platform in DEX_LINKS.keys():
                pair_str = f"USDT->{token}->USDT"
                platform_url = f"https://{platform}.com" if platform != "uniswap" else "https://app.uniswap.org/"
                row = pd.DataFrame([{
                    "pair": pair_str,
                    "timing": timing,
                    "platform": platform_url
                }])
                X = pd.get_dummies(row).reindex(columns=model.feature_names_in_, fill_value=0)
                pred = model.predict(X)[0]
                options.append((pred, pair_str, timing, platform))

    options.sort(reverse=True)
    return options[0]

# === REAL PROFIT CALCULATION (On-chain via Alchemy) ===
def fetch_onchain_profit(token_address):
    try:
        headers = {"accept": "application/json"}
        url = f"{POLYGON_API_URL}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{
                "to": token_address,
                "data": "0x70a08231000000000000000000000000" + os.getenv("WALLET_ADDRESS")[2:]
            }, "latest"]
        }
        resp = requests.post(url, json=payload, headers=headers).json()
        value = int(resp["result"], 16)
        return round(value / 1e18, 4)  # USDT or token decimals assumed
    except Exception as e:
        print("Ошибка при fetch_onchain_profit:", e)
        return None

# === MAIN LOOP ===
def main():
    print("✅ main.py стартовал")
    print("✅ TELEGRAM_TOKEN:", "OK" if TELEGRAM_TOKEN else "❌")
    print("✅ TELEGRAM_CHAT_ID:", "OK" if TELEGRAM_CHAT_ID else "❌")
    print("✅ POLYGON_API_URL:", "OK" if POLYGON_API_URL else "❌")

    send_telegram_message("🚀 Бот мониторинга и прогноза запущен")

    model = load_or_train_model()

    while True:
        predicted_profit, pair, timing, platform = predict_best_trade(model)
        token = pair.split("->")[1]
        token_address = TOKEN_PAIRS[token]
        platform_link = DEX_LINKS[platform].format(token=token_address)

        start_time = datetime.utcnow()
        end_time = start_time + timedelta(minutes=timing)

        predicted_msg = (
            f"📉{pair}📈\n"
            f"TIMING: {timing} MIN ⏱️\n"
            f"TIME FOR START: {start_time.strftime('%H:%M')}\n"
            f"TIME FOR SELL: {end_time.strftime('%H:%M')}\n"
            f"PROFIT: {round(predicted_profit - 0.1, 2)}–{round(predicted_profit + 0.1, 2)} 💸\n"
            f"PLATFORM:\n{platform_link}"
        )
        send_telegram_message(predicted_msg)

        time.sleep(timing * 60)

        # CONFIRMATION
        real_profit = fetch_onchain_profit(token_address)
        if real_profit is not None:
            confirm_msg = (
                f"✅ CONFIRMED TRADE\n"
                f"{pair}\n"
                f"REAL TOKEN BALANCE: {real_profit} {token} 💰\n"
                f"PLATFORM:\n{platform_link}"
            )
            send_telegram_message(confirm_msg)

        time.sleep(60)

if __name__ == "__main__":
    main()
    
