"""
Polygon-bot: DexScreener + Uniswap + Sushi (+1inch) + Gecko
• EARLY LEAD ALERT  (+0.7 % за 2 мин) – предупреждение
• CONFIRMED ALERT   (+1.5 % факт)     – подтверждение
"""

import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz, math

# ── настройки ──────────────────────────────────────────
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = int(os.getenv("CHAT_ID", "-1000000000000"))
ONEINCH_KEY = os.getenv("ONEINCH_KEY")             # опционально

CHECK_SEC      = 15
LEAD_WINDOW    = 2       # мин
LEAD_THRESH    = 0.7     # % за окно
CONFIRM_THRESH = 1.5     # % к min-10m
LONDON         = pytz.timezone("Europe/London")

TOKENS = {
    "BET":  "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO":  "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT":  "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":  "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
}

# id пулов token/USDT (v3)
UNI_POOLS   = { "BET": "0x049b3ff7e8a2b8e61334a956b9bb904f1d693572",
                "LDO": "0xd4ca3960626c2c0d01cfba0412afe1e424d4ecc2" }
SUSHI_POOLS = { "BET": "0x45294918dbcd2ca522fd3e6e1751a32b31dcf484",
                "LDO": "0xd255d32b714b589ca0f7bf1483e67a6cf18ea4a2" }

DEX_URL   = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI="https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"
GECKO     = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot      = Bot(TG_TOKEN)
history  = {s: deque(maxlen=600) for s in TOKENS}
sem      = asyncio.Semaphore(10)

# ── утилиты ────────────────────────────────────────────
def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")
async def send(msg): await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

async def g_query(sess,url,q):
    try:
        async with sess.post(url,json={"query":q},timeout=8) as r:
            js=await r.json()
            return js["data"]["pool"]
    except Exception as e:
        print("[GraphQL]", e); return None

# ── источники цены ─────────────────────────────────────
async def price_uniswap(sess,sym):
    pid = UNI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_UNI,
        f'{{ pool(id:"{pid}") {{ token0Price }} }}')
    if not pool:
        print(f"[WARN] No Uni pool for {sym}")
        return None
    return float(pool["token0Price"])

async def price_sushi(sess,sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None
    pool = await g_query(sess, GRAPH_SUSHI,
        f'{{ pool(id:"{pid}") {{ token0Price }} }}')
    if not pool:
        print(f"[WARN] No Sushi pool for {sym}")
        return None
    return float(pool["token0Price"])

async def price_dex(sess,addr):
    try:
        js = await (await sess.get(DEX_URL+addr,timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None
        best = max(pools, key=lambda p: float(p.get("liquidity",{}).get("usd",0)))
        return float(best["priceUsd"])
    except Exception as e:
        print("[DexS] err", e); return None

async def price_1inch(sess,addr):
    if not ONEINCH_KEY: return None  # пропускаем, если нет ключа
    url = f"https://api.1inch.dev/price/v1.1/137/{addr}"
    try:
        async with sess.get(url,headers={"Authorization":f"Bearer {ONEINCH_KEY}"},timeout=8) as r:
            if r.status!=200: return None
            js=await r.json(); return float(js["price"])
    except Exception as e:
        print("[1inch]", e); return None

async def price_gecko(sess,addr):
    try:
        js = await (await sess.get(GECKO+addr,timeout=8)).json()
        return float(js["data"]["attributes"]["price_usd"])
    except: return None

async def best_price(sess,sym,addr):
    tasks=[
        price_dex(sess,addr),
        price_uniswap(sess,sym),
        price_sushi(sess,sym),
        price_1inch(sess,addr)
    ]
    prices = [p for p in await asyncio.gather(*tasks) if p]
    if prices:
        avg = sum(prices)/len(prices)
        return avg
    return await price_gecko(sess,addr)

# ── монитор ───────────────────────────────────────────
async def monitor(sess,sym,addr):
    async with sem:
        price = await best_price(sess,sym,addr)
        if price is None: return
        now = datetime.now(LONDON)
        history[sym].append((now,price))
        print(f"[{ts()}] {sym}: {price:.6f}$")

        # Lead-скорость
        last=[p for t,p in history[sym] if now-t<=timedelta(minutes=LEAD_WINDOW)]
        if len(last)>=3:
            speed=(price/min(last)-1)*100
            proj=speed*(3/LEAD_WINDOW)
            if speed>=LEAD_THRESH and proj>=CONFIRM_THRESH:
                await send(
f"📈 *EARLY LEAD ALERT*\n*{sym} → USDT*\n"
f"↗️ +{speed:.2f}% за {LEAD_WINDOW} м (proj +{proj:.2f}% через 3 м)\n"
f"[DexScreener](https://dexscreener.com/polygon/{addr})"
)

        # Confirmed
        past=[p for t,p in history[sym] if timedelta(minutes=3)<=now-t<=timedelta(minutes=10)]
        if past:
            min_p=min(past)
            if price>=min_p*(1+CONFIRM_THRESH/100):
                await send(
f"✅ *CONFIRMED ALERT*\n*{sym} → USDT*\n"
f"+{(price/min_p-1)*100:.2f}% за 3 м\n"
f"[DexScreener](https://dexscreener.com/polygon/{addr})"
)

# ── loop ───────────────────────────────────────────────
async def main():
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess,sym,addr) for sym,addr in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__=="__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("🔥 Fatal:", e)
