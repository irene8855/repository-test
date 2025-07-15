import os, asyncio, aiohttp, time
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot

# ─── CONFIG ─────────────────────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 30          # polling interval (sec)
THRESHOLD = 1.5         # % rise to trigger
HEARTBEAT = 60          # log heartbeat every N sec

# Polygon token addresses
TOKENS = {
    "BET" : "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "EMT" : "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "GMT" : "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "LDO" : "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SUSHI":"0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "UNI" : "0xb33eaad8d922b1083446dc23f610c2567fb5180f",
    "APE" : "0x4d224452801aced8b2f0aebe155379bb5d594381",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"
}

# DEX display name + link for markdown
DEX_LINKS = {
    "sushiswap": ("SushiSwap", "https://app.sushi.com/?chainId=137"),
    "quickswap": ("QuickSwap", "https://quickswap.exchange/#/swap?chainId=137"),
    "uniswap":   ("Uniswap",   "https://app.uniswap.org/#/swap?chain=polygon"),
    "1inch":     ("1inch",     "https://app.1inch.io/#/137/simple/swap"),
    "apeswap":   ("ApeSwap",   "https://app.apeswap.finance/swap?chainId=137"),
    "kyberswap": ("KyberSwap", "https://kyberswap.com")
}

DEX_URL      = "https://api.dexscreener.com/latest/dex/tokens/"
GECKO_TOKEN  = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

bot      = Bot(TG_TOKEN)
history  = {s: deque(maxlen=600) for s in TOKENS}   # 10 min @ 1 s
sem      = asyncio.Semaphore(10)

# ─── HELPERS ───────────────────────────────────────────
def ts(offset=0):
    return (datetime.utcnow() + timedelta(minutes=offset)).strftime("%H:%M")

async def fetch_json(sess, url):
    for _ in range(3):
        try:
            async with sess.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            await asyncio.sleep(2)
    return None

async def send(text: str):
    if not TG_TOKEN or not CHAT_ID:
        print("❌ Missing TG_TOKEN or CHAT_ID"); return
    async with aiohttp.ClientSession() as sess:
        await sess.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                        data={"chat_id": CHAT_ID,
                              "text": text,
                              "parse_mode": "Markdown"})

# ─── PRICE via address ─────────────────────────────────
async def get_price(sess, addr):
    try:
        js = await fetch_json(sess, DEX_URL + addr)
        if js and js.get("pairs"):
            best = max(
                (p for p in js["pairs"]
                 if p["chainId"] == "polygon"
                 and p["quoteToken"]["symbol"].upper() == "USDT"),
                key=lambda p: float(p.get("liquidity", {}).get("usd", 0)),
                default=None
            )
            if best:
                return float(best["priceUsd"]), best["dexId"].lower()
    except Exception:
        pass
    # fallback GeckoTerminal by address
    js = await fetch_json(sess, GECKO_TOKEN + addr)
    if js and "data" in js:
        a = js["data"]["attributes"]
        return float(a.get("price_usd") or 0), "gecko"
    return None, None

# ─── MONITOR one token ─────────────────────────────────
async def monitor(sess, sym, addr):
    async with sem:
        price, dex = await get_price(sess, addr)
        if price is None: return
        now = datetime.utcnow()
        history[sym].append((now, price))

        past = [p for t, p in history[sym]
                if timedelta(minutes=3) <= now - t <= timedelta(minutes=10)]
        if not past: return
        min_p = min(past)
        if price < min_p * (1 + THRESHOLD/100): return

        proj = (price / min_p - 1) * 100
        dex_name, dex_url = DEX_LINKS.get(dex,
                            (dex.capitalize(), f"https://dexscreener.com/polygon/{addr}"))

        msg = (f"🚀 *EARLY ALERT*
"
               f"{sym} → USDT
"
               f"BUY NOW  : {ts()} on {dex_name}
"
               f"SELL ETA : {ts(3)}  _(proj +{proj:.2f}%)_
"
               f"DEX now  : [{dex_name}]({dex_url})
"
               f"Now      : {price:.6f} $
"
               f"Min (3–10 m): {min_p:.6f} $
"
               f"Threshold: {THRESHOLD}%")
        await send(msg)
        print(f"[ALERT] {sym} +{proj:.2f}% via {dex_name}")

# ─── MAIN LOOP ─────────────────────────────────────────
async def main():
    await send("✅ *Crypto-bot online* 🚀")
    hb = time.time() + HEARTBEAT
    async with aiohttp.ClientSession() as sess:
        while True:
            await asyncio.gather(*(monitor(sess, s, a) for s, a in TOKENS.items()))
            if time.time() >= hb:
                print("[HB]", datetime.utcnow().strftime("%H:%M:%S"))
                hb += HEARTBEAT
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
