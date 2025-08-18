# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
import threading
from dotenv import load_dotenv

load_dotenv()

# ===================== ENV & SETTINGS =====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

LONDON_TZ = pytz.timezone("Europe/London")

SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))

MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))
STOP_LOSS_PERCENT  = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))
REPORT_INTERVAL = int(float(os.getenv("REPORT_INTERVAL", "900")))  # 15 мин

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

TRY_REVERSE_ON_NO_ROUTE = True

# ===================== TOKENS (Polygon) =====================
TOKENS = {
    "USDT":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "USDC":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "DAI":    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX":   "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "wstETH": "0x03b54a6e9a984069379fae1a4fc4dbae93b3bccd",
    "BET":    "0xbf7970d56a150cd0b60bd08388a4a75a27777777",
    "WPOL":   "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
    "tBTC":   "0x236aa50979d5f3de3bd1eeb40e81137f22ab794b",
    "SAND":   "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":    "0x714db550b574b3e927af3d93e26127d15721d4c2",
    "LINK":   "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "EMT":    "0x708383ae0e80e75377d664e4d6344404dede119a",
    "AAVE":   "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LDO":    "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "POL":    "0x0000000000000000000000000000000000001010",
    "WETH":   "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "SUSHI":  "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
}

DECIMALS = {
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18, "wstETH": 18,
    "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18,
    "WETH": 18, "SUSHI": 18,
}

ADDRESS_TO_SYMBOL = {addr.lower(): sym for sym, addr in TOKENS.items()}
RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}

# ===================== ENDPOINTS =====================
CHAIN_ID = 137
ONEINCH_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

# ===================== BAN & STATE =====================
ban_list = {}
tracked_trades = {}
last_report_time = time.time()
_last_cycle_report = time.time()

last_request_time_lock = threading.Lock()
_last_request_time = 0.0

# ===================== UTILS =====================
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram muted]\n", msg)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=(5, 10)
        )
        if r.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] HTTP {r.status_code}: {r.text[:400]}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception: {e}")

def get_local_time():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def pace_requests():
    global _last_request_time
    with last_request_time_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()

def ban_pair(key, reason, duration=900):
    ban_list[key] = {"time": time.time(), "reason": reason, "duration": duration}
    if DEBUG_MODE:
        print(f"[BAN] {key} -> {reason} ({duration}s)")

def clean_ban_list():
    now_ts = time.time()
    for pair in list(ban_list.keys()):
        info = ban_list[pair]
        if now_ts - info["time"] > info["duration"]:
            ban_list.pop(pair, None)

# ===================== Dexscreener =====================
def fetch_dexscreener_pairs(token_addr):
    try:
        pace_requests()
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=(5, 10))
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Exception: {e}")
    return None

def get_token_usd_price_from_dxs(token_addr):
    data = fetch_dexscreener_pairs(token_addr)
    if not data: return None
    for p in data.get("pairs", []):
        if p.get("priceUsd"):
            try: return float(p["priceUsd"])
            except: continue
    return None

def get_token_candles(token_addr):
    data = fetch_dexscreener_pairs(token_addr)
    if not data: return None
    pairs = data.get("pairs", [])
    if not pairs: return None
    return pairs[0].get("candles", [])

def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1: return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > 0: gains.append(delta)
        else: losses.append(-delta)
    if not gains and not losses: return None
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def safe_format_rsi(rsi):
    if rsi is None: return "—"
    try: return f"{float(rsi):.2f}"
    except: return "—"

# ===================== 1inch =====================
def query_1inch_price(from_symbol, to_symbol, amount_units):
    key = f"{from_symbol}->{to_symbol}"
    try:
        pace_requests()
        url = ONEINCH_PUBLIC
        params = {
            "fromTokenAddress": TOKENS[from_symbol],
            "toTokenAddress": TOKENS[to_symbol],
            "amount": str(int(amount_units))
        }
        resp = requests.get(url, params=params, timeout=(5, 10))
        if resp.status_code != 200:
            ban_pair(key, f"1inch HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        try:
            data = resp.json()
        except Exception:
            ban_pair(key, f"1inch invalid JSON: {resp.text[:200]}")
            return None
        if "toTokenAmount" not in data:
            ban_pair(key, f"1inch no route: {resp.text[:200]}")
            return None
        return int(data["toTokenAmount"])
    except Exception as e:
        ban_pair(key, f"1inch exception: {e}")
        return None

# ===================== MAIN STRATEGY =====================
def run_real_strategy():
    global last_report_time, _last_cycle_report
    while True:
        cycle_start = time.time()
        clean_ban_list()
        profiler = {
            "total_checked_pairs": 0,
            "skipped_reasons": {},
            "profitable": 0,
            "success_signals": 0,
        }

        for base_symbol in ["USDT"]:
            for target_symbol in TOKENS:
                if base_symbol == target_symbol: continue
                key = f"{base_symbol}->{target_symbol}"
                if key in ban_list:
                    profiler["skipped_reasons"].setdefault("Banned", []).append(
                        f"{key} ({ban_list[key]['reason']})"
                    )
                    continue
                amount_units = int(SELL_AMOUNT_USD * (10 ** DECIMALS[base_symbol]))
                out_units = query_1inch_price(base_symbol, target_symbol, amount_units)
                if not out_units:
                    profiler["skipped_reasons"].setdefault("No quote", []).append(key)
                    continue
                back_units = query_1inch_price(target_symbol, base_symbol, out_units)
                if not back_units:
                    profiler["skipped_reasons"].setdefault("No quote", []).append(key)
                    continue
                profit_pct = (back_units / amount_units - 1) * 100
                if profit_pct > MIN_PROFIT_PERCENT:
                    send_telegram(
                        f"💰 Сделка найдена: {base_symbol}->{target_symbol}->{base_symbol}\n"
                        f"Прибыль: {profit_pct:.2f}%"
                    )
                    profiler["success_signals"] += 1

                profiler["total_checked_pairs"] += 1

        # ===== REPORT =====
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
            report = []
            report.append("===== PROFILER REPORT =====")
            report.append(f"⏱ Время полного цикла: {time.time() - cycle_start:.2f} сек")
            report.append(f"🚫 Пар в бан-листе: {len(ban_list)}")
            for reason, pairs in profiler["skipped_reasons"].items():
                report.append(f"🧹 {reason}: {', '.join(pairs)}")
            report.append(f"✔️ Успешных сигналов за цикл: {profiler['success_signals']}")
            report.append(f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}")
            report.append("===========================")
            send_telegram("\n".join(report))
            last_report_time = now_ts

        # safety ping
        if now_ts - _last_cycle_report > 2 * REPORT_INTERVAL:
            send_telegram("⚠️ No reports generated, possibly stuck")
        _last_cycle_report = now_ts

        time.sleep(5)

# ===================== START =====================
if __name__ == "__main__":
    send_telegram("🚀 Bot started")
    run_real_strategy()
    
