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
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# Feature flags and parameters (can be changed via env)
REAL_TRADING = os.getenv("REAL_TRADING", "False").lower() == "true"  # аналитику оставляем, реальных ордеров нет
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "900"))  # сек (по умолчанию 15 мин)
SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))         # сколько USD продаём в базовой валюте
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))  # минимальная профитность %
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))   # стоп-лосс в %, отрицательное число
SLIPPAGE_PERCENT = float(os.getenv("SLIPPAGE_PERCENT", "0.01"))     # 0.01 = 1% (используем для 1inch quote)
TRY_REVERSE_ON_NO_ROUTE = os.getenv("TRY_REVERSE_ON_NO_ROUTE", "True").lower() == "true"

# 1inch API (ключ опционален)
ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY", "").strip()

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
# 1inch quote endpoints (попробуем по очереди: dev v6 (с ключом/без), потом v5 публичный)
CHAIN_ID = 137
ONEINCH_V6_DEV = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"

# Dexscreener
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

# ---------------- Platforms helper ----------------
def extract_platforms(protocols_field):
    """
    Пробует достать список платформ из ответа 1inch.
    Ожидаем, что там может быть список шагов/маршрутов с указанием DEX.
    Возвращает список уникальных человеческих названий из PLATFORMS.
    """
    platforms_used = []
    try:
        # 1inch может возвращать массив 'protocols' со списком массивов шагов
        # Пройдёмся и попытаемся извлечь названия источников.
        for route in protocols_field or []:
            for step in route or []:
                name = step.get("name", "") or step.get("id", "") or ""
                for short, human in PLATFORMS.items():
                    if short.lower() in name.lower():
                        if human not in platforms_used:
                            platforms_used.append(human)
    except Exception:
        pass
    return platforms_used

# ---------------- 1inch price query ----------------
def query_1inch_price(sell_token: str, buy_token: str, sell_amount: int, symbol_pair=""):
    """
    Запрос к 1inch quote.
    Возвращает dict (json) при успехе, None при ошибке (и банит пару).
    Порядок попыток:
      1) dev v6 с ключом (если задан),
      2) dev v6 без ключа,
      3) v5 публичный.
    """
    key = tuple(symbol_pair.split("->")) if symbol_pair else (sell_token, buy_token)

    params = {
        "src": sell_token,
        "dst": buy_token,
        "amount": str(sell_amount),
        "includeTokensInfo": "true",
        "includeProtocols": "true",
        "slippage": str(int(SLIPPAGE_PERCENT * 100))  # 1% -> "1"
    }

    # Попробуем по очереди несколько эндпоинтов
    attempts = []

    headers = {"Accept": "application/json"}
    if ONEINCH_API_KEY:
        headers_with_key = {**headers, "Authorization": f"Bearer {ONEINCH_API_KEY}"}
        attempts.append((ONEINCH_V6_DEV, headers_with_key))
    attempts.append((ONEINCH_V6_DEV, headers))
    attempts.append((ONEINCH_V5_PUBLIC, headers))

    last_err_snippet = ""
    for url, hdrs in attempts:
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=12)
        except requests.exceptions.RequestException as e:
            last_err_snippet = f"Request exception: {e}"
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                last_err_snippet = "Invalid JSON"
                continue

            # Проверим наличие нужных полей
            # В разных версиях поле может называться 'toTokenAmount' или 'dstAmount'
            buy_amount = data.get("toTokenAmount") or data.get("dstAmount")
            if not buy_amount:
                last_err_snippet = "No buy amount in response"
                continue

            # Доп. проверка на нулевую ликвидность
            try:
                if int(buy_amount) == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    if DEBUG_MODE:
                        print(f"[1inch] Zero buy amount for {symbol_pair}")
                    return None
            except Exception:
                pass

            # Нормализуем ответ под формат, похожий на прежний
            standardized = {
                "buyAmount": str(buy_amount),
                "protocols": data.get("protocols") or [],
                "route": {"fills": []}  # для совместимости со старой логикой (если понадобится)
            }
            return standardized

        elif resp.status_code in (400, 404, 422):
            # отсутствие маршрута / неправильные параметры — баним коротко
            ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[1inch] {resp.status_code} for {symbol_pair}")
            return None
        else:
            try:
                last_err_snippet = resp.text[:300].replace("\n", " ")
            except Exception:
                last_err_snippet = f"HTTP {resp.status_code}"

    # Если все попытки не удались
    ban_pair(key, f"1inch error: {last_err_snippet}", duration=BAN_OTHER_REASON_DURATION)
    if DEBUG_MODE:
        print(f"[1inch] Error for {symbol_pair}: {last_err_snippet}")
    return None

