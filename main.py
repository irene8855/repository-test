import os
import asyncio
import aiohttp
import csv
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz
import traceback
from web3 import Web3
from sklearn.linear_model import LogisticRegression
from sklearn.exceptions import NotFittedError
import numpy as np

# === Настройки ===
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
POLYGON_RPC = os.getenv("POLYGON_RPC")

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3

# 🔽 Обновлённые пороги
PREDICT_THRESH = 1.0       # было 1.2
CONFIRM_THRESH = 1.6       # было 2.0
CONFIDENCE_THRESH = 1.3    # было 1.5

LONDON = pytz.timezone("Europe/London")
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

TOKENS = {
    "BET": "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b"
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"

DEX_LINKS = {
    "sushiswap": "https://sushi.com",
    "uniswap": "https://app.uniswap.org",
    "1inch": "https://1inch.io"
}

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
entries = {}
sem = asyncio.Semaphore(10)
model = LogisticRegression()

def ts(dt=None): 
    return (dt or datetime.now(LONDON)).strftime("%H:%M")

def log(msg: str):
    with open("logs.txt", "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")

async def send(msg):
    try:
        send_coroutine = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        if asyncio.iscoroutine(send_coroutine):
            await send_coroutine
    except Exception as e:
        log(f"[SEND ERROR] {e}")
    log(msg.replace("\n", " | "))

# === Утилиты ML ===
def load_historical_data(filename="historical_trades.csv"):
    X = []
    y = []
    try:
        with open(filename, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                profit = float(row["profit_percent"])
                start_time = datetime.fromisoformat(row["start_time"])
                sell_time = datetime.fromisoformat(row["sell_time"])
                timing = (sell_time - start_time).total_seconds() / 60
                label = 1 if profit > 0 else 0
                X.append([profit, timing])
                y.append(label)
        return np.array(X), np.array(y)
    except Exception as e:
        log(f"Error loading historical data: {e}")
        return None, None

def train_model():
    X, y = load_historical_data()
    if X is not None and y is not None and len(y) > 10:
        model.fit(X, y)
        log(f"ML model trained on {len(y)} samples")
    else:
        log("Not enough data to train ML model")

# === Метрики анализа цен ===
def check_volatility(prices):
    if not prices or len(prices) < 2:
        return 0
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return variance ** 0.5

def check_trend(prices):
    return all(x < y for x, y in zip(prices, prices[1:]))

# === Получение данных о цене ===
async def best_price(sess, sym, addr):
    try:
        async with sess.get(DEX_URL + addr) as r:
            data = await r.json()
            d = data["pairs"][0]
            price = float(d["priceUsd"])
            return price, d["dexId"], d["url"]
    except Exception as e:
        log(f"[PRICE] {sym}: {e}")
        return None

# === Основной мониторинг токена ===
async def monitor(sess, sym, addr):
    async with sem:
        try:
            res = await best_price(sess, sym, addr)
            if not res: 
                return
            price, source, url = res

            now = datetime.now(LONDON)
            history[sym].append((now, price))

            lead = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
            vol_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=VOLATILITY_WINDOW)]
            trend_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=TREND_WINDOW)]

            if len(lead) >= 3 and all(p is not None for p in lead):
                min_lead = min(lead)
                speed = (price / min_lead - 1) * 100
                volatility = check_volatility(vol_window)
                confidence = speed / volatility if volatility > 0 else 0
                proj = speed * (3 / LEAD_WINDOW)
                entry = now + timedelta(minutes=2)
                exit_ = entry + timedelta(minutes=3)

                X_pred = np.array([[proj, LEAD_WINDOW]])
                try:
                    ml_pred = model.predict(X_pred)[0]
                except NotFittedError:
                    ml_pred = 0

                if (
                    speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                    check_trend(trend_window) and confidence >= CONFIDENCE_THRESH and ml_pred == 1
                ):
                    entries[sym] = (entry, None)
                    platform_link = DEX_LINKS.get(source.lower(), url)
                    await send(f"🔮 *PREDICTIVE ALERT*\n💡 _Ожидается рост_\n{sym} → USDT\n⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}\n📈 Прогноз: +{proj:.2f}%\n🌐 Платформа: [{source}]({platform_link})\n🔗 [Торговля]({url})\n🕒 {ts(now)}")

            if sym in entries:
                entry_time, entry_price = entries[sym]
                if not entry_price and now >= entry_time:
                    entries[sym] = (entry_time, price)
                elif entry_price and now >= entry_time + timedelta(minutes=3):
                    growth = (price / entry_price - 1) * 100
                    platform_link = DEX_LINKS.get(source.lower(), url)
                    await send(f"✅ *CONFIRMED ALERT*\n📊 _Сделка завершена_\n{sym} → USDT\n📈 Результат: {'+' if growth >= 0 else ''}{growth:.2f}% за 3м\n🌐 Платформа: [{source}]({platform_link})\n🔗 [Торговля]({url})\n🕒 {ts(now)}")
                    del entries[sym]

        except Exception as e:
            log(f"[MONITOR ERROR] {sym}: {e}")
            traceback.print_exc()

# === Основной цикл ===
async def main():
    train_model()
    await send("✅ Crypto Arbitrage Bot запущен. Мониторинг цен и арбитражных возможностей начался.")
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
            except Exception as e:
                log(f"[MAIN LOOP ERROR] {e}")
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
