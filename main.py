# -*- coding: utf-8 -*-
"""
Trading signals bot (full). Sources: 1inch (if key), Uniswap V3 subgraph (if GRAPH_API_KEY), Dexscreener.
Features:
 - send start message on launch
 - pre-trade message with price source
 - monitor trade in background, send final message on target/stop/time
 - report every REPORT_INTERVAL seconds (default 15 min) with detailed error reasons
"""

import os
import time
import datetime
import threading
import requests
import pytz
from math import isfinite
from dotenv import load_dotenv

load_dotenv()

# ===================== CONFIG =====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

LONDON_TZ = pytz.timezone("Europe/London")

SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))

MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))

REPORT_INTERVAL = int(float(os.getenv("REPORT_INTERVAL", "900")))  # seconds

MAX_REQUESTS_PER_SECOND = int(os.getenv("MAX_REQUESTS_PER_SECOND", "5"))
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

# Optional keys
ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY")  # optional, better accuracy if present
GRAPH_API_KEY = os.getenv("GRAPH_API_KEY")      # optional, required for Uniswap subgraph via gateway.thegraph.com

# Uniswap V3 subgraph ID for Polygon (from your uploaded file)
UNISWAP_V3_SUBGRAPH_ID = "BvYimJ6vCLkk63oWZy7WB5cVDTVVMugUAF35RAUZpQXE"
UNISWAP_V3_GATEWAY_BASE = "https://gateway.thegraph.com/api"

# 1inch endpoints
CHAIN_ID = 137
ONEINCH_V6_DEV = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

# ===================== TOKENS & DECIMALS =====================
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

RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}

# ===================== STATE =====================
ban_list = {}  # {(base, token): {"time":ts, "reason":str, "duration":int}}
tracked_trades = {}
last_report_time = time.time()
_last_cycle_report = time.time()
_last_watchdog_ping = 0.0

last_request_time_lock = threading.Lock()
_last_request_time = 0.0

