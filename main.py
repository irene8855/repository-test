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

MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))     # %
STOP_LOSS_PERCENT  = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))      # %

REPORT_INTERVAL = int(float(os.getenv("REPORT_INTERVAL", "900")))       # 15 мин

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY", "").strip()

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
    "GMT":   "0x714db550b574b3e927af3d93e26127d15721d4c2",
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

PLATFORMS = {"1inch": "1inch", "Sushi": "SushiSwap", "Uniswap": "UniswapV3"}

# ===================== ENDPOINTS =====================
CHAIN_ID = 137
ONEINCH_V6_DEV = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_PUBLIC = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
UNISWAP_V3_POLY = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"

# ===================== BAN & STATE =====================
BAN_NO_LIQUIDITY_REASON = "No liquidity"
BAN_NO_LIQUIDITY_DURATION = 120   # 2 минуты
BAN_OTHER_REASON_DURATION = 900   # 15 минут

ban_list = {}        # (base, token) -> {time, reason, duration}
tracked_trades = {}  # (base, token) -> ts последнего сигнала
last_report_time = 0.0

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
            timeout=10
        )
        if r.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] HTTP {r.status_code}: {r.text[:400]}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Exception: {e}")

def get_local_time():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def pace_requests():
    """Глобальный троттлинг для всех потоков."""
    global _last_request_time
    with last_request_time_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()

def ban_pair(key, reason, duration=None):
    now_ts = time.time()
    if duration is None:
        if BAN_NO_LIQUIDITY_REASON.lower() in reason.lower() or "404" in reason:
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
            if DEBUG_MODE:
                print(f"[BAN] expired: {pair} ({info['reason']})")
            ban_list.pop(pair, None)

# ===================== Dexscreener =====================
def fetch_dexscreener_pairs(token_addr):
    try:
        pace_requests()
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        if DEBUG_MODE:
            print(f"[Dexscreener] HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Dexscreener] Exception: {e}")
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

# ===================== USD Profit Estimate =====================
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

# ===================== Uniswap V3 (TheGraph) Fallback =====================
def graph_query(query: str, variables: dict):
    try:
        pace_requests()
        r = requests.post(UNISWAP_V3_POLY, json={"query": query, "variables": variables}, timeout=12)
        if r.status_code == 200:
            return r.json()
        if DEBUG_MODE:
            print(f"[Graph] HTTP {r.status_code}: {r.text[:400]}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Graph] Exception: {e}")
    return None

def univ3_get_best_pool_and_price(token0: str, token1: str):
    """
    Возвращает (direction, price, liquidity) где:
      direction = 0 если цена как token1_per_token0 (pool token0->token1),
                = 1 если цена как token0_per_token1 (pool token1->token0)
      price = float
    Ищем среди пулов с наибольшей ликвидностью.
    """
    token0 = token0.lower()
    token1 = token1.lower()
    q = """
    query Pools($t0: String!, $t1: String!) {
      pools(first: 10, where: { token0_in: [$t0, $t1], token1_in: [$t0, $t1] }, orderBy: totalValueLockedUSD, orderDirection: desc) {
        id
        token0 { id symbol decimals }
        token1 { id symbol decimals }
        token0Price
        token1Price
        totalValueLockedUSD
      }
    }
    """
    data = graph_query(q, {"t0": token0, "t1": token1})
    if not data or "data" not in data or "pools" not in data["data"]:
        return None
    pools = data["data"]["pools"]
    best = None
    for p in pools:
        t0 = p["token0"]["id"].lower()
        t1 = p["token1"]["id"].lower()
        tvl = float(p.get("totalValueLockedUSD") or 0.0)
        if not ((t0 == token0 and t1 == token1) or (t0 == token1 and t1 == token0)):
            continue
        # выбираем пул с наибольшим TVL
        if best is None or tvl > best["tvl"]:
            best = {
                "t0": t0, "t1": t1, "t0dec": int(p["token0"]["decimals"]), "t1dec": int(p["token1"]["decimals"]),
                "t0p": float(p["token0Price"]), "t1p": float(p["token1Price"]), "tvl": tvl
            }
    if not best:
        return None
    # Если best.t0 == token0 и best.t1 == token1 => token1_per_token0 = t0p
    # Если наоборот => нам нужна цена token1_per_token0 в прямом направлении
    if best["t0"] == token0 and best["t1"] == token1:
        return {"direction": 0, "price": best["t0p"], "t0dec": best["t0dec"], "t1dec": best["t1dec"]}
    else:
        # пул с обратным направлением — используем token0_per_token1 = t1p,
        # но для конверсии token0->token1 нам нужна именно token1_per_token0 => это 1 / t1p
        price = 1.0 / best["t1p"] if best["t1p"] != 0 else None
        return {"direction": 0, "price": price, "t0dec": best["t0dec"], "t1dec": best["t1dec"]}

