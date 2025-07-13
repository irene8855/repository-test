"""
Crypto-alert bot for Polygon
— следит за ценами токенов из DexScreener
— ищет рост ≥ 1.5 % за 3-10 мин
— шлёт ранний алерт в Telegram
Работает 24/7 на Fly.io (или любом VPS).
"""

import asyncio, aiohttp, os, pytz, time
from datetime import datetime, timedelta
from telegram import Bot

# ─── Параметры ──────────────────────────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID    = int(os.getenv("CHAT_ID", "-1000000000000"))
CHECK_SEC  = 30          # частота опроса (сек)
THRESHOLD  = 1.5         # % прироста
LONDON_TZ  = pytz.timezone("Europe/London")

# токены (адрес = Polygon)
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
bot     = Bot(TG_TOKEN)
history = {sym: [] for sym in TOKENS}      # [(time, price, dex)]

# ─── Сервисные функции ──────────────────────────────────────────────────────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text)

async def fetch_price(session: aiohttp.ClientSession, addr: str):
    try:
        async with session.get(DEX_URL + addr, timeout=15) as r:
            js = await r.json()
        pools = js.get("pairs", [])
        best = None
        for p in pools:
            if p.get("chainId") == "polygon" and p["quoteToken"]["symbol"].upper() == "USDT":
                price = float(p["priceUsd"])
                dex   = p.get("dexId", "unknown")
                liq   = float(p.get("liquidity", {}).get("usd", 0))
                if not best or liq > best[2]:
                    best = (price, dex, liq)
        if best:
            return best[0], best[1]
        if pools:
            p = pools[0]
            return float(p["priceUsd"]), p.get("dexId", "unknown")
    except Exception as e:
        print("fetch error:", e)
    return None, None

async def monitor_token(session, sym, addr):
    now = datetime.now(LONDON_TZ)
    price, dex = await fetch_price(session, addr)
    if price is None:
        print(f"{sym}: None")
        return
    print(f"{sym:5} {price:.6f} {dex}")

    buf = history[sym]
    buf.append((now, price, dex))
    cutoff = now - timedelta(minutes=10)
    history[sym] = [(t, p, d) for t, p, d in buf if t >= cutoff]

    # минимальная цена 3-10 мин назад
    past = [(p, d) for t, p, d in history[sym] if timedelta(minutes=3) <= (now - t) <= timedelta(minutes=10)]
    if not past:
        return
    min_price, _ = min(past, key=lambda x: x[0])
    if price >= min_price * (1 + THRESHOLD / 100):
        proj = (price / min_price - 1) * 100
        buy = now.strftime("%H:%M")
        sell = (now + timedelta(minutes=3)).strftime("%H:%M")
        text = (
            f"🚀 EARLY ALERT\n"
            f"{sym} → USDT\n"
            f"BUY NOW  : {buy} on {dex}\n"
            f"SELL ETA : {sell}  (proj +{proj:.2f}%)\n"
            f"DEX now  : {dex}\n"
            f"Threshold: {THRESHOLD}%"
        )
        await send(text)

# ─── Основной цикл ───────────────────────────────────────────────────────────
async def main_loop():
    await send("✅ Crypto-bot online")
    async with aiohttp.ClientSession() as session:
        while True:
            tasks = [monitor_token(session, s, a) for s, a in TOKENS.items()]
            await asyncio.gather(*tasks)
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except Exception as e:
        # выводим ошибку, но оставляем контейнер живым
        print("❌ Fatal error:", e)
        while True:
            time.sleep(3600)
