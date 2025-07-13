import asyncio
import aiohttp
import os
import time
from datetime import datetime
from collections import deque
from math import isclose

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
THRESHOLD = 1.5
MAX_PARALLEL = 5
RETRY_LIMIT = 3
RETRY_DELAY = 2

semaphore = asyncio.Semaphore(MAX_PARALLEL)
recent_signals = deque(maxlen=30)

DEX_URLS = {
    "uniswap": "https://app.uniswap.org",
    "sushiswap": "https://www.sushi.com",
    "pancakeswap": "https://pancakeswap.finance",
    "stepn": "https://www.google.com/search?q=stepn+exchange",  # fallback
}

def ts():
    return datetime.utcnow().strftime('%H:%M')

async def send(msg: str):
    if not TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=payload) as r:
                if r.status != 200:
                    print(f"Telegram error {r.status}")
        except Exception as e:
            print("Send error:", e)

async def fetch_json(session, url):
    for attempt in range(RETRY_LIMIT):
        try:
            async with session.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
    return None

async def fetch_data():
    url = "https://api.dexscreener.com/latest/dex/pairs"
    async with aiohttp.ClientSession() as session:
        js = await fetch_json(session, url)
        pools = js.get("pairs") if js else []

        if not pools:
            print("fallback: using GeckoTerminal")
            url = "https://api.geckoterminal.com/api/v2/networks/eth/pools"
            js = await fetch_json(session, url)
            pools = js.get("data", []) if js else []

        return pools

async def analyze(pool):
    async with semaphore:
        try:
            symbol = pool.get("baseToken", {}).get("symbol")
            target = pool.get("quoteToken", {}).get("symbol")
            dex = pool.get("dexId", "")
            price = float(pool.get("priceUsd") or 0)
            change = float(pool.get("priceChange", {}).get("m5") or 0)

            if not symbol or not target or not price or not change:
                return

            if not isclose(change, 0.0) and change >= THRESHOLD:
                pair_id = pool.get("pairAddress", "")
                signal_id = f"{symbol}->{target}:{pair_id}"

                if signal_id in recent_signals:
                    return
                recent_signals.append(signal_id)

                price_now = price
                min_price = float(pool.get("priceChange", {}).get("m10Low") or price_now)

                dex_fmt = dex.capitalize()
                dex_url = DEX_URLS.get(dex.lower(), f"https://www.google.com/search?q={dex}+dex")

                msg = f"""🚀 EARLY ALERT
{symbol} → {target}
BUY NOW  : {ts()} on {dex_fmt}
SELL ETA : {ts3m()}  (proj +{change:.2f}%)
DEX now  : {dex_fmt}
Now      : {price_now:.6f} $
Min (3–10 m): {min_price:.6f} $
Threshold: {THRESHOLD}%"""

                await send(msg)
                asyncio.create_task(result_report(symbol, target, price_now, dex_fmt, dex_url))

        except Exception as e:
            print("analyze error:", e)

def ts3m():
    return (datetime.utcnow() + timedelta(minutes=3)).strftime('%H:%M')

async def result_report(symbol, target, entry_price, dex_fmt, dex_url):
    await asyncio.sleep(180)
    url = "https://api.dexscreener.com/latest/dex/pairs"
    async with aiohttp.ClientSession() as session:
        js = await fetch_json(session, url)
        pools = js.get("pairs") if js else []

        for pool in pools:
            if pool.get("baseToken", {}).get("symbol") == symbol and \
               pool.get("quoteToken", {}).get("symbol") == target:
                try:
                    exit_price = float(pool.get("priceUsd") or 0)
                    delta = ((exit_price - entry_price) / entry_price) * 100

                    msg = f"""✅ RESULT {symbol} → {target}
ENTRY {ts(-3)} : {entry_price:.6f} $
EXIT  {ts()} : {exit_price:.6f} $
P/L   : {delta:.2f} %
DEX   : {dex_fmt}
🔗 {dex_url}"""

                    await send(msg)
                except Exception as e:
                    print("Result calc error:", e)
                break

from datetime import timedelta

def ts(offset_min=0):
    return (datetime.utcnow() + timedelta(minutes=offset_min)).strftime('%H:%M')

async def main_loop():
    print("DEBUG: patched version running")
    await send("✅ Crypto-bot online 🚀")

    while True:
        try:
            pools = await fetch_data()
            tasks = [analyze(pool) for pool in pools]
            await asyncio.gather(*tasks)
        except Exception as e:
            print("fetch error:", e)
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main_loop())
