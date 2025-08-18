# -*- coding: utf-8 -*-
import os
import time
import datetime
import threading
import requests
import pytz
from dotenv import load_dotenv
from math import isfinite

load_dotenv()

# ===================== ENV & SETTINGS =====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

# таймзона для времени в отчётах
LONDON_TZ = pytz.timezone("Europe/London")

# базовые параметры торговли / фильтров
SELL_AMOUNT_USD = float(os.getenv("SELL_AMOUNT_USD", "50"))

MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))  # минимальная целевая прибыль для сигнала
STOP_LOSS_PERCENT  = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))   # стоп-лосс при мониторинге сделки
REPORT_INTERVAL    = int(float(os.getenv("REPORT_INTERVAL", "900"))) # 15 минут по умолчанию

# лимитер запросов
MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

# каскад при отсутствии маршрута: пробовать обратное направление
TRY_REVERSE_ON_NO_ROUTE = True

# ключ 1inch (опционально — можно не задавать)
ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY", "").strip() or None

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

# обратное сопоставление адрес -> символ
ADDRESS_TO_SYMBOL = {addr.lower(): sym for sym, addr in TOKENS.items()}
RSI_TOKENS = {"AAVE", "LINK", "EMT", "LDO", "SUSHI", "GMT", "SAND", "tBTC", "wstETH", "WETH"}

PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

# ===================== ENDPOINTS =====================
CHAIN_ID = 137
ONEINCH_V6_DEV = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
UNISWAP_V3_POLY = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"

# ===================== BAN & STATE =====================
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = 120       # 2 мин
BAN_OTHER_REASON_DURATION = 900       # 15 мин по умолчанию

ban_list = {}             # {(from_symbol, to_symbol): {"time": ts, "reason": str, "duration": int}}
tracked_trades = {}       # {trade_id: {...}}
last_report_time = time.time()
_last_cycle_report = time.time()
_last_watchdog_ping = 0.0

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

def ban_pair(key, reason, duration=None):
    now_ts = time.time()
    if duration is None:
        if BAN_NO_LIQUIDITY_REASON.lower() in (reason or "").lower() or "404" in (reason or ""):
            duration = BAN_NO_LIQUIDITY_DURATION
        else:
            duration = BAN_OTHER_REASON_DURATION
    ban_list[key] = {"time": now_ts, "reason": reason, "duration": duration}
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
    if not data:
        return None
    for p in data.get("pairs", []):
        if p.get("priceUsd"):
            try:
                return float(p["priceUsd"])
            except:
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

# ===================== Uniswap V3 (subgraph fallback) =====================
def _pick_best_univ3_pool(pools):
    """Выбираем «лучший» пул — по наибольшей ликвидности/TVL."""
    if not pools:
        return None
    def _to_int(x):
        try:
            return int(x)
        except:
            return 0
    # сортируем по liquidity (как есть в сабграфе); если нет — оставим как пришло
    pools_sorted = sorted(pools, key=lambda p: _to_int(p.get("liquidity") or 0), reverse=True)
    return pools_sorted[0]

def _calc_amount_out_from_pool(sell_addr, buy_addr, amount_units, pool):
    """
    Приблизительный расчёт через mid-price пула:
    price (token1 per token0) = (sqrtP/2^96)^2 * 10^(dec0 - dec1)
    fee учитываем через (1 - feeTier/1e6).
    """
    if not pool:
        return None
    t0 = pool.get("token0") or {}
    t1 = pool.get("token1") or {}
    if not (t0 and t1):
        return None

    addr0 = (t0.get("id") or "").lower()
    addr1 = (t1.get("id") or "").lower()
    dec0  = int(t0.get("decimals") or 18)
    dec1  = int(t1.get("decimals") or 18)
    fee_tier = int(pool.get("feeTier") or 3000)

    sqrtP = pool.get("sqrtPrice") or pool.get("sqrtPriceX96")
    try:
        sqrtP = int(sqrtP)
    except:
        return None

    # (sqrtP / 2^96)^2
    Q96 = 2 ** 96
    price_1_per_0 = (sqrtP / Q96) ** 2
    # учёт разницы dec
    price_1_per_0 *= 10 ** (dec0 - dec1)

    if price_1_per_0 <= 0 or not isfinite(price_1_per_0):
        return None

    fee_factor = 1.0 - (fee_tier / 1_000_000.0)  # 3000 -> 0.997

    if sell_addr == addr0 and buy_addr == addr1:
        # продаём token0, покупаем token1
        out_amount = amount_units * price_1_per_0 * fee_factor
    elif sell_addr == addr1 and buy_addr == addr0:
        # продаём token1, покупаем token0
        # цена token0 per token1 = 1 / price_1_per_0
        out_amount = amount_units * (1.0 / price_1_per_0) * fee_factor
    else:
        return None

    try:
        out_int = int(out_amount)
        if out_int < 0:
            return None
        return out_int
    except:
        return None

