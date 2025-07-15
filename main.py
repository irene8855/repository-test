"""
Crypto-alert bot (Polygon)
 • опрашивает DexScreener по адресу токена
 • ловит рост ≥ THRESHOLD % за 3-10 мин
 • шлёт единый EARLY ALERT с кликабельной ссылкой, Now и Min
"""

import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz, time

# ─────────────── ПАРАМЕТРЫ ───────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 30        # период опроса
THRESHOLD = 1.5       # % роста
LONDON    = pytz.timezone("Europe/London")

# адреса токенов Polygon
TOKENS = {
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "UNI":   "0xb33eaad8d922b1083446dc23f610c2567fb5180f",
    "APE":   "0x4d224452801aced8b2f0aebe155379bb5d594381",
    "AAVE":  "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "LINK":  "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"
}

DEX_LINKS = {  # dexId → (имя, ссылка)
    "sushiswap": ("SushiSwap", "https://app.sushi.com?chainId=137"),
    "quickswap": ("QuickSwap", "https://quickswap.exchange/#/swap?chainId=137"),
    "uniswap":   ("Uniswap",   "https://app.uniswap.org/#/swap?chain=polygon"),
    "1inch":     ("1inch",     "https://app.1inch.io/#/137/simple/swap"),
    "apeswap":   ("ApeSwap",   "https://app.apeswap.finance/swap?chainId=137"),
    "kyberswap": ("KyberSwap", "https://kyberswap.com")
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
bot     = Bot(TG_TOKEN)
history = {sym: deque(maxlen=600) for sym in TOKENS}   # 10 мин @ 1 с

# ───── Telegram ─────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# ───── получаем (price, dexId) ─────
async def fetch_price(session, addr: str):
    try:
        async with session.get(DEX_URL + addr, timeout=15) as r:
            js = await r.json()

        # защита от пустого ответа
        pools = js.get("pairs") if isinstance(js, dict) else None
        if not pools:
            print(f"⚠️  DexScreener пуст для {addr[:6]}…")
            return None, None

        best = None
        for p in pools:
            if p.get("chainId") == "polygon" and p["quoteToken"]["symbol"].upper() == "USDT":
                price = float(p["priceUsd"])
                dex   = p.get("dexId", "unknown").lower()
                liq   = float(p.get("liquidity", {}).get("usd", 0))
                if not best or liq > best[2]:
                    best = (price, dex, liq)
        if best:
            return best[0], best[1]

        # fallback: берём первый пул
        price = float(pools[0]["priceUsd"])
        dex   = pools[0].get("dexId", "unknown").lower()
        return price, dex

    except Exception as e:
        print("fetch error:", e)
        return None, None

# ───── монитор токена ─────
async def monitor_token(session, sym, addr):
    now = datetime.now(LONDON)
    price, dex = await fetch_price(session, addr)
    if price is None:
        return

    history[sym].append((now, price))
    past = [p for t, p in history[sym]
            if timedelta(minutes=3) <= now - t <= timedelta(minutes=10)]
    if not past:
        return
    min_p = min(past)
    if price < min_p * (1 + THRESHOLD/100):
        return

    proj = (price / min_p - 1) * 100
    dex_name, dex_url = DEX_LINKS.get(
        dex, (dex.capitalize(), f"https://dexscreener.com/polygon/{addr}")
    )

    text = (
        "🚀 *EARLY ALERT*\n"
        f"{sym} → USDT\n"
        f"BUY NOW  : {now.strftime('%H:%M')}\n"
        f"SELL ETA : {(now+timedelta(minutes=3)).strftime('%H:%M')}  _(proj +{proj:.2f}%)_\n"
        f"DEX now  : [{dex_name}]({dex_url})\n"
        f"Now      : {price:.6f} $\n"
        f"Min (3–10 m): {min_p:.6f} $\n"
        f"Threshold: {THRESHOLD}%"
    )
    await send(text)
    print(f"[ALERT] {sym} +{proj:.2f}% via {dex_name}")

# ───── основной цикл ─────
async def main_loop():
    print("DEBUG: bot started")
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.gather(*(monitor_token(session, s, a) for s, a in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except Exception as e:
        print("Fatal error:", e)
        while True:
            time.sleep(3600)