# ---------------- Helpers: profit calc & guards ----------------
def safe_format_rsi(rsi):
    return f"{rsi:.2f}" if (rsi is not None) else "N/A"

def compute_profit_percent_by_units(sell_amount_units, final_amount_units):
    try:
        # оба — сырые целочисленные юниты одной и той же базы (sell denom)
        return ((final_amount_units / sell_amount_units) - 1) * 100
    except Exception:
        return None

def compute_profit_usd(sell_amount_units, final_amount_units, base_symbol, token_symbol):
    """
    Попытка вычислить прибыль в USD:
    - sell_amount_units (в base_symbol) -> USD (для USDT/USDC ≈ 1:1)
    - final_amount_units — в token_symbol -> USD (через Dexscreener price)
    Возвращает (usd_sell, usd_final, profit_percent_usd) или (None, None, None)
    """
    try:
        base_dec = DECIMALS.get(base_symbol, 18)
        usd_sell = (sell_amount_units / (10 ** base_dec))  # если base — USDT/USDC, это уже USD
    except Exception:
        usd_sell = None

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

# ---------------- Monitoring helper ----------------
def monitor_trade_window(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec):
    """
    Мониторинг цены выхода в течение timing_sec.
    Каждые 15 сек опрашиваем 1inch на котировку выхода token->base для buy_amount_token.
    Отправляем в TG:
      - 🎯 при достижении MIN_PROFIT_PERCENT,
      - ⚠️ при достижении STOP_LOSS_PERCENT,
      - ⏳ по истечении окна — текущую прибыль.
    """
    check_interval = 15  # сек
    started = time.time()
    alerted_take = False
    alerted_stop = False

    # Для мониторинга нам нужно знать, за сколько токена мы "вошли".
    # Здесь мы используем те же условия, что и при входе: получили buyAmount на входе.
    # Но эта функция вызывается ПОСЛЕ отправки pre_msg и получения входной котировки.
    # Мы пробросим сюда значение buy_amount_token через внешнюю замыкалку — вернём функцию-замыкание.
    pass

# Мы реализуем monitor как фабрику, чтобы передать внутрь buy_amount_token
def make_monitor(buy_amount_token):
    def _run(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec):
        check_interval = 15
        started = time.time()
        alerted_take = False
        alerted_stop = False

        while True:
            elapsed = time.time() - started
            if elapsed >= timing_sec:
                # финальная проверка перед выходом по времени
                quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
                if quote_exit and "buyAmount" in quote_exit:
                    try:
                        final_amount_exit = int(quote_exit["buyAmount"])
                    except Exception:
                        final_amount_exit = None
                    if final_amount_exit:
                        actual_profit = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit)
                        msg = (
                            f"⏳ Время удержания вышло\n"
                            f"Текущая прибыль: {actual_profit:.2f}%\n"
                            f"Time: {get_local_time().strftime('%H:%M')}\n"
                            f"Token: {token_symbol}"
                        )
                        send_telegram(msg)
                else:
                    send_telegram(f"⏳ Время удержания вышло\nНе удалось обновить котировку выхода для {token_symbol}")
                break

            # промежуточная проверка
            quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
            if quote_exit and "buyAmount" in quote_exit:
                try:
                    final_amount_exit = int(quote_exit["buyAmount"])
                except Exception:
                    final_amount_exit = None
                if final_amount_exit:
                    actual_profit = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit)
                    if actual_profit is not None:
                        if (not alerted_take) and actual_profit >= MIN_PROFIT_PERCENT:
                            send_telegram(f"🎯 Цель достигнута: {actual_profit:.2f}% по {token_symbol}")
                            alerted_take = True
                        if (not alerted_stop) and actual_profit <= STOP_LOSS_PERCENT:
                            send_telegram(f"⚠️ Стоп-лосс: {actual_profit:.2f}% по {token_symbol}")
                            alerted_stop = True
            # Пауза до следующей проверки
            time.sleep(check_interval)
    return _run