def univ3_estimate_amount_out(src_addr, dst_addr, amount_units):
    """
    Полноценный fallback на Uniswap V3 (Polygon):
    - ищем все пулы между src и dst
    - берём пул с наибольшей ликвидностью
    - считаем mid-price из sqrtPrice
    - учитываем комиссию пула feeTier
    Возвращает dict {"buyAmount": str, "source": "UniswapV3"} или None.
    """
    try:
        src = src_addr.lower()
        dst = dst_addr.lower()
        # 500 / 3000 / 10000 — стандартные фи-тиеры
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
        resp = requests.post(UNISWAP_V3_POLY, json={"query": query, "variables": variables}, timeout=(7, 15))
        if resp.status_code != 200:
            if DEBUG_MODE:
                print(f"[UniswapV3] HTTP {resp.status_code}: {resp.text[:400]}")
            return None
        data = resp.json()
        pools = (data.get("data") or {}).get("pools") or []
        # отфильтруем строго по паре (исключим случайные другие)
        pools = [p for p in pools if { (p.get("token0") or {}).get("id","").lower(), (p.get("token1") or {}).get("id","").lower() } == {src, dst}]
        if not pools:
            return None

        best = _pick_best_univ3_pool(pools)
        out_units = _calc_amount_out_from_pool(src, dst, amount_units, best)
        if not out_units:
            return None

        return {
            "buyAmount": str(int(out_units)),
            "protocols": [],
            "route": {"fills": []},
            "source": "UniswapV3"
        }

    except Exception as e:
        if DEBUG_MODE:
            print("[UniswapV3] exception:", e)
        return None

