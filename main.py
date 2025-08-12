# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

# -------- CONFIG from ENV --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ZEROX_API_KEY = os.getenv("ZEROX_API_KEY")  # recommended
ZEROX_SKIP_VALIDATION = os.getenv("ZEROX_SKIP_VALIDATION", "False").lower() == "true"
ZEROX_SLIPPAGE = float(os.getenv("ZEROX_SLIPPAGE", "0.01"))  # 0.01 == 1%

SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))  # USD
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))  # %
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "900"))  # seconds (default 15 min)

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"  # if False -> dry, but messages still sent
RUN_MODE = os.getenv("RUN_MODE", "real").lower()
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# timezone
LONDON_TZ = pytz.timezone("Europe/London")

# -------- TOKENS & DECIMALS (Polygon addresses as provided) --------
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

# 0x v2 permit2 price endpoint
API_0X_URL = "https://api.0x.org/swap/permit2/price"
CHAIN_ID = 137

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"

# rate limits and ban durations
MAX_REQUESTS_PER_SECOND = int(os.getenv("MAX_REQUESTS_PER_SECOND", "5"))
REQUEST_INTERVAL = 1.0 / MAX_REQUESTS_PER_SECOND
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = int(os.getenv("BAN_NO_LIQUIDITY_DURATION", "120"))  # 2 min default
BAN_OTHER_REASON_DURATION = int(os.getenv("BAN_OTHER_REASON_DURATION", "900"))  # 15 min default

# runtime state
ban_list = {}          # key: (base_symbol, token_symbol) -> {"time": ts, "reason": str, "duration": int}
tracked_trades = {}    # key -> last trade timestamp
last_report_time = 0

# ----------------- Helpers -----------------
def mask_key_for_log(key: str) -> str:
    if not key:
        return "<none>"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + ("*" * (len(key) - 8)) + key[-4:]

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram] (no token/chat) message would be:\n", msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception: {e}")

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
        print(f"[BAN] {key} -> {reason} (for {duration}s)")

def clean_ban_list():
    now_ts = time.time()
    to_remove = [pair for pair, info in ban_list.items() if now_ts - info["time"] > info["duration"]]
    for pair in to_remove:
        if DEBUG_MODE:
            print(f"[BAN] Removing expired ban for {pair} (reason={ban_list[pair].get('reason')})")
        ban_list.pop(pair, None)

def extract_platforms(protocols):
    found = set()
    if not protocols:
        return []
    try:
        for segment in protocols:
            for route in segment:
                try:
                    dex = route[0].lower()
                except Exception:
                    continue
                for platform_key, platform_name in PLATFORMS.items():
                    if platform_key.lower() in dex:
                        found.add(platform_name)
    except Exception:
        pass
    return list(found)

# --------------- Dexscreener helpers ----------------
def fetch_dexscreener_data(token_addr):
    try:
        resp = requests.get(f"{DEXSCREENER_API}{token_addr}", timeout=8)
        if resp.status_code == 200:
            return resp.json()
        else:
            if DEBUG_MODE:
                print(f"[Dexscreener] Error {resp.status_code} for {token_addr}: {resp.text}")
            return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Exception for {token_addr}: {e}")
        return None

def get_token_price_usd(token_addr):
    """
    Try to obtain USD price via Dexscreener:
     - pairs[0]['priceUsd'] if available
     - fallback to last candle close
    Returns float priceUSD or None
    """
    ds = fetch_dexscreener_data(token_addr)
    if not ds:
        return None
    pairs = ds.get("pairs", [])
    if not pairs:
        return None
    p0 = pairs[0]
    # try priceUsd
    try:
        price = p0.get("priceUsd")
        if price:
            return float(price)
    except Exception:
        pass
    # fallback to last candle close
    try:
        candles = p0.get("candles", [])
        if candles:
            last = candles[-1]
            close = last.get("close")
            if close:
                return float(close)
    except Exception:
        pass
    return None

def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta); losses.append(0)
        else:
            gains.append(0); losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# ----------------- 0x price query (v2 / permit2 /price) -----------------
