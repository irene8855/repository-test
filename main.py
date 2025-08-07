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
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# timezone
LONDON_TZ = pytz.timezone("Europe/London")

# --- Tokens & decimals (kept exactly as provided) ---
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
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18, "wstETH": 18,
    "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18,
    "WETH": 18, "SUSHI": 18
}

# tokens for which we compute RSI (only these tokens use RSI filter)
RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}

PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

API_0X_URL = "https://polygon.api.0x.org/swap/v1/quote"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND  # rate limiting for 0x queries
BAN_DURATION_SECONDS = 900  # 15 minutes ban for pairs after 404
MIN_404_INTERVAL = 120  # seconds: per-pair 404 Telegram suppression interval

# runtime state
ban_list = {}  # key -> timestamp when added to ban
tracked_trades = {}  # key -> last time trade signalled
per_pair_404_last_sent = {}  # key -> timestamp last 404 telegram sent

# ---- Utilities ----
def send_telegram(msg: str):
    """Send a message to telegram (safe)."""
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
    """Return timezone-aware current time in Europe/London (robust)."""
    # take UTC now and convert to Europe/London
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    """
    Query 0x quote. On 404 -> add to ban_list and send per-pair suppressed telegram.
    Returns JSON dict or None.
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)
    try:
        params = {"sellToken": sell_token, "buyToken": buy_token, "sellAmount": str(sell_amount)}
        resp = requests.get(API_0X_URL, params=params, timeout=10)
        if resp.status_code == 200:
            if DEBUG_MODE:
                print(f"[0x API] 200 for {symbol_pair}")
            return resp.json()
        elif resp.status_code == 404:
            # ban the pair
            ban_list[key] = time.time()
            now = time.time()
            last_sent = per_pair_404_last_sent.get(key, 0)
            if now - last_sent > MIN_404_INTERVAL:
                send_telegram(f"[0x API] 404 for {symbol_pair}, pair added to ban list for 15 minutes.")
                per_pair_404_last_sent[key] = now
            if DEBUG_MODE:
                print(f"[0x API] 404 for {symbol_pair}; banned until +{BAN_DURATION_SECONDS}s")
            return None
        else:
            # other error codes -> notify (important)
            msg = f"[0x API] Error {resp.status_code} for {symbol_pair}: {resp.text}"
            send_telegram(msg)
            if DEBUG_MODE:
                print(msg)
            return None
    except Exception as e:
        send_telegram(f"[0x API] Exception for {symbol_pair}: {e}")
        if DEBUG_MODE:
            print(f"[0x API] Exception for {symbol_pair}: {e}")
        return None

def extract_platforms(protocols):
    found = set()
    for segment in protocols:
        for route in segment:
            # route is list of steps; first element contains DEX id
            try:
                dex = route[0].lower()
            except Exception:
                continue
            for platform_key, platform_name in PLATFORMS.items():
                if platform_key.lower() in dex:
                    found.add(platform_name)
    return list(found)

def clean_ban_list():
    """Remove expired bans and notify on exit."""
    now_ts = time.time()
    to_remove = [pair for pair, ts in ban_list.items() if now_ts - ts > BAN_DURATION_SECONDS]
    for pair in to_remove:
        del ban_list[pair]
        # reset per-pair 404 suppression so we can notify again later if needed
        per_pair_404_last_sent.pop(pair, None)
        send_telegram(f"🔓 Pair {pair[0]} -> {pair[1]} removed from ban list.")
        if DEBUG_MODE:
            print(f"[BAN] {pair} removed (expired)")

def fetch_dexscreener_data(token_addr):
    """Fetch dexscreener data for token address. Return JSON or None."""
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

def calculate_rsi(prices, period=14):
    """Calculate RSI from list of closing prices. Return float 0-100 or None if not enough data."""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    # Use simple RSI as before (average gain/loss over last 'period' deltas)
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
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

# --- Main strategy ---
def run_real_strategy():
    send_telegram("🤖 Bot started (real strategy).")
    if DEBUG_MODE:
        print("🤖 Real strategy started")

    base_tokens = ["USDT"]  # operate from USDT
    min_profit_percent = 1.0  # minimum estimated profit to trigger signal
    sell_amount_usd = 50  # reduced to increase chance of valid route
    last_request_time = 0

    while True:
        now = get_local_time()
        clean_ban_list()

        for base_token in base_tokens:
            base_addr = TOKENS.get(base_token)
            if not base_addr:
                continue

            decimals = DECIMALS.get(base_token, 18)
            sell_amount = int(sell_amount_usd * (10 ** decimals))

            for token_symbol, token_addr in TOKENS.items():
                # skip base token itself
                if token_symbol == base_token:
                    continue

                key = (base_token, token_symbol)

                # skip if in ban
                if key in ban_list:
                    if DEBUG_MODE:
                        print(f"[SKIP] {key} in ban list")
                    continue

                # skip if recently had a signal
                if time.time() - tracked_trades.get(key, 0) < BAN_DURATION_SECONDS:
                    if DEBUG_MODE:
                        print(f"[SKIP] {key} recently signalled (cooldown)")
                    continue

                # rate limiting
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # If token needs RSI -> fetch candles and calculate
                if token_symbol in RSI_TOKENS:
                    ds_data = fetch_dexscreener_data(token_addr)
                    if not ds_data:
                        if DEBUG_MODE:
                            print(f"[RSI] No dexscreener data for {token_symbol}; skipping RSI token")
                        continue
                    try:
                        pairs = ds_data.get("pairs", [])
                        if not pairs:
                            if DEBUG_MODE:
                                print(f"[RSI] No pairs for {token_symbol} in dexscreener response")
                            continue
                        candles = pairs[0].get("candles", [])
                        prices = [float(c["close"]) for c in candles if "close" in c]
                        rsi = calculate_rsi(prices)
                        if DEBUG_MODE:
                            print(f"[RSI] {token_symbol} RSI = {rsi}")
                        # fallback behavior: if not enough candles -> skip (we don't take risk)
                        if rsi is None:
                            if DEBUG_MODE:
                                print(f"[RSI] Not enough candles for {token_symbol}; skipping")
                            continue
                        # only consider tokens with RSI < 30
                        if rsi >= 30:
                            if DEBUG_MODE:
                                print(f"[RSI] {token_symbol} RSI {rsi} >= 30 -> skip")
                            continue
                    except Exception as e:
                        if DEBUG_MODE:
                            print(f"[RSI] Exception for {token_symbol}: {e}")
                        continue
                else:
                    rsi = None  # not applicable for stable tokens

                # Query 0x for entry quote (USDT -> token)
                quote_entry = query_0x_quote(base_addr, token_addr, sell_amount, f"{base_token}->{token_symbol}")
                if not quote_entry or "buyAmount" not in quote_entry:
                    if DEBUG_MODE:
                        print(f"[DEBUG] No quote or missing buyAmount for {key}")
                    continue

                try:
                    buy_amount_token = int(quote_entry["buyAmount"])
                except Exception:
                    if DEBUG_MODE:
                        print(f"[DEBUG] buyAmount parse error for {key}")
                    continue

                if buy_amount_token == 0:
                    if DEBUG_MODE:
                        print(f"[DEBUG] buyAmount == 0 for {key}")
                    continue

                # profit estimate (both amounts are in smallest units, so ratio works)
                profit_estimate = ((buy_amount_token / sell_amount) - 1) * 100
                if DEBUG_MODE:
                    print(f"[DEBUG] Profit estimate for {key}: {profit_estimate:.4f}% (threshold {min_profit_percent}%)")

                if profit_estimate < min_profit_percent:
                    if DEBUG_MODE:
                        print(f"[SKIP] Profit {profit_estimate:.4f}% < threshold for {key}")
                    continue

                # platforms used
                protocols = quote_entry.get("protocols", [])
                platforms_used = extract_platforms(protocols)
                if not platforms_used:
                    if DEBUG_MODE:
                        print(f"[SKIP] No supported platforms found for {key}")
                    continue

                # timing selection based on RSI if present, otherwise default 3 min
                timing_min = 3
                if rsi is not None:
                    # rsi in [0,100], lower -> potentially longer hold; clamp to 3-8
                    timing_min = 3 + int(max(0, (30 - rsi)) // 6)  # small formula to map RSI to minutes
                    timing_min = min(8, max(3, timing_min))
                timing_sec = timing_min * 60

                # Compose and send trade notification (this is the primary message)
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

                # Send the predicted-trade message (this is required)
                send_telegram(msg_entry)
                print(f"[REAL] Trade predicted: {msg_entry}")

                # record the signal time so we don't resignal same pair for BAN_DURATION
                tracked_trades[key] = time.time()

                # Wait the holding period (user will trade manually)
                time.sleep(timing_sec)

                # Query exit quote (token -> USDT) using the token amount we "virtually" bought
                quote_exit = query_0x_quote(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_token}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                        actual_profit = (final_amount_exit / sell_amount - 1) * 100
                        msg_exit = (
                            f"✅ TRADE COMPLETED\n"
                            f"Actual PROFIT: {actual_profit:.2f}%\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                        send_telegram(msg_exit)
                        print(f"[REAL] Trade completed: {msg_exit}")
                    except Exception as e:
                        if DEBUG_MODE:
                            print(f"[EXIT] Error parsing exit quote for {key}: {e}")

                # After the cycle, put the pair into ban_list (short cooldown)
                ban_list[key] = time.time()

        # end for base_token loop
    # end while

if __name__ == "__main__":
    try:
        run_real_strategy()
    except Exception as e:
        # critical crash notification
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            print(f"[CRASH] {e}")
            
