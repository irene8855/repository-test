import os
import csv
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Конфиги и ключи
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
INFURA_URL = f"https://polygon-mainnet.infura.io/v3/{POLYGON_API_KEY}"  # Или Alchemy URL
w3 = Web3(Web3.HTTPProvider(INFURA_URL))

# Платформы и токены
PLATFORMS = {
    "https://www.sushi.com": "SushiSwap",
    "https://app.uniswap.org/": "Uniswap",
    "https://1inch.io": "1inch"
}

TOKENS = {
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "FRAX": "0x853d955acef822db058eb8505911ed77f175b99e",
    "EMT": "0x0000000000000000000000000000000000000000",  # заменить на реальный адрес
    "AAVE": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "LDO": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
    "BET": "0x0000000000000000000000000000000000000000",  # заменить
    "wstETH": "0x7f39C581F595B53c5cbf63B5b4F30D47b810F1eC",
    "GMT": "0x0000000000000000000000000000000000000000",  # заменить
    "Link": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "SAND": "0x0000000000000000000000000000000000000000"  # заменить
}

HISTORICAL_CSV = "historical.csv"

# Загрузка исторических данных и подготовка ML модели
def load_and_train_model():
    df = pd.read_csv(HISTORICAL_CSV, delimiter='\t')
    # Преобразуем категориальные данные: platform и pair в числовые индексы
    df['platform_id'] = df['platform'].astype('category').cat.codes
    df['pair_id'] = df['pair'].astype('category').cat.codes

    features = df[['timing', 'platform_id', 'pair_id']]
    target_low = df['profit_low']
    target_high = df['profit_high']

    X_train, X_test, y_train_low, y_test_low = train_test_split(features, target_low, test_size=0.2, random_state=42)
    _, _, y_train_high, y_test_high = train_test_split(features, target_high, test_size=0.2, random_state=42)

    model_low = RandomForestRegressor(n_estimators=100, random_state=42)
    model_high = RandomForestRegressor(n_estimators=100, random_state=42)

    model_low.fit(X_train, y_train_low)
    model_high.fit(X_train, y_train_high)

    return model_low, model_high, df

# Отправка сообщений в Telegram
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    r = requests.post(url, data=payload)
    if not r.ok:
        print(f"Telegram send error: {r.text}")

# Генерация ссылок с prefilled swap на платформе
def generate_trade_link(platform_url, pair):
    tokens = pair.split("->")
    base_urls = {
        "https://1inch.io": f"https://app.1inch.io/#/polygon/swap/{tokens[0]}/{tokens[1]}",
        "https://www.sushi.com": f"https://app.sushi.com/swap?inputCurrency={tokens[0]}&outputCurrency={tokens[1]}",
        "https://app.uniswap.org/": f"https://app.uniswap.org/#/swap?inputCurrency={tokens[0]}&outputCurrency={tokens[1]}"
    }
    return base_urls.get(platform_url, platform_url)

# Получение текущей цены по on-chain через Polygon API (пример)
def get_onchain_price(token_address):
    # Пример запроса к Polygon API или Web3 контрактам — здесь нужно заменить на реальный вызов
    # Для упрощения можно использовать данные с публичных API DEX или ораклов
    # Возвращаем float цену токена в USDT, заглушка:
    return 1.0  # заменить на реальный запрос

# Проверка результата сделки (confirmed) — расчет прибыли on-chain
def check_trade_result(pair, buy_time, sell_time):
    # Разбор пары
    tokens = pair.split("->")
    token_in = TOKENS.get(tokens[0])
    token_out = TOKENS.get(tokens[1])
    if not token_in or not token_out:
        return None

    # Получить цены buy и sell on-chain (запросы к API или контракты)
    price_buy = get_onchain_price(token_in)  # пример
    price_sell = get_onchain_price(token_out)  # пример

    profit_percent = ((price_sell - price_buy) / price_buy) * 100
    return profit_percent

# Формирование predicted сообщения
def send_predicted(trade, model_low, model_high, df):
    pair = trade["pair"]
    timing = trade["timing"]
    platform = trade["platform"]

    # Подготовка входа для модели
    platform_id = df.loc[df['platform'] == platform, 'platform'].astype('category').cat.codes.iloc[0]
    pair_id = df.loc[df['pair'] == pair, 'pair'].astype('category').cat.codes.iloc[0]

    X_pred = [[timing, platform_id, pair_id]]
    profit_low_pred = model_low.predict(X_pred)[0]
    profit_high_pred = model_high.predict(X_pred)[0]

    now = datetime.utcnow()
    start_time = now + timedelta(minutes=1)
    sell_time = start_time + timedelta(minutes=timing)

    trade_link = generate_trade_link(platform, pair)

    msg = (f"📉{pair}📈\n"
           f"TIMING: {timing} MIN⌛️\n"
           f"TIME FOR START: {start_time.strftime('%H:%M UTC')}\n"
           f"TIME FOR SELL: {sell_time.strftime('%H:%M UTC')}\n"
           f"PROFIT: {profit_low_pred:.2f}-{profit_high_pred:.2f} 💸\n"
           f"PLATFORMS: 📊\n"
           f"{trade_link}")
    send_telegram_message(msg)
    return start_time, sell_time

# Формирование confirmed сообщения
def send_confirmed(trade, start_time, sell_time):
    pair = trade["pair"]
    platform = trade["platform"]

    profit_real = check_trade_result(pair, start_time, sell_time)
    if profit_real is None:
        msg = f"⚠️ Не удалось получить результат сделки {pair} на {platform}."
    else:
        msg = (f"✅ Сделка {pair} на платформе {platform} завершена.\n"
               f"Реальная прибыль: {profit_real:.2f}%.\n"
               f"Прогноз оправдан." if profit_real > 0 else f"Прогноз не подтвердился.")

    send_telegram_message(msg)

def main():
    send_telegram_message("🤖 Бот запущен и работает на сети Polygon для платформ Sushi, Uniswap и 1inch.")

    model_low, model_high, df = load_and_train_model()

    while True:
        now = datetime.utcnow()

        # Пример логики: проходим по всем сделкам из historical и отправляем predicted
        for trade in df.to_dict(orient='records'):
            start_time, sell_time = send_predicted(trade, model_low, model_high, df)

            # Ждем время сделки + небольшой буфер (в реальном боте можно лучше расписать по расписанию)
            wait_seconds = (sell_time - datetime.utcnow()).total_seconds()
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            send_confirmed(trade, start_time, sell_time)

            time.sleep(5)  # небольшой буфер между итерациями

        time.sleep(60)  # через минуту повторяем цикл

if __name__ == "__main__":
    main()
    
