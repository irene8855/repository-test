import asyncio, aiohttp, os
from datetime import datetime, timedelta
from collections import deque
from math import isclose

# ─── конфиг ────────────────────────────────────────────────────
TOKEN   = os.getenv("TG_TOKEN", os.getenv("BOT_TOKEN"))
CHAT_ID = os.getenv("CHAT_ID")
THRESHOLD    = 1.5
CHECK_SEC    = 30
MAX_PARALLEL = 5
RETRY_LIMIT  = 6
RETRY_DELAY  = 3

# тикеры → адреса (Polygon)
TOKEN_ADDR = {
    "BET" : "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "EMT" : "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "GMT" : "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "LDO" : "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SUSHI":"0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "UNI" : "0xb33eaad8d922b1083446dc23f610c2567fb5180f",
    "APE" : "0xb7b31a6bc18e48888545ce79e83e06003be70930",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"
}
TOKENS = set(TOKEN_ADDR.keys())

DEX_URLS = {
    "uniswap":   "https://app.uniswap.org",
    "sushiswap": "https://www.sushi.com",
    "1inch":     "https://app.1inch.io",
    "pancakeswap":"https://pancakeswap.finance",
    "quickswap": "https://quickswap.exchange"
}

sem  = asyncio.Semaphore(MAX_PARALLEL)
seen = deque(maxlen=30)

# ─── helpers ───────────────────────────────────────────────────
def ts(off=0): return (datetime.utcnow()+timedelta(minutes=off)).strftime('%H:%M')

async def send(txt:str):
    if not TOKEN or not CHAT_ID: return
    await aiohttp.ClientSession().post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id":CHAT_ID,"text":txt,"parse_mode":"Markdown"}
    )

async def fetch_json(sess,url):
    for a in range(RETRY_LIMIT):
        try:
            async with sess.get(url,timeout=10) as r:
                if r.status==200: return await r.json()
        except: pass
        await asyncio.sleep(RETRY_DELAY*(2**a))
    return None

# ─── получить список пулов ───────────────────────────
async def get_pools():
    async with aiohttp.ClientSession() as s:
        # 1) DexScreener
        js = await fetch_json(s,"https://api.dexscreener.com/latest/dex/pairs")
        pools = js.get("pairs") if js else []
        if pools: return pools

        # 2) GeckoTerminal /pools
        js = await fetch_json(s,"https://api.geckoterminal.com/api/v2/networks/polygon/pools")
        raw = js.get("data",[]) if js else []
        if raw:
            pools=[]
            for it in raw:
                a=it["attributes"]
                pools.append({
                    "baseToken":{"symbol":a["token0_symbol"]},
                    "quoteToken":{"symbol":a["token1_symbol"]},
                    "dexId":a.get("dex_name","gecko"),
                    "priceUsd":a["price_usd"],
                    "priceChange":{
                        "m5":float(a.get("price_change_percentage_5m") or 0),
                        "m10Low":float(a.get("price_low_10m") or a["price_usd"])
                    }
                })
            return pools

        # 3) адресный fallback по каждому токену
        pools=[]
        for sym,addr in TOKEN_ADDR.items():
            url=f"https://api.geckoterminal.com/api/v2/networks/polygon/tokens/{addr}"
            js = await fetch_json(s,url)
            if not js or "data" not in js: continue
            a = js["data"]["attributes"]
            price=float(a.get("price_usd") or 0)
            chg = float(a.get("price_change_percentage_5m") or 0)
            low = float(a.get("price_low_10m") or price)
            pools.append({
                "baseToken":{"symbol":sym},
                "quoteToken":{"symbol":"USDT"},
                "dexId":"gecko",
                "priceUsd":price,
                "priceChange":{"m5":chg,"m10Low":low}
            })
        return pools

# ─── RESULT через 3 минуты ───────────────────────────
async def result(sym, entry, dex_fmt, dex_url):
    await asyncio.sleep(180)
    js = await get_pools()
    for p in js:
        if p["baseToken"]["symbol"]==sym:
            exit_p=float(p.get("priceUsd") or 0)
            if exit_p:
                pl=(exit_p/entry-1)*100
                await send(
f"""✅ *RESULT* {sym} → USDT
ENTRY {ts(-3)} : {entry:.6f} $
EXIT  {ts()} : {exit_p:.6f} $
P/L   : {pl:+.2f} %
DEX   : [{dex_fmt}]({dex_url})"""
                )
            break

# ─── анализ пула ─────────────────────────────────────
async def analyze(pool):
    async with sem:
        try:
            sym = pool["baseToken"]["symbol"]
            if sym not in TOKENS: return
            chg=float(pool["priceChange"]["m5"]); now=float(pool["priceUsd"] or 0)
            if now==0 or chg<THRESHOLD: return
            sig=sym+str(now)
            if sig in seen: return
            seen.append(sig)

            low=float(pool["priceChange"]["m10Low"] or now)
            dex=pool.get("dexId",""); dex_fmt=dex.capitalize()
            dex_url=DEX_URLS.get(dex.lower(), f"https://google.com/search?q={dex}+dex")

            await send(
f"""🚀 *EARLY ALERT*
*{sym} → USDT*
BUY NOW  : {ts()}
SELL ETA : {ts(3)} _(proj +{chg:.2f}%)_
DEX now  : [{dex_fmt}]({dex_url})
Now      : {now:.6f} $
Min (3–10 m): {low:.6f} $
Threshold: {THRESHOLD}%"""
            )
            asyncio.create_task(result(sym, now, dex_fmt, dex_url))
        except Exception as e:
            print("analyze error:", e)

# ─── основной цикл ───────────────────────────────────
async def main():
    print("DEBUG: address-fallback version running")
    await send("✅ *Crypto-bot online* 🚀")
    while True:
        pools=await get_pools()
        await asyncio.gather(*(analyze(p) for p in pools))
        await asyncio.sleep(CHECK_SEC)

if __name__=="__main__":
    asyncio.run(main())
