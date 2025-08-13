# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------- Settings (ENV-driven) ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ZEROX_API_KEY = os.getenv("ZEROX_API_KEY")  # ваш ключ 0x (опционально)
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# Feature flags and parameters (can be changed via env)
REAL_TRADING = os.getenv("REAL_TRADING", "False").lower() == "true"  # если True — реальные ордера (требует вашей реализации)
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "900"))  # сек (по умолчанию 15 мин)
SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))  # сколько USD продаём в базовой валюте
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))  # минимальная профитность %
SLIPPAGE_PERCENT = float(os.getenv("SLIPPAGE_PERCENT", "0.01"))  # 0.01 = 1%
ZEROX_SKIP_VALIDATION = os.getenv("ZEROX_SKIP_VALIDATION", "False").lower() == "true"
TRY_REVERSE_ON_NO_ROUTE = os.getenv("TRY_REVERSE_ON_NO_ROUTE", "True").lower() == "true"

# timezone
LONDON_TZ = pytz.timezone("Europe/London")

# ---------------- Tokens & decimals ----------------
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

# ---------------- APIs ----------------
API_0X_URL = "https://api.0x.org/swap/permit2/price"
CHAIN_ID = 137
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

# ---------------- Limits & ban durations ----------------
MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = 120  # 2 минуты
BAN_OTHER_REASON_DURATION = 900  # 15 минут

# ---------------- Runtime state ----------------
ban_list = {}       # key: (base_symbol, token_symbol) -> {"time": ts, "reason": str, "duration": int}
tracked_trades = {} # key -> last trade timestamp (post-trade cooldown)
last_report_time = 0

# ---------------- Utilities ----------------
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram] token/chat not set. Message would be:\n", msg)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] Error {resp.status_code}: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception while sending telegram: {e}")

def get_local_time():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def ban_pair(key, reason, duration=None):
    now_ts = time.time()
    if duration is None:
        if BAN_NO_LIQUIDITY_REASON.lower() in reason.lower() or "404" in reason:
            duration = BAN_NO_LIQUIDITY_DURATION
        else:
            duration = BAN_OTHER_REASON_DURATION
    ban_list[key] = {"time": now_ts, "reason": reason, "duration": duration}
    if DEBUG_MODE:
        print(f"[BAN] {key} -> {reason} (dur={duration}s)")

def clean_ban_list():
    now_ts = time.time()
    to_remove = [pair for pair, info in ban_list.items() if now_ts - info["time"] > info["duration"]]
    for pair in to_remove:
        if DEBUG_MODE:
            print(f"[BAN] Removing expired ban for {pair} (reason={ban_list[pair]['reason']})")
        ban_list.pop(pair, None)