def query_0x_price(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
        "chainId": CHAIN_ID
    }

    # add slippage/protection if enabled
    if ZEROX_SKIP_VALIDATION:
        params["slippagePercentage"] = str(ZEROX_SLIPPAGE)
        params["enableSlippageProtection"] = "true"

    headers = {"0x-version": "v2"}
    if ZEROX_API_KEY:
        headers["0x-api-key"] = ZEROX_API_KEY

    if DEBUG_MODE:
        if "0x-api-key" in headers:
            print(f"[0x] headers: 0x-version=v2, 0x-api-key={mask_key_for_log(headers.get('0x-api-key'))}")
        else:
            print("[0x] headers: 0x-version=v2 (no key)")
        sample_params = {k: params[k] for k in list(params)[:4]}
        print(f"[0x] request params sample for {symbol_pair}: {sample_params} ...")

    try:
        resp = requests.get(API_0X_URL, params=params, headers=headers, timeout=12)
    except requests.exceptions.RequestException as e:
        ban_pair(key, f"Request exception: {e}", duration=BAN_OTHER_REASON_DURATION)
        if DEBUG_MODE:
            print(f"[0x] RequestException for {symbol_pair}: {e}")
        return None

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            ban_pair(key, "Invalid JSON from 0x", duration=BAN_OTHER_REASON_DURATION)
            if DEBUG_MODE:
                print(f"[0x] Invalid JSON for {symbol_pair}: {resp.text[:200]}")
            return None

        # v2: check liquidityAvailable
        if "liquidityAvailable" in data and (data.get("liquidityAvailable") is False):
            ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[0x] liquidityAvailable=false for {symbol_pair}")
            return None

        # fallback: check route / fills
        if "route" in data:
            route = data.get("route")
            fills = []
            try:
                if isinstance(route, dict):
                    fills = route.get("fills", []) or []
                elif isinstance(route, list):
                    fills = route
            except Exception:
                fills = []
            if not fills:
                ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                if DEBUG_MODE:
                    print(f"[0x] empty route/fills for {symbol_pair}")
                return None

        return data

    elif resp.status_code == 404:
        ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
        if DEBUG_MODE:
            print(f"[0x] 404 for {symbol_pair}")
        return None
    else:
        reason_text = f"HTTP {resp.status_code}"
        try:
            snippet = resp.text[:200].replace("\n", " ")
            reason_text += f" - {snippet}"
        except Exception:
            pass
        ban_pair(key, reason_text, duration=BAN_OTHER_REASON_DURATION)
        if DEBUG_MODE:
            print(f"[0x] Error {resp.status_code} for {symbol_pair}: {resp.text[:200]}")
        return None

# ----------------- Profit calc (USD aware) -----------------
def compute_profit_usd(quote_buy, sell_token_addr, buy_token_addr, sell_amount_raw):
    """
    Compute net profit in USD:
      - parse buyAmount
      - get token decimals by symbol mapping
      - fetch USD prices (Dexscreener) for sell & buy tokens
      - consider totalNetworkFee (wei -> native -> USD)
      - try to include fees.integratorFee / zeroExFee / gasFee if present
    Returns (net_profit_usd, sell_usd, buy_usd, fees_usd) or (None,...)
    """
    # parse buy amount
    try:
        buy_amount_raw = int(quote_buy.get("buyAmount", 0))
    except Exception:
        return (None, None, None, None)
    if buy_amount_raw == 0:
        return (None, None, None, None)

    def symbol_by_addr(addr):
        for s,a in TOKENS.items():
            if a.lower() == (addr or "").lower():
                return s
        return None

    sell_sym = symbol_by_addr(sell_token_addr)
    buy_sym = symbol_by_addr(buy_token_addr)
    dec_sell = DECIMALS.get(sell_sym, 18)
    dec_buy = DECIMALS.get(buy_sym, 18)

    sell_units = sell_amount_raw / (10 ** dec_sell)
    buy_units = buy_amount_raw / (10 ** dec_buy)

    sell_price = get_token_price_usd(sell_token_addr)
    buy_price = get_token_price_usd(buy_token_addr)
    # assume stablecoins 1 USD if missing
    if sell_price is None and sell_sym in ("USDT", "USDC"):
        sell_price = 1.0
    if buy_price is None and buy_sym in ("USDT", "USDC"):
        buy_price = 1.0

    if sell_price is None or buy_price is None:
        # cannot compute exact USD profit
        return (None, sell_units * (sell_price or 0), buy_units * (buy_price or 0), None)

    sell_usd = sell_units * sell_price
    buy_usd = buy_units * buy_price

    fees_usd = 0.0
    # totalNetworkFee often in wei -> convert to native token (e.g., WMATIC) then USD
    try:
        total_network_fee_str = quote_buy.get("totalNetworkFee")
        if total_network_fee_str:
            native_amount = int(total_network_fee_str) / (10 ** 18)
            native_price = get_token_price_usd(TOKENS.get("WPOL"))
            if native_price:
                fees_usd += native_amount * native_price
    except Exception:
        pass

    # consider fees.integratorFee / zeroExFee / gasFee if provided as token amounts
    try:
        fees_obj = quote_buy.get("fees", {}) or {}
        for fee_key in ("integratorFee", "zeroExFee", "gasFee"):
            f = fees_obj.get(fee_key)
            if f and isinstance(f, dict):
                amt = f.get("amount")
                token_addr = f.get("token")
                if amt and token_addr:
                    try:
                        amt_raw = float(amt)
                        fee_sym = symbol_by_addr(token_addr)
                        fee_dec = DECIMALS.get(fee_sym, 18)
                        amt_units = amt_raw / (10 ** fee_dec)
                        fee_price = get_token_price_usd(token_addr)
                        if fee_price:
                            fees_usd += amt_units * fee_price
                    except Exception:
                        pass
    except Exception:
        pass

    net_profit_usd = buy_usd - sell_usd - fees_usd
    return (net_profit_usd, sell_usd, buy_usd, fees_usd)

