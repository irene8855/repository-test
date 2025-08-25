# -*- coding: utf-8 -*-
"""
Main.py — Trading Signals Bot (full, single-file)
Источники цен: 1inch (если есть ключ) → UniswapV3 (если есть GRAPH_API_KEY) → Dexscreener (всегда).
Телеграм-сообщения:
  1) о запуске,
  2) предварительное по сделке (указан источник),
  3) финальное по результату удержания/цели/стопа (реальный PnL в % и USDT, источник),
  4) отчёт каждые REPORT_INTERVAL секунд (по умолчанию 900).
"""

import os
import time
import json
import math
import threading
import datetime as dt
from math import isfinite

import requests
import sqlite3, queue, atexit, joblib

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ===================== CONFIG =====================
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# торговые параметры
BASE_TOKENS        = os.getenv("BASE_TOKENS", "USDT").split(",")  # можно несколько базовых через запятую
SELL_AMOUNT_USD    = float(os.getenv("SELL_AMOUNT_USD", "100"))   # объём входа на оценку, в USD эквиваленте базового
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "1.0"))
STOP_LOSS_PERCENT  = float(os.getenv("STOP_LOSS_PERCENT", "-1.0"))
HOLD_SECONDS       = int(float(os.getenv("HOLD_SECONDS", "300"))) # окно удержания (5 мин по умолчанию)

# отчётность
REPORT_INTERVAL    = int(float(os.getenv("REPORT_INTERVAL", "900")))  # 15 минут
DEBUG_MODE         = os.getenv("DEBUG_MODE", "True").lower() == "true"

# лимиты запросов/таймауты
REQUEST_TIMEOUT    = (5, 12)  # (connect, read) seconds
MAX_RPS            = int(os.getenv("MAX_RPS", "5"))
REQUEST_INTERVAL   = 1 / max(1, MAX_RPS)
GRAPH_INTERVAL     = int(os.getenv("GRAPH_INTERVAL", "300"))
_last_graph_call   = 0  # глобальная переменная для контроля интервала

# === realistic trade settings ===
DEX_FEE            = float(os.getenv("DEX_FEE", "0.003"))        # комиссия пула в долях (0.003 = 0.3%)
SLIPPAGE           = float(os.getenv("SLIPPAGE", "0.002"))      # допущение проскальзывания (0.002 = 0.2%)
MIN_LIQ_USD        = float(os.getenv("MIN_LIQ_USD", "50000"))  # минимальная ликвидность пары в $
ORDERFLOW_RATIO    = float(os.getenv("ORDERFLOW_RATIO", "1.5"))
VOLUME_SPIKE_RATIO = float(os.getenv("VOLUME_SPIKE_RATIO", "2.0"))
MOMENTUM_THRESHOLD = float(os.getenv("MOMENTUM_THRESHOLD", "0.5"))

# опциональные ключи
ONEINCH_API_KEY    = os.getenv("ONEINCH_API_KEY", "").strip()   # если пуст — 1inch v6 будет пропущен
GRAPH_API_KEY      = os.getenv("GRAPH_API_KEY", "").strip()     # если пуст — UniswapV3 через gateway недоступен

# 1inch
CHAIN_ID           = int(os.getenv("CHAIN_ID", "137"))  # Polygon
ONEINCH_V6_URL     = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_URL     = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"  # публичный — часто отдаёт HTML; используем лишь как попытку

# UniswapV3 graph
UNISWAP_V3_SUBGRAPH_ID = os.getenv("UNISWAP_V3_SUBGRAPH_ID")
SUSHI_SUBGRAPH_ID      = os.getenv("SUSHI_SUBGRAPH_ID")
GRAPH_GATEWAY_BASE     = "https://gateway.thegraph.com/api"

# Dexscreener
DEXSCREENER_TOKEN_URL  = "https://api.dexscreener.com/latest/dex/tokens/"

# ===================== TOKENS & DECIMALS =====================
TOKENS = {
    # базовые
    "USDT":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "USDC":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "DAI":    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX":   "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    # ликвидные/наблюдаемые
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
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18,
    "wstETH": 18, "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18, "WETH": 18, "SUSHI": 18
}
ADDRESS_TO_SYMBOL = {addr.lower(): sym for sym, addr in TOKENS.items()}

RSI_TOKENS = {"AAVE","LINK","EMT","LDO","SUSHI","GMT","SAND","tBTC","wstETH","WETH"}

