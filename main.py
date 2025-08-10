# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

# --- Settings ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ZEROX_API_KEY = os.getenv("ZEROX_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# timezone
LONDON_TZ = pytz.timezone("Europe/London")

# --- Tokens & decimals (unchanged) ---
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

# tokens that we try to compute RSI for
RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}
PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

# --- 0x API (use v2 / permit2 price endpoint for indicative prices) ---
API_0X_URL = "https://api.0x.org/swap/permit2/price"
CHAIN_ID = 137

# --- Limits & timings ---
MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

# Ban durations
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = 120   # 2 minutes
BAN_OTHER_REASON_DURATION = 900   # 15 minutes

# runtime state
ban_list = {}          # key: (base_symbol, token_symbol) -> {"time": ts, "reason": str, "duration": int}
tracked_trades = {}    # key -> last trade timestamp (post-trade cooldown)
last_report_time = 0   # last time a Telegram report was sent

# --- Utilities ---
def send_telegram(msg: str):
    """
    Send a Telegram message (if configured), otherwise print in debug mode.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram] Token or chat id not configured. Message would be:\n", msg)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] Error ({resp.status_code}): {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception while sending Telegram message: {e}")

def get_local_time():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def ban_pair(key, reason, duration=None):
    """
    Put a pair into ban_list with a reason and duration (seconds).
    If duration is None, pick based on reason (no liquidity -> short ban).
    """
    now_ts = time.time()
    if duration is None:
        if BAN_NO_LIQUIDITY_REASON.lower() in reason.lower() or "404" in reason:
            duration = BAN_NO_LIQUIDITY_DURATION
        else:
            duration = BAN_OTHER_REASON_DURATION
    ban_list[key] = {"time": now_ts, "reason": reason, "duration": duration}
    if DEBUG_MODE:
        print(f"[BAN] {key} -> reason: {reason}, duration: {duration}s")

def clean_ban_list():
    """
    Remove expired bans from ban_list.
    """
    now_ts = time.time()
    to_remove = [pair for pair, info in ban_list.items() if now_ts - info["time"] > info["duration"]]
    for pair in to_remove:
        if DEBUG_MODE:
            info = ban_list.get(pair, {})
            print(f"[BAN] Removing expired ban for {pair}: reason={info.get('reason')} (expired)")
        ban_list.pop(pair, None)

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

def fetch_dexscreener_data(token_addr):
    try:
        resp = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=8)
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

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    # compute last `period` deltas
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

def query_0x_price(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    """
    Query 0x v2 price endpoint (permit2/price) with required headers.
    Returns parsed JSON (dict) on success (and liquidityAvailable True),
    or None on no-liquidity / error (and bans the pair appropriately).
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
        "chainId": CHAIN_ID
    }
    headers = {"0x-version": "v2"}
    if ZEROX_API_KEY:
        headers["0x-api-key"] = ZEROX_API_KEY
    else:
        if DEBUG_MODE:
            print("[0x] ZEROX_API_KEY not set in env; requests may be limited or blocked by 0x API.")

    try:
        resp = requests.get(API_0X_URL, params=params, headers=headers, timeout=10)
    except requests.exceptions.RequestException as e:
        # network error: ban for other reason (longer)
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

        # v2 introduces 'liquidityAvailable' boolean. If it's False — no liquidity.
        if "liquidityAvailable" in data and (data.get("liquidityAvailable") is False):
            ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[0x] No liquidity for {symbol_pair} (liquidityAvailable=false).")
            return None

        # In some cases v2 may not use liquidityAvailable but returns route empty - double-check
        if "route" in data:
            route = data.get("route")
            if not route or (isinstance(route, dict) and not route.get("fills")):
                # treat as no liquidity
                ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                if DEBUG_MODE:
                    print(f"[0x] Empty route for {symbol_pair}.")
                return None

        # success — return the JSON (containing buyAmount etc.)
        return data

    elif resp.status_code == 404:
        # treat as no liquidity
        ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
        if DEBUG_MODE:
            print(f"[0x] 404 for {symbol_pair}; banned short.")
        return None
    else:
        # other errors: ban for longer time
        reason_text = f"HTTP {resp.status_code}"
        # try to include short snippet of body for diagnostics
        try:
            snippet = resp.text[:200].replace("\n", " ")
            reason_text += f" - {snippet}"
        except Exception:
            pass
        ban_pair(key, reason_text, duration=BAN_OTHER_REASON_DURATION)
        if DEBUG_MODE:
            print(f"[0x] Error {resp.status_code} for {symbol_pair}: {resp.text[:200]}")
        return None

