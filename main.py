import os
import sys
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
import logging

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def log(msg: str):
    logging.info(msg)

# === Настройки ===
TG_TOKEN = os.getenv("TG_TOKEN")
if not TG_TOKEN:
    log("[ERROR] Не найден TG_TOKEN в окружении")
    sys.exit(1)
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
POLYGON_RPC = os.getenv("POLYGON_RPC")
if not POLYGON_RPC:
    log("[ERROR] Не найден POLYGON_RPC в окружении")
    sys.exit(1)

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3

PREDICT_THRESH = 1.0
CONFIRM_THRESH = 1.6
CONFIDENCE_THRESH = 1.3

LONDON = pytz.timezone("Europe/London")
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

# Убрали BET, добавили новые токены
TOKENS = {
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "MATIC": "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0",
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    "MKR": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"
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

async def send(msg):
    try:
        send_coroutine = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        if asyncio.iscoroutine(send_coroutine):
            await send_coroutine
    except Exception as e:
        log(f"[SEND ERROR] {e}")
    log(msg.replace("\n", " | "))

# === Machine Learning ===
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
        log(f"📊 Загружено {len(y)} исторических сделок")
        return np.array(X), np.array(y)
    except Exception as e:
        log(f"[LOAD HISTORICAL ERROR] {e}")
        return None, None

def train_model():
    X, y = load_historical_data()
    if X is not None and y is not None and len(y) > 10:
        try:
            model.fit(X, y)
            acc = model.score(X, y)
            log(f"✅ ML модель обучена на {len(y)} сделках")
            log(f"   ➤ Точность: {acc:.2f}")
            log(f"   ➤ Коэффициенты: {model.coef_.tolist()}")
            log(f"   ➤ Смещение: {model.intercept_.tolist()}")
        except Exception as e:
            log(f"[TRAIN ERROR] {e}")
    else:
        log("❌ Недостаточно данных для обучения ML модели")

# === Метрики ===
def check_volatility(prices):
    if not prices or len(prices) < 2:
        return 0
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return variance ** 0.5

def check_trend(prices):
    return all(x < y for x, y in zip(prices, prices[1:]))

# === Получение цены ===
async def best_price(sess, sym, addr):
    try:
        async with sess.get(DEX_URL + addr) as r:
            data = await r.json()
            if not data or "pairs" not in data or not data["pairs"]:
                log(f"[PRICE] {sym}: No pairs found in API response")
                return None
            d = data["pairs"][0]
            if "priceUsd" not in d or "dexId" not in d or "url" not in d:
                log(f"[PRICE] {sym}: Missing expected keys in pair data")
                return None
            price = float(d["priceUsd"])
            return price, d["dexId"], d["url"]
    except Exception as e:
        log(f"[PRICE] {sym}: {e}")
        return None

# === Мониторинг ===
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

                log(f"[PREDICT] {sym}: speed={speed:.2f}%, proj={proj:.2f}%, confidence={confidence:.2f}, ml_pred={ml_pred}")

                if (
                    speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                    check_trend(trend_window) and confidence >= CONFIDENCE_THRESH and ml_pred == 1
                ):
                    entries[sym] = (entry, None)
                    platform_link = DEX_LINKS.get(source.lower(), url)
                    await send(
                        f"🔮 *PREDICTIVE ALERT*\n"
                        f"💡 _Ожидается рост_\n"
                        f"{sym} → USDT\n"
                        f"⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}\n"
                        f"📈 Прогноз: +{proj:.2f}%\n"
                        f"🌐 Платформа: [{source}]({platform_link})\n"
                        f"🔗 [Торговля]({url})\n"
                        f"🕒 {ts(now)}"
                    )

            if sym in entries:
                entry_time, entry_price = entries[sym]
                if not entry_price and now >= entry_time:
                    entries[sym] = (entry_time, price)
                elif entry_price and now >= entry_time + timedelta(minutes=3):
                    growth = (price / entry_price - 1) * 100
                    platform_link = DEX_LINKS.get(source.lower(), url)
                    await send(
                        f"✅ *CONFIRMED ALERT*\n"
                        f"📊 _Сделка завершена_\n"
                        f"{sym} → USDT\n"
                        f"📈 Результат: {'+' if growth >= 0 else ''}{growth:.2f}% за 3м\n"
                        f"🌐 Платформа: [{source}]({platform_link})\n"
                        f"🔗 [Торговля]({url})\n"
                        f"🕒 {ts(now)}"
                    )
                    del entries[sym]

        except Exception as e:
            log(f"[MONITOR ERROR] {sym}: {e}")
            log(traceback.format_exc())

# === Главный цикл ===
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
    try:
        asyncio.run(main())
    except Exception as e:
        log(f"[FATAL ERROR] {e}")
        log(traceback.format_exc())
        
