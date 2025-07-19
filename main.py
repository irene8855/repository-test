import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz
import traceback

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3
LEAD_THRESH = 0.7
CONFIRM_THRESH = 2.5
PREDICT_THRESH = 2.5
CONFIDENCE_THRESH = 1.5

LONDON = pytz.timezone("Europe/London")

TOKENS = {
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK":  "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH":"0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"
}

UNI_POOLS = {
    "LDO": "0xd4ca396007c5d043fae4d14f95b9ed581055264d",
    "SAND": "0x49aa71c4f44c2d60c285346071cf0413deec1877",
    "FRAX": "0x43e59f7ddbe2c2ad8e51c29112ee8e473b31f4f3",
    "LINK": "0xa3f558aeb1f5f60c36f6ee62bfb9a1dbb5fc7c53"
}

SUSHI_POOLS = {
    "SUSHI": "0x3e2d3c1e052c481832c1082d7f6a3ceef24502f7",
    "wstETH": "0x817f7c0c764f74e6b0a67f1185c907c0eb6f39f3",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740"
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI = "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
entries = {}
sem = asyncio.Semaphore(10)

def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

async def g_query(sess, url, q):
    try:
        async with sess.post(url, json={"query": q}, timeout=8) as r:
            js = await r.json()
            return js.get("data", {}).get("pool")
    except: return None

async def price_uniswap(sess, sym):
    pid = UNI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_UNI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    if pool: return float(pool["token0Price"]), "Uniswap", ""
    return None

async def price_sushi(sess, sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_SUSHI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    if pool: return float(pool["token0Price"]), "SushiSwap", ""
    return None

async def price_dex(sess, addr):
    try:
        js = await (await sess.get(DEX_URL + addr, timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None
        best = max(pools, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        price = float(best["priceUsd"])
        platform = best["dexId"].capitalize()
        url = best.get("url", "")
        return price, platform, url
    except: return None

async def best_price(sess, sym, addr):
    results = await asyncio.gather(
        price_dex(sess, addr),
        price_uniswap(sess, sym),
        price_sushi(sess, sym)
    )
    best = [r for r in results if r and r[0] is not None]
    if not best: return None, None, None
    return max(best, key=lambda x: x[0])

def check_volatility(prices):
    if len(prices) < 2: return 0
    try:
        min_price = min(prices)
        max_price = max(prices)
        if min_price == 0:  # избегаем деления на 0
            return 0
        return max_price / min_price - 1
    except Exception:
        return 0

def check_trend(prices):
    if len(prices) < 2:
        return False
    if prices[0] is None or prices[-1] is None:
        return False
    return prices[-1] > prices[0]

async def monitor(sess, sym, addr):
    async with sem:
        res = await best_price(sess, sym, addr)
        if not res:
            return
        price, source, url = res
        if price is None:
            return

        now = datetime.now(LONDON)
        history[sym].append((now, price))

        lead = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW) and p is not None]
        vol_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=VOLATILITY_WINDOW) and p is not None]
        trend_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=TREND_WINDOW) and p is not None]

        if sym in entries:
            entry_time, _ = entries[sym]
            if now >= entry_time and entries[sym][1] is None:
                entries[sym] = (entry_time, price)
                await send(f"🚀 *ENTRY ALERT*\n{sym} → USDT\n💰 Цена входа: {price:.4f}\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")

        if len(lead) >= 3:
            min_lead = min(lead)
            if min_lead is None or min_lead == 0:
                return
            speed = (price / min_lead - 1) * 100

            volatility = check_volatility(vol_window)
            confidence = speed / volatility if volatility > 0 else 0
            proj = speed * (3 / LEAD_WINDOW)
            entry = now + timedelta(minutes=2)
            exit_ = entry + timedelta(minutes=3)

            if (
                speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                check_trend(trend_window) and confidence >= CONFIDENCE_THRESH and price > min_lead
            ):
                entries[sym] = (entry, None)
                await send(f"🔮 *PREDICTIVE ALERT*\n💡 _Вход в сделку через 2 минуты_\n{sym} → USDT\n⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}\n📈 Прогноз: +{proj:.2f}%\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")
            elif speed >= LEAD_THRESH:
                await send(f"📉 *EARLY LEAD ALERT*\n⚠️ _Цена уже растёт. Можно входить, но без прогноза_\n{sym} → USDT\n📈 Рост: +{speed:.2f}% за {LEAD_WINDOW} мин\n📡 Источник: {source or
