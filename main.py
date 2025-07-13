import asyncio, aiohttp, os, pytz, time
from datetime import datetime, timedelta
from telegram import Bot

TG_TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID    = int(os.getenv("CHAT_ID", "-1000000000000"))
CHECK_SEC  = 30
THRESHOLD  = 1.5
LONDON_TZ  = pytz.timezone("Europe/London")
MAX_PARALLEL = 5

TOKENS = {
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
ALT_URL = "https://api.geckoterminal.com/api/v2/search?query="
DEX_LINKS = {
    "sushiswap": "https://app.sushi.com",
    "uniswap": "https://app.uniswap.org",
    "quickswap": "https://quickswap.exchange",
    "apeswap": "https://apeswap.finance",
    "kyberswap": "https://kyberswap.com",
    "dfyn": "https://exchange.dfyn.network",
    "jetswap": "https://polygon.jetswap.finance",
    "wault": "https://swap.wault.finance",
}

bot = Bot(TG_TOKEN)
sema = asyncio.Semaphore(MAX_PARALLEL)
history = {sym: [] for sym in TOKENS}
results = {}

async def send(text): await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

def fmt_dex(dex): return f"[{dex}]({DEX_LINKS.get(dex.lower(), 'https://dexscreener.com')})"

async def fetch_price(session, addr, symbol):
    for attempt in range(5):
        async with sema:
            try:
                async with session.get(DEX_URL + addr, timeout=10) as r:
                    js = await r.json()
                pools = js.get("pairs") or []
                best = None
                for p in pools:
                    if p.get("chainId") == "polygon" and p["quoteToken"]["symbol"].upper() == "USDT":
                        price = float(p["priceUsd"])
                        dex   = p.get("dexId", "unknown")
                        liq   = float(p.get("liquidity", {}).get("usd", 0))
                        if not best or liq > best[2]:
                            best = (price, dex, liq)
                if best: return best[0], best[1]
                if pools: return float(pools[0]["priceUsd"]), pools[0].get("dexId", "unknown")
            except Exception:
                await asyncio.sleep(1 + attempt * 2)
    # fallback: geckoterminal
    try:
        async with session.get(ALT_URL + symbol.lower()) as r:
            js = await r.json()
        items = js.get("data", [])
        for item in items:
            attr = item.get("attributes", {})
            if "usd" in attr.get("name", "").lower():
                price = float(attr.get("price_usd", 0))
                return price, "gecko"
    except: pass
    return None, None

async def monitor_token(session, sym, addr):
    now = datetime.now(LONDON_TZ)
    price, dex = await fetch_price(session, addr, sym)
    if price is None: return

    buf = history[sym]
    buf.append((now, price, dex))
    cutoff = now - timedelta(minutes=10)
    history[sym] = [(t, p, d) for t, p, d in buf if t >= cutoff]

    past = [(p, d) for t, p, d in history[sym] if timedelta(minutes=3) <= (now - t) <= timedelta(minutes=10)]
    if not past: return
    min_price, _ = min(past, key=lambda x: x[0])
    if price >= min_price * (1 + THRESHOLD / 100):
        proj = (price / min_price - 1) * 100
        buy = now.strftime("%H:%M")
        sell = (now + timedelta(minutes=3)).strftime("%H:%M")
        dex_fmt = fmt_dex(dex)
        await send(
            f"ð EARLY ALERT
"
            f"{sym} â USDT
"
            f"BUY NOW  : {buy}
"
            f"SELL ETA : {sell}  (proj +{proj:.2f}%)
"
            f"DEX now  : {dex_fmt}
"
            f"Now      : {price:.6f} $
"
            f"Min (3â10 m): {min_price:.6f} $
"
            f"Threshold: {THRESHOLD}%"
        )
        results[sym] = (now + timedelta(minutes=3), price, dex_fmt)

async def send_results(session):
    now = datetime.now(LONDON_TZ)
    for sym in list(results):
        sell_time, entry_price, dex_fmt = results[sym]
        if now >= sell_time:
            addr = TOKENS[sym]
            price, _ = await fetch_price(session, addr, sym)
            if price:
                pl = (price / entry_price - 1) * 100
                await send(
                    f"ð RESULT {sym} â USDT
"
                    f"ENTRY {sell_time - timedelta(minutes=3):%H:%M} : {entry_price:.6f} $
"
                    f"EXIT  {sell_time:%H:%M} : {price:.6f} $
"
                    f"P/L   : {pl:+.2f}%
"
                    f"DEX   : {dex_fmt}"
                )
            results.pop(sym)

async def main_loop():
    print("DEBUG: patched version running")
    await send("ð  Bot updated and running (patched version)")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.gather(*(monitor_token(session, s, a) for s, a in TOKENS.items()))
            await send_results(session)
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    try: asyncio.run(main_loop())
    except Exception as e:
        print("â Fatal error:", e)
        while True: time.sleep(3600)