# ===================== UTIL FUNCTIONS =====================
def send_telegram(text: str):
    """Send Telegram message if token/chat present; print if debug."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram muted] " + text)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=(5, 10)
        )
        if r.status_code != 200 and DEBUG_MODE:
            print("Telegram send failed:", r.status_code, r.text[:400])
    except Exception as e:
        if DEBUG_MODE:
            print("Telegram exception:", e)

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
    now = time.time()
    for k in list(ban_list.keys()):
        if now - ban_list[k]["time"] > ban_list[k]["duration"]:
            if DEBUG_MODE:
                print(f"[BAN expired] {k}")
            ban_list.pop(k, None)

# ===================== Dexscreener helpers =====================
def fetch_dexscreener_pairs(token_addr: str):
    try:
        pace_requests()
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=(5, 10))
        if resp.status_code == 200:
            return resp.json()
        if DEBUG_MODE:
            print("[Dexscreener] HTTP", resp.status_code, resp.text[:300])
    except Exception as e:
        if DEBUG_MODE:
            print("[Dexscreener] exception", e)
    return None

def get_token_usd_price_from_dxs(token_addr: str):
    data = fetch_dexscreener_pairs(token_addr)
    if not data:
        return None
    for p in data.get("pairs", []):
        try:
            if "priceUsd" in p and p["priceUsd"]:
                return float(p["priceUsd"])
        except Exception:
            continue
    return None

def get_token_candles(token_addr: str):
    data = fetch_dexscreener_pairs(token_addr)
    if not data:
        return None
    pairs = data.get("pairs", [])
    if not pairs:
        return None
    return pairs[0].get("candles", [])

# ===================== RSI =====================
def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(-delta)
    if not gains and not losses:
        return None
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def safe_format_rsi(rsi):
    if rsi is None:
        return "—"
    try:
        return f"{float(rsi):.2f}"
    except:
        return "—"

# ===================== Uniswap V3 subgraph fallback (real) =====================
def univ3_graph_url():
    if not GRAPH_API_KEY:
        return None
    return f"{UNISWAP_V3_GATEWAY_BASE}/{GRAPH_API_KEY}/subgraphs/id/{UNISWAP_V3_SUBGRAPH_ID}"

def _pick_best_univ3_pool(pools):
    if not pools:
        return None
    # order by liquidity (field might be string)
    def liq(p):
        try:
            return float(p.get("liquidity") or 0.0)
        except:
            return 0.0
    pools_sorted = sorted(pools, key=liq, reverse=True)
    return pools_sorted[0]

def _calc_amount_out_from_pool(sell_addr, buy_addr, amount_units, pool):
    try:
        t0 = pool.get("token0", {})
        t1 = pool.get("token1", {})
        addr0 = (t0.get("id") or "").lower()
        addr1 = (t1.get("id") or "").lower()
        dec0 = int(t0.get("decimals") or 18)
        dec1 = int(t1.get("decimals") or 18)
        fee_tier = int(pool.get("feeTier") or 3000)
        sqrtP = pool.get("sqrtPrice") or pool.get("sqrtPriceX96")
        sqrtP = int(sqrtP)
        Q96 = 2 ** 96
        price_1_per_0 = (sqrtP / Q96) ** 2
        price_1_per_0 *= 10 ** (dec0 - dec1)
        if not isfinite(price_1_per_0) or price_1_per_0 <= 0:
            return None
        fee_factor = 1.0 - (fee_tier / 1_000_000.0)  # e.g., 3000->0.997
        if sell_addr == addr0 and buy_addr == addr1:
            out_amount = amount_units * price_1_per_0 * fee_factor
        elif sell_addr == addr1 and buy_addr == addr0:
            out_amount = amount_units * (1.0 / price_1_per_0) * fee_factor
        else:
            return None
        out_int = int(out_amount)
        if out_int <= 0:
            return None
        return out_int
    except Exception:
        return None

def univ3_estimate_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    url = univ3_graph_url()
    if not url:
        return None
    try:
        src = src_addr.lower()
        dst = dst_addr.lower()
        query = """
        query Pools($a:String!, $b:String!) {
          pools(
            where: { token0_in: [$a, $b], token1_in: [$a, $b], feeTier_in: [500, 3000, 10000] }
            first: 20,
            orderBy: liquidity,
            orderDirection: desc
          ) {
            id
            feeTier
            liquidity
            sqrtPrice
            token0 { id symbol decimals }
            token1 { id symbol decimals }
          }
        }
        """
        variables = {"a": src, "b": dst}
        pace_requests()
        resp = requests.post(url, json={"query": query, "variables": variables}, timeout=(7, 15))
        if resp.status_code != 200:
            if DEBUG_MODE:
                print("[UniswapV3] HTTP", resp.status_code, resp.text[:400])
            return None
        data = resp.json()
        pools = (data.get("data") or {}).get("pools") or []
        # keep only exact pools
        pools = [p for p in pools if { (p.get("token0") or {}).get("id","").lower(), (p.get("token1") or {}).get("id","").lower() } == {src, dst}]
        if not pools:
            return None
        best = _pick_best_univ3_pool(pools)
        out = _calc_amount_out_from_pool(src, dst, amount_units, best)
        if not out:
            return None
        return {"buyAmount": str(out), "protocols": [], "route": {"fills": []}, "source": "UniswapV3"}
    except Exception as e:
        if DEBUG_MODE:
            print("[UniswapV3] exception", e)
        return None

# ===================== 1inch querying (with diagnostics) =====================
def query_1inch_via_endpoint(url, params, headers, symbol_pair):
    """Call 1inch endpoint and return (data_dict or None, last_err_snippet)"""
    try:
        pace_requests()
        resp = requests.get(url, params=params, headers=headers, timeout=(5, 10))
    except Exception as e:
        if DEBUG_MODE:
            print(f"[1inch] HTTP exception for {symbol_pair}: {e}")
        return None, f"HTTP error: {e}"

    if resp.status_code != 200:
        snippet = (resp.text[:400].replace("\n"," ")) if resp.text else str(resp.status_code)
        if DEBUG_MODE:
            print(f"[1inch] HTTP {resp.status_code} for {symbol_pair}: {snippet}")
        return None, f"HTTP {resp.status_code}: {snippet}"

    try:
        data = resp.json()
    except Exception:
        raw = resp.text[:1500]
        if DEBUG_MODE:
            print(f"[1inch] invalid JSON for {symbol_pair}: {raw[:500]}")
        return None, f"Invalid JSON: {raw[:300]}"

    return data, None

def query_1inch_price(sell_token_addr: str, buy_token_addr: str, sell_amount_units: int, symbol_pair: str = ""):
    """
    Attempts in order:
      - 1inch v6 dev with API key (if set)
      - 1inch v6 dev without auth
      - 1inch v5 public
      - Uniswap V3 subgraph fallback (if GRAPH_API_KEY set)
      - Dexscreener final fallback (best estimate via priceUsd)
    Returns dict with buyAmount (string), protocols, route, source, or None.
    Also writes ban_pair on clear failures with reason.
    """
    params_v = {
        "src": sell_token_addr,
        "dst": buy_token_addr,
        "amount": str(sell_amount_units),
        "disableEstimate": "true",
        "includeTokensInfo": "false",
        "includeProtocols": "true",
        "includeGas": "false",
    }
    headers_base = {"Accept": "application/json"}
    attempts = []
    if ONEINCH_API_KEY:
        attempts.append(("v6_dev_auth", ONEINCH_V6_DEV, {**headers_base, "Authorization": f"Bearer {ONEINCH_API_KEY}"}))
    attempts.append(("v6_dev_noauth", ONEINCH_V6_DEV, headers_base))
    attempts.append(("v5_public", ONEINCH_V5_PUBLIC, headers_base))

    last_err = None
    for name, url, headers in attempts:
        data, err = query_1inch_via_endpoint(url, params_v, headers, symbol_pair)
        if err:
            last_err = f"1inch/{name} {err}"
            # ban for some HTTP errors (400/404/422) or invalid JSON
            try:
                sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                if sp and len(sp) == 2:
                    ban_pair(sp, last_err)
            except Exception:
                pass
            # continue to next attempt
            continue
        # got JSON
        buy_amount = data.get("toTokenAmount") or data.get("dstAmount")
        if not buy_amount:
            last_err = f"1inch/{name} no buy amount"
            try:
                sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                if sp and len(sp) == 2:
                    ban_pair(sp, last_err)
            except Exception:
                pass
            continue
        try:
            if int(buy_amount) == 0:
                last_err = f"1inch/{name} buy amount zero"
                try:
                    sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                    if sp and len(sp) == 2:
                        ban_pair(sp, last_err)
                except Exception:
                    pass
                return None
        except Exception:
            pass
        # success
        return {"buyAmount": str(buy_amount), "protocols": data.get("protocols") or [], "route": {"fills": []}, "source": f"1inch:{name}"}

    # 1inch failed — try Uniswap V3 subgraph fallback
    uni = univ3_estimate_amount_out(sell_token_addr, buy_token_addr, sell_amount_units)
    if uni:
        # mark source
        uni["source"] = "UniswapV3"
        return uni

    # Last fallback: Dexscreener price estimation (coarse)
    try:
        # Try to get USD price of buy and base by dexscreener; compute ratio
        pace_requests()
        sell_sym = ADDRESS_TO_SYMBOL.get(sell_token_addr.lower())
        buy_sym = ADDRESS_TO_SYMBOL.get(buy_token_addr.lower())
        # If we have token addresses, use them
        sell_price = get_token_usd_price_from_dxs(sell_token_addr)
        buy_price = get_token_usd_price_from_dxs(buy_token_addr)
        if sell_price is not None and buy_price is not None:
            # amount_units -> tokens -> usd -> amount of buy tokens
            sell_dec = DECIMALS.get(sell_sym, 18)
            buy_dec = DECIMALS.get(buy_sym, 18)
            sell_tokens = sell_amount_units / (10 ** sell_dec)
            usd_value = sell_tokens * sell_price
            buy_tokens = usd_value / buy_price
            buy_units = int(buy_tokens * (10 ** buy_dec))
            if buy_units > 0:
                return {"buyAmount": str(buy_units), "protocols": [], "route": {"fills": []}, "source": "Dexscreener"}
    except Exception as e:
        if DEBUG_MODE:
            print("Dexscreener fallback exception:", e)

    # nothing worked
    if DEBUG_MODE and last_err:
        print("[quote fail]", symbol_pair, last_err)
    try:
        sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
        if sp and len(sp) == 2:
            ban_pair(sp, f"quote fail: {last_err or 'no source'}")
    except Exception:
        pass
    return None

# ===================== Monitoring thread =====================
def monitor_trade_thread(entry_sell_amount_units, base_addr, token_addr,
                         base_symbol, token_symbol, timing_sec, buy_amount_token, source_tag):
    """
    Monitor opened trade window for timing_sec seconds.
    Send messages: target reached, stop loss reached, final report on timeout.
    """
    start_ts = time.time()
    alerted_take = False
    alerted_stop = False

    while True:
        elapsed = time.time() - start_ts
        is_final = elapsed >= timing_sec

        # check exit quote
        quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
        final_amount_exit = None
        if quote_exit and quote_exit.get("buyAmount"):
            try:
                final_amount_exit = int(quote_exit.get("buyAmount"))
            except Exception:
                final_amount_exit = None

        if final_amount_exit:
            _, _, profit_pct = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit, base_symbol, token_symbol)
        else:
            profit_pct = None

        if is_final:
            if profit_pct is not None:
                send_telegram(
                    f"⏳ Время удержания вышло\nPAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Source: {source_tag}\nFinal PnL: {profit_pct:.2f}%\nTime: {get_local_time().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                send_telegram(
                    f"⏳ Время удержания вышло\nPAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Source: {source_tag}\nFinal: котировку выхода получить не удалось."
                )
            return

        else:
            if profit_pct is not None:
                if (not alerted_take) and profit_pct >= MIN_PROFIT_PERCENT:
                    send_telegram(f"🎯 Цель достигнута: {profit_pct:.2f}% по {token_symbol} (Source: {source_tag})")
                    alerted_take = True
                if (not alerted_stop) and profit_pct <= STOP_LOSS_PERCENT:
                    send_telegram(f"⚠️ Стоп-лосс: {profit_pct:.2f}% по {token_symbol} (Source: {source_tag})")
                    alerted_stop = True

        time.sleep(15)

def start_monitor(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token, source_tag):
    t = threading.Thread(
        target=monitor_trade_thread,
        args=(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token, source_tag),
        daemon=True
    )
    t.start()

# ===================== Helpers =====================
def extract_platforms(protocols_field):
    platforms_used = []
    try:
        for route in (protocols_field or []):
            for step in (route or []):
                name = step.get("name", "") or step.get("id", "") or ""
                for short, human in PLATFORMS.items():
                    if short.lower() in name.lower():
                        if human not in platforms_used:
                            platforms_used.append(human)
    except Exception:
        pass
    return platforms_used

def compute_profit_percent_by_units(sell_amount_units, final_amount_units, base_symbol="USDT", token_symbol=None):
    try:
        profit_pct = (final_amount_units / sell_amount_units - 1) * 100
    except Exception:
        profit_pct = None
    return sell_amount_units, final_amount_units, profit_pct

# ===================== MAIN STRATEGY =====================
def run_real_strategy():
    global last_report_time, _last_cycle_report, _last_watchdog_ping

    # send startup message
    send_telegram(f"🚀 Bot started at {get_local_time().strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"Sources: 1inch={'yes' if ONEINCH_API_KEY else 'no'}, UniswapGraph={'yes' if GRAPH_API_KEY else 'no'}, Dexscreener=yes")

    base_tokens = ["USDT"]
    report_interval = REPORT_INTERVAL

    while True:
        cycle_start = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "skipped_reasons": {},
            "profit_gt_min_skipped": [],
            "dexscreener_skipped": [],
            "total_checked_pairs": 0,
            "successful_trades": 0,
        }

        clean_ban_list()

        for base_symbol in base_tokens:
            base_addr = TOKENS.get(base_symbol).lower()
            base_dec = DECIMALS.get(base_symbol, 18)
            sell_amount_units = int(SELL_AMOUNT_USD * (10 ** base_dec))

            for token_symbol, token_addr in TOKENS.items():
                token_addr = token_addr.lower()
                if token_symbol == base_symbol:
                    continue

                profiler["total_checked_pairs"] += 1
                key = (base_symbol, token_symbol)

                # ban skip
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    reason = ban_list[key]["reason"]
                    profiler["skipped_reasons"].setdefault("Banned", []).append(f"{base_symbol}->{token_symbol} ({reason})")
                    continue

                # RSI check for certain tokens
                rsi = None
                if token_symbol in RSI_TOKENS:
                    candles = get_token_candles(token_addr)
                    if not candles:
                        profiler["dexscreener_skipped"].append((token_symbol, "Dexscreener candles missing"))
                        profiler["skipped_reasons"].setdefault("Dexscreener candles missing", []).append(token_symbol)
                    else:
                        try:
                            closes = [float(c["close"]) for c in candles if "close" in c]
                        except Exception:
                            closes = []
                        rsi = calculate_rsi(closes)
                        if rsi is not None and rsi > 70:
                            profiler["skipped_reasons"].setdefault("RSI>70", []).append(token_symbol)
                            continue

                # Primary quote attempt
                quote_entry = query_1inch_price(base_addr, token_addr, sell_amount_units, f"{base_symbol}->{token_symbol}")
                source_tag = "Unknown"
                if not quote_entry:
                    # we tried, either 1inch failed -> univ3 attempted inside function -> dexscreener fallback may have been used.
                    profiler["skipped_reasons"].setdefault("No quote", []).append(f"{base_symbol}->{token_symbol}")
                    continue
                else:
                    source_tag = quote_entry.get("source", "Unknown")

                # parse buy amount
                try:
                    buy_amount_token = int(quote_entry.get("buyAmount", 0))
                except Exception:
                    ban_pair(key, "Invalid buyAmount", duration=900)
                    profiler["skipped_reasons"].setdefault("Invalid buyAmount", []).append(f"{base_symbol}->{token_symbol}")
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, "No liquidity", duration=120)
                    profiler["skipped_reasons"].setdefault("No liquidity", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # EXIT quote (estimate)
                quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
                if not quote_exit:
                    profiler["skipped_reasons"].setdefault("No quote (exit)", []).append(f"{token_symbol}->{base_symbol}")
                    continue
                try:
                    back_units = int(quote_exit.get("buyAmount", 0))
                except Exception:
                    back_units = 0

                # compute profit
                _, _, profit_est_units = compute_profit_percent_by_units(sell_amount_units, back_units, base_symbol, token_symbol)
                if profit_est_units is None:
                    profiler["skipped_reasons"].setdefault("Profit calc error", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # sanity checks
                if abs(profit_est_units) > 1e6:
                    profiler["skipped_reasons"].setdefault("Unrealistic profit", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # decide if signal
                if profit_est_units < MIN_PROFIT_PERCENT:
                    profiler["profit_gt_min_skipped"].append((token_symbol, f"{profit_est_units:.2f}% < {MIN_PROFIT_PERCENT}%"))
                    profiler["skipped_reasons"].setdefault(f"Low profit < {MIN_PROFIT_PERCENT}", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # platforms string
                platforms_used = extract_platforms(quote_entry.get("protocols")) if quote_entry.get("protocols") else []
                if not platforms_used and source_tag == "UniswapV3":
                    platforms_used = ["UniswapV3(est)"]
                platforms_str = ", ".join(platforms_used) if platforms_used else source_tag

                # Pre-trade message (with source)
                send_telegram(
                    f"📣 Предварительный сигнал\nPAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Source: {source_tag}\nPlatforms: {platforms_str}\n"
                    f"EST Profit: {profit_est_units:.2f}%\nRSI: {safe_format_rsi(rsi)}\n"
                    f"Plan: hold ~3-8 min, target {MIN_PROFIT_PERCENT:.2f}%, stop {STOP_LOSS_PERCENT:.2f}%"
                )

                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # start monitoring thread; pass source_tag for messages
                start_monitor(sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, 3 * 60, buy_amount_token, source_tag)

                # cooldown to avoid duplicate signaling
                ban_pair(key, "Post-trade cooldown", duration=900)

        # REPORT every REPORT_INTERVAL
        now_ts = time.time()
        if now_ts - last_report_time >= report_interval:
            clean_ban_list()
            report_lines = []
            report_lines.append("===== PROFILER REPORT =====")
            report_lines.append(f"⏱ Cycle time: {time.time() - cycle_start:.2f} sec")
            report_lines.append(f"🚫 Pairs in ban-list: {len(ban_list)}")
            if ban_list:
                report_lines.append("Ban-list details:")
                for pair, info in ban_list.items():
                    left = int(info["duration"] - (now_ts - info["time"]))
                    if left < 0:
                        left = 0
                    report_lines.append(f"  - {pair[0]} -> {pair[1]}: reason - {info['reason']}, left: {left}s")
            report_lines.append(f"💤 Cooldown skips: {profiler['cooldown_skips']}")
            report_lines.append(f"💰 Pairs with profit > {MIN_PROFIT_PERCENT}% (but not sent): {len(profiler['profit_gt_min_skipped'])}")
            if profiler["profit_gt_min_skipped"]:
                for s in profiler["profit_gt_min_skipped"][:30]:
                    report_lines.append(f"   - {s[0]}: {s[1]}")
            if profiler["dexscreener_skipped"]:
                report_lines.append("🔎 Dexscreener issues:")
                for t,reason in profiler["dexscreener_skipped"]:
                    report_lines.append(f"   - {t}: {reason}")
            if profiler["skipped_reasons"]:
                report_lines.append("🧹 Skip reasons:")
                for reason, items in profiler["skipped_reasons"].items():
                    report_lines.append(f"   - {reason}: {', '.join(items[:200])}")
            report_lines.append(f"✔️ Success signals this cycle: {profiler['successful_trades']}")
            report_lines.append(f"🔍 Total checked pairs: {profiler['total_checked_pairs']}")
            report_lines.append("===========================")
            send_telegram("\n".join(report_lines))
            # update last report time
            globals()['last_report_time'] = now_ts
            globals()['_last_cycle_report'] = now_ts

        # watchdog ping if no reports for long
        if now_ts - last_report_time > 2 * report_interval and now_ts - _last_watchdog_ping > 60:
            send_telegram("⚠️ No reports generated for a long time. Possibly stuck loop or upstream timeouts.")
            _last_watchdog_ping = now_ts

        time.sleep(0.5)

# ===================== ENTRY =====================
if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Stopped by user")
    except Exception as e:
        send_telegram(f"❗ Bot crashed: {e}")
        if DEBUG_MODE:
            raise
            
