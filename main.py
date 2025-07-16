"""
Polygon-bot: DexScreener + Uniswap + Sushi + 1inch (+Gecko)
• EARLY LEAD ALERT (+0.7 % за 2 мин)  →  предупреждение
• CONFIRMED ALERT  (+1.5 % факт)      →  подтверждение
"""

import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz

# ─── настройки ───────────────────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))
ONEINCH_KEY = os.getenv("ONEINCH_KEY")       # ← можно не задавать

CHECK_SEC      = 15
LEAD_WINDOW    = 2       # мин
LEAD_THRESH    = 0.7     # % за окно
CONFIRM_THRESH = 1.5     # итог % к min-10m
LONDON         = pytz.timezone("Europe/London")

TOKENS = {
    "BET":  "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO":  "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT":  "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":  "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
}

# id пулов token/USDT для Uniswap-v3 и Sushi-v3
UNI_POOLS   = { "BET": "0x049b3ff7e…572", "LDO":"0xd4ca3960…cc2" }
SUSHI_POOLS = { "BET": "0x45294918…f484", "LDO":"0xd255d32b…a4a2" }

# постоянные адреса
USDT = "0xc2132d05d31c914a87c6611c10748aacb21d4fb"

DEX_URL   = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI="https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"
GECKO     = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot     = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
sem     = asyncio.Semaphore(10)

# ─── утилиты ─────────────────────────────────────────────
def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID,text=msg,parse_mode="Markdown")

async def g_query(sess,url,q):
    try:
        async with sess.post(url,json={"query":q},timeout=8) as r:
            js=await r.json(); return js["data"]["pool"]
    except: return None

async def price_uniswap(sess,sym):
    pid=UNI_POOLS.get(sym); 
    if not pid: return None
    pool=await g_query(sess,GRAPH_UNI,f'{{pool(id:"{pid}"){{token0Price}}}}')
    return float(pool["token0Price"]) if pool else None

async def price_sushi(sess,sym):
    pid=SUSHI_POOLS.get(sym)
    if not pid: return None
    pool=await g_query(sess,GRAPH_SUSHI,f'{{pool(id:"{pid}"){{token0Price}}}}')
    return float(pool["token0Price"]) if pool else None

async def price_dex(sess,addr):
    try:
        js=await (await sess.get(DEX_URL+addr,timeout=10)).json()
        pools=js.get("pairs") or []
        if not pools: return None
        best=max(pools,key=lambda p: float(p.get("liquidity",{}).get("usd",0)))
        return float(best["priceUsd"])
    except: return None

async def price_1inch(sess,addr):
    headers={}
    if ONEINCH_KEY:
        headers["Authorization"]=f"Bearer {ONEINCH_KEY}"
        url=f"https://api.1inch.dev/price/v1.1/137/{addr}"
    else:
        url=f"https://api.1inch.io/price/v1.1/137/{addr}"
    try:
        async with sess.get(url,headers=headers,timeout=8) as r:
            if r.status!=200: return None
            js=await r.json()
            return float(js["price"])
    except: return None

async def price_gecko(sess,addr):
    js=await (await sess.get(GECKO+addr,timeout=8)).json()
    if js and "data" in js:
        return float(js["data"]["attributes"]["price_usd"])
    return None

async def best_price(sess,sym,addr):
    tasks=[
        price_dex(sess,addr),
        price_uniswap(sess,sym),
        price_sushi(sess,sym),
        price_1inch(sess,addr)
    ]
    for p in await asyncio.gather(*tasks):
        if p: return p
    return await price_gecko(sess,addr)

# ─── монитор токена ────────────────────────────────────
async def monitor(sess,sym,addr):
    async with sem:
        price=await best_price(sess,sym,addr)
        if price is None: return
        now=datetime.now(LONDON)
        history[sym].append((now,price))

        # Lead-скорость
        last=[p for t,p in history[sym] if now-t<=timedelta(minutes=LEAD_WINDOW)]
        if len(last)>=3:
            speed=(price/min(last)-1)*100
            proj=speed*(3/LEAD_WINDOW)
            if speed>=LEAD_THRESH and proj>=CONFIRM_THRESH:
                await send(
f"📈 *EARLY LEAD ALERT*\n"
f"{sym} → USDT  |  +{speed:.2f}% за {LEAD_WINDOW} м\n"
f"Прогноз на 3 м: +{proj:.2f}%\n"
f"Start: {ts(now)}"
)

        # Confirmed
        past=[p for t,p in history[sym] if timedelta(minutes=3)<=now-t<=timedelta(minutes=10)]
        if past:
            min_p=min(past)
            if price>=min_p*(1+CONFIRM_THRESH/100):
                await send(
f"✅ *CONFIRMED ALERT*\n"
f"{sym} → USDT  |  +{(price/min_p-1)*100:.2f}%\n"
f"Entry: {ts(now-timedelta(minutes=3))} → Exit: {ts(now)}"
)

# ─── основной цикл ─────────────────────────────────────
async def main():
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess,sym,addr) for sym,addr in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__=="__main__":
    asyncio.run(main())