# ---------------- Dexscreener helpers ----------------
def fetch_dexscreener_pairs(token_addr):
    """Вернёт JSON pairs (или None)."""
    try:
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=8)
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Request error for {token_addr}: {e}")
        return None
    if resp.status_code != 200:
        if DEBUG_MODE:
            print(f"[Dexscreener] HTTP {resp.status_code} for {token_addr}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except Exception:
        if DEBUG_MODE:
            print(f"[Dexscreener] JSON parse error for {token_addr}")
        return None

def get_token_usd_price_from_dxs(token_addr):
    """Попробовать извлечь цену USD для токена из Dexscreener (берём первую пару с priceUsd)."""
    data = fetch_dexscreener_pairs(token_addr)
    if not data:
        return None
    pairs = data.get("pairs", [])
    for p in pairs:
        price_usd = p.get("priceUsd")
        if price_usd:
            try:
                return float(price_usd)
            except Exception:
                continue
    return None

def get_token_candles(token_addr):
    """Возвращает candles для первого pair, если есть."""
    data = fetch_dexscreener_pairs(token_addr)
    if not data:
        return None
    pairs = data.get("pairs", [])
    if not pairs:
        return None
    return pairs[0].get("candles", [])

# ---------------- RSI ----------------
def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
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

# ---------------- 0x price query ----------------
def query_0x_price(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    """
    Запрос к 0x v2 permit2/price.
    Возвращает dict (json) при успехе, None при ошибке (и банит пару).
    Включает заголовок 0x-api-key если установлен.
    При отсутствии маршрута — бан short (no liquidity).
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
        "chainId": CHAIN_ID
    }

    # optional slippage/protection params (applied when ZEROX_SKIP_VALIDATION == False)
    if ZEROX_SKIP_VALIDATION:
        # if skipping validation, do not provide slippage protection params
        pass
    else:
        params["slippagePercentage"] = str(SLIPPAGE_PERCENT)
        params["enableSlippageProtection"] = "true"

    headers = {"0x-version": "v2"}
    if ZEROX_API_KEY:
        headers["0x-api-key"] = ZEROX_API_KEY
    else:
        if DEBUG_MODE:
            print("[0x] ZEROX_API_KEY not set; requests may be rate-limited by 0x.")

    try:
        resp = requests.get(API_0X_URL, params=params, headers=headers, timeout=12)
    except requests.exceptions.RequestException as e:
        ban_pair(key, f"Request exception: {e}", duration=BAN_OTHER_REASON_DURATION)
        if DEBUG_MODE:
            print(f"[0x] RequestException for {symbol_pair}: {e}")
        return None

    # analyze response
    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            ban_pair(key, "Invalid JSON from 0x", duration=BAN_OTHER_REASON_DURATION)
            if DEBUG_MODE:
                print(f"[0x] Invalid JSON for {symbol_pair}: {resp.text[:300]}")
            return None

        # check liquidity flags / route emptiness
        if "liquidityAvailable" in data and data.get("liquidityAvailable") is False:
            ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[0x] liquidityAvailable=false for {symbol_pair}")
            return None

        # some responses may include route or fills
        if "route" in data:
            route = data.get("route")
            # route maybe dict with fills
            if not route:
                ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                if DEBUG_MODE:
                    print(f"[0x] Empty route for {symbol_pair}")
                return None
            # else accept

        # OK
        return data

    elif resp.status_code == 404:
        ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
        if DEBUG_MODE:
            print(f"[0x] 404 for {symbol_pair}")
        return None
    else:
        # other HTTP errors, try to include text snippet
        snippet = ""
        try:
            snippet = resp.text[:300].replace("\n", " ")
        except Exception:
            pass
        reason = f"HTTP {resp.status_code} - {snippet}"
        ban_pair(key, reason, duration=BAN_OTHER_REASON_DURATION)
        if DEBUG_MODE:
            print(f"[0x] Error {resp.status_code} for {symbol_pair}: {snippet}")
        return None

# ---------------- Helpers: profit calc & guards ----------------
def safe_format_rsi(rsi):
    return f"{rsi:.2f}" if (rsi is not None) else "N/A"

def compute_profit_percent_by_units(sell_amount_units, final_amount_units):
    try:
        # both are raw integer units in same base (sell denom)
        return ((final_amount_units / sell_amount_units) - 1) * 100
    except Exception:
        return None

def compute_profit_usd(sell_amount_units, final_amount_units, base_symbol, token_symbol):
    """
    Попытка вычислить прибыль в USD:
    - конвертируем sell_amount_units (в base_symbol) в USD (для USDT ~1)
    - final_amount_units — в token_symbol -> USD (через Dexscreener price)
    Возвращает (usd_sell, usd_final, profit_percent_usd) или (None, None, None)
    """
    try:
        # amount in "base" (e.g. USDT) to USD
        base_dec = DECIMALS.get(base_symbol, 18)
        usd_sell = (sell_amount_units / (10 ** base_dec))  # if base is USDT this is USD
    except Exception:
        usd_sell = None

    # final amount is in token_symbol units; convert to token USD price via Dexscreener
    token_price_usd = get_token_usd_price_from_dxs(TOKENS.get(token_symbol))
    if token_price_usd is None:
        return (usd_sell, None, None)
    try:
        token_dec = DECIMALS.get(token_symbol, 18)
        final_tokens = final_amount_units / (10 ** token_dec)
        usd_final = final_tokens * token_price_usd
        if usd_sell is None:
            return (None, usd_final, None)
        profit_usd_pct = (usd_final / usd_sell - 1) * 100
        return (usd_sell, usd_final, profit_usd_pct)
    except Exception:
        return (usd_sell, None, None)

# ---------------- Main strategy ----------------
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (real strategy).")
    base_tokens = ["USDT"]
    last_request_time = 0

    REPORT_INTERVAL = REPORT_INTERVAL if isinstance(REPORT_INTERVAL, int) else int(REPORT_INTERVAL)

    while True:
        cycle_start_time = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "profit_gt_min_skipped": [],  # (sym, reason)
            "dexscreener_skipped": [],    # (sym, reason)
            "total_checked_pairs": 0,
            "successful_trades": 0,
        }

        clean_ban_list()

        for base_symbol in base_tokens:
            base_addr = TOKENS.get(base_symbol)
            base_dec = DECIMALS.get(base_symbol, 18)
            sell_amount_units = int(SELL_AMOUNT_USD * (10 ** base_dec))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_symbol:
                    continue
                profiler["total_checked_pairs"] += 1
                key = (base_symbol, token_symbol)

                # if banned — skip
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    continue

                # cooldown after trade (post-trade cooldown)
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
                    profiler["cooldown_skips"] += 1
                    continue

                # rate limit pacing
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # RSI: get candles from Dexscreener and compute RSI if enough data
                rsi = None
                if token_symbol in RSI_TOKENS:
                    candles = get_token_candles(token_addr)
                    if not candles:
                        profiler["dexscreener_skipped"].append((token_symbol, "Dexscreener candles missing"))
                    else:
                        # extract close prices
                        try:
                            closes = [float(c["close"]) for c in candles if "close" in c]
                        except Exception:
                            closes = []
                        rsi = calculate_rsi(closes)
                        if rsi is not None and rsi > 70:
                            profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f}"))
                            # do not ban, just skip
                            continue

                # primary quote (base -> token)
                quote_entry = query_0x_price(base_addr, token_addr, sell_amount_units, f"{base_symbol}->{token_symbol}")
                if not quote_entry:
                    # if 0x returned None, it already handled ban; attempt reverse check optionally (for diagnostic)
                    if TRY_REVERSE_ON_NO_ROUTE:
                        # try reverse direction just for diagnostic (do not execute trade based on reverse result here)
                        if DEBUG_MODE:
                            print(f"[INFO] Trying reverse check for {token_symbol}->{base_symbol}")
                        # respect rate limiting
                        elapsed = time.time() - last_request_time
                        if elapsed < REQUEST_INTERVAL:
                            time.sleep(REQUEST_INTERVAL - elapsed)
                        last_request_time = time.time()
                        reverse = query_0x_price(token_addr, base_addr, sell_amount_units, f"{token_symbol}->{base_symbol}")
                        # we don't use reverse result to trade in this step; it's diagnostic
                        if reverse:
                            if DEBUG_MODE:
                                print(f"[INFO] Reverse direction available for {token_symbol}->{base_symbol}")
                    continue

                # parse buyAmount (amount of token we would receive)
                try:
                    buy_amount_token = int(quote_entry.get("buyAmount", 0))
                except Exception:
                    ban_pair(key, "Invalid buyAmount in 0x response", duration=BAN_OTHER_REASON_DURATION)
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    continue

                # estimate profit in raw units (final exit amount will be checked later)
                profit_estimate = ((buy_amount_token / sell_amount_units) - 1) * 100
                # filter unrealistic huge values
                if abs(profit_estimate) > 1e6:
                    if DEBUG_MODE:
                        print(f"[WARN] Unrealistic profit_estimate {profit_estimate} for {base_symbol}->{token_symbol}; skipping")
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Unrealistic profit estimate"))
                    continue

                if profit_estimate < MIN_PROFIT_PERCENT:
                    profiler["profit_gt_min_skipped"].append((token_symbol, f"Profit {profit_estimate:.2f}% < {MIN_PROFIT_PERCENT}%"))
                    continue

                # find platforms used if possible
                platforms_used = []
                if quote_entry.get("protocols"):
                    platforms_used = extract_platforms(quote_entry.get("protocols"))
                # fallback to route/fills
                if not platforms_used and "route" in quote_entry:
                    try:
                        fills = quote_entry["route"].get("fills", []) if isinstance(quote_entry["route"], dict) else []
                        for f in fills:
                            src = f.get("source", "") or ""
                            for pk, pn in PLATFORMS.items():
                                if pk.lower() in src.lower():
                                    if pn not in platforms_used:
                                        platforms_used.append(pn)
                    except Exception:
                        pass

                if not platforms_used:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    continue

                # compute human timing
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                # build and send preliminary trade message (always send according to request)
                time_start = get_local_time().strftime("%H:%M")
                time_sell = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                pre_msg = (
                    f"{base_symbol} -> {token_symbol} -> {base_symbol} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT ESTIMATE: {profit_estimate:.2f}% 💸\n"
                    f"RSI: {safe_format_rsi(rsi)}\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"
                )
                send_telegram(pre_msg)

                # mark attempted/tracked
                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # sleep until sell time (simulate hold)
                time.sleep(timing_sec)

                # Query exit price (token -> base), using buy_amount_token as sellAmount
                # Note: buy_amount_token is in token units, but 0x expects raw integer sellAmount (we have it)
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                quote_exit = query_0x_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                    except Exception:
                        final_amount_exit = None
                    if final_amount_exit:
                        # compute actual profit in percent (units)
                        actual_profit = compute_profit_percent_by_units(sell_amount_units, final_amount_exit)
                        # try to compute USD profit for better insight
                        usd_sell, usd_final, usd_profit_pct = compute_profit_usd(sell_amount_units, final_amount_exit, base_symbol, base_symbol)
                        # Wait: note we used base_symbol for both — better compute via token USD price:
                        # For exit, final_amount_exit is in base units -> this path above works if base==USDT.
                        # For robust USD profit, attempt alternative:
                        usd_sell_alt = SELL_AMOUNT_USD
                        usd_final_alt = None
                        # compute usd_final by converting final_amount_exit (base units) to USD via price of base token
                        # if base is USDT we already have usd; else try using Dexscreener
                        try:
                            if base_symbol == "USDT" or base_symbol == "USDC":
                                usd_final_alt = final_amount_exit / (10 ** DECIMALS.get(base_symbol, 18))
                                usd_profit_alt = (usd_final_alt / usd_sell_alt - 1) * 100
                            else:
                                base_price = get_token_usd_price_from_dxs(TOKENS.get(base_symbol))
                                if base_price:
                                    usd_final_alt = (final_amount_exit / (10 ** DECIMALS.get(base_symbol, 18))) * base_price
                                    usd_profit_alt = (usd_final_alt / usd_sell_alt - 1) * 100
                                else:
                                    usd_profit_alt = None
                        except Exception:
                            usd_final_alt = None
                            usd_profit_alt = None

                        # send completion message
                        actual_profit_str = f"{actual_profit:.2f}%" if actual_profit is not None else "N/A"
                        usd_profit_str = None
                        if usd_profit_pct is not None:
                            usd_profit_str = f"{usd_profit_pct:.2f}%"
                        elif usd_profit_alt is not None:
                            usd_profit_str = f"{usd_profit_alt:.2f}%"

                        completion_msg = (
                            f"✅ TRADE COMPLETED\n"
                            f"Actual PROFIT (units): {actual_profit_str}\n"
                            + (f"Actual PROFIT (USD): {usd_profit_str}\n" if usd_profit_str else "")
                            + f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                        send_telegram(completion_msg)
                    else:
                        ban_pair(key, "Exit quote returned no buyAmount", duration=BAN_OTHER_REASON_DURATION)
                else:
                    # exit quote failed (ban longer)
                    ban_pair(key, "Exit quote failed", duration=BAN_OTHER_REASON_DURATION)
                    if DEBUG_MODE:
                        print(f"[Trade] Exit quote failed for {token_symbol}: {quote_exit}")

                # after real trade apply post-trade cooldown (15 min)
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # periodic report every REPORT_INTERVAL seconds
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
            clean_ban_list()
            # banned pairs detail lines
            banned_pairs_lines = []
            for pair, info in ban_list.items():
                seconds_left = int(info["duration"] - (now_ts - info["time"]))
                if seconds_left < 0:
                    seconds_left = 0
                banned_pairs_lines.append(f"  - {pair[0]} -> {pair[1]}: причина - {info['reason']}, осталось: {seconds_left}s")

            report_msg = (
                f"===== PROFILER REPORT =====\n"
                f"⏱ Время полного цикла: {time.time() - cycle_start_time:.2f} сек\n"
                f"🚫 Пар в бан-листе: {len(ban_list)}\n"
            )
            if banned_pairs_lines:
                report_msg += "Бан-лист детали:\n" + "\n".join(banned_pairs_lines) + "\n"
            report_msg += (
                f"💤 Пропущено по cooldown: {profiler['cooldown_skips']}\n"
                f"💰 Пар с прибылью > {MIN_PROFIT_PERCENT}% (но не отправлены): {len(profiler['profit_gt_min_skipped'])}\n"
            )
            if profiler["profit_gt_min_skipped"]:
                for sym, reason in profiler["profit_gt_min_skipped"]:
                    report_msg += f"   - {sym}: {reason}\n"
            # dexscreener skipped diagnostics
            if profiler["dexscreener_skipped"]:
                report_msg += "🔎 Пропущенные (dexscreener/price issues):\n"
                for sym, reason in profiler["dexscreener_skipped"]:
                    report_msg += f"   - {sym}: {reason}\n"
            report_msg += f"✔️ Успешных торгов за цикл: {profiler['successful_trades']}\n"
            report_msg += f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}\n"
            report_msg += "===========================\n"

            send_telegram(report_msg)
            last_report_time = now_ts

        # small sleep to avoid tight-loop; main pacing is by REQUEST_INTERVAL and REPORT_INTERVAL
        time.sleep(0.5)

# ---------------- Entrypoint ----------------
if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            print(f"[CRASH] {e}")
            
