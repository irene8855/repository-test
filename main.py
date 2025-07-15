"""
Crypto-alert bot for Polygon
 • следит за ценами токенов через DexScreener
 • ловит рост ≥ THRESHOLD % за 3-10 мин
 • шлёт алерт в Telegram c:
      - кликабельным названием биржи,
      - текущей ценой,
      - минимумом за окно.
"""

import os, time, asyncio, aiohttp, pytz
from datetime import datetime, timedelta
from telegram import Bot

# ──────────────────────────── ПАРАМЕТРЫ ────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 30      # опрос API (сек.)
THRESHOLD = 1.5     # % прироста для сигнала
LONDON    = pytz.timezone("Europe/London")

TOKENS = {
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
}

DEX_LINKS = {  # id → (показать, ссылка)
    "sushiswap": ("SushiSwap", "https://app.sushi.com?chainId=137"),
    "quickswap": ("QuickSwap", "https://quickswap.exchange/#/swap?chainId=137"),
    "1inch":     ("1inch",     "https://app.1inch.io/#/137/simple/swap"),
    "uniswap":   ("Uniswap",   "https://app.uniswap.org/#/swap?chain=polygon"),
    "apeswap":   ("ApeSwap",   "https://app.apeswap.finance/swap?chainId=137"),
    "kyberswap": ("KyberSwap", "https://kyberswap.com"),
}

DEX_URL   = "https://api.dexscreener.com/latest/dex/tokens/"
bot       = Bot(TG_TOKEN)
history   = {sym: [] for sym in TOKENS}        # [(time, price, dex)]

# ──────────────────────────── СЕРВИС ───────────────────────────────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

async def fetch_price(session: aiohttp.ClientSession, addr: str):
    try:
        async with session.get(DEX_URL + addr, timeout=15) as r:
            js = await r.json()
        pools = js.get("pairs", [])
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
        if pools:
            p = pools[0]
            return float(p["priceUsd"]), p.get("dexId", "unknown").lower()
    except Exception as e:
        print("fetch error:", e)
    return None, None

# ────────────────────────── МОНИТОРИНГ ТОКЕНА ──────────────────────
async def monitor_token(session, sym, addr):
    now = datetime.now(LONDON)
    price, dex = await fetch_price(session, addr)
    if price is None:
        print(f"{sym}: None")
        return

    buf = history[sym]
    buf.append((now, price, dex))
    history[sym] = [(t, p, d) for t, p, d in buf if t >= now - timedelta(minutes=10)]

    past = [(p, d) for t, p, d in history[sym] if timedelta(minutes=3) <= now - t <= timedelta(minutes=10)]
    if not past:
        return
    min_price, _ = min(past, key=lambda x: x[0])

    if price >= min_price * (1 + THRESHOLD / 100):
        proj = (price / min_price - 1) * 100
        buy  = now.strftime("%H:%M")
        sell = (now + timedelta(minutes=3)).strftime("%H:%M")

        dex_name, dex_url = DEX_LINKS.get(dex, (dex, f"https://dexscreener.com/polygon/{addr}"))

        text = (
            "🚀 *EARLY ALERT*\n"
            f"{sym} → USDT\n"
            f"BUY NOW  : {buy}\n"
            f"SELL ETA : {sell}  _(proj +{proj:.2f}%)_\n"
            f"DEX now  : [{dex_name}]({dex_url})\n"
            f"Now      : {price:.6f} $\n"
            f"Min (3–10 m): {min_price:.6f} $\n"
            f"Threshold: {THRESHOLD}%"
        )
        await send(text)
        print(f"{sym}: alert sent")

# ─────────────────────────── ОСНОВНОЙ ЦИКЛ ─────────────────────────
async def main_loop():
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.gather(*(monitor_token(session, s, a) for s, a in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

# ────────────────────────────── RUN ────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except Exception as e:
        print("❌ Fatal error:", e)
        while True:
            time.sleep(3600)   # держим контейнер живым для отладки
