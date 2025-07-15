"""
Crypto-alert bot (Polygon, адресный)
• опрашивает DexScreener по адресу токена
• ловит рост ≥ THRESHOLD % за 3-10 мин
• шлёт единый EARLY ALERT в Telegram c:
     – кликабельным названием биржи
     – текущей ценой (Now)
     – минимумом за окно (Min)
"""

import os, asyncio, aiohttp, time
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot

# ─── ПАРАМЕТРЫ ─────────────────────────────────────────────
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID","-1000000000000"))

CHECK_SEC = 30          # период опроса
THRESHOLD = 1.5         # % роста
HEARTBEAT = 60          # вывод [HB] раз в N сек

# адреса токенов Polygon
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

# dexId → (отображаемое имя, ссылка)
DEX_LINKS = {
    "sushiswap": ("SushiSwap", "https://app.sushi.com/?chainId=137"),
    "quickswap": ("QuickSwap", "https://quickswap.exchange/#/swap?chainId=137"),
    "uniswap":   ("Uniswap",   "https://app.uniswap.org/#/swap?chain=polygon"),
    "1inch":     ("1inch",     "https://app.1inch.io/#/137/simple/swap"),
    "apeswap":   ("ApeSwap",   "https://app.apeswap.finance/swap?chainId=137"),
    "kyberswap": ("KyberSwap", "https://kyberswap.com"),
}

DEX_URL      = "https://api.dexscreener.com/latest/dex/tokens/"
GECKO_TOKEN  = "https://api.geckoterminal.com/api/v2/networks/polygon/tokens/"
bot          = Bot(TG_TOKEN)
history      = {sym: deque(maxlen=600) for sym in TOKENS}  # 10 мин @ 1 cек

# ─── Telegram ──────────────────────────────────────────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# ─── HTTP helper ───────────────────────────────────────
async def fetch_json(session, url):
    for _ in range(3):
        try:
            async with session.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            await asyncio.sleep(2)
    return None

# ─── Получить (price, dexId) ───────────────────────────
async def get_price(session, addr):
    # 1) DexScreener
    js = await fetch_json(session, DEX_URL + addr)
    if js and js.get("pairs"):
        best = max(
            (p for p in js["pairs"] if p["chainId"]=="polygon" and p["quoteToken"]["symbol"].upper()=="USDT"),
            key=lambda p: float(p.get("liquidity", {}).get("usd", 0)),
            default=None
        )
        if best:
            return float(best["priceUsd"]), best["dexId"].lower()
    # 2) GeckoTerminal address fallback
    js = await fetch_json(session, GECKO_TOKEN + addr)
    if js and "data" in js:
        a = js["data"]["attributes"]
        return float(a.get("price_usd") or 0), "gecko"
    return None, None

# ─── Мониторинг токена ─────────────────────────────────
async def monitor(session, sym, addr):
    price, dex = await get_price(session, addr)
    if price is None:
        return

    now = datetime.utcnow()
    history[sym].append((now, price))

    # минимум 3–10 мин назад
    cutoff_hi = now - timedelta(minutes=3)
    cutoff_lo = now - timedelta(minutes=10)
    past_prices = [p for t, p in history[sym] if cutoff_lo <= t <= cutoff_hi]
    if not past_prices:
        return
    min_price = min(past_prices)
    if price < min_price * (1 + THRESHOLD/100):
        return

    proj = (price / min_price - 1) * 100
    dex_name, dex_url = DEX_LINKS.get(dex, (dex.capitalize(), f"https://dexscreener.com/polygon/{addr}"))

    text = (
        "🚀 *EARLY ALERT*\n"
        f"{sym} → USDT\n"
        f"BUY NOW  : {now.strftime('%H:%M')} on {dex_name}\n"
        f"SELL ETA : {(now+timedelta(minutes=3)).strftime('%H:%M')}  _(proj +{proj:.2f}%)_\n"
        f"DEX now  : [{dex_name}]({dex_url})\n"
        f"Now      : {price:.6f} $\n"
        f"Min (3–10 m): {min_price:.6f} $\n"
        f"Threshold: {THRESHOLD}%"
    )
    await send(text)
    print(f"[ALERT] {sym} +{proj:.2f}% via {dex_name}")

# ─── Основной цикл ─────────────────────────────────────
async def main_loop():
    await send("✅ *Crypto-bot online* :rocket:")
    hb_next = time.time() + HEARTBEAT
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.gather(*(monitor(session, s, a) for s, a in TOKENS.items()))
            if time.time() >= hb_next:
                print("[HB]", datetime.utcnow().strftime('%H:%M:%S'))
                hb_next += HEARTBEAT
            await asyncio.sleep(CHECK_SEC)

# ─── run ───────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main_loop())
