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

# Токены с реальными адресами в Polygon
TOKENS = {
    "USDT": "0xc2132D05D31C914a87C6611C10748AaCbA6cD43E",
    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "DAI":  "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "FRAX": "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
    "wstETH": "0x3a58f48e0b7f8b5bbe44c96fb95e9c1f9e246e13",
    "BET": "0xBED2c2e1138a2d8db36137f7f0b3de09a6215bd6",
    "WPOL": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "tBTC": "0x2f2a2543b76a4166549f7aaB2e75Bef0aefC5B0f",
    "SAND": "0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683",
    "GMT": "0x7Dd9c5Cba05E151C895FDe1CF355C9A1D5DA6429",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "EMT": "0x95A4492F028AA1fd432Ea71146b433E7B4446611",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LDO": "0x2862a5c0322db0d4ae3f5c5bb4b6f92f5d9b9f01",
}

# Десятичные знаки для каждого токена
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
    "GMT": 18,
    "LINK": 18,
    "EMT": 18,
    "AAVE": 18,
    "LDO": 18,
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
            print(f"[Telegram] Ошибка: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Исключение: {e}")

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
    