# ===================== STATE =====================
ban_list = {}  # {(base, token): {"time":ts, "reason":str, "duration":int}}
stats_lock = threading.Lock()
stats_snapshot = {
    "checked": 0,
    "signals": 0,
    "skipped": {},       # reason -> [ "USDT->AAVE", ... ]
    "dex_issues": [],    # list of text entries
    "ban_details": {},   # copy of ban_list for report
}
last_report_time = 0.0

_last_req_lock = threading.Lock()
_last_req_ts = 0.0

# ===================== UTIL =====================
def pace_requests():
    global _last_req_ts
    with _last_req_lock:
        elapsed = time.time() - _last_req_ts
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_req_ts = time.time()

def now_local():
    # используем локальное время системы
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(text: str):
    """Жёсткая диагностика доставки в Telegram (не молчим, если ошибка)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[TG muted]", text[:4000])
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=REQUEST_TIMEOUT
        )
        if r.status_code != 200:
            print("[TG ERROR]", r.status_code, r.text[:400])
    except Exception as e:
        print("[TG EXCEPTION]", repr(e))

def add_skip(reason: str, pair_label: str):
    with stats_lock:
        stats_snapshot["skipped"].setdefault(reason, []).append(pair_label)

def add_dex_issue(text: str):
    with stats_lock:
        stats_snapshot["dex_issues"].append(text)

def inc_checked():
    with stats_lock:
        stats_snapshot["checked"] += 1

def inc_signal():
    with stats_lock:
        stats_snapshot["signals"] += 1

def copy_ban_for_report():
    with stats_lock:
        stats_snapshot["ban_details"] = dict(ban_list)

def reset_cycle_stats():
    with stats_lock:
        stats_snapshot["checked"] = 0
        stats_snapshot["signals"] = 0
        stats_snapshot["skipped"] = {}
        stats_snapshot["dex_issues"] = []
        stats_snapshot["ban_details"] = {}

from collections import deque
PAIR_BUFFERS = {}  # key -> {"price": deque(), "vol": deque(), "buys": deque(), "sells": deque(), "ts": deque()}
BUFFER_LEN = 12  # храним последние 12 значений (пример — 12*5min = 60min, но у тебя m5)

def ban_pair(key, reason, duration=900):
    ban_list[key] = {"time": time.time(), "reason": reason, "duration": duration}

def clean_ban_list():
    now = time.time()
    for k in list(ban_list.keys()):
        if now - ban_list[k]["time"] > ban_list[k]["duration"]:
            ban_list.pop(k, None)

# ===================== Dexscreener =====================
def dxs_fetch(token_addr: str):
    try:
        pace_requests()
        resp = requests.get(DEXSCREENER_TOKEN_URL + token_addr, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        add_dex_issue(f"Dexscreener HTTP {resp.status_code} for {token_addr} | {resp.text[:150]}")
    except Exception as e:
        add_dex_issue(f"Dexscreener EXC for {token_addr}: {repr(e)}")
    return None

def dxs_price_usd(token_addr: str):
    data = dxs_fetch(token_addr)
    if not data:
        return None
    best = None
    for p in data.get("pairs", []):
        try:
            pu = p.get("priceUsd")
            if not pu:
                continue
            price = float(pu)
            # выбираем пару с наибольшей ликвидностью
            liq = float((p.get("liquidity") or {}).get("usd") or 0.0)
            if not best or liq > best[1]:
                best = (price, liq)
        except Exception:
            continue
    return best[0] if best else None

# ===================== Uniswap V3 (Graph) =====================
def graph_url():
    if not GRAPH_API_KEY:
        return None
    return f"{GRAPH_GATEWAY_BASE}/{GRAPH_API_KEY}/subgraphs/id/{UNISWAP_V3_SUBGRAPH_ID}"

def _safe_get(d: dict, path: str, default=None):
    cur = d or {}
    for p in path.split('.'):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur

def evaluate_trade_signal_from_ds_pair(pair: dict):
    """
    Проверяет OrderFlow (m5), Volume Spike (m5 vs avg5), Momentum (m5) и Liquidity.
    Возвращает (ok: bool, reason: str, features: dict)
    """
    try:
        buys = int(_safe_get(pair, "txns.m5.buys", 0) or 0)
        sells = int(_safe_get(pair, "txns.m5.sells", 0) or 0)
        vol_m5 = float(_safe_get(pair, "volume.m5", 0.0) or 0.0)
        vol_h1 = float(_safe_get(pair, "volume.h1", 0.0) or 0.0)
        if vol_h1 <= 0:
            vol_h1 = 1.0
        avg_m5 = vol_h1 / 12.0
        momentum_m5 = float(_safe_get(pair, "priceChange.m5", 0.0) or 0.0)
        liquidity_usd = float(_safe_get(pair, "liquidity.usd", 0.0) or 0.0)

        if liquidity_usd < MIN_LIQ_USD:
            return False, f"Low liquidity: ${liquidity_usd:,.0f} < ${MIN_LIQ_USD:,.0f}", {
                "liquidity_usd": liquidity_usd, "buys": buys, "sells": sells,
                "vol_m5": vol_m5, "avg_m5": avg_m5, "momentum_m5": momentum_m5
            }

        ratio = buys / max(1, sells)
        if ratio < ORDERFLOW_RATIO:
            return False, f"Weak orderflow: buys={buys}, sells={sells}, ratio={ratio:.2f} < {ORDERFLOW_RATIO}", {
                "liquidity_usd": liquidity_usd, "buys": buys, "sells": sells,
                "vol_m5": vol_m5, "avg_m5": avg_m5, "momentum_m5": momentum_m5
            }

        if vol_m5 < avg_m5 * VOLUME_SPIKE_RATIO:
            return False, f"No volume spike: m5={vol_m5:.0f}, avg5={avg_m5:.0f}, need×{VOLUME_SPIKE_RATIO}", {
                "liquidity_usd": liquidity_usd, "buys": buys, "sells": sells,
                "vol_m5": vol_m5, "avg_m5": avg_m5, "momentum_m5": momentum_m5
            }

        if momentum_m5 < MOMENTUM_THRESHOLD:
            return False, f"Weak momentum: {momentum_m5:.2f}% < {MOMENTUM_THRESHOLD}%", {
                "liquidity_usd": liquidity_usd, "buys": buys, "sells": sells,
                "vol_m5": vol_m5, "avg_m5": avg_m5, "momentum_m5": momentum_m5
            }

        return True, "Signal OK", {
            "liquidity_usd": liquidity_usd, "buys": buys, "sells": sells,
            "vol_m5": vol_m5, "avg_m5": avg_m5, "momentum_m5": momentum_m5
        }
    except Exception as e:
        return False, f"Signal error: {e}", {}

def adjust_for_fees_pct(raw_profit_pct: float) -> float:
    """
    На входе — проценты (например 1.23 -> +1.23% raw).
    DEX_FEE и SLIPPAGE заданы в долях (0.003 = 0.3%).
    Возвращает чистую прибыль в процентах после вычетов.
    """
    fees_pct = (DEX_FEE * 2.0) * 100.0   # вход+выход (в процентах)
    slip_pct = SLIPPAGE * 100.0
    return raw_profit_pct - fees_pct - slip_pct

def univ3_quote_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    """Грубая оценка через sqrtPrice и самый ликвидный пул."""
    global _last_graph_call
    now = time.time()
    if now - _last_graph_call < GRAPH_INTERVAL:
        return None, "Uniswap skipped (graph interval)"
    _last_graph_call = now

    url = graph_url()
    if not url:
        return None, "Uniswap skipped (no GRAPH_API_KEY)"

    src = src_addr.lower()
    dst = dst_addr.lower()
    q = """
    query Pools($a:String!, $b:String!){
      pools(
        where:{ token0_in:[$a,$b], token1_in:[$a,$b], feeTier_in:[500,3000,10000] }
        first: 20, orderBy: liquidity, orderDirection: desc
      ){
        id feeTier liquidity sqrtPrice token0{ id decimals } token1{ id decimals }
      }
    }"""
    try:
        pace_requests()
        resp = requests.post(url, json={"query": q, "variables":{"a":src,"b":dst}}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None, f"Uniswap HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        pools = (data.get("data") or {}).get("pools") or []
        if not pools:
            return None, "Uniswap: no pools"
        # оставляем пулы только ровно для пары
        pools = [p for p in pools if {p["token0"]["id"].lower(), p["token1"]["id"].lower()} == {src, dst}]
        if not pools:
            return None, "Uniswap: no exact pool"
        # берём самый ликвидный
        def liq(x):
            try: return float(x.get("liquidity") or 0)
            except: return 0.0
        pool = sorted(pools, key=liq, reverse=True)[0]
        t0, t1 = pool["token0"], pool["token1"]
        dec0, dec1 = int(t0["decimals"]), int(t1["decimals"])
        sqrtP = int(pool["sqrtPrice"])
        Q96 = 2**96
        price1_per_0 = (sqrtP / Q96) ** 2 * (10 ** (dec0 - dec1))
        if not isfinite(price1_per_0) or price1_per_0 <= 0:
            return None, "Uniswap: bad sqrtP"
        # направление
        if src == t0["id"].lower() and dst == t1["id"].lower():
            out_units = int(amount_units * price1_per_0 * (1 - int(pool["feeTier"])/1_000_000))
        elif src == t1["id"].lower() and dst == t0["id"].lower():
            out_units = int(amount_units * (1/price1_per_0) * (1 - int(pool["feeTier"])/1_000_000))
        else:
            return None, "Uniswap: dir mismatch"
        if out_units <= 0:
            return None, "Uniswap: zero out"
        return {"buyAmount": str(out_units), "source": "UniswapV3"}, None
    except Exception as e:
        return None, f"Uniswap EXC: {repr(e)}"

# ===================== SushiSwap v3 (Graph) =====================
def sushi_graph_url():
    # Требуются GRAPH_API_KEY и SUSHI_SUBGRAPH_ID в переменных окружения
    if not GRAPH_API_KEY or not SUSHI_SUBGRAPH_ID:
        return None
    return f"{GRAPH_GATEWAY_BASE}/{GRAPH_API_KEY}/subgraphs/id/{SUSHI_SUBGRAPH_ID}"

def sushi_quote_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    """Грубая оценка для Sushi v3 по sqrtPrice самого ликвидного пула."""
    url = sushi_graph_url()
    if not url:
        return None, "Sushi skipped (no GRAPH_API_KEY or SUSHI_SUBGRAPH_ID)"

    src = src_addr.lower()
    dst = dst_addr.lower()
    q = """
    query Pools($a:String!, $b:String!){
      pools(
        where:{ token0_in:[$a,$b], token1_in:[$a,$b], feeTier_in:[500,3000,10000] }
        first: 20, orderBy: liquidity, orderDirection: desc
      ){
        id feeTier liquidity sqrtPrice token0{ id decimals } token1{ id decimals }
      }
    }"""
    try:
        pace_requests()
        resp = requests.post(url, json={"query": q, "variables":{"a":src,"b":dst}}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None, f"Sushi HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        pools = (data.get("data") or {}).get("pools") or []
        if not pools:
            return None, "Sushi: no pools"

        # берём именно нашу пару и самый ликвидный пул
        pools = [p for p in pools if {p["token0"]["id"].lower(), p["token1"]["id"].lower()} == {src, dst}]
        if not pools:
            return None, "Sushi: no exact pool"

        def liq(x):
            try: return float(x.get("liquidity") or 0)
            except: return 0.0
        pool = sorted(pools, key=liq, reverse=True)[0]

        t0, t1 = pool["token0"], pool["token1"]
        dec0, dec1 = int(t0["decimals"]), int(t1["decimals"])
        sqrtP = int(pool["sqrtPrice"]); Q96 = 2**96
        price1_per_0 = (sqrtP / Q96) ** 2 * (10 ** (dec0 - dec1))
        if not isfinite(price1_per_0) or price1_per_0 <= 0:
            return None, "Sushi: bad sqrtP"

        fee = int(pool["feeTier"]) / 1_000_000
        if src == t0["id"].lower() and dst == t1["id"].lower():
            out_units = int(amount_units * price1_per_0 * (1 - fee))
        elif src == t1["id"].lower() and dst == t0["id"].lower():
            out_units = int(amount_units * (1/price1_per_0) * (1 - fee))
        else:
            return None, "Sushi: dir mismatch"

        if out_units <= 0:
            return None, "Sushi: zero out"
        return {"buyAmount": str(out_units), "source": "SushiSwap"}, None
    except Exception as e:
        return None, f"Sushi EXC: {repr(e)}"

# ===================== 1inch =====================
def oneinch_quote_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    params = {
        # оба формата, т.к. v6 и v5 используют разные названия
        "src": src_addr, "dst": dst_addr,
        "fromTokenAddress": src_addr, "toTokenAddress": dst_addr,
        "amount": str(amount_units),
        "disableEstimate": "true",
        "includeTokensInfo": "false",
        "includeProtocols": "true",
        "includeGas": "false"
    }
    # 1) v6 (dev) с ключом
    if ONEINCH_API_KEY:
        try:
            pace_requests()
            r = requests.get(ONEINCH_V6_URL, params=params,
                             headers={"Authorization": f"Bearer {ONEINCH_API_KEY}", "Accept":"application/json"},
                             timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                try:
                    data = r.json()
                    amt = data.get("toTokenAmount") or data.get("dstAmount")
                    if amt and int(amt) > 0:
                        return {"buyAmount": str(amt), "protocols": data.get("protocols") or [], "source": "1inch:v6"}, None
                    return None, "1inch v6: no buyAmount"
                except Exception:
                    return None, f"1inch v6: invalid JSON {r.text[:180]}"
            else:
                return None, f"1inch v6 HTTP {r.status_code}: {r.text[:180]}"
        except Exception as e:
            return None, f"1inch v6 EXC: {repr(e)}"
    # 2) v5 (публичный) — может вернуть HTML → ловим и пишем как причину
    try:
        pace_requests()
        r = requests.get(ONEINCH_V5_URL, params=params, timeout=REQUEST_TIMEOUT)
        # если прилетел HTML — json() упадёт
        data = r.json()
        amt = data.get("toTokenAmount") or data.get("dstAmount")
        if amt and int(amt) > 0:
            return {"buyAmount": str(amt), "protocols": data.get("protocols") or [], "source": "1inch:v5"}, None
        return None, "1inch v5: no buyAmount"
    except Exception as e:
        return None, f"1inch v5 invalid/err: {repr(e)}"

# ===================== MULTI-SOURCE QUOTE =====================
def quote_amount_out(src_symbol: str, dst_symbol: str, amount_units: int):
    """Пробуем 1inch → Uniswap → Dexscreener. Возвращаем (dict|None, reasons[list])."""
    src_addr = TOKENS[src_symbol].lower()
    dst_addr = TOKENS[dst_symbol].lower()
    reasons = []

    # 1) 1inch
    q, err = oneinch_quote_amount_out(src_addr, dst_addr, amount_units)
    if q and q.get("buyAmount"):
        q["source"] = q.get("source") or "1inch"
        return q, reasons
    if err: reasons.append(err)

    # 2) UniswapV3
    q, err = univ3_quote_amount_out(src_addr, dst_addr, amount_units)
    if q and q.get("buyAmount"):
        q["source"] = "UniswapV3"
        return q, reasons
    if err:
        reasons.append(err)

    # 2b) SushiSwap (если Uniswap не дал данных)
    q, err = sushi_quote_amount_out(src_addr, dst_addr, amount_units)
    if q and q.get("buyAmount"):
        q["source"] = "SushiSwap"
        return q, reasons
    if err:
        reasons.append(err)
      
    # 3) Dexscreener (грубая оценка через USD-цены)
    try:
        src_dec = DECIMALS.get(src_symbol, 18)
        dst_dec = DECIMALS.get(dst_symbol, 18)
        src_tokens = amount_units / (10 ** src_dec)

        p_src = dxs_price_usd(src_addr)
        p_dst = dxs_price_usd(dst_addr)
        if p_src is None or p_dst is None or p_src <= 0 or p_dst <= 0:
            reasons.append("Dexscreener: no USD price")
            return None, reasons

        usd_value = src_tokens * p_src
        dst_tokens = usd_value / p_dst
        out_units = int(dst_tokens * (10 ** dst_dec))
        if out_units <= 0:
            reasons.append("Dexscreener: zero out")
            return None, reasons

        return {"buyAmount": str(out_units), "protocols": [], "source": "Dexscreener"}, reasons
    except Exception as e:
        reasons.append(f"Dexscreener EXC: {repr(e)}")
        return None, reasons

# ===================== PnL helper =====================
def profit_pct_by_units(entry_units_base: int, exit_units_base: int) -> float:
    try:
        return (exit_units_base / entry_units_base - 1) * 100.0
    except Exception:
        return None

# ===================== Мониторинг сделки =====================
def monitor_trade_thread(base_symbol, token_symbol, entry_sell_units, buy_amount_token_units, source_tag):
    """Параллельный монитор входа: ждём до HOLD_SECONDS, следим за целью/стопом, шлём финал."""
    start = time.time()
    alerted_take = False
    alerted_stop = False

    base_addr  = TOKENS[base_symbol].lower()
    token_addr = TOKENS[token_symbol].lower()

    while True:
        elapsed = time.time() - start
        is_final = elapsed >= HOLD_SECONDS

        # котировка выхода (token -> base)
        q_exit, _ = quote_amount_out(token_symbol, base_symbol, buy_amount_token_units)
        exit_units = None
        if q_exit and q_exit.get("buyAmount"):
            try:
                exit_units = int(q_exit["buyAmount"])
            except Exception:
                exit_units = None

        pnl = profit_pct_by_units(entry_sell_units, exit_units) if exit_units else None

        if is_final:
            # финальное сообщение
            if pnl is not None:
                # абсолют в USDT — эквивалент входа
                base_dec = DECIMALS.get(base_symbol, 6)
                entry_tokens = entry_sell_units / (10 ** base_dec)
                abs_usdt = entry_tokens * (pnl / 100.0) if pnl is not None else 0.0
                final_net = adjust_for_fees_pct(pnl) if (pnl is not None) else None

                msg_lines = [
                    "✅ Финальный результат",
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}",
                    f"Источник: {source_tag}"
                ]
                if pnl is not None:
                    msg_lines.append(f"PnL (raw): {pnl:.2f}% (~{abs_usdt:.2f} {base_symbol})")
                    if final_net is not None:
                        msg_lines.append(f"PnL (net): {final_net:.2f}%")
                else:
                    msg_lines.append("PnL: — (котировка выхода не получена)")

                msg_lines.append(f"Время: {now_local()}")
                send_telegram("\n".join(msg_lines))
            else:
                send_telegram(
                    f"✅ Финальный результат\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Источник: {source_tag}\n"
                    f"PnL: — (котировка выхода не получена)\n"
                    f"Время: {now_local()}"
                )
            return

        # промежуточные алерты (однократно)
        if pnl is not None:
            final_net = adjust_for_fees_pct(pnl)

            if (not alerted_take) and pnl >= MIN_PROFIT_PERCENT:
                if final_net is not None:
                    send_telegram(f"🎯 Цель достигнута: {pnl:.2f}% (net {final_net:.2f}%) по {token_symbol} (Источник: {source_tag})")
                else:
                    send_telegram(f"🎯 Цель достигнута: {pnl:.2f}% по {token_symbol} (Источник: {source_tag})")
                alerted_take = True

            if (not alerted_stop) and pnl <= STOP_LOSS_PERCENT:
                if final_net is not None:
                    send_telegram(f"⚠️ Стоп-лосс: {pnl:.2f}% (net {final_net:.2f}%) по {token_symbol} (Источник: {source_tag})")
                else:
                    send_telegram(f"⚠️ Стоп-лосс: {pnl:.2f}% по {token_symbol} (Источник: {source_tag})")
                alerted_stop = True

        time.sleep(20)

def start_monitor(*args):
    t = threading.Thread(target=monitor_trade_thread, args=args, daemon=True)
    t.start()

# ===================== Основной цикл =====================
def init_logging_db():
    global _db_conn
    _db_conn = sqlite3.connect(LOG_DB_PATH, check_same_thread=False)
    cur = _db_conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        base TEXT,
        token TEXT,
        source TEXT,
        exp_pnl REAL,
        net_pnl REAL,
        predicted_prob REAL,
        features_json TEXT,
        entry_sell_units INTEGER,
        buy_amount_token_units INTEGER,
        exit_units_est INTEGER,
        outcome INTEGER,   -- 1 win, 0 lose, -1 unknown/pending
        pnl_real REAL,
        hold_seconds INTEGER
    )""")
    _db_conn.commit()