# ===================== 1inch =====================
def query_1inch_price(sell_token_addr: str, buy_token_addr: str, sell_amount_units: int, symbol_pair: str = ""):
    """
    Возвращает dict c "buyAmount" (строка) и meta, либо None.
    Каскад:
      1) v6 dev с ключом (если есть)
      2) v6 dev без ключа
      3) v5 публичный
      4) fallback на Uniswap V3 subgraph (реальный)
    При Invalid JSON логируем raw resp.text и идём дальше.
    """
    params = {
        "src": sell_token_addr,
        "dst": buy_token_addr,
        "amount": str(sell_amount_units),
        "disableEstimate": "true",
        "includeTokensInfo": "false",
        "includeProtocols": "true",
        "includeGas": "false",
    }
    attempts = []
    headers_base = {"Accept": "application/json"}
    if ONEINCH_API_KEY:
        attempts.append(("v6_dev_auth", ONEINCH_V6_DEV, {**headers_base, "Authorization": f"Bearer {ONEINCH_API_KEY}"}))
    attempts.append(("v6_dev_noauth", ONEINCH_V6_DEV, headers_base))
    attempts.append(("v5_public", ONEINCH_V5_PUBLIC, headers_base))

    last_err_snippet = None

    for name, url, headers in attempts:
        try:
            pace_requests()
            resp = requests.get(url, params=params, headers=headers, timeout=(5, 10))
        except Exception as e:
            last_err_snippet = f"HTTP error: {e}"
            if DEBUG_MODE:
                print(f"[1inch/{name}] Exception: {e}")
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                raw = resp.text[:1500]
                last_err_snippet = f"Invalid JSON: {raw}"
                if DEBUG_MODE:
                    print(f"[1inch/{name}] Invalid JSON for {symbol_pair}\nRaw:\n{raw}")
                try:
                    sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                    if sp and len(sp)==2:
                        ban_pair(sp, f"1inch invalid JSON: {raw[:200]}")
                except Exception:
                    pass
                continue

            buy_amount = data.get("toTokenAmount") or data.get("dstAmount")
            if not buy_amount:
                last_err_snippet = "No buy amount in response"
                continue
            try:
                if int(buy_amount) == 0:
                    # явное отсутствие маршрута/ликвидности
                    return None
            except Exception:
                pass

            return {
                "buyAmount": str(buy_amount),
                "protocols": data.get("protocols") or [],
                "route": {"fills": []},
                "source": f"1inch:{name}"
            }

        elif resp.status_code in (400, 404, 422):
            try:
                last_err_snippet = f"{resp.status_code}: {resp.text[:400]}"
            except Exception:
                last_err_snippet = str(resp.status_code)
            if DEBUG_MODE:
                print(f"[1inch/{name}] {last_err_snippet} for {symbol_pair}")
            try:
                sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                if sp and len(sp)==2:
                    ban_pair(sp, f"1inch {last_err_snippet}")
            except Exception:
                pass
            return None
        else:
            try:
                last_err_snippet = f"HTTP {resp.status_code}: {resp.text[:400].replace(chr(10),' ')}"
            except Exception:
                last_err_snippet = f"HTTP {resp.status_code}"
            if DEBUG_MODE:
                print(f"[1inch/{name}] {last_err_snippet}")
            try:
                sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
                if sp and len(sp)==2:
                    ban_pair(sp, f"1inch {last_err_snippet}")
            except Exception:
                pass
            continue

    # 1inch не дал котировку — пробуем Uniswap V3 (реальный fallback)
    uni_quote = univ3_estimate_amount_out(sell_token_addr, buy_token_addr, sell_amount_units)
    if uni_quote:
        return uni_quote

    # вообще ничего — вернём None и пометим причину
    if DEBUG_MODE and last_err_snippet:
        print(f"[1inch cascade fail] {symbol_pair}: {last_err_snippet}")
    try:
        sp = tuple(symbol_pair.split("->")) if "->" in symbol_pair else None
        if sp and len(sp)==2 and last_err_snippet:
            ban_pair(sp, f"1inch fail: {last_err_snippet}")
    except Exception:
        pass
    return None

# ===================== Монитор сделки (поток) =====================
def monitor_trade_thread(entry_sell_amount_units, base_addr, token_addr,
                         base_symbol, token_symbol, timing_sec, buy_amount_token):
    """
    Каждые 15 сек запрашиваем котировку в обратную сторону и отслеживаем:
      - достижение цели прибыли
      - достижение стоп-лосса
      - истечение времени удержания
    """
    start_ts = time.time()
    target_profit = MIN_PROFIT_PERCENT
    stop_loss = STOP_LOSS_PERCENT
    last_ping = 0

    while True:
        elapsed = time.time() - start_ts
        if elapsed >= timing_sec:
            # по истечении времени — финальная проверка
            pace_requests()
            quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
            final_amount_exit = None
            try:
                final_amount_exit = int(quote_exit.get("buyAmount", 0)) if quote_exit else None
            except Exception:
                final_amount_exit = None

            if final_amount_exit:
                _, _, actual_profit = compute_profit_percent_by_units(
                    entry_sell_amount_units, final_amount_exit, base_symbol, token_symbol
                )
            else:
                actual_profit = None

            if actual_profit is not None:
                send_telegram(
                    f"⏳ Время удержания вышло\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Параметры: цель {target_profit:.2f}% / стоп {stop_loss:.2f}%\n"
                    f"Фактический результат: {actual_profit:.2f}%"
                )
            else:
                send_telegram(
                    f"⏳ Время удержания вышло, котировки нет\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Параметры: цель {target_profit:.2f}% / стоп {stop_loss:.2f}%"
                )
            return

        # периодический пинг статуса (не спамим чаще раза в минуту)
        if time.time() - last_ping > 60:
            last_ping = time.time()

        # регулярная проверка на стоп/профит
        pace_requests()
        quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
        if not quote_exit:
            time.sleep(10)
            continue

        try:
            out_back = int(quote_exit.get("buyAmount", 0))
        except Exception:
            time.sleep(10)
            continue

        units_in = int(entry_sell_amount_units)
        _, _, profit_pct = compute_profit_percent_by_units(units_in, out_back, base_symbol, token_symbol)

        if profit_pct <= stop_loss:
            send_telegram(
                f"🛑 Стоп-лосс выполнен: {profit_pct:.2f}%\n"
                f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}"
            )
            return

        if profit_pct >= target_profit:
            send_telegram(
                f"✅ Цель прибыли достигнута: {profit_pct:.2f}%\n"
                f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}"
            )
            return

        time.sleep(15)

