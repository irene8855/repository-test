"""
Polygon-bot: DexScreener + Uniswap v3 + Sushi v3 + 1inch + Gecko
• EARLY Lead Alert  (+0.7 % за 2 мин)          — предупреждение
• CONFIRMED Alert   (+1.5 % к min-10 мин)      — подтверждение
"""

import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz

# ────────── базовые параметры ──────────
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = int(os.getenv("CHAT_ID", "-1000000000000"))
ONEINCH_KEY = os.getenv("ONEINCH_KEY")          # можно не задавать

CHECK_SEC      = 15
LEAD_WINDOW    = 2       # минут
LEAD_THRESH    = 0.7     # % за окно
CONFIRM_THRESH = 1.5     # % к min-10 мин
LONDON         = pytz.timezone("Europe/London")

# — адреса токенов (Polygon PoS)
TOKENS = {
    "BET"  : "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO"  : "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT"  : "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND" : "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT"  : "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX" : "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK" : "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "wstETH":"0x7f39c581f595b53c5cb06146cf25b2b466d7a7e",
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
}

# — v3-пулы (token / USDT, fee 0.05 %)
UNI_POOLS = {
    "BET"  : "0x049b3ff7e8a2b8e61334a956b9bb904f1d693572",
    "LDO"  : "0xd4ca3960626c2c0d01cfba0412afe1e424d4ecc2",
    "FRAX" : "0x4d79ed6c50f1f6eecbba049df9fa53d690a05f3b",
    "GMT"  : "0x6a103c9847ef8e26cb75843cfe1ccf18906f5d76",
    "SAND" : "0x9449e0c9c54d91e98c81ee1e15b5f8b32c1b34f4",
    "LINK" : "0x1dEaC4c3fd9f93A1E63b9dF4e621A332bDf6890D",
    "wstETH":"0x8e6ca8e16907b8f60fa1f4e5cc14ce826aff06be",
    "EMT"  : "0x1bd32df6c1e65bca308f87b3ddd207ed3ae03c0f",
    "SUSHI": "0x5e4cb52ecfe51463706e4d9dc47e630f240896b1",
}
SUSHI_POOLS = {
    "BET"  : "0x45294918dbcd2ca522fd3e6e1751a32b31dcf484",
    "LDO"  : "0xd255d32b714b589ca0f7bf1483e67a6cf18ea4a2",
    "FRAX" : "0xd5c13e4dcad5fd0a299d7a9d15dd579bc59e234b",
    "GMT"  : "0x4c4b0a8b1493b3db0f8a705bc4e285255a0d7ef6",
    "SAND" : "0xc09bbf77bb5f9fba5b9917c52d28c2c4e8c6e84d",
    "LINK" : "0x057954Da8D1eB1e574e0bBb6F3F9DEB4CAb07Fe4",
    "wstETH":"0x9b5c82c89d01b80507c246697b72edc4ec39dd21",
    "EMT"  : "0x0f2b4a1e212b56d71cd924ff65c7c739caed73eb",
    "SUSHI": "0x68319db85a383a1f06f52fd23a158b0996101f0b",
}

# — API-эндпойнты
DEX_URL     = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI   = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI = "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"
GECKO_URL   = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot      = Bot(TG_TOKEN)
history  = {sym: deque(maxlen=600) for sym in TOKENS}
sem      = asyncio.Semaphore(10)

# ────────── утилиты ──────────
def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

async def g_query(sess,url,q):
    try:
        async with sess.post(url,json={"query":q},timeout=8) as r:
            js = await r.json()
            return js["data"]["pool"]
    except: return None

# ────────── источники цены ──────────
async def price_uniswap(sess,sym):
    pid = UNI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_UNI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return float(pool["token0Price"]) if pool else None

async def price_sushi(sess,sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_SUSHI, f'{{pool(id:"{pid}"){{token0Price}}}}')
    return float(pool["token0Price"]) if pool else None

async def price_dex(sess,addr):
    try:
        js = await (await sess.get(DEX_URL+addr,timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None
        best = max(pools,key=lambda p: float(p.get("liquidity",{}).get("usd",0)))
        return float(best["priceUsd"])
    except: return None

async def price_1inch(sess,addr):
    if not ONEINCH_KEY: return None
    url = f"https://api.1inch.dev/price/v1.1/137/{addr}"
    try:
        h = {"Authorization": f"Bearer {ONEINCH_KEY}"}
        async with sess.get(url,headers=h,timeout=8) as r:
            if r.status != 200: return None
            js = await r.json(); return float(js["price"])
    except: return None

async def price_gecko(sess,addr):
    try:
        js = await (await sess.get(GECKO_URL+addr,timeout=8)).json()
        return float(js["data"]["attributes"]["price_usd"])
    except: return None

async def best_price(sess,sym,addr):
    tasks = [
        price_dex(sess,addr),
        price_uniswap(sess,sym),
        price_sushi(sess,sym),
        price_1inch(sess,addr)
    ]
    prices = [p for p in await asyncio.gather(*tasks) if p]
    if prices:
        return sum(prices)/len(prices)        # средняя цена
    return await price_gecko(sess,addr)

# ────────── монитор токена ──────────
async def monitor(sess,sym,addr):
    async with sem:
        price = await best_price(sess,sym,addr)
        if price is None: return
        now = datetime.now(LONDON)
        history[sym].append((now,price))
        print(f"[{ts()}] {sym}: {price:.6f}$")

        # — Lead-алерт
        recent = [p for t,p in history[sym] if now-t<=timedelta(minutes=LEAD_WINDOW)]
        if len(recent) >= 3:
            speed = (price/min(recent)-1)*100
            proj  = speed * (3/LEAD_WINDOW)
            if speed >= LEAD_THRESH and proj >= CONFIRM_THRESH:
                await send(
f"📈 *EARLY LEAD ALERT*\n*{sym} → USDT*\n"
f"+{speed:.2f}% за {LEAD_WINDOW}м  (proj +{proj:.2f}% через 3м)\n"
f"[DexScreener](https://dexscreener.com/polygon/{addr})"
)

        # — Confirmed-алерт
        past = [p for t,p in history[sym] if timedelta(minutes=3)<=now-t<=timedelta(minutes=10)]
        if past:
            min_p = min(past)
            if price >= min_p*(1+CONFIRM_THRESH/100):
                await send(
f"✅ *CONFIRMED ALERT*\n*{sym} → USDT*\n"
f"+{(price/min_p-1)*100:.2f}% за 3м\n"
f"[DexScreener](https://dexscreener.com/polygon/{addr})"
)

# ────────── основной цикл ──────────
async def main():
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess,sym,addr) for sym,addr in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__=="__main__":
    asyncio.run(main())