# ---------------- Main strategy ----------------
def run_real_strategy():
    global last_report_time
    send_telegram("🤖 Bot started (analysis mode, 1inch).")
    base_tokens = ["USDT"]
    last_request_time = 0

    # избегаем "cannot access local variable ..." — работаем с локальной копией
    report_interval = REPORT_INTERVAL if isinstance(REPORT_INTERVAL, int) else int(REPORT_INTERVAL)

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
                        continue  # без свечей RSI — пропуск
                    else:
                        # extract close prices
                        try:
                            closes = [float(c["close"]) for c in candles if "close" in c]
                        except Exception:
                            closes = []
                        rsi = calculate_rsi(closes)
                        if rsi is not None and rsi > 70:
                            profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f} (>70)"))
                            # do not ban, just skip
                            continue

                # primary quote (base -> token) via 1inch
                quote_entry = query_1inch_price(base_addr, token_addr, sell_amount_units, f"{base_symbol}->{token_symbol}")
                if not quote_entry:
                    # если 1inch вернул None, он уже обработал бан/лог; попытаться обратный маршрут опционально
                    if TRY_REVERSE_ON_NO_ROUTE:
                        if DEBUG_MODE:
                            print(f"[INFO] Trying reverse check for {token_symbol}->{base_symbol}")
                        elapsed = time.time() - last_request_time
                        if elapsed < REQUEST_INTERVAL:
                            time.sleep(REQUEST_INTERVAL - elapsed)
                        last_request_time = time.time()
                        reverse = query_1inch_price(token_addr, base_addr, sell_amount_units, f"{token_symbol}->{base_symbol}")
                        if reverse and DEBUG_MODE:
                            print(f"[INFO] Reverse direction available for {token_symbol}->{base_symbol}")
                    continue

                # parse buyAmount (amount of token we would receive)
                try:
                    buy_amount_token = int(quote_entry.get("buyAmount", 0))
                except Exception:
                    ban_pair(key, "Invalid buyAmount in 1inch response", duration=BAN_OTHER_REASON_DURATION)
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    continue

                # estimate profit in raw units (на самом деле это не арбитраж, но фильтр оставим как в твоём коде)
                profit_estimate = ((buy_amount_token / sell_amount_units) - 1) * 100
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

                if not platforms_used:
                    # если не распознали — всё равно не блокируем; просто пометим причину
                    profiler["profit_gt_min_skipped"].append((token_symbol, "No supported platforms"))
                    # можно продолжать, если хочешь требовать платформы — тогда continue
                    continue

                # compute human timing (как и раньше, зависящее от RSI)
                timing_min = 3
                if rsi is not None:
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                # build and send preliminary trade message (как у тебя)
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

                # Мониторинг окна удержания (вместо тупого sleep)
                monitor = make_monitor(buy_amount_token)
                monitor(sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec)

                # после «виртуальной сделки» — пост-кулин (15 мин), как раньше
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # periodic report every report_interval seconds
        now_ts = time.time()
        if now_ts - last_report_time >= report_interval:
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
            report_msg += f"✔️ Успешных сигналов за цикл: {profiler['successful_trades']}\n"
            report_msg += f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}\n"
            report_msg += "===========================\n"

            send_telegram(report_msg)
            last_report_time = now_ts

        # small sleep to avoid tight-loop; main pacing is by REQUEST_INTERVAL and report_interval
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
            
