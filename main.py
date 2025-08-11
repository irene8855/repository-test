# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

# --- Settings (from env) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ZEROX_API_KEY = os.getenv("ZEROX_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# Behavior/config
SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))           # main sell amount in USD
SELL_AMOUNT_USD_REVERSE = float(os.getenv("SELL_AMOUNT_USD_REVERSE", "10"))  # used to probe reverse direction
TRY_REVERSE_DIRECTION = os.getenv("TRY_REVERSE_DIRECTION", "True").lower() == "true"

ZEROX_ENABLE_SLIPPAGE_PROTECTION = os.getenv("ZEROX_ENABLE_SLIPPAGE_PROTECTION", "False").lower() == "true"
ZEROX_SLIPPAGE_PERCENTAGE = float(os.getenv("ZEROX_SLIPPAGE_PERCENTAGE", "0.01"))

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

RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}
PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

# --- 0x API (v2 permit2/price) ---
API_0X_URL = "https://api.0x.org/swap/permit2/price"
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))  # polygon by default

# --- Limits & timings ---
MAX_REQUESTS_PER_SECOND = int(os.getenv("MAX_REQUESTS_PER_SECOND", "5"))
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

# Ban durations
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = int(os.getenv("BAN_NO_LIQUIDITY_DURATION", "120"))   # 2 minutes
BAN_OTHER_REASON_DURATION = int(os.getenv("BAN_OTHER_REASON_DURATION", "900"))   # 15 minutes

# runtime state
ban_list = {}          # key: (base_symbol, token_symbol) -> {"time": ts, "reason": str, "duration": int}
tracked_trades = {}    # key -> last trade timestamp (post-trade cooldown)
last_report_time = 0   # last time a Telegram report was sent

# --- Utilities ---
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram] (dry) Message would be:\n", msg)
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
    now_ts = time.time()
    if duration is None:
        if BAN_NO_LIQUIDITY_REASON.lower() in (reason or "").lower():
            duration = BAN_NO_LIQUIDITY_DURATION
        else:
            duration = BAN_OTHER_REASON_DURATION
    ban_list[key] = {"time": now_ts, "reason": reason, "duration": duration}
    if DEBUG_MODE:
        print(f"[BAN] {key} -> reason: {reason}, duration: {duration}s")

def clean_ban_list():
    now_ts = time.time()
    to_remove = [pair for pair, info in ban_list.items() if now_ts - info["time"] > info["duration"]]
    for pair in to_remove:
        if DEBUG_MODE:
            info = ban_list.get(pair, {})
            print(f"[BAN] Removing expired ban for {pair}: reason={info.get('reason')} (expired)")
        ban_list.pop(pair, None)

def extract_platforms(protocols):
    found = set()
    # old v1 used 'protocols'; v2 uses 'route.fills[].source'
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

def _try_extract_price_from_dexscreener(ds_data):
    """
    Helper: try to extract USD price for token from dexscreener response.
    Returns float priceUsd or None.
    """
    try:
        pairs = ds_data.get("pairs", [])
        if pairs:
            p = pairs[0]
            # try various common fields
            for k in ("priceUsd", "price", "tokenPriceUsd", "price_usd"):
                val = p.get(k)
                if val is not None:
                    try:
                        return float(val)
                    except Exception:
                        pass
            # fallback: use latest candle close if exists
            candles = p.get("candles", [])
            if candles:
                last = candles[-1]
                if "close" in last:
                    try:
                        return float(last["close"])
                    except Exception:
                        pass
    except Exception:
        pass
    return None

def usd_to_token_units(token_addr, usd_amount, token_symbol):
    """
    Convert USD amount to token smallest units using dexscreener if possible.
    Fallback to 1 token unit if price unknown.
    """
    decimals = DECIMALS.get(token_symbol, 18)
    ds = fetch_dexscreener_data(token_addr)
    if not ds:
        # fallback: sell 1 token unit
        return 1 * (10 ** decimals)
    price = _try_extract_price_from_dexscreener(ds)
    if price and price > 0:
        units = int((usd_amount / price) * (10 ** decimals))
        if units <= 0:
            units = 1 * (10 ** decimals)
        return units
    else:
        return 1 * (10 ** decimals)

