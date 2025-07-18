import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz

# ─── Настройки ──────────────────────────────────────────
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
ONEINCH_KEY = os.getenv("ONEINCH_KEY")  # не обязательно

CHECK_SEC = 15
LEAD_WINDOW = 2
LEAD_THRESH = 0.7
CONFIRM_THRESH = 1.5
PREDICT_THRESH = 0.9
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

USDT = "0xc2132d05d31c914a87c6611c10748aacb21d4fb"

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI = "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"
GECKO = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
sem = asyncio.Semaphore(10)

def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

# Источники данных
async def g_query(sess, url, q):
    try:
        async with sess.post(url, json={"query": q}, timeout=8) as r:
            js = await r.json()
            return js.get("data", {}).get("pool")
    except: return None

async def price_uniswap(sess, sym):
    pid = UNI_POOLS.get(sym)
    if not pid: return None, None
    pool = await g_query(sess, GRAPH_UNI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return (float(pool["token0Price"]), "Uniswap") if pool else (None, None)

async def price_sushi(sess, sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None, None
    pool = await g_query(sess, GRAPH_SUSHI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return (float(pool["token0Price"]), "SushiSwap") if pool else (None, None)

async def price_dex(sess, addr):
    try:
        js = await (await sess.get(DEX_URL + addr, timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None, None, None
        best = max(pools, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        price = float(best["priceUsd"])
        platform = best["dexId"].capitalize()
        url = best.get("url", "")
        return price, platform, url
    except: return None, None, None

async def price_1inch(sess, addr):
    headers = {}
    if ONEINCH_KEY:
        headers["Authorization"] = f"Bearer {ONEINCH_KEY}"
        url = f"https://api.1inch.dev/price/v1.1/137/{addr}"
    else:
        url = f"https://api.1inch.io/price/v1.1/137/{addr}"
    try:
        async with sess.get(url, headers=headers, timeout=8) as r:
            if r.status != 200: return None, None
            js = await r.json()
            return float(js["price"]), "1inch"
    except: return None, None

async def price_gecko(sess, addr):
    try:
        js = await (await sess.get(GECKO + addr, timeout=8)).json()
        if js and "data" in js:
            return float(js["data"]["attributes"]["price_usd"]), "GeckoTerminal"
    except: pass
    return None, None

async def best_price(sess, sym, addr):
    tasks = [
        price_dex(sess, addr),
        price_uniswap(sess, sym),
        price_sushi(sess, sym),
        price_1inch(sess, addr)
    ]
    results = await asyncio.gather(*tasks)
    for res in results:
        if res and res[0]:
            return res
    return await price_gecko(sess, addr) + (None,)

# Мониторинг монет
async def monitor(sess, sym, addr):
    async with sem:
        result = await best_price(sess, sym, addr)
        if not result: return
        price, source, url = result
        if not price: return

        now = datetime.now(LONDON)
        history[sym].append((now, price))

        last = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
        if len(last) >= 3:
            speed = (price / min(last) - 1) * 100
            proj = speed * (3 / LEAD_WINDOW)
            entry = now + timedelta(minutes=2)
            exit_ = entry + timedelta(minutes=3)

            if speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH:
                msg = (
f"🔮 *PREDICTIVE ALERT*
"
f"💡 _Вход в сделку через 2 минуты_
"
f"{sym} → USDT
"
f"⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}
"
f"📈 Прогноз: +{proj:.2f}%
"
f"📡 Источник: {source or '—'}
"
f"{'🔗 [Купить](' + url + ')' if url else ''}
"
f"🕒 {ts(now)}"
)
                await send(msg)

            elif speed >= LEAD_THRESH:
                msg = (
f"📉 *EARLY LEAD ALERT*
"
f"⚠️ _Цена уже растёт. Можно входить, но без прогноза_
"
f"{sym} → USDT
"
f"📈 Рост: +{speed:.2f}% за {LEAD_WINDOW} мин
"
f"📡 Источник: {source or '—'}
"
f"{'🔗 [Купить](' + url + ')' if url else ''}
"
f"🕒 {ts(now)}"
)
                await send(msg)

        past = [p for t, p in history[sym] if timedelta(minutes=3) <= now - t <= timedelta(minutes=10)]
        if past:
            min_p = min(past)
            if price >= min_p * (1 + CONFIRM_THRESH / 100):
                msg = (
f"✅ *CONFIRMED ALERT*
"
f"📊 _Сделка завершилась успешно_
"
f"{sym} → USDT
"
f"📈 Рост: +{(price / min_p - 1) * 100:.2f}% за 3м
"
f"📡 Источник: {source or '—'}
"
f"{'🔗 [Купить](' + url + ')' if url else ''}
"
f"🕒 {ts(now)}"
)
                await send(msg)

# Основной цикл
async def main():
    await send("✅ Crypto Bot запущен и следит за рынком...")
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