def univ3_estimate_amount_out(src_addr: str, dst_addr: str, amount_in_units: int):
    """
    Приблизительный quote через Uniswap V3 subgraph: берём лучший пул по TVL и умножаем на цену.
    Это НЕ учитывает комиссию пула и проскальзывание, но даёт «хвост» вместо полного молчания 1inch.
    Возвращает {"buyAmount": "<int>"} или None.
    """
    src_addr = src_addr.lower()
    dst_addr = dst_addr.lower()
    best = univ3_get_best_pool_and_price(src_addr, dst_addr)
    if not best or not best.get("price"):
        return None
    try:
        src_dec = DECIMALS.get(ADDRESS_TO_SYMBOL.get(src_addr, ""), 18)
        dst_dec = DECIMALS.get(ADDRESS_TO_SYMBOL.get(dst_addr, ""), 18)
        amount_in = amount_in_units / (10 ** src_dec)
        amount_out = amount_in * float(best["price"])
        amount_out_units = int(amount_out * (10 ** dst_dec))
        if amount_out_units <= 0:
            return None
        return {"buyAmount": str(amount_out_units), "protocols": [], "route": {"fills": []}, "source": "UniswapV3"}
    except Exception:
        return None

# ===================== 1inch with Fallback =====================
def query_1inch_price(sell_token_addr: str, buy_token_addr: str, sell_amount_units: int, symbol_pair=""):
    """
    Возвращает dict с "buyAmount" (строка) и meta, либо None.
    Каскад:
      1) v6 dev с ключом (если есть)
      2) v6 dev без ключа
      3) v5 публичный
      4) fallback на Uniswap V3 subgraph (оценка)
    При Invalid JSON добавляем raw в лог и идём дальше по каскаду.
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
            resp = requests.get(url, params=params, headers=headers, timeout=12)
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
                continue

            buy_amount = data.get("toTokenAmount") or data.get("dstAmount")
            if not buy_amount:
                last_err_snippet = "No buy amount in response"
                continue

            try:
                if int(buy_amount) == 0:
                    ban_pair(key, BAN_NO_LIQUIDITY_REASON, duration=BAN_NO_LIQUIDITY_DURATION)
                    if DEBUG_MODE:
                        print(f"[1inch/{name}] Zero buy amount for {symbol_pair}")
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
            # как правило, «маршрута нет» — баним коротко
            ban_pair(key, f"{BAN_NO_LIQUIDITY_REASON} ({resp.status_code})", duration=BAN_NO_LIQUIDITY_DURATION)
            if DEBUG_MODE:
                print(f"[1inch/{name}] {resp.status_code} for {symbol_pair}: {resp.text[:400]}")
            return None
        else:
            try:
                last_err_snippet = f"HTTP {resp.status_code}: {resp.text[:400].replace(chr(10),' ')}"
            except Exception:
                last_err_snippet = f"HTTP {resp.status_code}"
            if DEBUG_MODE:
                print(f"[1inch/{name}] {last_err_snippet}")
            continue

    # 1inch не дал котировку — пробуем Uniswap V3 subgraph (оценочно)
    uni_quote = univ3_estimate_amount_out(sell_token_addr, buy_token_addr, sell_amount_units)
    if uni_quote:
        uni_quote["protocols"] = [["UniswapV3(estimation)"]]
        return uni_quote

    ban_pair(key, f"1inch error: {last_err_snippet or 'No route/No quote'}", duration=BAN_OTHER_REASON_DURATION)
    return None

# ===================== Монитор сделки (поток) =====================
def monitor_trade_thread(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token):
    """
    Каждые 15 сек запрашиваем котировку выхода (token->base): сначала 1inch, если нет — Uniswap V3.
    Считаем актуальный PnL и шлём сигналы 🎯/⚠️, по окончанию окна — финальное сообщение.
    """
    check_interval = 15
    started = time.time()
    alerted_take = False
    alerted_stop = False

    while True:
        elapsed = time.time() - started
        is_final = elapsed >= timing_sec

        # котировка выхода
        quote_exit = query_1inch_price(token_addr, base_addr, buy_amount_token, f"{token_symbol}->{base_symbol}")
        final_amount_exit = None
        if quote_exit and "buyAmount" in quote_exit:
            try:
                final_amount_exit = int(quote_exit["buyAmount"])
            except Exception:
                final_amount_exit = None

        if final_amount_exit:
            _, _, actual_profit = compute_profit_percent_by_units(entry_sell_amount_units, final_amount_exit, base_symbol, token_symbol)
        else:
            actual_profit = None

        if is_final:
            if actual_profit is not None:
                send_telegram(
                    f"⏳ Время удержания вышло\n"
                    f"PAIR: {base_symbol}->{token_symbol}\n"
                    f"Текущая прибыль: {actual_profit:.2f}%\n"
                    f"Time: {get_local_time().strftime('%H:%M')}"
                )
            else:
                send_telegram(
                    f"⏳ Время удержания вышло\n"
                    f"PAIR: {base_symbol}->{token_symbol}\n"
                    f"Не удалось получить котировку выхода."
                )
            break
        else:
            if actual_profit is not None:
                if (not alerted_take) and actual_profit >= MIN_PROFIT_PERCENT:
                    send_telegram(f"🎯 Цель достигнута: {actual_profit:.2f}% по {token_symbol}")
                    alerted_take = True
                if (not alerted_stop) and actual_profit <= STOP_LOSS_PERCENT:
                    send_telegram(f"⚠️ Стоп-лосс: {actual_profit:.2f}% по {token_symbol}")
                    alerted_stop = True

        time.sleep(check_interval)

def start_monitor_in_thread(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token):
    t = threading.Thread(
        target=monitor_trade_thread,
        args=(entry_sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token),
        daemon=True
    )
    t.start()

# ===================== MAIN STRATEGY =====================
def run_real_strategy():
    """
    Основной цикл:
      - обходит пары,
      - фильтрует по бан-листу/cooldown, RSI, прибыльности,
      - отправляет pre-сообщение о сделке,
      - запускает мониторинг в отдельном потоке,
      - каждые REPORT_INTERVAL секунд шлёт детальный отчёт с причинами отсева.
    """
    global last_report_time
    send_telegram("🤖 Bot started (1inch+UniswapV3 fallback, threaded monitor).")
    base_tokens = ["USDT"]

    report_interval = int(REPORT_INTERVAL)

    while True:
        cycle_start = time.time()
        profiler = {
            "ban_skips": 0,
            "cooldown_skips": 0,
            "skipped_reasons": {},          # {reason: [pairs]}
            "profit_gt_min_skipped": [],    # [(sym, reason)]
            "dexscreener_skipped": [],      # [(sym, reason)]
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

                # Бан-лист
                if key in ban_list:
                    profiler["ban_skips"] += 1
                    profiler["skipped_reasons"].setdefault("Ban list", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # Cooldown после недавнего сигнала
                if time.time() - tracked_trades.get(key, 0) < BAN_OTHER_REASON_DURATION:
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
                        if rsi is not None and rsi > 70:
                            profiler["profit_gt_min_skipped"].append((token_symbol, f"RSI={rsi:.2f} (>70)"))
                            profiler["skipped_reasons"].setdefault("RSI>70", []).append(token_symbol)
                            continue

                # Котировка входа: base -> token (каскад 1inch + fallback на Uniswap)
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
                    profiler["skipped_reasons"].setdefault("No liquidity", []).append(f"{base_symbol}->{token_symbol}")
                    continue

                # Грубая оценка по units
                units_profit_estimate = ((buy_amount_token / sell_amount_units) - 1) * 100
                if abs(units_profit_estimate) > 1e6:  # фильтр мусора
                    profiler["profit_gt_min_skipped"].append((token_symbol, "Unrealistic profit estimate"))
                    profiler["skipped_reasons"].setdefault("Unrealistic profit", []).append(token_symbol)
                    continue

                # Оценка в USD (если есть цены), иначе — по units
                _, _, profit_estimate_usd = compute_profit_percent_by_units(sell_amount_units, buy_amount_token, base_symbol, token_symbol)
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
                    timing_min = min(8, max(3, 3 + int(max(0, (30 - rsi)) // 6)))
                timing_sec = timing_min * 60

                # Сообщение о сделке (pre)
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

                # Мониторинг окна удержания — в отдельном потоке
                start_monitor_in_thread(sell_amount_units, base_addr, token_addr, base_symbol, token_symbol, timing_sec, buy_amount_token)

                # Cooldown после сигнала — чтобы не спамить
                ban_pair(key, "Post-trade cooldown", duration=BAN_OTHER_REASON_DURATION)

        # ====== ОТЧЁТ КАЖДЫЕ 15 МИНУТ (детальный, всегда) ======
        now_ts = time.time()
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
            report.append(f"💰 Пар с прибылью > {MIN_PROFIT_PERCENT}% (но не отправлены): {len(profiler['profit_gt_min_skipped'])}")
            if profiler["profit_gt_min_skipped"]:
                for sym, reason in profiler["profit_gt_min_skipped"]:
                    report.append(f"   - {sym}: {reason}")

            if profiler["dexscreener_skipped"]:
                report.append("🔎 Пропущенные (dexscreener/price issues):")
                for sym, reason in profiler["dexscreener_skipped"]:
                    report.append(f"   - {sym}: {reason}")

            if profiler["skipped_reasons"]:
                report.append("🧹 Причины отсева:")
                for reason, items in profiler["skipped_reasons"].items():
                    listed = ", ".join(items[:20])
                    more = "" if len(items) <= 20 else f" (+{len(items)-20} ещё)"
                    report.append(f"   - {reason}: {listed}{more}")

            report.append(f"✔️ Успешных сигналов за цикл: {profiler['successful_trades']}")
            report.append(f"🔍 Всего проверено пар: {profiler['total_checked_pairs']}")
            report.append("===========================")

            send_telegram("\n".join(report))
            last_report_time = now_ts

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
            
