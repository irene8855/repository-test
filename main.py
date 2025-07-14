import asyncio, aiohttp, os
from datetime import datetime, timedelta
from collections import deque
from math import isclose

TOKEN   = os.getenv("TG_TOKEN", os.getenv("BOT_TOKEN"))
CHAT_ID = os.getenv("CHAT_ID")

THRESHOLD    = 1.5
CHECK_SEC    = 30
MAX_PARALLEL = 5
RETRY_LIMIT  = 6   # ⬆ увеличили (было 3)
RETRY_DELAY  = 3   # ⬆ увеличили (было 2)

TOKENS = {
    "BET", "FRAX", "EMT",
    "GMT", "SAND", "LDO", "SUSHI",
    "UNI", "APE", "AAVE", "LINK"
}

DEX_URLS = {
    "uniswap": "https://app.uniswap.org",
    "sushiswap": "https://www.sushi.com",
    "1inch": "https://app.1inch.io",
    "pancakeswap": "https://pancakeswap.finance",
    "stepn": "https://google.com/search?q=stepn+exchange",
}

sem  = asyncio.Semaphore(MAX_PARALLEL)
seen = deque(maxlen=30)

def ts(off=0): return (datetime.utcnow()+timedelta(minutes=off)).strftime('%H:%M')

async def send(msg):
    if not TOKEN or not CHAT_ID:
        print('Missing TOKEN/CHAT_ID'); return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.post(url, data=payload) as r:
                if r.status != 200:
                    print('Telegram error', r.status)
        except Exception as e:
            print('Send error:', e)

async def fetch_json(session, url):
    for attempt in range(RETRY_LIMIT):
        try:
            async with session.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            await asyncio.sleep(RETRY_DELAY * (2**attempt))
    return None

async def get_pools():
    url = "https://api.dexscreener.com/latest/dex/pairs"
    async with aiohttp.ClientSession() as s:
        js = await fetch_json(s, url)
        pools = js.get('pairs') if js else []
        if not pools:
            print('fallback: using GeckoTerminal')
            url = "https://api.geckoterminal.com/api/v2/networks/eth/pools"
            js = await fetch_json(s, url)
            pools = js.get('data', []) if js else []
        return pools

async def result_report(sym,tgt,entry,dex_fmt,dex_url):
    await asyncio.sleep(180)
    pools = await get_pools()
    for p in pools:
        if p.get('baseToken',{}).get('symbol')==sym and p.get('quoteToken',{}).get('symbol')==tgt:
            exit_price = float(p.get('priceUsd') or 0)
            if exit_price:
                delta=(exit_price/entry-1)*100
                msg=f"✅ *RESULT* {sym} → {tgt}\nENTRY {ts(-3)} : {entry:.6f} $\nEXIT  {ts()} : {exit_price:.6f} $\nP/L   : {delta:+.2f} %\nDEX   : [{dex_fmt}]({dex_url})"
                await send(msg)
            break

async def analyze(pool):
    async with sem:
        try:
            sym  = pool.get('baseToken',{}).get('symbol')
            tgt  = pool.get('quoteToken',{}).get('symbol')
            dex  = pool.get('dexId','')
            now  = float(pool.get('priceUsd') or 0)
            chg  = float(pool.get('priceChange',{}).get('m5') or 0)

            # DEBUG вывод всех видимых пар
            print(f"[DEBUG] pair {sym} → {tgt}  dex={dex}")

            if sym not in TOKENS or chg < THRESHOLD or now==0:
                return
            pair_id = pool.get('pairAddress','')
            sig_id = f"{sym}->{tgt}:{pair_id}"
            if sig_id in seen: return
            seen.append(sig_id)

            min_p = float(pool.get('priceChange',{}).get('m10Low') or now)
            dex_fmt = dex.capitalize()
            dex_url = DEX_URLS.get(dex.lower(), f"https://google.com/search?q={dex}+dex")

            msg=f"""🚀 *EARLY ALERT*
*{sym} → {tgt}*
BUY NOW  : {ts()}
SELL ETA : {ts(3)}  _(proj +{chg:.2f}%)_
DEX now  : [{dex_fmt}]({dex_url})
Now      : {now:.6f} $
Min (3–10 m): {min_p:.6f} $
Threshold: {THRESHOLD}%"""
            await send(msg)
            asyncio.create_task(result_report(sym,tgt,now,dex_fmt,dex_url))
        except Exception as e:
            print('analyze error:',e)

async def main():
    print('DEBUG: patched version running')
    await send('✅ *Crypto-bot online* 🚀')
    while True:
        try:
            pools=await get_pools()
            await asyncio.gather(*(analyze(p) for p in pools))
        except Exception as e:
            print('loop error:',e)
        await asyncio.sleep(CHECK_SEC)

if __name__=='__main__':
    asyncio.run(main())

