# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
import random
import csv
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

LONDON_TZ = pytz.timezone("Europe/London")

TOKENS = {
    "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    "USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "DAI":  "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX": "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
    "wstETH": "0x03b54A6e9a984069379fae1a4fC4dBAE93B3bCCD",
    "BET": "0xbF7970D56a150cD0b60BD08388A4A75a27777777",
    "WPOL": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    "tBTC": "0x236aa50979d5f3de3bd1eeb40e81137f22ab794b",
    "SAND": "0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683",
    "GMT": "0x714DB550b574b3E927af3D93E26127D15721D4C2",
    "LINK": "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",
    "EMT": "0x708383ae0e80E75377d664E4D6344404dede119A",
    "AAVE": "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "POL": "0x0000000000000000000000000000000000001010",
    "WETH": "0x11CD37bb86F65419713f30673A480EA33c826872",
    "SUSHI": "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a"
}

DECIMALS = {
    "USDT": 6,
    "USDC": 6,
    "DAI": 18,
    "FRAX": 18,
    "wstETH": 18,
    "BET": 18,
    "WPOL": 18,
    "tBTC": 18,
    "SAND": 18,
    "GMT": 8,
    "LINK": 18,
    "EMT": 18,
    "AAVE": 18,
    "LDO": 18,
    "POL": 18,
    "WETH": 18,
    "SUSHI": 18
}

PLATFORMS = {
    "1inch": "1inch",
    "Sushi": "SushiSwap",
    "Uniswap": "UniswapV3",
}

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND
API_URL = "https://polygon.api.0x.org/swap/v1/quote"
BAN_DURATION_SECONDS = 3600

ban_list = {}

def send_telegram(msg: str):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] ÐÑÐ¸Ð±ÐºÐ°: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] ÐÑÐºÐ»ÑÑÐµÐ½Ð¸Ðµ: {e}")

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int):
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount)
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                print(f"[0x API] ÐÑÐ¸Ð±ÐºÐ° {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[0x API] ÐÑÐºÐ»ÑÑÐµÐ½Ð¸Ðµ: {e}")
        return None

def extract_platforms(protocols):
    found = set()
    for segment in protocols:
        for route in segment:
            dex = route[0].lower()
            for platform_key, platform_name in PLATFORMS.items():
                if platform_key.lower() in dex:
                    found.add(platform_name)
    return list(found)

def clean_ban_list():
    now_ts = time.time()
    to_remove = [pair for pair, ts in ban_list.items() if now_ts - ts > BAN_DURATION_SECONDS]
    for pair in to_remove:
        del ban_list[pair]
        if DEBUG_MODE:
            send_telegram(f"ð¢ ÐÐ°ÑÐ° {pair[0]}->{pair[1]} ÑÐ½ÑÑÐ° Ñ Ð±Ð°Ð½-Ð»Ð¸ÑÑÐ°")

def run_real_strategy():
    print("ð Real strategy started")
    send_telegram("ð¤ ÐÐ¾Ñ ÑÐµÐ°Ð»ÑÐ½Ð¾Ð¹ ÑÐ¾ÑÐ³Ð¾Ð²Ð»Ð¸ Ð·Ð°Ð¿ÑÑÐµÐ½")

    base_tokens = ["USDT", "USDC"]
    tracked = {}
    min_profit_percent = 0.5
    last_request_time = 0

    while True:
        now = get_local_time()
        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            if not base_addr:
                continue

            decimals = DECIMALS.get(base_token, 18)
            sell_amount_min = 100 * (10 ** decimals)

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token or (base_token, token_symbol) in ban_list:
                    continue

                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                quote = query_0x_quote(base_addr, token_addr, sell_amount_min)
                if quote is None:
                    ban_list[(base_token, token_symbol)] = time.time()
                    continue

                buy_amount = int(quote.get("buyAmount", "0"))
                if buy_amount == 0:
                    continue

                profit = (buy_amount / sell_amount_min - 1) * 100
                if profit < min_profit_percent:
                    continue

                protocols = quote.get("protocols", [])
                platforms_found = extract_platforms(protocols)
                platforms_used = [p for p in platforms_found if p in PLATFORMS.values()]
                if not platforms_used:
                    continue

                key = (base_token, token_symbol)
                last_time = tracked.get(key, 0)
                if time.time() - last_time < 600:
                    continue
                tracked[key] = time.time()

                url = f"https://app.1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                msg = (f"ð [REAL] Ð¡Ð´ÐµÐ»ÐºÐ°:
{base_token} â {token_symbol}
"
                       f"ÐÑÐ¾ÑÐ¸Ñ: {profit:.2f}%