# --- Main strategy ---
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (real strategy).")
    base_tokens = ["USDT"]
    min_profit_percent = 1.0
    sell_amount_usd = 50
    last_request_time = 0

    # we keep a local variable to avoid spamming Telegram each cycle;
    # a single detailed report will be sent every 15 minutes (900s).
    REPORT_INTERVAL = 900

    while True:
        cycle_start_time = time.time()

        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "profit_gt_min_skipped": [],  # list of tuples (symbol, reason)
            "total_checked_pairs": 0,
            "successful_trades": 0,
        }

        # remove expired bans before starting the cycle
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

                # if pair is banned, skip and count
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    continue

                # post-trade cooldown (do not attempt new trade for a while)
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
                    profiler["cooldown_skips"] += 1
                    continue

                # rate limiting between 0x requests
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # RSI check (if applicable)
                rsi = None
                if token_symbol in RSI_TOKENS:
                    ds_data = fetch_dexscreener_data(token_addr)
                    if not ds_data:
                        # if Dexscreener failed, skip this token for now
                        continue
                    pairs = ds_data.get("pairs", [])
                    if not pairs:
                        continue
                    candles = pairs[0].get("candles", [])
                    prices = [float(c["close"]) for c in candles if "close" in c]
                    rsi = calculate_rsi(prices)

                    # skip if RSI > 70 (do not ban because RSI is not an API error)
                    if rsi is not None and rsi > 70:
                        profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f}"))
                        continue

                # Query 0x v2 price endpoint (permit2/price)
                quote_entry = query_0x_price(base_addr, token_addr, sell_amount, f"{base_token}->{token_symbol}")
                if not quote_entry or "buyAmount" not in quote_entry:
                    # if quote_entry is None, query_0x_price already handled banning where needed
                    continue

                # parse buy amount
                try:
                    buy_amount_token = int(quote_entry["buyAmount"])
                except Exception:
                    # malformed buyAmount -> skip and ban as "other"
                    ban_pair(key, "Invalid buyAmount in 0x response", duration=BAN_OTHER_REASON_DURATION)
                    continue
                if buy_amount_token == 0:
                    # weird zero buy amount -> treat as no liquidity
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    continue

                # estimate profit (note: units are raw base units; original logic retained)
                profit_estimate = ((buy_amount_token / sell_amount) - 1) * 100
                if profit_estimate < min_profit_percent:
                    # not enough profit
                    continue

                # extract platforms used by the route (if present)
                platforms_used = extract_platforms(quote_entry.get("protocols", [])) if quote_entry.get("protocols") else []
                # if extract_platforms returned empty, try fallback: look at route.tokens or fills
                if not platforms_used and "route" in quote_entry:
                    # try to infer platform names from route.fills.source
                    try:
                        fills = quote_entry["route"].get("fills", [])
                        for f in fills:
                            source = f.get("source", "")
                            for platform_key, platform_name in PLATFORMS.items():
                                if platform_key.lower() in source.lower():
                                    platforms_used.append(platform_name)
                    except Exception:
                        pass

                if not platforms_used:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    continue

                # compute timing and prepare message
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                time_start = get_local_time().strftime("%H:%M")
                time_sell = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                url = f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                # preliminary trade message
                pre_msg = (
                    f"{base_token} -> {token_symbol} -> {base_token} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT ESTIMATE: {profit_estimate:.2f}% 💸\n"
                    f"RSI: {rsi:.2f if rsi is not None else 'N/A'}\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"{url}"
                )
                send_telegram(pre_msg)

                # mark as attempted / on cooldown
                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # wait until planned sell time (simulating hold)
                time.sleep(timing_sec)

                # Query price for exit (token -> base)
                # Note: use sellAmount = buy_amount_token (we received buyAmount as token units)
                quote_exit = query_0x_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}")
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
                        # don't crash on conversion/parsing errors
                        if DEBUG_MODE:
                            print(f"[Trade] Failed to parse exit buyAmount for {token_symbol}: {quote_exit}")
                        send_telegram(
                            f"✅ TRADE COMPLETED (result parsing failed)\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                else:
                    # exit quote failed — ban the pair for other reason
                    ban_pair(key, "Exit quote failed", duration=BAN_OTHER_REASON_DURATION)

                # after a real trade, apply a post-trade cooldown (15 min)
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # Periodic detailed report to Telegram every REPORT_INTERVAL seconds
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
            clean_ban_list()  # ensure ban list up to date

            # build banned pairs details
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
                f"💰 Пар с прибылью > {min_profit_percent}% (но не отправлены): {len(profiler['profit_gt_min_skipped'])}\n"
            )
            if profiler["profit_gt_min_skipped"]:
                for sym, reason in profiler["profit_gt_min_skipped"]:
                    report_msg += f"   - {sym}: {reason}\n"
            else:
                report_msg += "💰 Все пары с прибылью были отправлены.\n"
            report_msg += f"✔️ Успешных торгов за цикл: {profiler['successful_trades']}\n"
            report_msg += f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}\n"
            report_msg += "===========================\n"

            # send aggregated report
            send_telegram(report_msg)
            last_report_time = now_ts

        # small sleep to prevent 100% CPU tight loop (rate limiting already handles request pacing)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        # fatal crash — notify via Telegram if possible
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            print(f"[CRASH] {e}")
            