# ----------------- Main strategy -----------------
def run_real_strategy():
    global last_report_time
    send_telegram(f"🤖 Bot started. Mode: {RUN_MODE} | REAL_TRADING: {REAL_TRADING}")
    base_tokens = ["USDT"]
    sell_amount_usd = SELL_AMOUNT_USD
    last_request_time = 0
    REPORT_INTERVAL_LOCAL = REPORT_INTERVAL

    while True:
        cycle_start_time = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "profit_gt_min_skipped": [],
            "total_checked_pairs": 0,
            "successful_trades": 0,
            "filtered_skipped": [],
        }

        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            base_dec = DECIMALS.get(base_token, 18)
            sell_amount_raw = int(sell_amount_usd * (10 ** base_dec))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token:
                    continue
                profiler["total_checked_pairs"] += 1
                key = (base_token, token_symbol)

                # skip banned
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    continue

                # post-trade cooldown (global 15min for other reasons)
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
                    profiler["cooldown_skips"] += 1
                    continue

                # rate limiting
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # RSI filter (if token in RSI_TOKENS)
                rsi = None
                if token_symbol in RSI_TOKENS:
                    ds_data = fetch_dexscreener_data(token_addr)
                    if not ds_data:
                        profiler["filtered_skipped"].append((token_symbol, "Dexscreener failed"))
                        continue
                    pairs = ds_data.get("pairs", [])
                    if not pairs:
                        profiler["filtered_skipped"].append((token_symbol, "No pairs on Dexscreener"))
                        continue
                    candles = pairs[0].get("candles", [])
                    prices = [float(c["close"]) for c in candles if "close" in c]
                    rsi = calculate_rsi(prices)
                    if rsi is not None and rsi > 70:
                        profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f}"))
                        continue

                # Query 0x for quote (sell base -> buy token)
                symbol_pair = f"{base_token}->{token_symbol}"
                quote_entry = query_0x_price(base_addr, token_addr, sell_amount_raw, symbol_pair)
                if not quote_entry or "buyAmount" not in quote_entry:
                    # If the pair was banned due to No liquidity, try reverse direction as informational fallback.
                    # We do not execute reverse trades automatically here, but this helps find liquidity direction.
                    if key in ban_list and BAN_NO_LIQUIDITY_REASON.lower() in ban_list[key]["reason"].lower():
                        dec_token = DECIMALS.get(token_symbol, 18)
                        sell_amount_rev = int(sell_amount_usd * (10 ** dec_token))
                        if DEBUG_MODE:
                            print(f"[Fallback] Trying reverse {token_symbol}->{base_token} (informational) sellAmount ~{sell_amount_usd} USD")
                        quote_rev = query_0x_price(token_addr, base_addr, sell_amount_rev, f"{token_symbol}->{base_token}")
                        if quote_rev and "buyAmount" in quote_rev:
                            profiler["profit_gt_min_skipped"].append((token_symbol, "Reverse route exists; forward had no liquidity"))
                        else:
                            # nothing available; skip
                            pass
                    continue

                # parse buy amount
                try:
                    buy_amount_token = int(quote_entry["buyAmount"])
                except Exception:
                    ban_pair(key, "Invalid buyAmount in 0x response", duration=BAN_OTHER_REASON_DURATION)
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    continue

                # Compute USD profit using quote & prices
                profit_calc = compute_profit_usd(quote_entry, base_addr, token_addr, sell_amount_raw)
                if profit_calc[0] is None:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Could not compute USD profit"))
                    continue

                net_profit_usd, sell_usd, buy_usd, fees_usd = profit_calc
                if not sell_usd or sell_usd == 0:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Sell USD unknown"))
                    continue

                profit_percent = (net_profit_usd / sell_usd) * 100

                if profit_percent < MIN_PROFIT_PERCENT:
                    profiler["profit_gt_min_skipped"].append((token_symbol, f"Profit {profit_percent:.2f}% < {MIN_PROFIT_PERCENT}%"))
                    continue

                # extract platforms
                platforms_used = extract_platforms(quote_entry.get("protocols", [])) if quote_entry.get("protocols") else []
                if not platforms_used and "route" in quote_entry:
                    try:
                        fills = []
                        route = quote_entry.get("route")
                        if isinstance(route, dict):
                            fills = route.get("fills", []) or []
                        elif isinstance(route, list):
                            fills = route
                        for f in fills:
                            source = f.get("source", "")
                            for pk, pn in PLATFORMS.items():
                                if pk.lower() in (source or "").lower():
                                    platforms_used.append(pn)
                    except Exception:
                        pass

                if not platforms_used:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    continue

                # timing logic
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                time_start = get_local_time().strftime("%H:%M")
                time_sell = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                url = f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                rsi_str = f"{rsi:.2f}" if (rsi is not None) else "N/A"
                fees_usd_safe = fees_usd if fees_usd is not None else 0.0

                pre_msg = (
                    f"{base_token} -> {token_symbol} -> {base_token} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT ESTIMATE: {profit_percent:.2f}% 💸\n"
                    f"SELL USD: {sell_usd:.6f}, BUY USD: {buy_usd:.6f}, FEES USD: {fees_usd_safe:.6f}\n"
                    f"RSI: {rsi_str}\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"{url}"
                )

                # Always send message about the planned trade (even if REAL_TRADING == False)
                send_telegram(pre_msg)

                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # sleep until planned sell (simulate hold)
                time.sleep(timing_sec)

                # Query exit quote (sell token -> buy base) using token units we would have obtained
                quote_exit = query_0x_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        # compute final P&L using exit quote too
                        exit_calc = compute_profit_usd(quote_exit, token_addr, base_addr, buy_amount_token)
                        if exit_calc[0] is not None and exit_calc[1]:
                            # final buy USD and fees
                            exit_net_usd, exit_sell_usd, exit_buy_usd, exit_fees_usd = exit_calc
                            # overall net relative to initial sell_usd:
                            overall_net_usd = exit_buy_usd - sell_usd - (fees_usd + (exit_fees_usd or 0.0))
                            overall_profit_percent = (overall_net_usd / sell_usd) * 100
                            send_telegram(
                                f"✅ TRADE COMPLETED\n"
                                f"Actual PROFIT: {overall_profit_percent:.2f}%\n"
                                f"Time: {get_local_time().strftime('%H:%M')}\n"
                                f"Token: {token_symbol}"
                            )
                        else:
                            send_telegram(
                                f"✅ TRADE COMPLETED\n"
                                f"Time: {get_local_time().strftime('%H:%M')}\n"
                                f"Token: {token_symbol}"
                            )
                    except Exception:
                        if DEBUG_MODE:
                            print(f"[Trade] Parsing exit failed for {token_symbol}: {quote_exit}")
                        send_telegram(
                            f"✅ TRADE COMPLETED (result parsing failed)\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                else:
                    ban_pair(key, "Exit quote failed", duration=BAN_OTHER_REASON_DURATION)

                # post-trade cooldown ban 15 min (other reason)
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # Periodic aggregated report (every REPORT_INTERVAL_LOCAL)
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL_LOCAL:
            clean_ban_list()
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
            if profiler['profit_gt_min_skipped']:
                for sym, reason in profiler['profit_gt_min_skipped']:
                    report_msg += f"   - {sym}: {reason}\n"
            if profiler['filtered_skipped']:
                report_msg += "🔎 Пропущенные (dexscreener/price issues):\n"
                for sym, reason in profiler['filtered_skipped']:
                    report_msg += f"   - {sym}: {reason}\n"
            report_msg += f"✔️ Успешных торгов за цикл: {profiler['successful_trades']}\n"
            report_msg += f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}\n"
            report_msg += "===========================\n"

            send_telegram(report_msg)
            last_report_time = now_ts

        # small sleep to avoid busy-loop
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        try:
            send_telegram(f"❗ Bot crashed with exception: {repr(e)}")
        except Exception:
            pass
        if DEBUG_MODE:
            print("[CRASH]", repr(e))
            
