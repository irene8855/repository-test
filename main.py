import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz

# ─── Настройки ───────────────────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))
ONEINCH_KEY = os.getenv("ONEINCH_KEY")

CHECK_SEC      = 15
LEAD_WINDOW    = 2       # мин
LEAD_THRESH    = 0.7     # % за окно
CONFIRM_THRESH = 1.5     # итог % к min-10m
LONDON         = pytz.timezone("Europe/London")

TOKENS = {
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK":  "0x53e0bca35ec356bd5dddfebbD1Fc0fD03Fabad39",
    "SUSHI": "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",
    "wstETH":"0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"
}

UNI_POOLS = {
    "BET":   "0x049b3ff7e9c042b2fd408c83c94c760f53eb4572",
    "LDO":   "0xd4ca3960aa711b50b3c154471eb48fca31c47cc2",
    "SAND":  "0x7e48d1f02beff495704d9f611b2f1c48c71fede0",
    "LINK":  "0x8f8ef111b67c04eb1641f5ff19ee54cda062f163",
    "SUSHI": "0x88be0d52b0f3bf610ce7f42669e99f8655e5349d",
}

SUSHI_POOLS = {
    "BET":   "0x45294918781f0edc524309b9c9ae7d73564ff484",
    "LDO":   "0xd255d32b927b37b4e3a7fa5e1cfd89d460d7a4a2",
    "SAND":  "0xa23c5c53c0931255a1ec11cb6dc918547d218ac0",
    "LINK":  "0x3d71e9f87f6ecf5c3e6c4816c7c1e6542cddc4d0",
    "SUSHI": "0x42e091a565c1d02c3d4b4e2eea36468f2e8289ba",
}

USDT = "0xc2132d05d31c914a87c6611c10748aacb21d4fb"
DEX_URL    = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI  = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI= "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"
GECKO      = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot     = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
sem     = asyncio.Semaphore(10)

# ─── Утилиты ─────────────────────────────────────────────
def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID,text=msg,parse_mode="Markdown")

async def g_query(sess,url,q):
    try:
        async with sess.post(url,json={"query":q},timeout=8) as r:
            js=await r.json(); return js["data"]["pool"]
    except: return None

async def price_uniswap(sess,sym):
    pid = UNI_POOLS.get(sym); 
    if not pid: return None, None
    pool = await g_query(sess, GRAPH_UNI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return (float(pool["token0Price"]), "Uniswap") if pool else (None, None)

async def price_sushi(sess,sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None, None
    pool = await g_query(sess, GRAPH_SUSHI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return (float(pool["token0Price"]), "SushiSwap") if pool else (None, None)

async def price_dex(sess,addr):
    try:
        js = await (await sess.get(DEX_URL + addr, timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None, None
        best = max(pools, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        return float(best["priceUsd"]), "DexScreener"
    except: return None, None

async def price_1inch(sess,addr):
    headers={}
    if ONEINCH_KEY:
        headers["Authorization"]=f"Bearer {ONEINCH_KEY}"
        url=f"https://api.1inch.dev/price/v1.1/137/{addr}"
    else:
        url=f"https://api.1inch.io/price/v1.1/137/{addr}"
    try:
        async with sess.get(url, headers=headers, timeout=8) as r:
            if r.status != 200: return None, None
            js = await r.json()
            return float(js["price"]), "1inch"
    except: return None, None

async def price_gecko(sess,addr):
    try:
        js = await (await sess.get(GECKO + addr, timeout=8)).json()
        if js and "data" in js:
            return float(js["data"]["attributes"]["price_usd"]), "GeckoTerminal"
    except: pass
    return None, None

async def best_price(sess, sym, addr):
    sources = [
        price_dex(sess, addr),
        price_uniswap(sess, sym),
        price_sushi(sess, sym),
        price_1inch(sess, addr)
    ]
    results = await asyncio.gather(*sources)
    for price, src in results:
        if price: return price, src
    return await price_gecko(sess, addr)

# ─── Монитор токена ────────────────────────────────────
async def monitor(sess, sym, addr):
    async with sem:
        price, source = await best_price(sess, sym, addr)
        if price is None: return
        now = datetime.now(LONDON)
        history[sym].append((now, price))

        # Скорость изменения
        last = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
        if len(last) >= 3:
            min_last = min(last)
            speed = (price / min_last - 1) * 100
            proj  = speed * (3 / LEAD_WINDOW)
            if speed >= LEAD_THRESH and proj >= CONFIRM_THRESH:
                await send(
f"📉 *EARLY LEAD ALERT*\n"
f"*{sym} → USDT*\n"
f"📈 Рост: *+{speed:.2f}%* за {LEAD_WINDOW} мин\n"
f"🕰 Прогноз на 3 мин: *+{proj:.2f}%*\n"
f"📡 Источник: _{source}_\n"
f"🕒 {ts(now)}"
)

        # Подтверждённый рост
        past = [p for t, p in history[sym] if timedelta(minutes=3) <= now - t <= timedelta(minutes=10)]
        if past:
            min_p = min(past)
            if price >= min_p * (1 + CONFIRM_THRESH / 100):
                gain = (price / min_p - 1) * 100
                await send(
f"✅ *CONFIRMED ALERT*\n"
f"*{sym} → USDT*\n"
f"📊 Подтверждённый рост: *+{gain:.2f}%* за 3м\n"
f"📡 Источник: _{source}_\n"
f"🕒 {ts(now)}"
)

# ─── Основной цикл ─────────────────────────────────────
async def main():
    await send("✅ Crypto-бот активен 🚀")
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
