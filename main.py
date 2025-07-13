"""
Crypto-alert bot for Polygon
• EARLY ALERT при росте ≥ THRESHOLD % за 3-10 мин
• RESULT через 3 мин: фактический P/L
"""

import os, time, asyncio, aiohttp, pytz
from datetime import datetime, timedelta
from telegram import Bot

# ── настройки ───────────────────────────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 30          # опрос DexScreener, сек
THRESHOLD = 1.5         # % прироста для сигнала
WINDOW_LO = 3           # мин – начало окна
WINDOW_HI = 10          # мин – конец окна
RESULT_DELAY = 180      # сек до отправки RESULT (3 мин)

LONDON = pytz.timezone("Europe/London")

TOKENS = {
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "GMT":   "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "EMT":   "0x8e0fe2947752be0d5acb1ba75e30e0cbc0f2a57",
    "SAND":  "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "BET":   "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "FRAX":  "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
}

DEX_LINKS = {
    "sushiswap": ("SushiSwap", "https://app.sushi.com/?chainId=137"),
    "quickswap": ("QuickSwap", "https://quickswap.exchange/#/swap?chainId=137"),
    "1inch":     ("1inch",     "https://app.1inch.io/#/137/simple/swap"),
    "uniswap":   ("Uniswap",   "https://app.uniswap.org/#/swap?chain=polygon"),
}

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/"
bot     = Bot(TG_TOKEN)
history = {s: [] for s in TOKENS}      # {sym: [(t, price, dex)]}

# ── сервис ──────────────────────────────────────────────────────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

async def fetch_price(session, addr):
    try:
        async with session.get(DEX_API + addr, timeout=15) as r:
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
    except Exception as e:
        print("fetch error:", e)
    return None, None

# ── RESULT через 3 мин ──────────────────────────────────────────
async def send_result(sym, addr, entry_price, entry_time, dex_id):
    await asyncio.sleep(RESULT_DELAY)
    async with aiohttp.ClientSession() as s:
        exit_price, _ = await fetch_price(s, addr)
    if exit_price is None:
        return
    pl = (exit_price / entry_price - 1) * 100
    dex_name, _ = DEX_LINKS.get(dex_id, (dex_id, ""))
    text = (
        "🔍 *RESULT*\n"
        f"{sym} → USDT\n"
        f"ENTRY {entry_time.strftime('%H:%M')} : {entry_price:.6f} $\n"
        f"EXIT  {(entry_time + timedelta(seconds=RESULT_DELAY)).strftime('%H:%M')} : {exit_price:.6f} $\n"
        f"P/L         : {pl:+.2f} %\n"
        f"DEX         : {dex_name}"
    )
    await send(text)

# ── мониторинг токена ───────────────────────────────────────────
async def monitor(session, sym, addr):
    now = datetime.now(LONDON)
    price, dex = await fetch_price(session, addr)
    if price is None:
        return
    buf = history[sym]
    buf.append((now, price, dex))
    history[sym] = [(t, p, d) for t, p, d in buf if t >= now - timedelta(minutes=WINDOW_HI)]

    past = [(p, d) for t, p, d in history[sym] if timedelta(minutes=WINDOW_LO) <= now - t <= timedelta(minutes=WINDOW_HI)]
    if not past:
        return
    min_price, _ = min(past, key=lambda x: x[0])
    if price < min_price * (1 + THRESHOLD / 100):
        return  # роста ещё нет

    # --- EARLY ALERT ---------------------------------------------------------
    proj = (price / min_price - 1) * 100
    buy_time  = now
    sell_time = now + timedelta(seconds=RESULT_DELAY)

    dex_name, dex_url = DEX_LINKS.get(dex, (dex, f"https://dexscreener.com/polygon/{addr}"))
    text = (
        "🚀 *EARLY ALERT*\n"
        f"{sym} → USDT\n"
        f"BUY NOW  : {buy_time.strftime('%H:%M')}\n"
        f"SELL ETA : {sell_time.strftime('%H:%M')}  _(proj +{proj:.2f}%)_\n"
        f"DEX now  : [{dex_name}]({dex_url})\n"
        f"Now      : {price:.6f} $\n"
        f"Min (3–10 m): {min_price:.6f} $\n"
        f"Threshold: {THRESHOLD}%"
    )
    await send(text)
    print(f"{sym}: alert sent")

    # запланировать RESULT
    asyncio.create_task(send_result(sym, addr, price, buy_time, dex))

# ── основной цикл ───────────────────────────────────────────────
async def main_loop():
    await send("✅ Crypto-bot online 🚀")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.gather(*(monitor(session, s, a) for s, a in TOKENS.items()))
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except Exception as e:
        print("❌ Fatal error:", e)
        while True:
            time.sleep(3600)  # контейнер остаётся живым
