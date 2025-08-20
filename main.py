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
import traceback
from math import isfinite

import requests

# ===================== SECRETS / ENV =====================
# телеграм
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

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
GRAPH_INTERVAL    = int(os.getenv("GRAPH_INTERVAL", "300"))  # минимум секунд между запросами к The Graph (Uniswap)

# опциональные ключи
ONEINCH_API_KEY    = os.getenv("ONEINCH_API_KEY", "").strip()   # если пуст — 1inch v6 будет пропущен
GRAPH_API_KEY      = os.getenv("GRAPH_API_KEY", "").strip()     # если пуст — UniswapV3 через gateway недоступен

# 1inch
CHAIN_ID           = int(os.getenv("CHAIN_ID", "137"))  # Polygon
ONEINCH_V6_URL     = f"https://api.1inch.dev/swap/v6.0/{CHAIN_ID}/quote"
ONEINCH_V5_URL     = f"https://api.1inch.io/v5.0/{CHAIN_ID}...e"  # публичный — часто отдаёт HTML; используем лишь как попытку

# UniswapV3 graph
UNISWAP_V3_SUBGRAPH_ID = os.getenv("UNISWAP_V3_SUBGRAPH_ID", "BvYimJ6vCLkk63oWZy7WB5cVDTVVMugUAF35RAUZpQXE")
GRAPH_GATEWAY_BASE     = "https://gateway.thegraph.com/api"

# Dexscreener
DEXSCREENER_TOKEN_URL  = "https://api.dexscreener.com/latest/dex/tokens/"

# ===================== TOKENS & DECIMALS =====================
TOKENS = {
    # базовые
    "USDT":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "USDC":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "DAI":    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX":   "0x104592a158490a9228070e0a8e5343b499e125d0",

    # ликвидные и из твоего списка
    "wstETH": "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0",
    "BET":    "0x1bdf71ede1a4777db1eebe7232bcda20d6fc1610",
    "WPOL":   "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",  # wrapped POL (на самом деле WETH на PoS, но оставляю как в твоих названиях)
    "tBTC":   "0x00e38e0875737b4665e764f0ce0e5d2c1d1723a9",
    "SAND":   "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":    "0xe631d4f0f1e4b4f1165f8f9a036d2b0b3f2a992b",
    "LINK":   "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "EMT":    "0x09ee5f2b2af9cefc4a62f1dd8b0f2e1c044234df",
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
last_graph_ts = 0.0

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
    return time.strftime("%Y-%m-%d %H:%M:%S")

def add_skip(reason: str, pair: str):
    with stats_lock:
        stats_snapshot["skipped"].setdefault(reason, []).append(pair)

def add_dex_issue(text: str):
    with stats_lock:
        stats_snapshot["dex_issues"].append(text)

def inc_checked():
    with stats_lock:
        stats_snapshot["checked"] += 1

def inc_signal():
    with stats_lock:
        stats_snapshot["signals"] += 1

def clean_ban_list():
    now_ts = time.time()
    expired = []
    for k, v in ban_list.items():
        if now_ts - v["time"] >= v["duration"]:
            expired.append(k)
    for k in expired:
        del ban_list[k]

def set_ban(pair, reason, duration=60):
    ban_list[pair] = {"time": time.time(), "reason": reason, "duration": duration}

def snapshot_ban():
    with stats_lock:
        stats_snapshot["ban_details"] = dict(ban_list)

# ===================== TELEGRAM =====================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM:", text)
        return
    try:
        pace_requests()
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=REQUEST_TIMEOUT)
    except Exception:
        traceback.print_exc()

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
            price = float(pu) if pu is not None else None
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

def univ3_quote_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    """Грубая оценка через sqrtPrice и самый ликвидный пул."""
    url = graph_url()
    if not url:
        return None, "Uniswap skipped (no GRAPH_API_KEY)"
    src = src_addr.lower()
    dst = dst_addr.lower()
    # cooldown (экономим квоту The Graph)
    global last_graph_ts
    # cooldown for The Graph to save monthly quota
    now_ts = time.time()
    if now_ts - last_graph_ts < GRAPH_INTERVAL:
        left = int(GRAPH_INTERVAL - (now_ts - last_graph_ts))
        return None, f"Uniswap skipped (cooldown {left}s)"
    # mark request time immediately (count even if fails)
    last_graph_ts = now_ts
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

        # берём самый ликвидный пул
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

