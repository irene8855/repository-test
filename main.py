import os
import time
import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import pickle
from datetime import datetime

# --- Секреты ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLYGON_API_URL = os.getenv("POLYGON_API_URL")  # Например https://polygon-mainnet.g.alchemy.com/v2/your-api-key

# --- Адреса токенов (Polygon) ---
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

# --- Пары для мониторинга (название: [адрес токена]) ---
PAIRS = {
    "LDOUSDT": TOKENS["LDO"],
    "SANDUSDT": TOKENS["SAND"],
    "GMTUSDT": TOKENS["GMT"],
    "FRAXUSDT": TOKENS["FRAX"],
    "LINKUSDT": TOKENS["LINK"],
    "SUSHIUSDT": TOKENS["SUSHI"],
    "wstETHUSDT": TOKENS["wstETH"],
    "AAVEUSDT": TOKENS["AAVE"],
    "MATICUSDT": TOKENS["MATIC"],
    "UNIUSDT": TOKENS["UNI"],
    "MKRUSDT": TOKENS["MKR"],
}

# --- Логирование запуска ---
print("✅ main.py стартовал")
print("✅ TELEGRAM_TOKEN присутствует:", TELEGRAM_TOKEN[:5] + "..." if TELEGRAM_TOKEN else "❌ НЕТ")
print("✅ TELEGRAM_CHAT_ID присутствует:", TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else "❌ НЕТ")
print("✅ POLYGON_API_URL присутствует:", POLYGON_API_URL[:10] + "..." if POLYGON_API_URL else "❌ НЕТ")

# --- Функция отправки сообщений в Telegram ---
def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram token или chat_id отсутствует, не могу отправить сообщение")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            print(f"✅ Отправлено в Telegram: {text}")
        else:
            print(f"❌ Ошибка отправки в Telegram: {r.status_code} {r.text}")
    except Exception as e:
        print(f"❌ Исключение при отправке в Telegram: {e}")

# --- Отправляем стартовое сообщение ---
send_telegram_message("🚀 Бот мониторинга и прогноза запущен")

# --- Получение исторических данных по цене токена с Polygon API ---
def fetch_historical_prices(token_address, limit=200):
    """
    Получить последние сделки для токена с Polygon API
    """
    url = f"{POLYGON_API_URL}/v2/aggs/ticker/{token_address}/range/1/minute/{int(time.time()-limit*60)}/{int(time.time())}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Ошибка запроса исторических данных: {response.status_code}")
            return None
        data = response.json()
        if 'results' not in data:
            print("Нет данных в ответе от Polygon")
            return None
        df = pd.DataFrame(data['results'])
        # Основные признаки: c - close, o - open, h - high, l - low, v - volume
        df = df.rename(columns={"c": "close", "o": "open", "h": "high", "l": "low", "v": "volume"})
        df['t'] = pd.to_datetime(df['t'], unit='ms')
        df.set_index('t', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"Исключение при запросе исторических данных: {e}")
        return None

# --- Обучение модели ---
def train_model(df: pd.DataFrame):
    """
    Обучить классификатор на основе исторических данных.
    Для простоты — будем предсказывать, вырастет ли цена через 5 минут на 0.5% и более.
    """
    df = df.copy()
    df['future_close'] = df['close'].shift(-5)
    df['target'] = (df['future_close'] / df['close'] - 1) >= 0.005  # 0.5% рост через 5 минут
    df.dropna(inplace=True)

    features = ['open', 'high', 'low', 'close', 'volume']
    X = df[features]
    y = df['target'].astype(int)

    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X, y)
    print("✅ Модель обучена")
    return model

# --- Функция для мониторинга и предсказаний ---
def monitor_and_predict():
    model_store = {}

    while True:
        for pair_name, token_addr in PAIRS.items():
            print(f"🔄 Получаем данные для {pair_name}")
            df = fetch_historical_prices(token_addr)
            if df is None or len(df) < 50:
                print(f"❌ Недостаточно данных для {pair_name}, пропускаем")
                continue

            # Обучаем модель на данных
            model = train_model(df)

            # Используем последние данные для предсказания
            latest = df.iloc[-1][['open', 'high', 'low', 'close', 'volume']].values.reshape(1, -1)
            pred_prob = model.predict_proba(latest)[0][1]
            pred_label = model.predict(latest)[0]

            print(f"ℹ️ Прогноз для {pair_name}: вероятность роста {pred_prob:.2f}, метка {pred_label}")

            # Если вероятность высокая, отправляем предсказание
            if pred_prob > 0.7:
                send_telegram_message(f"🔮 Predictive сигнал по {pair_name}: вероятность роста {pred_prob:.2%}")

                # Дополнительно ждем для подтверждения через 5 минут
                time.sleep(300)  # 5 минут
                df_confirm = fetch_historical_prices(token_addr)
                if df_confirm is None or len(df_confirm) < 50:
                    print(f"❌ Недостаточно данных для подтверждения {pair_name}, пропускаем")
                    continue
                last_close = df.iloc[-1]['close']
                confirm_close = df_confirm.iloc[-1]['close']
                growth = (confirm_close / last_close - 1) * 100

                if growth >= 0.5:
                    send_telegram_message(f"✅ Confirmed сигнал по {pair_name}: рост +{growth:.2f}% за 5 минут")
                else:
                    send_telegram_message(f"❌ Confirmed сигнал по {pair_name} НЕ подтвердился: рост {growth:.2f}%")
            else:
                print(f"ℹ️ Нет сильного сигнала по {pair_name}")

            time.sleep(5)  # небольшой таймаут между парами

        print("⏳ Цикл мониторинга завершён, начинаем заново через 1 минуту")
        time.sleep(60)


if __name__ == "__main__":
    try:
        monitor_and_predict()
    except KeyboardInterrupt:
        print("🛑 Остановка бота пользователем")
    except Exception as e:
        print(f"❌ Ошибка в работе бота: {e}")
        
