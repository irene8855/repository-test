# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests

from dotenv import load_dotenv

load_dotenv()

# --- Настройки ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

LONDON_TZ = pytz.timezone("Europe/London")

# Токены и адреса
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

API_0X_URL = "https://polygon.api.0x.org/swap/v1/quote"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND
BAN_DURATION_SECONDS = 900  # 15 минут

ban_list = {}
tracked_trades = {}

# --- Волатильные токены для RSI ---
VOLATILE_TOKENS = ["AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "WETH"]

# --- Функции ---

def send_telegram(msg: str):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] Error: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception: {e}")

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int):
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount)
    }
    try:
        resp = requests.get(API_0X_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                send_telegram(f"[0x API] Error {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            send_telegram(f"[0x API] Exception: {e}")
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
            send_telegram(f"🛡️ Pair {pair[0]}->{pair[1]} removed from ban list")

def fetch_dexscreener_data(token_addr):
    try:
        resp = requests.get(f"{DEXSCREENER_API}{token_addr}")
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                send_telegram(f"[Dexscreener] Error {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            send_telegram(f"[Dexscreener] Exception: {e}")
        return None

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100
    rs = average_gain / average_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# --- Главная стратегия ---

def run_real_strategy():
    send_telegram("🤖 Trading bot started with real strategy.")
    print("🤖 Real strategy started")

    base_tokens = ["USDT", "USDC"]
    min_profit_percent = 1.0
    last_request_time = 0

    while True:
        now = get_local_time()
        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            if not base_addr:
                continue

            decimals = DECIMALS.get(base_token, 18)
            trade_amount_usd = 100 + int((time.time() * 1000) % 401)  # 100-500 USDT approx
            sell_amount = int(trade_amount_usd * (10 ** decimals))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token or (base_token, token_symbol) in ban_list:
                    continue

                key = (base_token, token_symbol)
                last_trade_time = tracked_trades.get(key, 0)
                if time.time() - last_trade_time < BAN_DURATION_SECONDS:
                    continue

                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                ds_data = fetch_dexscreener_data(token_addr)
                if not ds_data:
                    # Dexscreener не ответил
                    if token_symbol in VOLATILE_TOKENS:
                        # Пропускаем волатильные без данных
                        ban_list[(base_token, token_symbol)] = time.time()
                        continue
                    else:
                        # Для стабильных токенов разрешаем торговать дальше
                        prices = []
                else:
                    try:
                        candles = ds_data.get("pairs", [])[0].get("candles", [])
                        prices = [float(c["close"]) for c in candles if "close" in c]
                    except Exception:
                        prices = []

                if token_symbol in VOLATILE_TOKENS:
                    if not prices:
                        # Нет свечей — пропускаем
                        continue
                    rsi = calculate_rsi(prices)
                    if rsi is None or rsi >= 30:
                        continue
                else:
                    # Для стабильных токенов RSI не считается
                    rsi = None

                quote_entry = query_0x_quote(base_addr, token_addr, sell_amount)
                if not quote_entry or "buyAmount" not in quote_entry:
                    ban_list[(base_token, token_symbol)] = time.time()
                    continue

                buy_amount_token = int(quote_entry["buyAmount"])
                if buy_amount_token == 0:
                    continue

                profit_estimate = ((buy_amount_token / sell_amount) - 1) * 100
                if profit_estimate < min_profit_percent:
                    continue

                protocols = quote_entry.get("protocols", [])
                platforms_used = extract_platforms(protocols)
                if not platforms_used:
                    continue

                timing_min = 3 + int((30 - (rsi if rsi is not None else 0)) / 10)  # 3-8 минут
                timing_sec = timing_min * 60

                time_start = now.strftime("%H:%M")
                time_sell = (now + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")

                url = f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                msg_entry = (
                    f"{base_token} -> {token_symbol} -> {base_token} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT: {profit_estimate:.2f}% 💸\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"{url}"
                )
                send_telegram(msg_entry)
                print(f"[REAL] Trade predicted: {msg_entry}")

                tracked_trades[key] = time.time()

                # Ждём время сделки
                time.sleep(timing_sec)

                quote_exit = query_0x_quote(token_addr, base_addr, buy_amount_token)
                if quote_exit and "buyAmount" in quote_exit:
                    final_amount_exit = int(quote_exit["buyAmount"])
                    actual_profit = (final_amount_exit / sell_amount - 1) * 100

                    msg_exit = (
                        f"✅ TRADE COMPLETED\n"
                        f"Actual PROFIT: {actual_profit:.2f}%\n"
                        f"Time: {get_local_time().strftime('%H:%M')}\n"
                        f"Token: {token_symbol}\n"
                        f"https://dexscreener.com/polygon"
                    )
                    send_telegram(msg_exit)
                    print(f"[REAL] Trade completed: {msg_exit}")

                ban_list[(base_token, token_symbol)] = time.time()

if __name__ == "__main__":
    try:
        run_real_strategy()
    except Exception as e:
        err_msg = f"❗ Bot crashed with exception: {e}"
        send_telegram(err_msg)
        if DEBUG_MODE:
            print(err_msg)
            
