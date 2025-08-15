# -*- coding: utf-8 -*-
import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

# ===================== ENV & SETTINGS =====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# Таймзона как ты используешь
LONDON_TZ = pytz.timezone("Europe/London")

# Торговые настройки анализа/симуляции
SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))

# Порог отправки сигнала (минимальная оценка прибыли)
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))       # %, по умолчанию 1.0
STOP_LOSS_PERCENT  = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))       # %, по умолчанию -1.0

# Интервал отчёта в секундах
REPORT_INTERVAL = int(float(os.getenv("REPORT_INTERVAL", "900")))        # 900 сек = 15 мин

# Сколько запросов в секунду не превышаем
MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

# 1inch API key (не обязателен)
ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY", "").strip()

# Флаг: если 1inch не даёт маршрут base->token, попробуем обратный для бана
TRY_REVERSE_ON_NO_ROUTE = True

# ===================== ТОКЕНЫ (Polygon, chainId=137) =====================
TOKENS = {
    "USDT":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "USDC":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "DAI":    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX":   "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "wstETH": "0x03b54a6e9a984069379fae1a4fc4dbae93b3bccd",
    "BET":    "0xbf7970d56a150cd0b60bd08388a4a75a27777777",  # оставляю как в твоём наборе
    "WPOL":   "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
    "tBTC":   "0x236aa50979d5f3de3bd1eeb40e81137f22ab794b",
    "SAND":   "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":    "0x714db550b574b3e927af3d93e26127d15721d4c2",
    "LINK":   "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "EMT":    "0x708383ae0e80e75377d664e4d6344404dede119a",
    "AAVE":   "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LDO":    "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "POL":    "0x0000000000000000000000000000000000001010",  # MATIC(POL) pseudo-address
    "WETH":   "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "SUSHI":  "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
}

DECIMALS = {
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18, "wstETH": 18,
    "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18,
    "WETH": 18, "SUSHI": 18,
}

# Обратный словарь: адрес -> символ
ADDRESS_TO_SYMBOL = {addr.lower(): sym for sym, addr in TOKENS.items()}

# Токены, для которых пытаемся считать RSI
RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}

PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

# ===================== API ENDPOINTS =====================
CHAIN_ID = 137
ONEINCH_V6_DEV = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
# Uniswap v3 Polygon subgraph (TheGraph)
UNISWAP_V3_POLY = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"

# ===================== LIMITS & RUNTIME STATE =====================
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = 120  # 2 мин
BAN_OTHER_REASON_DURATION = 900  # 15 мин

ban_list = {}        # (base_symbol, token_symbol) -> {"time": ts, "reason": str, "duration": int}
tracked_trades = {}  # (base_symbol, token_symbol) -> last_ts
last_report_time = 0

# ===================== UTILS =====================
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram muted]\n", msg)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if r.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] HTTP {r.status_code}: {r.text}")
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
        print(f"[BAN] {key} -> {reason} (dur={duration}s)")

def clean_ban_list():
    now_ts = time.time()
    for pair in list(ban_list.keys()):
        info = ban_list[pair]
        if now_ts - info["time"] > info["duration"]:
            if DEBUG_MODE:
                print(f"[BAN] Remove expired: {pair} (reason={info['reason']})")
            ban_list.pop(pair, None)

# ===================== DEXSCREENER HELPERS =====================
def fetch_dexscreener_pairs(token_addr):
    try:
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        if DEBUG_MODE:
            print(f"[Dexscreener] HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Error: {e}")
    return None

def get_token_usd_price_from_dxs(token_addr):
    data = fetch_dexscreener_pairs(token_addr)
    if not data:
        return None
    for p in data.get("pairs", []):
        price_usd = p.get("priceUsd")
        if price_usd:
            try:
                return float(price_usd)
            except Exception:
                continue
    return None

def get_token_candles(token_addr):
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
    except Exception:
        return "—"

# ===================== PROFIT IN USD (оценка) =====================
def compute_profit_percent_by_units(sell_amount_units, final_amount_units, base_symbol="USDT", token_symbol=None):
    try:
        base_price_usd = get_token_usd_price_from_dxs(TOKENS.get(base_symbol))
        base_dec = DECIMALS.get(base_symbol, 18)
        sell_tokens = sell_amount_units / (10 ** base_dec)
        usd_sell = sell_tokens * base_price_usd if base_price_usd is not None else None
    except Exception:
        usd_sell = None

    token_price_usd = get_token_usd_price_from_dxs(TOKENS.get(token_symbol)) if token_symbol else None
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

# ===================== ROUTE PARSER =====================
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

