import asyncio
import aiohttp
import time
import os
from datetime import datetime, timedelta
import logging

TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")
THRESHOLD = float(os.getenv("THRESHOLD", 1.5))
MAX_PARALLEL = 5

PAIRS = os.getenv("PAIRS", "GMT,SAND,APE").split(",")
DEX_NAMES = {
    "uniswap": "https://app.uniswap.org",
    "sushiswap": "https://www.sushi.com",
    "pancakeswap": "https://pancakeswap.finance",
    "stepn": "https://www.stepn.com",
    "raydium": "https://raydium.io",
}

semaphore = asyncio.Semaphore(MAX_PARALLEL)
results = {}
session = None


def log(msg):
    print(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), msg)


async def send(msg):
    if not TOKEN or not CHAT_ID:
        log("Missing TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            log(await resp.text())


async def fetch_with_retry(url, retries=3, delay=2):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log(f"fetch error: {e}")
        await asyncio.sleep(delay)
    return None


async def get_price_data(pair):
    async with semaphore:
        url = f"https://api.dexscreener.com/latest/dex/pairs/{pair}"
        data = await fetch_with_retry(url)
        if data and data.get("pair"):
            return data["pair"]
        # fallback
        alt_url = f"https://api.geckoterminal.com/api/v2/search/pairs?query={pair}"
        alt = await fetch_with_retry(alt_url)
        return alt["data"][0]["attributes"] if alt and "data" in alt and alt["data"] else None


async def track_pair(pair):
    global results
    data = await get_price_data(pair)
    if not data:
        return

    token0 = data.get("baseToken", {}).get("symbol") or data.get("base_symbol", "")
    token1 = data.get("quoteToken", {}).get("symbol") or data.get("quote_symbol", "")
    price_now = float(data.get("priceUsd", data.get("price_usd", 0)))
    min_price = float(data.get("priceNative", price_now)) * 0.985
    dex = data.get("dexId", "").lower()
    dex_link = DEX_NAMES.get(dex, f"https://dexscreener.com/{dex}")

    key = f"{token0}-{token1}"
    if key not in results:
        results[key] = {"time": datetime.utcnow(), "price": price_now}

        # early alert
        msg = (
            f"*ð EARLY ALERT*
"
            f"{token0} â {token1}
"
            f"BUY NOW  : {datetime.utcnow().strftime('%H:%M')}
"
            f"SELL ETA : {(datetime.utcnow() + timedelta(minutes=3)).strftime('%H:%M')}  "
            f"(proj +{round((price_now - min_price) / min_price * 100, 2)}%)
"
            f"DEX now  : [{dex.capitalize()}]({dex_link})
"
            f"Now      : {price_now:.6f} $
"
            f"Min (3â10 m): {min_price:.6f} $
"
            f"Threshold: {THRESHOLD}%"
        )
        await send(msg)

        await asyncio.sleep(180)  # wait 3 mins

        final_data = await get_price_data(pair)
        if final_data:
            exit_price = float(final_data.get("priceUsd", final_data.get("price_usd", 0)))
            pl = round((exit_price - price_now) / price_now * 100, 2)
            result = (
                f"*â RESULT {token0} â {token1}*
"
                f"ENTRY {results[key]['time'].strftime('%H:%M')} : {price_now:.6f} $
"
                f"EXIT  {(datetime.utcnow()).strftime('%H:%M')} : {exit_price:.6f} $
"
                f"P/L   : *{pl:+.2f}%*
"
                f"DEX   : [{dex.capitalize()}]({dex_link})"
            )
            await send(result)

        del results[key]


async def runner():
    await send("ð  Bot updated and running (patched version)")
    log("DEBUG: patched version running")
    while True:
        tasks = [track_pair(pair.strip()) for pair in PAIRS]
        await asyncio.gather(*tasks)
        await asyncio.sleep(30)


async def main():
    global session
    async with aiohttp.ClientSession() as sess:
        session = sess
        await runner()


if __name__ == "__main__":
    asyncio.run(main())