"
                       f"ÐÐ»Ð°ÑÑÐ¾ÑÐ¼Ñ: {', '.join(platforms_used)}
"
                       f"Ð¡ÑÑÐ»ÐºÐ°: {url}
"
                       f"ÐÑÐµÐ¼Ñ: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

                send_telegram(msg)
                if DEBUG_MODE:
                    print(msg)
        time.sleep(60)

def run_simulation_strategy():
    print("ð Simulation strategy started")
    send_telegram("ð¤ ÐÐ¾Ñ ÑÐ¸Ð¼ÑÐ»ÑÑÐ¸Ð¸ Ð·Ð°Ð¿ÑÑÐµÐ½")

    tracked_sim = {}
    file_path = "friend_trades.csv"

    try:
        with open(file_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            trades = list(reader)
    except Exception as e:
        send_telegram(f"[Sim] ÐÑÐ¸Ð±ÐºÐ° Ð·Ð°Ð³ÑÑÐ·ÐºÐ¸ {file_path}: {e}")
        return

    while True:
        now = get_local_time()
        for trade in trades:
            pair_str = trade['pair']
            parts = pair_str.split("->")
            if len(parts) < 2:
                continue
            base_token = parts[0].strip()
            buy_token_symbol = parts[-1].strip()

            sell_token = TOKENS.get(base_token)
            buy_token = TOKENS.get(buy_token_symbol)
            if not sell_token or not buy_token:
                continue

            key = (base_token, buy_token_symbol)
            last_time = tracked_sim.get(key, 0)
            if time.time() - last_time < 600:
                continue

            sell_amount_usd = random.randint(50, 500)
            decimals = DECIMALS.get(base_token, 18)
            sell_amount = int(sell_amount_usd * (10 ** decimals))

            quote = query_0x_quote(sell_token, buy_token, sell_amount)
            if quote is None:
                continue

            buy_amount = int(quote.get("buyAmount", "0"))
            if buy_amount == 0:
                continue

            profit = (buy_amount / sell_amount - 1) * 100

            protocols = quote.get("protocols", [])
            platforms_found = extract_platforms(protocols)
            platforms_used = [p for p in platforms_found if p in PLATFORMS.values()]
            if not platforms_used:
                continue

            tracked_sim[key] = time.time()

            url = f"https://app.1inch.io/#/polygon/swap/{sell_token}/{buy_token}"

            msg = (f"ð [SIM] Ð¡Ð´ÐµÐ»ÐºÐ° Ð´ÑÑÐ³Ð°:
{base_token} â {buy_token_symbol}
"
                   f"ÐÑÐ¾ÑÐ¸Ñ: {profit:.2f}%
"
                   f"ÐÐ»Ð°ÑÑÐ¾ÑÐ¼Ñ: {', '.join(platforms_used)}
"
                   f"Ð¡ÑÐ¼Ð¼Ð°: ${sell_amount_usd}
"
                   f"Ð¡ÑÑÐ»ÐºÐ°: {url}
"
                   f"ÐÑÐµÐ¼Ñ: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

            send_telegram(msg)
            if DEBUG_MODE:
                print(msg)
            time.sleep(5)

def main():
    import threading
    t1 = threading.Thread(target=run_real_strategy)
    t2 = threading.Thread(target=run_simulation_strategy)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

if __name__ == "__main__":
    main()
