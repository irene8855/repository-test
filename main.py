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
    "USDT": "0xc2132D05D31C914a87C6611C10748AaCbA6cD43E",
    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "DAI":  "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "FRAX": "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
}

PLATFORMS = {
    "1inch": "1inch",
    "SushiSwap": "SushiSwap",
    "Uniswap": "UniswapV3",
}

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND
API_URL = "https://polygon.api.0x.org/swap/v1/quote"
MIN_AMOUNT_USD = 100
DECIMALS = 6
BAN_DURATION_SECONDS = 3600

ban_list = {}

def send_telegram(msg: str):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] Ошибка: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Исключение: {e}")

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int):
    params = {"sellToken": sell_token, "buyToken": buy_token, "sellAmount": str(sell_amount)}
    try:
        resp = requests.get(API_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                print(f"[0x API] Ошибка {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[0x API] Исключение: {e}")
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
            send_telegram(f"🟢 Пара {pair[0]}->{pair[1]} снята с бан-листа")

def run_real_strategy():
    print("🚀 Real strategy started")
    send_telegram("🤖 Бот реальной торговли запущен")

    base_tokens = ["USDT", "USDC"]
    tracked = {}
    sell_amount_min = MIN_AMOUNT_USD * (10 ** DECIMALS)
    min_profit_percent = 0.5
    last_request_time = 0

    while True:
        now = get_local_time()
        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS[base_token]
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

                msg = (f"📈 [REAL] Сделка:\n{base_token} → {token_symbol}\n"
                       f"Профит: {profit:.2f}%\n"
                       f"Платформы: {', '.join(platforms_used)}\n"
                       f"Ссылка: {url}\n"
                       f"Время: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

                send_telegram(msg)
                if DEBUG_MODE:
                    print(msg)
        time.sleep(60)

def run_simulation_strategy():
    print("🚀 Simulation strategy started")
    send_telegram("🤖 Бот симуляции запущен")

    tracked_sim = {}
    file_path = "friend_trades.csv"

    try:
        with open(file_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            trades = list(reader)
    except Exception as e:
        send_telegram(f"[Sim] Ошибка загрузки {file_path}: {e}")
        return

    while True:
        now = get_local_time()
        for trade in trades:
            pair_str = trade['pair']
            parts = pair_str.split("->")
            if len(parts) < 2:
                continue
            base_token = parts[0]
            buy_token_symbol = parts[-1]

            sell_token = TOKENS.get(base_token)
            buy_token = TOKENS.get(buy_token_symbol)
            if not sell_token or not buy_token:
                continue

            key = (base_token, buy_token_symbol)
            last_time = tracked_sim.get(key, 0)
            if time.time() - last_time < 600:
                continue

            sell_amount_usd = random.randint(50, 500)
            sell_amount = sell_amount_usd * (10 ** DECIMALS)

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

            msg = (f"📊 [SIM] Сделка друга:\n{base_token} → {buy_token_symbol}\n"
                   f"Профит: {profit:.2f}%\n"
                   f"Платформы: {', '.join(platforms_used)}\n"
                   f"Сумма: ${sell_amount_usd}\n"
                   f"Ссылка: {url}\n"
                   f"Время: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

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
    
