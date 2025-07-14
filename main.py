import asyncio, aiohttp, os
from datetime import datetime, timedelta
from collections import deque
from math import isclose

# ─── конфиг ──────────────────────────────────────────
TOKEN   = os.getenv("TG_TOKEN", os.getenv("BOT_TOKEN"))
CHAT_ID = os.getenv("CHAT_ID")
THRESHOLD    = 1.5
CHECK_SEC    = 30
MAX_PARALLEL = 5
RETRY_LIMIT  = 6
RETRY_DELAY  = 3

TOKENS = {               # ▶︎ отслеживаемые тикеры
    "BET", "FRAX", "EMT",
    "GMT", "SAND", "LDO", "SUSHI",
    "UNI", "APE", "AAVE", "LINK"
}

DEX_URLS = {             # ссылки для клика
    "uniswap":   "https://app.uniswap.org",
    "sushiswap": "https://www.sushi.com",
    "1inch":     "https://app.1inch.io",
    "pancakeswap": "https://pancakeswap.finance",
    "quickswap": "https://quickswap.exchange",
    "stepn":     "https://google.com/search?q=stepn+exchange"
}

sem  = asyncio.Semaphore(MAX_PARALLEL)
seen = deque(maxlen=30)

# ─── helpers ─────────────────────────────────────────
def ts(off=0): return (datetime.utcnow()+timedelta(minutes=off)).strftime('%H:%M')

async def send(text:str):
    if not TOKEN or not CHAT_ID:
        print("Missing TG_TOKEN/BOT_TOKEN or CHAT_ID"); return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        await s.post(url, data={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown"})

async def fetch_json(s,url):
    for a in range(RETRY_LIMIT):
        try:
            async with s.get(url,timeout=10) as r:
                if r.status==200: return await r.json()
        except: pass
        await asyncio.sleep(RETRY_DELAY*(2**a))
    return None

# ─── пары DexScreener → fallback GeckoTerminal ───────
async def get_pools():
    async with aiohttp.ClientSession() as s:
        js = await fetch_json(s,"https://api.dexscreener.com/latest/dex/pairs")
        pools = js.get("pairs") if js else []
        if pools: return pools
        # fallback
        print("fallback: using GeckoTerminal")
        js = await fetch_json(s,"https://api.geckoterminal.com/api/v2/networks/polygon/pools")
        raw = js.get("data",[]) if js else []
        pools=[]
        for it in raw:
            a = it.get("attributes",{})
            pools.append({
                "baseToken":{"symbol":a.get("token0_symbol","")},
                "quoteToken":{"symbol":a.get("token1_symbol","")},
                "dexId":a.get("dex_name","gecko"),
                "priceUsd":a.get("price_usd"),
                "priceChange":{
                    "m5":float(a.get("price_change_percentage_5m") or 0),
                    "m10Low":float(a.get("price_low_10m") or a.get("price_usd") or 0)
                }
            })
        return pools

# ─── RESULT через 3 мин ───────────────────────────────
async def result_report(sym,tgt,entry,dex_fmt,dex_url):
    await asyncio.sleep(180)
    for p in await get_pools():
        if p["baseToken"]["symbol"]==sym and p["quoteToken"]["symbol"]==tgt:
            exit_price=float(p.get("priceUsd") or 0)
            if exit_price:
                pl=(exit_price/entry-1)*100
                await send(
f"""✅ *RESULT* {sym} → {tgt}
ENTRY {ts(-3)} : {entry:.6f} $
EXIT  {ts()} : {exit_price:.6f} $
P/L   : {pl:+.2f} %
DEX   : [{dex_fmt}]({dex_url})"""
                )
            break

# ─── анализ пула ───────────────────────────────────────
async def analyze(pool):
    async with sem:
        try:
            sym = pool["baseToken"]["symbol"]; tgt=pool["quoteToken"]["symbol"]
            if sym not in TOKENS or tgt!="USDT": return
            change=float(pool["priceChange"]["m5"]); now=float(pool["priceUsd"] or 0)
            if now==0 or change<THRESHOLD: return
            sig=f"{sym}->{tgt}:{pool.get('pairAddress','')}"
            if sig in seen: return
            seen.append(sig)

            min_p=float(pool["priceChange"]["m10Low"] or now)
            dex=pool.get("dexId",""); dex_fmt=dex.capitalize()
            dex_url=DEX_URLS.get(dex.lower(), f"https://google.com/search?q={dex}+dex")

            await send(
f"""🚀 *EARLY ALERT*
*{sym} → {tgt}*
BUY NOW  : {ts()}
SELL ETA : {ts(3)} _(proj +{change:.2f}%)_
DEX now  : [{dex_fmt}]({dex_url})
Now      : {now:.6f} $
Min (3–10 m): {min_p:.6f} $
Threshold: {THRESHOLD}%"""
            )
            asyncio.create_task(result_report(sym,tgt,now,dex_fmt,dex_url))
        except Exception as e:
            print("analyze error:",e)

# ─── основной цикл ────────────────────────────────────
async def main():
    print("DEBUG: patched version running")
    await send("✅ *Crypto-bot online* 🚀")
    while True:
        try:
            pools = await get_pools()
            await asyncio.gather(*(analyze(p) for p in pools))
        except Exception as e:
            print("loop error:",e)
        await asyncio.sleep(CHECK_SEC)

if __name__=="__main__":
    asyncio.run(main())