def writer_worker():
    global _db_conn
    cur = _db_conn.cursor()
    while True:
        item = _write_queue.get()
        if item is None:
            break
        try:
            cur.execute("""
            INSERT INTO signals
            (ts, base, token, source, exp_pnl, net_pnl, predicted_prob, features_json,
             entry_sell_units, buy_amount_token_units, exit_units_est, outcome, pnl_real, hold_seconds)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item.get("ts"), item.get("base"), item.get("token"), item.get("source"),
                item.get("exp_pnl"), item.get("net_pnl"), item.get("predicted_prob"),
                json.dumps(item.get("features") or {}), item.get("entry_sell_units"),
                item.get("buy_amount_token_units"), item.get("exit_units_est"),
                item.get("outcome", -1), item.get("pnl_real"), item.get("hold_seconds")
            ))
            _db_conn.commit()
        except Exception as e:
            # fallback: append CSV
            try:
                with open(LOG_CSV_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, default=str, ensure_ascii=False) + "\n")
            except Exception:
                print("[LOG WRITE ERR]", repr(e))
        _write_queue.task_done()

def start_writer():
    global _writer_thread
    init_logging_db()
    _writer_thread = threading.Thread(target=writer_worker, daemon=True)
    _writer_thread.start()
    atexit.register(stop_writer)

def stop_writer():
    try:
        _write_queue.put_nowait(None)
    except Exception:
        pass
    try:
        if _writer_thread:
            _writer_thread.join(timeout=2)
    except Exception:
        pass
    try:
        if _db_conn:
            _db_conn.close()
    except Exception:
        pass

def enqueue_signal_record(record: dict):
    try:
        _write_queue.put_nowait(record)
    except queue.Full:
        # drop if overloaded (or use blocking put with timeout)
        print("[LOG QUEUE FULL] Dropping record")

def strategy_loop():
    global last_report_time
    reset_cycle_stats()
    send_telegram(f"🚀 Бот запущен {now_local()}\n"
                  f"Источники: 1inch={'ON' if ONEINCH_API_KEY else 'OFF'}, UniswapGraph={'ON' if GRAPH_API_KEY else 'OFF'}, Dexscreener=ON\n"
                  f"Параметры: MIN_PROFIT={MIN_PROFIT_PERCENT}%, STOP_LOSS={STOP_LOSS_PERCENT}%, HOLD={HOLD_SECONDS}s, REPORT={REPORT_INTERVAL}s")

    while True:
        loop_start = time.time()
        clean_ban_list()

        for base_symbol in BASE_TOKENS:
            if base_symbol not in TOKENS:
                add_skip("Base token not in TOKENS", base_symbol)
                continue
            base_addr = TOKENS[base_symbol].lower()
            base_dec  = DECIMALS.get(base_symbol, 6)
            entry_sell_units = int(SELL_AMOUNT_USD * (10 ** base_dec))

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_symbol:
                    continue
                key = (base_symbol, token_symbol)
                inc_checked()

                # бан-лист
                if key in ban_list:
                    left = int(ban_list[key]["duration"] - (time.time()-ban_list[key]["time"]))
                    if left > 0:
                        add_skip(f"Banned ({ban_list[key]['reason']}, left {left}s)", f"{base_symbol}->{token_symbol}")
                        continue
                    else:
                        ban_list.pop(key, None)

                # Получаем цену входа base->token
                q_in, reasons = quote_amount_out(base_symbol, token_symbol, entry_sell_units)
                if not q_in or not q_in.get("buyAmount"):
                    add_skip("No quote", f"{base_symbol}->{token_symbol}")
                    for rs in reasons:
                        add_skip(f"Cause {base_symbol}->{token_symbol}", rs)
                    # мягкий бан на короткое время, чтобы не ддосить
                    ban_pair(key, "No quote", duration=60)
                    continue

                source_tag = q_in.get("source", "unknown")
                try:
                    buy_amount_token_units = int(q_in["buyAmount"])
                except Exception:
                    add_skip("Invalid buyAmount", f"{base_symbol}->{token_symbol}")
                    ban_pair(key, "Invalid buyAmount", duration=300)
                    continue
                if buy_amount_token_units <= 0:
                    add_skip("Zero buy", f"{base_symbol}->{token_symbol}")
                    ban_pair(key, "Zero buy", duration=120)
                    continue

                # Выходная оценка token->base для расчёта ожидаемого PnL
                q_out, reasons_out = quote_amount_out(token_symbol, base_symbol, buy_amount_token_units)
                if not q_out or not q_out.get("buyAmount"):
                    add_skip("No quote (exit)", f"{token_symbol}->{base_symbol}")
                    for rs in reasons_out:
                        add_skip(f"Cause {token_symbol}->{base_symbol}", rs)
                    ban_pair(key, "No exit quote", duration=60)
                    continue
                try:
                    exit_units_est = int(q_out["buyAmount"])
                except Exception:
                    add_skip("Invalid exit buyAmount", f"{token_symbol}->{base_symbol}")
                    ban_pair(key, "Invalid exit buyAmount", duration=300)
                    continue

                # ожидаемый PnL
                exp_pnl = profit_pct_by_units(entry_sell_units, exit_units_est)
                if exp_pnl is None:
                    add_skip("Profit calc error", f"{base_symbol}->{token_symbol}")
                    continue

                # фильтр по минимальной прибыли
                if exp_pnl < MIN_PROFIT_PERCENT:
                    add_skip(f"Low profit < {MIN_PROFIT_PERCENT}%", f"{base_symbol}->{token_symbol} ({exp_pnl:.2f}%)")
                    continue

                # --- Dexscreener indicators & net-profit calculation ---
                try:
                    token_addr = TOKENS.get(token_symbol) if 'token_symbol' in locals() else None
                    best_ds_pair = None
                    if token_addr:
                        try:
                            ds_raw = dxs_fetch(token_addr)  # у тебя есть dxs_fetch в коде
                        except Exception:
                            ds_raw = None
                        if ds_raw and isinstance(ds_raw.get('pairs'), list) and len(ds_raw.get('pairs')) > 0:
                            best_ds_pair = max(ds_raw['pairs'], key=lambda p: ((p.get('liquidity') or {}).get('usd', 0) or 0))
                except Exception:
                    best_ds_pair = None

                if best_ds_pair:
                    ds_ok, ds_reason, ds_feat = evaluate_trade_signal_from_ds_pair(best_ds_pair)
                    if not ds_ok:
                        add_skip(ds_reason, f"{base_symbol}->{token_symbol}")
                        ban_pair((base_symbol, token_symbol), 'DS indicators fail', duration=60)
                        continue
                else:
                    ds_ok, ds_reason, ds_feat = False, 'No Dexscreener data', {}

                # compute net profit after fees/slippage
                net_profit = adjust_for_fees_pct(exp_pnl)
                if net_profit < MIN_PROFIT_PERCENT:
                    add_skip(f"Low net profit {net_profit:.2f}%", f"{base_symbol}->{token_symbol}")
                    continue
                # --- end inserted block ---
              
                # ===== Предварительное сообщение о сделке =====
                inc_signal()
                send_telegram(
                    f"📣 Предварительный сигнал\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Источник входа: {source_tag}\n"
                    f"Ожидаемый PnL (raw): {exp_pnl:.2f}%\n"
                    f"Ожидаемый PnL (net): {net_profit:.2f}%\n"
                    f"Ликвидность (DS): ${ds_feat.get('liquidity_usd',0):,.0f}\n"
                    f"OrderFlow m5: buys={int(ds_feat.get('buys',0))}, sells={int(ds_feat.get('sells',0))}\n"
                    f"Volume m5: {ds_feat.get('vol_m5',0):.0f} vs avg5: {ds_feat.get('avg_m5',0):.0f}\n"
                    f"Momentum m5: {ds_feat.get('momentum_m5',0.0):.2f}%\n"
                    f"План: удержание ~{HOLD_SECONDS//60}-{(HOLD_SECONDS//60)+3} мин, цель {MIN_PROFIT_PERCENT:.2f}%, стоп {STOP_LOSS_PERCENT:.2f}%\n"
                    f"Время: {now_local()}"
                )

                # старт мониторинга (финальное сообщение будет из монитор-потока)
                start_monitor(base_symbol, token_symbol, entry_sell_units, buy_amount_token_units, source_tag)

                # пост-охлаждение на пару, чтобы не спамить повторы
                ban_pair(key, "Post-trade cooldown", duration=600)

        # ===== Периодический отчёт =====
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
            copy_ban_for_report()
            with stats_lock:
                checked = stats_snapshot["checked"]
                signals = stats_snapshot["signals"]
                skipped = stats_snapshot["skipped"]
                dex_iss = stats_snapshot["dex_issues"]
                ban_det = stats_snapshot["ban_details"]
            # формируем сообщение
            lines = []
            lines.append("===== PROFILER REPORT =====")
            lines.append(f"⏱ Время полного цикла: {time.time()-loop_start:.2f} сек")
            lines.append(f"🚫 Пар в бан-листе: {len(ban_det)}")
            if ban_det:
                lines.append("Бан-лист детали:")
                for pair, info in ban_det.items():
                    left = max(0, int(info["duration"] - (now_ts - info["time"])))
                    lines.append(f"  - {pair[0]} -> {pair[1]}: причина - {info['reason']}, осталось: {left}s")
            lines.append(f"✔️ Успешных сигналов за период: {signals}")
            lines.append(f"🔍 Всего проверено пар: {checked}")
            if dex_iss:
                lines.append("🔎 Dexscreener замечания:")
                for t in dex_iss[:50]:
                    lines.append(f"  - {t}")
            if skipped:
                lines.append("🧹 Причины отсева:")
                # ограничим объём, но покажем основные
                for reason, items in list(skipped.items())[:30]:
                    # резон и до ~10 примеров для читаемости
                    preview = ", ".join(items[:10])
                    more = "" if len(items) <= 10 else f" (+{len(items)-10})"
                    lines.append(f"  - {reason}: {preview}{more}")
            lines.append("===========================")
            send_telegram("\n".join(lines))
            # сбрасываем статистику периода
            reset_cycle_stats()
            last_report_time = now_ts

        time.sleep(0.5)

# ===================== ENTRY =====================
if __name__ == "__main__":
    try:
        strategy_loop()
    except KeyboardInterrupt:
        print("Stopped by user")
    except Exception as e:
        send_telegram(f"❗ Bot crashed: {repr(e)}")
        raise
      
