import os
import time
import pandas as pd
import numpy as np
import requests
import datetime
from sklearn.ensemble import RandomForestRegressor
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()
POLYGON_RPC = os.getenv("POLYGON_RPC")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

ROUTERS = {
    "Uniswap": {
        "url": "https://app.uniswap.org/#/swap?inputCurrency={}&outputCurrency={}",
        "router_address": Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564"),
    },
    "SushiSwap": {
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "router_address": Web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
    },
    "1inch": {
        "url": "https://app.1inch.io/#/137/swap/{}-{}/USDT",
        "api_url": "https://api.1inch.dev/swap/v5.2/137/quote",
    }
}

TOKENS = {
    "USDT": Web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"),
    "FRAX": Web3.to_checksum_address("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"),
    "AAVE": Web3.to_checksum_address("0xd6df932a45c0f255f85145f286ea0b292b21c90b"),
    "LDO": Web3.to_checksum_address("0xC3C7d422809852031b44ab29EEC9F1EfF2A58756"),
    "BET": Web3.to_checksum_address("0x46e6b214b524310239732D51387075E0e70970bf"),
    "wstETH": Web3.to_checksum_address("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"),
    "GMT": Web3.to_checksum_address("0x5fE80d2CD054645b9419657d3d10d26391780A7B"),
    "Link": Web3.to_checksum_address("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39"),
    "SAND": Web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "EMT": Web3.to_checksum_address("0x6bE7E4A2202cB6E60ef3F94d27a65b906FdA7D86")
}

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def get_real_price(token_in, token_out):
    try:
        router = ROUTERS["Uniswap"]["router_address"]
        abi = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
        contract = web3.eth.contract(address=router, abi=abi)
        amount_in = 10 ** 6  # 1 USDT with 6 decimals
        result = contract.functions.getAmountsOut(amount_in, [token_in, token_out, token_in]).call()
        profit_percent = (result[-1] / amount_in - 1) * 100
        return profit_percent
    except Exception as e:
        print(f"get_real_price error: {e}")
        return None

def predict_best(df, model):
    options = []
    for pair in df["pair"].unique():
        timing_vals = df[df["pair"] == pair]["timing"]
        if timing_vals.empty:
            continue
        timing = timing_vals.mean()
        platform_vals = df[df["pair"] == pair]["platform"]
        if platform_vals.empty:
            continue
        platform = platform_vals.mode().iloc[0]
        tokens = pair.split("->")
        if len(tokens) == 3:
            token1 = tokens[1]
            if token1 in TOKENS:
                price = get_real_price(TOKENS["USDT"], TOKENS[token1])
                if price is not None:
                    X = pd.DataFrame([[timing, price]], columns=["timing", "profit_low"])
                    try:
                        pred = model.predict(X)[0]
                        options.append({
                            "pair": pair,
                            "timing": timing,
                            "platform": platform,
                            "pred": pred
                        })
                    except Exception as e:
                        print(f"Model prediction error: {e}")
    if options:
        return max(options, key=lambda x: x["pred"])
    return None

def confirm_trade(pair):
    tokens = pair.split("->")
    if len(tokens) == 3 and tokens[1] in TOKENS:
        profit = get_real_price(TOKENS["USDT"], TOKENS[tokens[1]])
        return profit
    return None

def train_model(historical_path="historical.csv"):
    try:
        df = pd.read_csv(historical_path)
    except Exception as e:
        print(f"Failed to read historical data: {e}")
        raise

    required_cols = {"timing", "profit_low", "profit_high"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        raise ValueError(f"Historical data missing columns: {missing}")

    df = df.dropna(subset=["timing", "profit_low", "profit_high"])
    df["timing"] = pd.to_numeric(df["timing"], errors='coerce')
    df["profit_low"] = pd.to_numeric(df["profit_low"], errors='coerce')
    df["profit_high"] = pd.to_numeric(df["profit_high"], errors='coerce')
    df = df.dropna(subset=["timing", "profit_low", "profit_high"])

    model = RandomForestRegressor(n_estimators=100, random_state=42)
    model.fit(df[["timing", "profit_low"]], df["profit_high"])
    return model, df

def build_url(platform_name, token_symbol):
    token_addr = TOKENS[token_symbol]
    if platform_name == "1inch":
        return ROUTERS["1inch"]["url"].format("USDT", token_symbol)
    elif platform_name == "SushiSwap":
        return ROUTERS["SushiSwap"]["url"].format("USDT", token_addr)
    else:
        return ROUTERS["Uniswap"]["url"].format("USDT", token_addr)

if __name__ == "__main__":
    try:
        model, df = train_model()
    except Exception as e:
        print(f"Error training model or loading data: {e}")
        exit(1)

    send_telegram("🤖 Бот запущен и работает на реальных данных.")  # Отправляем один раз при запуске

    while True:
        now = datetime.datetime.now()
        best = predict_best(df, model)

        if best:
            start = now.strftime("%H:%M")
            end = (now + datetime.timedelta(minutes=int(best["timing"]))).strftime("%H:%M")
            url = build_url(best["platform"], best["pair"].split("->")[1])

            send_telegram(
                f"📉 {best['pair']} 📈\n"
                f"TIMING: {int(best['timing'])} мин ⌛️\n"
                f"Время старта: {start}\n"
                f"Время продажи: {end}\n"
                f"ПРЕДСКАЗАННАЯ ПРИБЫЛЬ: {round(best['pred'], 2)}%\n"
                f"ПЛАТФОРМА: {best['platform']}\n"
                f"Ссылка: {url}"
            )

            time.sleep(int(best["timing"]) * 60)

            real_profit = confirm_trade(best["pair"])
            if real_profit is not None:
                send_telegram(
                    f"✅ Подтверждение сделки: {best['pair']}\n"
                    f"Реальная прибыль: {round(real_profit, 2)}%"
                )
        else:
            print("No suitable pairs found, waiting...")

        time.sleep(60)
        