# ===================== 1inch =====================
def oneinch_quote_amount_out(src_addr: str, dst_addr: str, amount_units: int):
    params = {
        # оба формата, т.к. v6 и v5 используют разные названия
        "src": src_addr, "dst": dst_addr,
        "fromTokenAddress": src_addr, "toTokenAddress": dst_addr,
        "amount": str(amount_units),
        "disableEstimate": "true",
    }

    # 1) v6 (требует ключ)
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
        q["source"] = q.get("source") or "UniswapV3"
        return q, reasons
    if err: reasons.append(err)

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
    if entry_units_base <= 0: return 0.0
    return (exit_units_base - entry_units_base) / entry_units_base * 100.0

# ===================== STRATEGY LOOP =====================
def start_monitor(base_symbol, token_symbol, entry_sell_units, buy_amount_token_units, source_tag):
    # отдельный поток следит за окном удержания; шлёт финальное сообщение
    def _run():
        try:
            t0 = time.time()
            target = MIN_PROFIT_PERCENT
            stop = STOP_LOSS_PERCENT
            base_dec = DECIMALS.get(base_symbol, 6)
            token_dec = DECIMALS.get(token_symbol, 18)

            # опрос цены выхода через те же источники, но в обратном направлении
            while time.time() - t0 < HOLD_SECONDS:
                time.sleep(5)
                exit_quote, _ = quote_amount_out(token_symbol, base_symbol, int(buy_amount_token_units))
                if exit_quote and exit_quote.get("buyAmount"):
                    exit_units_base = int(exit_quote["buyAmount"])
                    pnl = profit_pct_by_units(entry_sell_units, exit_units_base)
                    # цель?
                    if pnl >= target:
                        send_telegram(
                            f"🎯 Цель достигнута: {pnl:.2f}% по {token_symbol} (Источник: {exit_quote.get('source','?')})"
                        )
                        break
                    # стоп?
                    if pnl <= stop:
                        send_telegram(
                            f"🛑 Стоп-лосс: {pnl:.2f}% по {token_symbol} (Источник: {exit_quote.get('source','?')})"
                        )
                        break

            # финальный пересчёт и сообщение
            exit_quote, _ = quote_amount_out(token_symbol, base_symbol, int(buy_amount_token_units))
            if exit_quote and exit_quote.get("buyAmount"):
                exit_units_base = int(exit_quote["buyAmount"])
                pnl = profit_pct_by_units(entry_sell_units, exit_units_base)
                pnl_usd = (exit_units_base - entry_sell_units) / (10 ** base_dec)
                send_telegram(
                    f"✅ Финальный результат\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Источник: {exit_quote.get('source','?')}\n"
                    f"PnL: {pnl:.2f}% (~{pnl_usd:.2f} {base_symbol})\n"
                    f"Время: {now_local()}"
                )
            else:
                send_telegram(
                    f"✅ Финальный результат\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Источник: н/д\n"
                    f"PnL: н/д (нет котировки на выход)\n"
                    f"Время: {now_local()}"
                )
        except Exception as e:
            send_telegram(f"❗ Monitor crashed: {repr(e)}\n{traceback.format_exc()}")
    th = threading.Thread(target=_run, daemon=True)
    th.start()

