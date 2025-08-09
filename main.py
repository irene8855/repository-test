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
    "WETH": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "SUSHI": "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a"
}

DECIMALS = {
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18, "wstETH": 18,
    "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18,
    "WETH": 18, "SUSHI": 18
}

RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}
PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

API_0X_URL = "https://polygon.api.0x.org/swap/v1/quote"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND
BAN_DURATION_SECONDS = 900  # 15 минут

# runtime state
ban_list = {}
tracked_trades = {}
last_report_time = 0  # время последнего отчёта в Telegram

# --- Функции ---

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram] Token or chat id not configured.")
        return
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
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    try:
        params = {"sellToken": sell_token, "buyToken": buy_token, "sellAmount": str(sell_amount)}
        resp = requests.get(API_0X_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            now = time.time()
            ban_list[key] = now
            if DEBUG_MODE:
                print(f"[0x API] 404 for {symbol_pair}; banned {BAN_DURATION_SECONDS}s")
            return None
        else:
            if DEBUG_MODE:
                print(f"[0x API] Error {resp.status_code} for {symbol_pair}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[0x API] Exception for {symbol_pair}: {e}")
        return None

def extract_platforms(protocols):
    found = set()
    for segment in protocols:
        for route in segment:
            try:
                dex = route[0].lower()
            except Exception:
                continue
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
            print(f"[BAN] {pair} removed (expired)")

def fetch_dexscreener_data(token_addr):
    try:
        resp = requests.get(f"{DEXSCREENER_API}{token_addr}", timeout=8)
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                print(f"[Dexscreener] Error {resp.status_code} for {token_addr}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Exception for {token_addr}: {e}")
        return None

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# --- Основная логика ---
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (real strategy).")
    base_tokens = ["USDT"]
    min_profit_percent = 1.0
    sell_amount_usd = 50
    last_request_time = 0

    while True:
        cycle_start_time = time.time()

        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "profit_gt_min_skipped": [],
            "total_checked_pairs": 0,
            "successful_trades": 0,
        }

        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            decimals = DECIMALS.get(base_token, 18)
            sell_amount = int(sell_amount_usd * (10 ** decimals))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token:
                    continue
                profiler["total_checked_pairs"] += 1
                key = (base_token, token_symbol)

                if key in ban_list:
                    profiler["ban_skips"] += 1
                    continue
                if time.time() - tracked_trades.get(key, 0) < BAN_DURATION_SECONDS:
                    profiler["cooldown_skips"] += 1
                    continue

                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                rsi = None
                if token_symbol in RSI_TOKENS:
                    ds_data = fetch_dexscreener_data(token_addr)
                    if not ds_data:
                        continue
                    pairs = ds_data.get("pairs", [])
                    if not pairs:
                        continue
                    candles = pairs[0].get("candles", [])
                    prices = [float(c["close"]) for c in candles if "close" in c]
                    rsi = calculate_rsi(prices)

                    if rsi is not None and rsi > 70:
                        profiler["profit_gt_min_skipped"].append(
                            (token_symbol, f"RSI={rsi:.2f}")
                        )
                        continue

                quote_entry = query_0x_quote(base_addr, token_addr, sell_amount, f"{base_token}->{token_symbol}")
                if not quote_entry or "buyAmount" not in quote_entry:
                    continue

                try:
                    buy_amount_token = int(quote_entry["buyAmount"])
                except Exception:
                    continue
                if buy_amount_token == 0:
                    continue

                profit_estimate = ((buy_amount_token / sell_amount) - 1) * 100
                if profit_estimate < min_profit_percent:
                    continue

                platforms_used = extract_platforms(quote_entry.get("protocols", []))
                if not platforms_used:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    continue

                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                time_start = get_local_time().strftime("%H:%M")
                time_sell = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                url = f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                msg_entry = (
                    f"{base_token} -> {token_symbol} -> {base_token} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT: {profit_estimate:.2f}% 💸\n"
                    f"RSI: {rsi:.2f if rsi is not None else 'N/A'}\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"{url}"
                )
                send_telegram(msg_entry)
                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                time.sleep(timing_sec)

                quote_exit = query_0x_quote(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                        actual_profit = (final_amount_exit / sell_amount - 1) * 100
                        send_telegram(
                            f"✅ TRADE COMPLETED\n"
                            f"Actual PROFIT: {actual_profit:.2f}%\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                    except Exception:
                        pass
                ban_list[key] = time.time()

        # Отчёт в Telegram не чаще чем каждые 15 минут
        now_ts = time.time()
        if now_ts - last_report_time > BAN_DURATION_SECONDS:
            banned_pairs = []
            for pair, ts in ban_list.items():
                seconds_left = int(BAN_DURATION_SECONDS - (now_ts - ts))
                banned_pairs.append(f"{pair} (left {seconds_left}s)")

            report_msg = (
                f"===== PROFILER REPORT =====\n"
                f"⏱ Cycle time: {time.time() - cycle_start_time:.2f} sec\n"
                f"🚫 Ban skips: {profiler['ban_skips']}\n"
                f"⏳ Cooldown skips: {profiler['cooldown_skips']}\n"
                f"✅ Successful trades: {profiler['successful_trades']}\n"
                f"🔍 Total checked pairs: {profiler['total_checked_pairs']}\n"
                f"🚫 Banned pairs: {len(ban_list)}\n"
                + ("\n".join(banned_pairs) if banned_pairs else "None") + "\n"
                f"⏸ Skipped due to RSI or other: {len(profiler['profit_gt_min_skipped'])}\n"
            )
            send_telegram(report_msg)
            last_report_time = now_ts

        time.sleep(1)  # пауза между циклами

if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
        
