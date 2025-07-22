import os
import time
import requests
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
from web3 import Web3

# ✅ Секреты из окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLYGON_API_URL = os.getenv("POLYGON_API_URL")

# ✅ Проверка секретов
print("✅ main.py стартовал")
print("✅ TELEGRAM_TOKEN:", TELEGRAM_TOKEN[:5] + "..." if TELEGRAM_TOKEN else "❌ НЕТ")
print("✅ TELEGRAM_CHAT_ID:", TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else "❌ НЕТ")
print("✅ POLYGON_API_URL:", POLYGON_API_URL[:20] + "..." if POLYGON_API_URL else "❌ НЕТ")

# ✅ Telegram функции
def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram credentials not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("❌ Ошибка при отправке в Telegram:", e)

send_telegram_message("🚀 Бот мониторинга и прогноза запущен")

# ✅ Список токенов (USDT-пары в сети Polygon)
TOKENS = {
    "MATIC": "0x0000000000000000000000000000000000001010",
    "USDT": "0x3813e82e6f7098b9583FC0F33a962D02018B6803",
    "UNI":  "0xb33EaAd8d922B1083446DC23f610c2567fB5180f",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "FRAX": "0x104592a158490a9228070e0a8e5343b499e125d0",
    "SUSHI": "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "MKR": "0x6f7c932e7684666c9fd1d44527765433e01ff61d"
}

# ✅ DEX-платформы
PLATFORMS = {
    "sushi": "https://www.sushi.com",
    "uniswap": "https://app.uniswap.org",
    "1inch": "https://app.1inch.io"
}

# ✅ Исторические данные через Alchemy (или другой Node API)
def get_price_data(symbol: str, interval_minutes: int = 1, limit: int = 20):
    # Пример на Binance — заменяется на нужный источник
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval_minutes}m&limit={limit}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        closes = [float(kline[4]) for kline in data]
        return closes
    except Exception as e:
        print(f"❌ Ошибка загрузки {symbol}: {e}")
        return None

# ✅ Прогноз модели
def predict_next(prices):
    if not prices or len(prices) < 5:
        return None
    X = np.array(range(len(prices))).reshape(-1, 1)
    y = np.array(prices)
    model = LinearRegression().fit(X, y)
    next_x = np.array([[len(prices)]])
    predicted_price = model.predict(next_x)[0]
    return predicted_price

# ✅ Основной мониторинг
def monitor():
    while True:
        print("🔍 Цикл мониторинга...")

        for symbol in ["MATICUSDT", "UNIUSDT", "AAVEUSDT"]:
            print(f"🔄 Получаем данные для {symbol}")
            prices = get_price_data(symbol)
            if not prices:
                print(f"❌ Пропускаем {symbol}")
                continue

            predicted = predict_next(prices)
            last = prices[-1]
            change_pct = (predicted - last) / last * 100

            print(f"📊 {symbol}: текущая={last:.4f}, прогноз={predicted:.4f}, изм={change_pct:.2f}%")

            if abs(change_pct) >= 1.5:
                start_time = datetime.utcnow() + timedelta(minutes=4)
                exit_time = start_time + timedelta(minutes=4)
                platform = list(PLATFORMS.values())[np.random.randint(0, 3)]

                message = (
                    f"📉<b>{symbol}</b>\n"
                    f"TIMING: 4 MIN ⌛️\n"
                    f"TIME FOR START: {start_time.strftime('%H:%M')}\n"
                    f"TIME FOR SELL: {exit_time.strftime('%H:%M')}\n"
                    f"PROFIT: {change_pct:.2f}% 💸\n"
                    f"PLATFORM: 📊\n{platform}"
                )
                send_telegram_message(message)

                # ⏳ Ждём для confirm
                time.sleep(240)
                new_price = get_price_data(symbol)[-1]
                real_change = (new_price - last) / last * 100

                confirm_msg = (
                    f"✅ <b>CONFIRMED {symbol}</b>\n"
                    f"Предсказание: {change_pct:.2f}%\n"
                    f"Факт: {real_change:.2f}%\n"
                    f"Результат: {'✅ Успешно' if abs(real_change) >= abs(change_pct * 0.8) else '❌ Мимо'}"
                )
                send_telegram_message(confirm_msg)

        print("⏳ Цикл завершён. Ждём 1 минуту.")
        time.sleep(60)

if __name__ == "__main__":
    monitor()
    
