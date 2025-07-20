import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
from web3 import Web3
import pytz
import traceback

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
POLYGON_RPC = os.getenv("POLYGON_RPC")
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3
PREDICT_THRESH = 1.2
CONFIRM_THRESH = 2.0
CONFIDENCE_THRESH = 1.5

LONDON = pytz.timezone("Europe/London")

TOKENS = {
    "BET":    "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO":    "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "EMT":    "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":   "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT":    "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX":   "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK":   "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SUSHI":  "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"
}

UNI_POOLS = {
    "LDO": "0xd4ca396007c5d043fae4d14f95b9ed581055264d",
    "SAND": "0x49aa71c4f44c2d60c285346071cf0413deec1877",
    "FRAX": "0x43e59f7ddbe2c2ad8e51c29112ee8e473b31f4f3",
    "LINK": "0xa3f558aeb1f5f60c36f6ee62bfb9a1dbb5fc7c53"
}

SUSHI_POOLS = {
    "SUSHI":  "0x3e2d3c1e052c481832c1082d7f6a3ceef24502f7",
    "wstETH": "0x8f8ef111b67c04eb1641f5ff19ee54cda062f163",
    "GMT":    "0xe3c408bd53c31c085a1746af401a4042954ff740"
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI = "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
entries = {}
sem = asyncio.Semaphore(10)

def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")

def log(msg: str):
    with open("logs.txt", "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")

async def send(msg): 
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    log(msg.replace("\n", " | "))

async def g_query(sess, url, q):
    try:
        async with sess.post(url, json={"query": q}, timeout=8) as r:
            js = await r.json()
            return js.get("data", {}).get("pool")
    except: return None

async def price_uniswap(sess, sym):
    pid = UNI_POOLS.get(sym)
    if not pid: return None
    pid = Web3.to_checksum_address(pid)
    pool = await g_query(sess, GRAPH_UNI, f'{{pool(id:"{pid.lower()}"){{token0Price}}}}')
    if pool: return float(pool["token0Price"]), "Uniswap", ""
    return None

async def price_sushi(sess, sym):
    pid = SUSHI_POOLS.get(sym)
    if not pid: return None
    pid = Web3.to_checksum_address(pid)
    pool = await g_query(sess, GRAPH_SUSHI, f'{{pool(id:"{pid.lower()}"){{token0Price}}}}')
    if pool: return float(pool["token0Price"]), "SushiSwap", ""
    return None

async def price_dex(sess, addr):
    try:
        js = await (await sess.get(DEX_URL + addr, timeout=10)).json()
        pools = js.get("pairs") or []
        if not pools: return None
        best = max(pools, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        price = float(best["priceUsd"])
        platform = best["dexId"].capitalize()
        url = best.get("url", "")
        return price, platform, url
    except: return None

async def best_price(sess, sym, addr):
    results = await asyncio.gather(
        price_dex(sess, addr),
        price_uniswap(sess, sym),
        price_sushi(sess, sym)
    )
    best = [r for r in results if r and r[0] is not None]
    if not best: return None, None, None
    return max(best, key=lambda x: x[0])

def check_volatility(prices):
    if len(prices) < 2: return 0
    try:
        return max(prices) / min(prices) - 1
    except: return 0

def check_trend(prices):
    return prices[-1] > prices[0] if len(prices) >= 2 else False

async def monitor(sess, sym, addr):
    async with sem:
        try:
            res = await best_price(sess, sym, addr)
            if not res: return
            price, source, url = res

            now = datetime.now(LONDON)
            history[sym].append((now, price))

            lead = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
            vol_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=VOLATILITY_WINDOW)]
            trend_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=TREND_WINDOW)]

            if len(lead) >= 3 and all(p is not None for p in lead):
                min_lead = min(lead)
                if min_lead == 0: return

                speed = (price / min_lead - 1) * 100
                volatility = check_volatility(vol_window)
                confidence = speed / volatility if volatility > 0 else 0
                proj = speed * (3 / LEAD_WINDOW)
                entry = now + timedelta(minutes=2)
                exit_ = entry + timedelta(minutes=3)

                if (
                    speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                    check_trend(trend_window) and confidence >= CONFIDENCE_THRESH
                ):
                    entries[sym] = (entry, None)
                    await send(f"ð® *PREDICTIVE ALERT*\nð¡ _ÐÐ¶Ð¸Ð´Ð°ÐµÑÑÑ ÑÐ¾ÑÑ_\n{sym} â USDT\nâ± ÐÑÐ¾Ð´: {ts(entry)} | ÐÑÑÐ¾Ð´: {ts(exit_)}\nð ÐÑÐ¾Ð³Ð½Ð¾Ð·: +{proj:.2f}%\nð¡ ÐÑÑÐ¾ÑÐ½Ð¸Ðº: {source}\nð [ÐÑÐ¿Ð¸ÑÑ]({url})\nð {ts(now)}")

            if sym in entries:
                entry_time, entry_price = entries[sym]
                if entry_price is None and now >= entry_time:
                    entries[sym] = (entry_time, price)
                    await send(f"ð *ENTRY ALERT*\n{sym} â USDT\nð° Ð¦ÐµÐ½Ð° Ð²ÑÐ¾Ð´Ð°: {price:.4f}\nð¡ ÐÑÑÐ¾ÑÐ½Ð¸Ðº: {source}\nð [ÐÑÐ¿Ð¸ÑÑ]({url})\nð {ts(now)}")
                elif entry_price and now >= entry_time + timedelta(minutes=3):
                    growth = (price / entry_price - 1) * 100
                    await send(f"â *CONFIRMED ALERT*\nð _Ð¡Ð´ÐµÐ»ÐºÐ° Ð·Ð°Ð²ÐµÑÑÐµÐ½Ð°_\n{sym} â USDT\nð Ð ÐµÐ·ÑÐ»ÑÑÐ°Ñ: {'+' if growth >= 0 else ''}{growth:.2f}% Ð·Ð° 3Ð¼\nð¡ ÐÑÑÐ¾ÑÐ½Ð¸Ðº: {source}\nð [ÐÑÐ¿Ð¸ÑÑ]({url})\nð {ts(now)}")
                    del entries[sym]

        except Exception as e:
            log(f"[MONITOR ERROR] {sym}: {e}")
            traceback.print_exc()

async def main():
    await send("â Crypto Bot Ð¿ÐµÑÐµÐ·Ð°Ð¿ÑÑÐµÐ½ Ñ Ð°ÐºÑÑÐ°Ð»ÑÐ½ÑÐ¼Ð¸ ÑÐ¸Ð»ÑÑÑÐ°Ð¼Ð¸ Ð¸ Ð»Ð¾Ð³Ð¸ÐºÐ¾Ð¹.")
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
            except Exception as e:
                log(f"[MAIN LOOP ERROR] {e}")
                traceback.print_exc()
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
