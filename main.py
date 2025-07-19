import os, asyncio, aiohttp
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz
import traceback

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3
LEAD_THRESH = 0.7
CONFIRM_THRESH = 2.5
PREDICT_THRESH = 2.5
CONFIDENCE_THRESH = 1.5

LONDON = pytz.timezone("Europe/London")
already_started = False

TOKENS = { ... }  # тот же словарь, можно не копировать сюда
UNI_POOLS = { ... }
SUSHI_POOLS = { ... }

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
GRAPH_UNI = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
GRAPH_SUSHI = "https://api.thegraph.com/subgraphs/name/sushiswap/v3-polygon"

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
entries = {}
sem = asyncio.Semaphore(10)

def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")

async def send(msg):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[SEND ERROR] {e}")

async def g_query(sess, url, q):
    try:
        async with sess.post(url, json={"query": q}, timeout=8) as r:
            js = await r.json()
            return js.get("data", {}).get("pool")
    except: return None

# ... все price_* функции без изменений ...

async def best_price(sess, sym, addr):
    try:
        results = await asyncio.gather(
            price_dex(sess, addr),
            price_uniswap(sess, sym),
            price_sushi(sess, sym)
        )
        best = [r for r in results if r and r[0] is not None]
        if not best: return None, None, None
        return max(best, key=lambda x: x[0])
    except Exception as e:
        print(f"[BEST_PRICE ERROR] {sym}: {e}")
        return None, None, None

def check_volatility(prices):
    return max(prices) / min(prices) - 1 if len(prices) >= 2 else 0

def check_trend(prices):
    return prices[-1] > prices[0] if len(prices) >= 2 else False

async def monitor(sess, sym, addr):
    try:
        async with sem:
            res = await best_price(sess, sym, addr)
            if not res: return
            price, source, url = res

            now = datetime.now(LONDON)
            history[sym].append((now, price))

            lead = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
            vol_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=VOLATILITY_WINDOW)]
            trend_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=TREND_WINDOW)]

            if sym in entries:
                entry_time, _ = entries[sym]
                if now >= entry_time and entries[sym][1] is None:
                    entries[sym] = (entry_time, price)
                    await send(f"🚀 *ENTRY ALERT*\n{sym} → USDT\n💰 Цена входа: {price:.4f}\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")

            if len(lead) >= 3:
                speed = (price / min(lead) - 1) * 100
                volatility = check_volatility(vol_window)
                confidence = speed / volatility if volatility > 0 else 0
                proj = speed * (3 / LEAD_WINDOW)
                entry = now + timedelta(minutes=2)
                exit_ = entry + timedelta(minutes=3)

                if (
                    speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                    check_trend(trend_window) and confidence >= CONFIDENCE_THRESH and price > min(lead)
                ):
                    entries[sym] = (entry, None)
                    await send(f"🔮 *PREDICTIVE ALERT*\n💡 _Вход в сделку через 2 минуты_\n{sym} → USDT\n⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}\n📈 Прогноз: +{proj:.2f}%\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")
                elif speed >= LEAD_THRESH:
                    await send(f"📉 *EARLY LEAD ALERT*\n⚠️ _Цена уже растёт. Можно входить, но без прогноза_\n{sym} → USDT\n📈 Рост: +{speed:.2f}% за {LEAD_WINDOW} мин\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")

            if sym in entries:
                entry_time, entry_price = entries[sym]
                if entry_price and now >= entry_time + timedelta(minutes=3):
                    growth = (price / entry_price - 1) * 100
                    await send(f"✅ *CONFIRMED ALERT*\n📊 _Сделка завершена_\n{sym} → USDT\n📈 Результат: {'+' if growth >= 0 else ''}{growth:.2f}% за 3м\n📡 Источник: {source or '—'}\n🔗 [Купить]({url})\n🕒 {ts(now)}")
                    del entries[sym]
    except Exception:
        print(f"[MONITOR ERROR] {sym}:\n{traceback.format_exc()}")

async def main():
    global already_started
    try:
        if not already_started:
            await send("✅ Crypto Bot запущен с новыми фильтрами: ликвидность, confidence, точность.")
            already_started = True

        async with aiohttp.ClientSession() as sess:
            while True:
                try:
                    await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
                    await asyncio.sleep(CHECK_SEC)
                except Exception as inner_e:
                    print(f"[MAIN LOOP ERROR] {inner_e}")
    except Exception as e:
        print(f"[FATAL ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(main())