def strategy_loop():
    global last_report_time
    # приветствие
    send_telegram("🚀 Бот запущен. Источники: 1inch (v6 при наличии ключа, v5 публичный) → UniswapV3 (Graph) → Dexscreener.")

    while True:
        loop_start = time.time()
        checked_before = 0

        # снимок бан-листа
        snapshot_ban()
        ban_det = stats_snapshot.get("ban_details", {})
        dex_iss = []
        skipped = {}
        signals = 0
        checked = 0

        def add_skip_local(reason, base_sym, tok_sym):
            key = reason
            skipped.setdefault(key, []).append(f"{base_sym}->{tok_sym}")

        # проход по базовым
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

                # бан-лист?
                if key in ban_list:
                    left = max(0, int(ban_list[key]["duration"] - (time.time() - ban_list[key]["time"])))
                    add_skip_local(f"Banned ({ban_list[key]['reason']}, left {left}s)", *key)
                    continue

                # котировка base -> token
                try:
                    quote, reasons = quote_amount_out(base_symbol, token_symbol, entry_sell_units)
                except Exception as e:
                    reasons = [f"quote EXC: {repr(e)}"]
                    quote = None

                if not quote or not quote.get("buyAmount"):
                    # отклоняем, баним на 40-60с
                    reason = "No quote"
                    if reasons:
                        # укоротим список, чтобы не раздувать отчёт
                        reason = f"Cause {base_symbol}->{token_symbol}: " + ", ".join(reasons[:10])
                    add_skip_local(reason, base_symbol, token_symbol)
                    set_ban(key, "No quote", duration=40 + int(20*math.sin(time.time())))
                    continue

                # оценка «замкнутого» цикла base -> token -> base (грубо)
                out_token_units = int(quote["buyAmount"])
                back_quote, back_reasons = quote_amount_out(token_symbol, base_symbol, out_token_units)
                if not back_quote or not back_quote.get("buyAmount"):
                    why = " / ".join(back_reasons[:5]) if back_reasons else "No back-quote"
                    add_skip_local(f"No back-quote: {why}", base_symbol, token_symbol)
                    set_ban(key, "No back-quote", duration=30)
                    continue

                exit_units_base = int(back_quote["buyAmount"])
                exp_pnl = profit_pct_by_units(entry_sell_units, exit_units_base)

                if exp_pnl < MIN_PROFIT_PERCENT:
                    add_skip_local(f"Low profit < {MIN_PROFIT_PERCENT:.1f}%", base_symbol, token_symbol)
                    continue

                # ===== Предварительное сообщение о сделке =====
                inc_signal()
                send_telegram(
                    f"📣 Предварительный сигнал\n"
                    f"PAIR: {base_symbol}->{token_symbol}->{base_symbol}\n"
                    f"Источник входа: {quote.get('source','?')}\n"
                    f"Ожидаемый PnL: {exp_pnl:.2f}%\n"
                    f"План: удержание ~{HOLD_SECONDS//60}-{(HOLD_SECONDS//60)+3} мин, цель {MIN_PROFIT_PERCENT:.2f}%, стоп {STOP_LOSS_PERCENT:.2f}%\n"
                    f"Время: {now_local()}"
                )

                # старт мониторинга (финальное сообщение будет из монитор-потока)
                start_monitor(base_symbol, token_symbol, entry_sell_units, out_token_units, quote.get('source','?'))

                # пост-охлаждение на пару, чтобы не спамить повторы
                set_ban(key, "cooldown", duration=20)

        # ========== 15-минутный отчёт ==========
        now_ts = time.time()
        if last_report_time == 0.0 or (now_ts - last_report_time) >= REPORT_INTERVAL:
            with stats_lock:
                ban_det = dict(ban_list)
                dex_iss = list(stats_snapshot.get("dex_issues", []))
                skipped = dict(stats_snapshot.get("skipped", {}))
                signals = int(stats_snapshot.get("signals", 0))
                checked = int(stats_snapshot.get("checked", 0))

            lines = []
            lines.append("===== PROFILER REPORT =====")
            lines.append(f"⏱ Время полного цикла: {time.time()-loop_start:.2f} сек")
            lines.append(f"🔧 MAX_RPS={MAX_RPS}, GRAPH_INTERVAL={GRAPH_INTERVAL}s, TheGraph cooldown left={max(0, int(GRAPH_INTERVAL - (time.time()-last_graph_ts)))}s")
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
                # отсортируем ключи для стабильности
                for k in sorted(skipped.keys()):
                    vals = skipped[k]
                    head = ", ".join(vals[:10])
                    extra = f" (+{len(vals)-10})" if len(vals) > 10 else ""
                    lines.append(f"  - {k}: {head}{extra}")

            send_telegram("\n".join(lines))

            # сбрасываем статистику периода
            with stats_lock:
                stats_snapshot["checked"] = 0
                stats_snapshot["signals"] = 0
                stats_snapshot["skipped"] = {}
                stats_snapshot["dex_issues"] = []
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
      
