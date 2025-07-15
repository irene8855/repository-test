import asyncio, aiohttp, os
from datetime import datetime, timedelta
from collections import deque

# ── конфиг ────────────────────────────────────────────────
TOKEN   = os.getenv("TG_TOKEN", os.getenv("BOT_TOKEN"))
CHAT_ID = os.getenv("CHAT_ID")
THRESHOLD     = 1.5          # % роста
CHECK_SEC     = 30           # период опроса, сек
MAX_PARALLEL  = 5            # семафор
RETRY_LIMIT   = 6            # попытки HTTP
RETRY_DELAY   = 3            # базовая задержка, сек

# токены Polygon (символ : адрес)
TOKEN_ADDR = {
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
TOKENS = set(TOKEN_ADDR.keys())

DEX_URLS = {
    "sushiswap": "https://www.sushi.com",
    "uniswap":   "https://app.uniswap.org",
    "1inch":     "https://app.1inch.io",
    "pancakeswap":"https://pancakeswap.finance",
    "quickswap": "https://quickswap.exchange"
}

sem  = asyncio.Semaphore(MAX_PARALLEL)
seen = deque(maxlen=30)

# ── утилиты ───────────────────────────────────────────────
def ts(off=0): return (datetime.utcnow()+timedelta(minutes=off)).strftime('%H:%M')

async def send(text: str):
    """Отправка в Telegram c корректным закрытием сессии"""
    if not TOKEN or not CHAT_ID:
        print("Missing TG_TOKEN/BOT_TOKEN or CHAT_ID"); return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as sess:
        await sess.post(url, data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        })

async def fetch_json(sess, url: str):
    for attempt in range(RETRY_LIMIT):
        try:
            async with sess.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
    return None

# ── источники цен ────────────────────────────────────────
DEX_SCREENER = "https://api.dexscreener.com/latest/dex/pairs"
GECKO_POOLS  = "https://api.geckoterminal.com/api/v2/networks/polygon/pools"
GECKO_TOKEN  = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"

async def get_pools():
    async with aiohttp.ClientSession() as sess:
        # 1) DexScreener
        js = await fetch_json(sess, DEX_SCREENER)
        pools = js.get("pairs") if js else []
        if pools:
            return pools

        # 2) GeckoTerminal /pools
        js = await fetch_json(sess, GECKO_POOLS)
        raw = js.get("data", []) if js else []
        if raw:
            pools=[]
            for it in raw:
                a = it["attributes"]
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

        # 3) адресный fallback
        pools=[]
        for sym, addr in TOKEN_ADDR.items():
            js = await fetch_json(sess, GECKO_TOKEN + addr)
            if not js or "data" not in js:
                continue
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

# ── RESULT через 3 мин ─────────────────────────────────
async def send_result(sym, entry, dex_fmt, dex_url):
    await asyncio.sleep(180)
    async with aiohttp.ClientSession() as sess:
        js = await fetch_json(sess, GECKO_TOKEN + TOKEN_ADDR[sym])
        if not js or "data" not in js:
            return
        exit_p=float(js["data"]["attributes"].get("price_usd") or 0)
    pnl=(exit_p/entry - 1)*100
    await send(
f"""✅ *RESULT* {sym} → USDT
ENTRY {ts(-3)} : {entry:.6f} $
EXIT  {ts()} : {exit_p:.6f} $
P/L   : {pnl:+.2f} %
DEX   : [{dex_fmt}]({dex_url})"""
    )

# ── анализ пула/токена ────────────────────────────────
async def analyze(pool: dict):
    async with sem:
        try:
            sym = pool["baseToken"]["symbol"]
            if sym not in TOKENS: return
            now = float(pool.get("priceUsd") or 0)
            chg = float(pool["priceChange"].get("m5") or 0)
            if now==0 or chg < THRESHOLD: return
            sig = f"{sym}:{int(now*1e6)}"
            if sig in seen: return
            seen.append(sig)

            low = float(pool["priceChange"].get("m10Low") or now)
            dex = pool.get("dexId","gecko").lower()
            dex_fmt = dex.capitalize()
            dex_url = DEX_URLS.get(dex, f"https://google.com/search?q={dex}+dex")

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
            asyncio.create_task(send_result(sym, now, dex_fmt, dex_url))
        except Exception as e:
            print("analyze error:", e)

# ── основной цикл ─────────────────────────────────────
async def main():
    print("DEBUG: address-fallback v2 running")
    await send("✅ *Crypto-bot online* 🚀")
    while True:
        try:
            pools = await get_pools()
            await asyncio.gather(*(analyze(p) for p in pools))
        except Exception as e:
            print("loop error:", e)
        await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