# ===================== 1INCH + FALLBACK =====================
def query_1inch_price(sell_token_addr: str, buy_token_addr: str, sell_amount_units: int, symbol_pair=""):
    """
    Возвращает quote-объект формата:
      {"buyAmount": "<int as str>", "protocols": [...], "route": {"fills": []}}
    либо None и банит пару.
    Каскад:
      1) v6 dev с ключом (если дан),
      2) v6 dev без ключа,
      3) v5 публичный,
      4) Fallback на Uniswap v3 Polygon subgraph (TheGraph).
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else ("?", "?")

    params = {
        "src": sell_token_addr,
        "dst": buy_token_addr,
        "amount": str(sell_amount_units),
        "disableEstimate": "true",
        "includeTokensInfo": "false",
        "includeProtocols": "true",
        "includeGas": "false",
    }

    endpoints = []
    if ONEINCH_API_KEY:
        endpoints.append(("v6_dev_auth", ONEINCH_V6_DEV, {"Authorization": f"Bearer {ONEINCH_API_KEY}"}))
    endpoints.append(("v6_dev_noauth", ONEINCH_V6_DEV, {}))
    endpoints.append(("v5_public", ONEINCH_V5_PUBLIC, {}))

    last_err_snippet = None

    for name, url, headers in endpoints:
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=12)
        except Exception as e:
            last_err_snippet = f"HTTP error: {e}"
            continue

        if resp.status_code == 200:
            # Иногда 1inch отдаёт «Invalid JSON» — ловим и логируем полный текст
            try:
                data = resp.json()
            except Exception:
                raw = resp.text[:1000]
                last_err_snippet = f"Invalid JSON. Raw: {raw}"
                if DEBUG_MODE:
                    print(f"[1inch] Invalid JSON for {symbol_pair} at {url}\nRaw:\n{raw}")
                # Ретрай — переходим к следующему эндпоинту каскада
                continue

            buy_amount = data.get("toTokenAmount") or data.get("dstAmount")
            if not buy_amount:
                last_err_snippet = "No buy amount in response"
                continue

            try:
                if int(buy_amount) == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    if DEBUG_MODE:
                        print(f"[1inch] Zero buy amount for {symbol_pair}")
                    return None
            except Exception:
                pass

            return {
                "buyAmount": str(buy_amount),
                "protocols": data.get("protocols") or [],
                "route": {"fills": []}
            }

        elif resp.status_code in (400, 404, 422):
            ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[1inch] {resp.status_code} for {symbol_pair}")
            return None
        else:
            try:
                last_err_snippet = resp.text[:300].replace("\n", " ")
            except Exception:
                last_err_snippet = f"HTTP {resp.status_code}"
            continue

    # ---- FALLBACK: Uniswap v3 Polygon subgraph ----
    try:
        if DEBUG_MODE:
            print(f"[Fallback] Trying Uniswap v3 subgraph for {symbol_pair}")
        base_addr_l = sell_token_addr.lower()
        token_addr_l = buy_token_addr.lower()

        # bundle может называться по-разному; токены — derivedETH/derivedMatic/derivedWMATIC
        query = f"""
        {{
          t1: token(id: "{token_addr_l}") {{
            derivedETH
            derivedMatic
            derivedWMATIC: derivedMatic
            decimals
          }}
          t2: token(id: "{base_addr_l}") {{
            derivedETH
            derivedMatic
            derivedWMATIC: derivedMatic
            decimals
          }}
          b: bundles(first: 1) {{
            ethPriceUSD
            maticPriceUSD
          }}
        }}
        """
        r = requests.post(UNISWAP_V3_POLY, json={"query": query}, timeout=12)
        if r.status_code != 200:
            if DEBUG_MODE:
                print(f"[Fallback] Subgraph HTTP {r.status_code}: {r.text[:300]}")
            return None
        j = r.json()
        t1 = (j.get("data") or {}).get("t1") or {}
        t2 = (j.get("data") or {}).get("t2") or {}
        b  = (j.get("data") or {}).get("b") or []
        b  = b[0] if b else {}

        def _num(x):
            try:
                return float(x)
            except Exception:
                return None

        t1_der = _num(t1.get("derivedETH")) or _num(t1.get("derivedMatic")) or _num(t1.get("derivedWMATIC"))
        t2_der = _num(t2.get("derivedETH")) or _num(t2.get("derivedMatic")) or _num(t2.get("derivedWMATIC"))
        eth_usd = _num(b.get("ethPriceUSD")) or _num(b.get("maticPriceUSD"))

        if t1_der and t2_der and eth_usd:
            t1_usd = t1_der * eth_usd
            t2_usd = t2_der * eth_usd

            base_sym  = ADDRESS_TO_SYMBOL.get(base_addr_l)
            token_sym = ADDRESS_TO_SYMBOL.get(token_addr_l)
            base_dec  = DECIMALS.get(base_sym, 18) if base_sym else int(t2.get("decimals") or 18)
            token_dec = DECIMALS.get(token_sym, 18) if token_sym else int(t1.get("decimals") or 18)

            # base_units -> base_tokens -> USD -> token_tokens -> token_units
            base_tokens = sell_amount_units / (10 ** base_dec)
            usd_value   = base_tokens * t2_usd
            token_tokens = usd_value / t1_usd
            buy_amount_units = int(token_tokens * (10 ** token_dec))

            return {
                "buyAmount": str(buy_amount_units),
                "protocols": [["UniswapV3"]],
                "route": {"fills": []}
            }
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Fallback Error] Uniswap v3 subgraph: {e}")

    # всё плохо — баним по последней причине
    ban_pair(key, last_err_snippet or "No route/No quote")
    return None

# ===================== МОНИТОРИНГ ОКНА УДЕРЖАНИЯ =====================
def make_monitor(buy_amount_token):
    """
    Возвращает функцию мониторинга окна удержания.
    buy_amount_token — сколько токена «получили» на входе (в юнитах токена).
    """
    def _run(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec):
        check_interval = 15
        started = time.time()
        alerted_take = False
        alerted_stop = False

        while True:
            elapsed = time.time() - started
            if elapsed >= timing_sec:
                # финальная проверка
                quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                    except Exception:
                        final_amount_exit = None
                    if final_amount_exit:
                        _, _, actual_profit = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit, base_symbol, token_symbol)
                        send_telegram(
                            f"⏳ Время удержания вышло\n"
                            f"Текущая прибыль: {actual_profit:.2f}%\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"PAIR: {base_symbol}->{token_symbol}"
                        )
                break

            # промежуточная проверка
            quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
            if quote_exit and "buyAmount" in quote_exit:
                try:
                    final_amount_exit = int(quote_exit["buyAmount"])
                except Exception:
                    final_amount_exit = None
                if final_amount_exit:
                    _, _, actual_profit = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit, base_symbol, token_symbol)
                    if actual_profit is not None:
                        if (not alerted_take) and actual_profit >= MIN_PROFIT_PERCENT:
                            send_telegram(f"🎯 Цель достигнута: {actual_profit:.2f}% по {token_symbol}")
                            alerted_take = True
                        if (not alerted_stop) and actual_profit <= STOP_LOSS_PERCENT:
                            send_telegram(f"⚠️ Стоп-лосс: {actual_profit:.2f}% по {token_symbol}")
                            alerted_stop = True

            time.sleep(check_interval)
    return _run

# ===================== MAIN STRATEGY =====================
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (analysis mode, 1inch + fallback).")
    base_tokens = ["USDT"]  # можно расширить
    last_request_time = 0

    # важное исправление — не затеняем глобальную переменную
    report_interval = REPORT_INTERVAL if isinstance(REPORT_INTERVAL, int) else int(REPORT_INTERVAL)

    while True:
        cycle_start = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "skipped_reasons": {},          # <== собираем причины отсева
            "profit_gt_min_skipped": [],
            "dexscreener_skipped": [],
            "total_checked_pairs": 0,
            "successful_trades": 0,
        }

        clean_ban_list()

        for base_symbol in base_tokens:
            base_addr = TOKENS.get(base_symbol).lower()
            base_dec  = DECIMALS.get(base_symbol, 18)
            sell_amount_units = int(SELL_AMOUNT_USD * (10 ** base_dec))

            for token_symbol, token_addr in TOKENS.items():
                token_addr = token_addr.lower()
                if token_symbol == base_symbol:
                    continue

                profiler["total_checked_pairs"] += 1
                key = (base_symbol, token_symbol)

                # бан-лист
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    profiler["skipped_reasons"].setdefault("Ban list", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # cooldown после «виртуальной сделки»
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
                    profiler["cooldown_skips"] += 1
                    profiler["skipped_reasons"].setdefault("Cooldown", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # pace по API
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # RSI (мягкий): если свечей нет — не стопим сделку, просто отмечаем
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
                            profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f} (>70)"))
                            profiler["skipped_reasons"].setdefault("RSI>70", []).append(token_symbol)
                            continue

                # котировка входа: base -> token
                quote_entry = query_1inch_price(base_addr, token_addr, sell_amount_units, f"{base_symbol}->{token_symbol}")
                if not quote_entry:
                    if TRY_REVERSE_ON_NO_ROUTE:
                        if DEBUG_MODE:
                            print(f"[INFO] Reverse check {token_symbol}->{base_symbol}")
                        elapsed = time.time() - last_request_time
                        if elapsed < REQUEST_INTERVAL:
                            time.sleep(REQUEST_INTERVAL - elapsed)
                        last_request_time = time.time()
                        _ = query_1inch_price(token_addr, base_addr, sell_amount_units, f"{token_symbol}->{base_symbol}")
                    profiler["skipped_reasons"].setdefault("No quote", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                try:
                    buy_amount_token = int(quote_entry.get("buyAmount", 0))
                except Exception:
                    ban_pair(key, "Invalid buyAmount in quote", duration=BAN_OTHER_REASON_DURATION)
                    profiler["skipped_reasons"].setdefault("Invalid buyAmount", []).append(f"{base_symbol}->{token_symbol}")
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    profiler["skipped_reasons"].setdefault("No liquidity", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # грубая оценка прибыли в процентах (как фильтр)
                profit_estimate = ((buy_amount_token / sell_amount_units) - 1) * 100
                if abs(profit_estimate) > 1e6:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Unrealistic profit estimate"))
                    profiler["skipped_reasons"].setdefault("Unrealistic profit", []).append(token_symbol)
                    continue
                if profit_estimate < MIN_PROFIT_PERCENT:
                    profiler["profit_gt_min_skipped"].append((token_symbol, f"Profit {profit_estimate:.2f}% < {MIN_PROFIT_PERCENT}%"))
                    profiler["skipped_reasons"].setdefault(f"Low profit < {MIN_PROFIT_PERCENT}%", []).append(token_symbol)
                    continue

                platforms_used = []
                if quote_entry.get("protocols"):
                    platforms_used = extract_platforms(quote_entry.get("protocols"))
                if not platforms_used:
                    # не стопим из-за отсутствия parse — просто отметим
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms parsed"))
                    # можно continue, если хочешь жёстко требовать платформы

                # тайминг (как у тебя, зависит от RSI)
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                # сообщение о сделке (pre)
                time_start = get_local_time().strftime("%H:%M")
                time_sell  = (get_local_time() + datetime.timedelta(seconds=timing_sec)).strftime("%H:%M")
                pre_msg = (
                    f"{base_symbol} -> {token_symbol} -> {base_symbol} 📈\n"
                    f"TIMING: {timing_min} MIN ⌛️\n"
                    f"TIME FOR START: {time_start}\n"
                    f"TIME FOR SELL: {time_sell}\n"
                    f"PROFIT ESTIMATE: {profit_estimate:.2f}% 💸\n"
                    f"RSI: {safe_format_rsi(rsi)}\n"
                    f"PLATFORMS: {', '.join(platforms_used) if platforms_used else '—'} 📊\n"
                    f"https://1inch.io/#/polygon/swap/{base_addr}/{token_addr}"
                )
                send_telegram(pre_msg)

                profiler["successful_trades"] += 1
                tracked_trades[key] = time.time()

                # мониторинг окна удержания
                monitor = make_monitor(buy_amount_token)
                monitor(sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec)

                # post-trade cooldown
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # периодический отчёт — всегда, даже без сделок
        now_ts = time.time()
        if now_ts - last_report_time >= report_interval:
            clean_ban_list()

            banned_pairs_lines = []
            for pair, info in ban_list.items():
                seconds_left = int(info["duration"] - (now_ts - info["time"]))
                if seconds_left < 0:
                    seconds_left = 0
                banned_pairs_lines.append(f"  - {pair[0]} -> {pair[1]}: причина - {info['reason']}, осталось: {seconds_left}s")

            report_msg = (
                f"===== PROFILER REPORT =====\n"
                f"⏱ Время полного цикла: {time.time() - cycle_start:.2f} сек\n"
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

            if profiler["dexscreener_skipped"]:
                report_msg += "🔎 Пропущенные (dexscreener/price issues):\n"
                for sym, reason in profiler["dexscreener_skipped"]:
                    report_msg += f"   - {sym}: {reason}\n"

            if profiler["skipped_reasons"]:
                report_msg += "🧹 Причины отсева:\n"
                for reason, items in profiler["skipped_reasons"].items():
                    listed = ", ".join(items[:20])
                    more = "" if len(items) <= 20 else f" (+{len(items)-20} ещё)"
                    report_msg += f"   - {reason}: {listed}{more}\n"

            report_msg += f"✔️ Успешных сигналов за цикл: {profiler['successful_trades']}\n"
            report_msg += f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}\n"
            report_msg += "===========================\n"

            send_telegram(report_msg)
            last_report_time = now_ts

        time.sleep(0.5)

# ===================== ENTRYPOINT =====================
if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            print(f"[CRASH] {e}")
            