def query_0x_price(sell_token: str, buy_token: str, sell_amount: int, symbol_pair="", ban_on_fail=True):
    """
    Query 0x v2 price endpoint (permit2/price) with required headers.
    If ban_on_fail==False returns tuple (data, reason) where reason is None on success or str on error.
    If ban_on_fail==True — original behavior: it will ban pair on error and return data or None.
    data: dict on success
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
        "chainId": CHAIN_ID
    }
    # add slippage protection params if enabled
    if ZEROX_ENABLE_SLIPPAGE_PROTECTION:
        # the API may accept slippagePercentage or slippageBps; using slippagePercentage as requested
        params["slippagePercentage"] = str(ZEROX_SLIPPAGE_PERCENTAGE)
        params["enableSlippageProtection"] = "true"

    headers = {"0x-version": "v2"}
    if ZEROX_API_KEY:
        headers["0x-api-key"] = ZEROX_API_KEY
    else:
        if DEBUG_MODE:
            print("[0x] ZEROX_API_KEY not set in env; continuing without key (may be rate-limited).")

    try:
        resp = requests.get(API_0X_URL, params=params, headers=headers, timeout=12)
    except requests.exceptions.RequestException as e:
        reason = f"Request exception: {e}"
        if ban_on_fail:
            ban_pair(key, reason, duration=BAN_OTHER_REASON_DURATION)
            return None
        else:
            return None, reason

    # handle response
    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            reason = "Invalid JSON from 0x"
            if ban_on_fail:
                ban_pair(key, reason, duration=BAN_OTHER_REASON_DURATION)
                if DEBUG_MODE:
                    print(f"[0x] Invalid JSON for {symbol_pair}: {resp.text[:200]}")
                return None
            else:
                return None, reason

        # v2: liquidityAvailable may indicate availability
        if "liquidityAvailable" in data and (data.get("liquidityAvailable") is False):
            reason = BAN_NO_LIQUIDITY_REASON
            if ban_on_fail:
                ban_pair(key, reason, duration=BAN_NO_LIQUIDITY_DURATION)
                if DEBUG_MODE:
                    print(f"[0x] No liquidity for {symbol_pair} (liquidityAvailable=false).")
                return None
            else:
                return None, reason

        # route empty check (some v2 responses may contain route)
        if "route" in data:
            route = data.get("route")
            if not route or (isinstance(route, dict) and not route.get("fills")):
                reason = BAN_NO_LIQUIDITY_REASON
                if ban_on_fail:
                    ban_pair(key, reason, duration=BAN_NO_LIQUIDITY_DURATION)
                    if DEBUG_MODE:
                        print(f"[0x] Empty route for {symbol_pair}.")
                    return None
                else:
                    return None, reason

        # success — return the JSON
        if ban_on_fail:
            return data
        else:
            return data, None

    elif resp.status_code == 404:
        reason = BAN_NO_LIQUIDITY_REASON
        if ban_on_fail:
            ban_pair(key, reason, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[0x] 404 for {symbol_pair}; banned short.")
            return None
        else:
            return None, reason
    else:
        # other errors
        snippet = ""
        try:
            snippet = resp.text[:200].replace("\n", " ")
        except Exception:
            pass
        reason = f"HTTP {resp.status_code}: {snippet}"
        if ban_on_fail:
            ban_pair(key, reason, duration=BAN_OTHER_REASON_DURATION)
            if DEBUG_MODE:
                print(f"[0x] Error {resp.status_code} for {symbol_pair}: {snippet}")
            return None
        else:
            return None, reason

# --- Main strategy ---
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (real strategy).")
    base_tokens = ["USDT"]
    min_profit_percent = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))
    sell_amount_usd = SELL_AMOUNT_USD
    last_request_time = 0
    REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "900"))  # 15 minutes default

    while True:
        cycle_start_time = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "profit_gt_min_skipped": [],  # list of tuples (symbol, reason)
            "total_checked_pairs": 0,
            "successful_trades": 0,
            "reversed_available": []  # tokens for which reverse had liquidity
        }

        # cleanup expired bans
        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            decimals_base = DECIMALS.get(base_token, 18)
            sell_amount_base_units = int(sell_amount_usd * (10 ** decimals_base))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token:
                    continue
                profiler["total_checked_pairs"] += 1
                key = (base_token, token_symbol)

                # if pair is banned, skip and count
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    continue

                # post-trade cooldown: do not attempt new trade too soon after a trade
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
                    profiler["cooldown_skips"] += 1
                    continue

                # respect 0x rate limit
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # RSI filter
                rsi = None
                if token_symbol in RSI_TOKENS:
                    ds_data = fetch_dexscreener_data(token_addr)
                    if not ds_data:
                        # can't compute RSI -> skip token this cycle
                        continue
                    pairs = ds_data.get("pairs", [])
                    if not pairs:
                        continue
                    candles = pairs[0].get("candles", [])
                    prices = [float(c["close"]) for c in candles if "close" in c]
                    rsi = calculate_rsi(prices)
                    if rsi is not None and rsi > 70:
                        profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f}"))
                        # do not ban for RSI, just skip
                        continue

                # First try direct quote (base -> token) WITHOUT immediate ban (ban_on_fail=False)
                quote_entry, reason = query_0x_price(base_addr, token_addr, sell_amount_base_units,
                                                      f"{base_token}->{token_symbol}", ban_on_fail=False)
                if quote_entry is None:
                    # reason may say "No liquidity" or HTTP error etc.
                    if reason == BAN_NO_LIQUIDITY_REASON:
                        # attempt reverse direction as a probe (diagnostics) if allowed
                        if TRY_REVERSE_DIRECTION:
                            # compute a reasonable sell amount in token units (smaller)
                            sell_amount_rev_units = usd_to_token_units(token_addr, SELL_AMOUNT_USD_REVERSE, token_symbol)
                            # probe reverse without banning
                            quote_rev, rev_reason = query_0x_price(token_addr, base_addr, sell_amount_rev_units,
                                                                   f"{token_symbol}->{base_token}", ban_on_fail=False)
                            if quote_rev is not None:
                                # reverse route available — record for report (but still ban direct for short time)
                                profiler["reversed_available"].append((token_symbol, f"reverse_sell={SELL_AMOUNT_USD_REVERSE}usd"))
                                if DEBUG_MODE:
                                    print(f"[0x] Direct {base_token}->{token_symbol} no-liq, but reverse available ({token_symbol}->{base_token}).")
                            # ban direct pair for no-liquidity duration (short)
                        ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    else:
                        # other error (HTTP, timeout...) — ban longer
                        ban_pair(key, reason or "0x error", duration=BAN_OTHER_REASON_DURATION)
                    # continue to next token
                    continue

                # success: we have a quote_entry for buyAmount
                try:
                    buy_amount_token = int(quote_entry["buyAmount"])
                except Exception:
                    ban_pair(key, "Invalid buyAmount in 0x response", duration=BAN_OTHER_REASON_DURATION)
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    continue

                # estimate profit (both sell_amount and buy_amount are in smallest units of their tokens)
                profit_estimate = ((buy_amount_token / sell_amount_base_units) - 1) * 100
                if profit_estimate < min_profit_percent:
                    # not enough profit to consider
                    continue

                # platforms: try to extract from protocols (v1 style) or route fills (v2)
                platforms_used = []
                if quote_entry.get("protocols"):
                    platforms_used = extract_platforms(quote_entry.get("protocols", []))
                if not platforms_used and "route" in quote_entry:
                    try:
                        fills = quote_entry["route"].get("fills", [])
                        for f in fills:
                            src = f.get("source", "") or ""
                            for platform_key, platform_name in PLATFORMS.items():
                                if platform_key.lower() in src.lower():
                                    if platform_name not in platforms_used:
                                        platforms_used.append(platform_name)
                    except Exception:
                        pass

                if not platforms_used:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    # do not ban: skip
                    continue

                # prepare timing and messages
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                time_start = get_local_time().strftime("%H:%M")
                time_sell = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                url = f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                rsi_str = f"{rsi:.2f}" if isinstance(rsi, (int, float)) else "N/A"

                pre_msg = (
                    f"{base_token} -> {token_symbol} -> {base_token} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT ESTIMATE: {profit_estimate:.2f}% 💸\n"
                    f"RSI: {rsi_str}\n"
                    f"PLATFORMS: {', '.join(platforms_used)} 📊\n"
                    f"{url}"
                )
                send_telegram(pre_msg)

                # mark as attempted / on cooldown
                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # simulate holding period
                time.sleep(timing_sec)

                # exit quote: sell token -> base
                # use the buy_amount_token as sellAmount for exit query (units: token smallest units)
                quote_exit = query_0x_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}", ban_on_fail=False)
                if quote_exit is None:
                    # exit failed — ban longer
                    # we want to record reason: if it was no liquidity treat accordingly
                    # call query_0x_price with ban_on_fail=True to apply ban and capture debug
                    _ = query_0x_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}", ban_on_fail=True)
                    # notify that exit failed
                    send_telegram(f"⚠️ Exit quote failed for {token_symbol} -> {base_token}")
                else:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                        actual_profit = (final_amount_exit / sell_amount_base_units - 1) * 100
                        send_telegram(
                            f"✅ TRADE COMPLETED\n"
                            f"Actual PROFIT: {actual_profit:.2f}%\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                    except Exception:
                        if DEBUG_MODE:
                            print(f"[Trade] Failed to parse exit buyAmount for {token_symbol}: {quote_exit}")
                        send_telegram(
                            f"✅ TRADE COMPLETED (result parsing failed)\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )

                # after a real trade, apply a post-trade cooldown ban
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # periodic detailed report every REPORT_INTERVAL seconds
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
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

            if profiler["reversed_available"]:
                report_msg += "Реверс-роуты найдены (прямой - нет):\n"
                for sym, note in profiler["reversed_available"]:
                    report_msg += f"  - {sym}: {note}\n"

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

            send_telegram(report_msg)
            last_report_time = now_ts

        # short sleep to avoid spinning too tight
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        # fatal crash — notify via Telegram if possible
        try:
            send_telegram(f"❗ Bot crashed with exception: {e}")
        except Exception:
            pass
        if DEBUG_MODE:
            print(f"[CRASH] {e}")
            