# ===================== Вспомогательные расчёты =====================
def extract_platforms(protocols):
    names = set()
    try:
        for route in protocols or []:
            for hop in route or []:
                for leg in hop or []:
                    n = leg.get("name") or leg.get("id") or ""
                    if n:
                        names.add(n)
    except Exception:
        pass
    return list(names) if names else []

def compute_profit_percent_by_units(entry_units, back_units, base_symbol, token_symbol):
    # просто сравнение units (без комиссий — как в твоём текущем коде)
    try:
        profit_pct = (back_units / entry_units - 1) * 100
    except Exception:
        profit_pct = None
    return entry_units, back_units, profit_pct

# ===================== ОСНОВНОЙ ЦИКЛ =====================
def run_real_strategy():
    global last_report_time, _last_cycle_report, _last_watchdog_ping

    base_tokens = ["USDT"]  # как было — базовые стэйблы/база
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
            "success_signals": 0,
        }

        now_ts = time.time()
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

                # Бан-лист
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    profiler["skipped_reasons"].setdefault("Ban list", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # Cooldown после недавнего сигнала
                if (token_symbol, base_symbol) in ban_list:
                    dt = now_ts - ban_list[(token_symbol, base_symbol)]["time"]
                    if dt < BAN_OTHER_REASON_DURATION:
                        profiler["cooldown_skips"] += 1
                        profiler["skipped_reasons"].setdefault("Cooldown", []).append(f"{base_symbol}->{token_symbol}")
                        continue

                # RSI
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

                # Котировка входа
                quote_entry = query_1inch_price(base_addr, token_addr, sell_amount_units, f"{base_symbol}->{token_symbol}")
                if not quote_entry:
                    if TRY_REVERSE_ON_NO_ROUTE:
                        _ = query_1inch_price(token_addr, base_addr, sell_amount_units, f"{token_symbol}->{base_symbol}")
                    profiler["skipped_reasons"].setdefault("No quote", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # buy amount
                try:
                    buy_amount_token = int(quote_entry.get("buyAmount", 0))
                except Exception:
                    ban_pair(key, "Invalid buyAmount in quote", duration=BAN_OTHER_REASON_DURATION)
                    profiler["skipped_reasons"].setdefault("Invalid buyAmount", []).append(f"{base_symbol}->{token_symbol}")
                    continue
                if buy_amount_token == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    profiler["skipped_reasons"].setdefault(BAN_NO_LIQUIDITY_REASON, []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # Оценка выхода (обратная котировка)
                quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
                if not quote_exit:
                    profiler["skipped_reasons"].setdefault("No quote (exit)", []).append(f"{token_symbol}->{base_symbol}")
                    continue
                try:
                    amount_back = int(quote_exit.get("buyAmount", 0))
                except Exception:
                    amount_back = 0

                # Фильтр нереалистичной прибыли
                try:
                    units_profit_estimate = (amount_back / sell_amount_units - 1) * 100
                except Exception:
                    units_profit_estimate = None

                if units_profit_estimate is None:
                    profiler["skipped_reasons"].setdefault("Profit calc error", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                if units_profit_estimate > 50:
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Unrealistic profit estimate"))
                    profiler["skipped_reasons"].setdefault("Unrealistic profit", []).append(token_symbol)
                    continue

                # Оценка в USD (если есть цены), иначе — по units
                price_base = get_token_usd_price_from_dxs(base_addr)
                price_token = get_token_usd_price_from_dxs(token_addr)
                if price_base and price_token:
                    profit_estimate_usd = (amount_back * price_base / (sell_amount_units * price_base) - 1) * 100
                else:
                    profit_estimate_usd = None

                profit_estimate = profit_estimate_usd if (profit_estimate_usd is not None) else units_profit_estimate

                if profit_estimate < MIN_PROFIT_PERCENT:
                    profiler["profit_gt_min_skipped"].append((token_symbol, f"Profit {profit_estimate:.2f}% < {MIN_PROFIT_PERCENT}%"))
                    profiler["skipped_reasons"].setdefault(f"Low profit < {MIN_PROFIT_PERCENT}%", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                platforms_used = extract_platforms(quote_entry.get("protocols")) if quote_entry.get("protocols") else []
                if not platforms_used and quote_entry.get("source") == "UniswapV3":
                    platforms_used = ["UniswapV3(est)"]

                # Тайминг: базово 3 мин, мягкая коррекция по RSI
                timing_min = 3
                if rsi is not None:
                    if rsi < 35:
                        timing_min += 2
                    elif rsi > 65:
                        timing_min -= 1
                timing_sec = max(60, timing_min * 60)

                # Сообщение о сделке (предварительное)
                send_telegram(
                    "📈 Найдена потенциальная сделка\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"RSI: {safe_format_rsi(rsi)}\n"
                    f"План: удержание ~{timing_min} мин, цель ≥ {MIN_PROFIT_PERCENT:.2f}%, стоп {STOP_LOСС_PERCENT:.2f}%\n"
                    f"Оценка прибыли: {profit_estimate:.2f}%\n"
                    f"Пулы: {', '.join(platforms_used) if platforms_used else '—'}"
                )

                # Запуск мониторинга (асинхронно)
                threading.Thread(
                    target=monitor_trade_thread,
                    args=(sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token),
                    daemon=True
                ).start()

                profiler["success_signals"] += 1

                # Cooldown после сигнала — чтобы не спамить дубликатами
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # ====== ОТЧЁТ КАЖДЫЕ REPORT_INTERVAL (детальный) ======
        now_ts = time.time()
        if now_ts - _last_watchdog_ping > 60:
            _last_watchdog_ping = now_ts

        if now_ts - last_report_time >= report_interval:
            clean_ban_list()

            banned_pairs_lines = []
            for pair, info in ban_list.items():
                seconds_left = int(info["duration"] - (now_ts - info["time"]))
                if seconds_left < 0:
                    seconds_left = 0
                banned_pairs_lines.append(f"  - {pair[0]} -> {pair[1]}: причина - {info['reason']}, осталось: {seconds_left}s")

            report = []
            report.append("===== PROFILER REPORT =====")
            report.append(f"⏱ Время полного цикла: {time.time() - cycle_start:.2f} сек")
            report.append(f"🚫 Пар в бан-листе: {len(ban_list)}")
            if banned_pairs_lines:
                report.append("Бан-лист детали:")
                report.extend(banned_pairs_lines)

            report.append(f"💤 Пропущено по cooldown: {profiler['cooldown_skips']}")
            report.append(f"💰 Пар с прибылью > {MIN_PROFIT_PERCENT:.1f}% (но не отправлены): {len(profiler['profit_gt_min_skipped'])}")
            if profiler["dexscreener_skipped"]:
                tokens_ds = ", ".join(sorted({t for t, _ in profiler["dexscreener_skipped"]}))
                report.append(f"🔎 Пропущенные (dexscreener/price issues):")
                report.append(f"   - {tokens_ds if tokens_ds else '—'}")

            # Расшифровка причин отсева
            if profiler["skipped_reasons"]:
                report.append("🧹 Причины отсева:")
                for reason, pairs in profiler["skipped_reasons"].items():
                    joined = ", ".join(pairs[:200])
                    report.append(f"   - {reason}: {joined}")

            report.append(f"✔️ Успешных сигналов за цикл: {profiler['success_signals']}")
            report.append(f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}")
            report.append("===========================")

            send_telegram("\n".join(report))
            last_report_time = now_ts
            _last_cycle_report = now_ts

        # Watchdog: если отчётов нет дольше, чем 2 * REPORT_INTERVAL — пингуем
        if now_ts - last_report_time > 2 * report_interval and now_ts - _last_watchdog_ping > 60:
            send_telegram("⚠️ No reports generated for a long time. Possibly stuck loop or upstream timeouts.")
            _last_watchdog_ping = now_ts

        time.sleep(0.5)

# ===================== ENTRY =====================
if __name__ == "__main__":
    try:
        run_real_strategy()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            raise
            
