import os
import time
import requests
import pandas as pd
import numpy as np
from web3 import Web3
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta

# === Настройки из environment / secrets ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")  # https://polygon-mainnet.g.alchemy.com/v2/yourkey

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALCHEMY_API_KEY]):
    raise Exception("Отсутствуют обязательные переменные окружения TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALCHEMY_API_KEY")

alchemy_url = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
web3 = Web3(Web3.HTTPProvider(alchemy_url))
if not web3.isConnected():
    raise Exception("Не удалось подключиться к Alchemy Polygon node")

# Адреса и пары
TOKENS = {
    "USDT": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    "MATIC": "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0",
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    "MKR": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",
    "BET": "0x4e3decbb3645551b8a19f0ea1678079fcb33fb4c",
    "EMT": "0x5aDfDf1B5Dc3846aAc80E5bAe86542795E23f798",
}

MONITORED_PAIRS = [
    ("USDT", "FRAX"),
    ("USDT", "LDO"),
    ("USDT", "BET"),
    ("USDT", "GMT"),
    ("USDT", "SAND"),
    ("USDT", "EMT"),
]

UNISWAP_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"  # Uniswap V3 Factory Polygon
SUSHISWAP_FACTORY = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"  # SushiSwap Factory Polygon

PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

FACTORY_ABI = [
    {
        "constant": True,
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

class Dex:
    def __init__(self, name, factory_address):
        self.name = name
        self.factory = web3.eth.contract(address=Web3.toChecksumAddress(factory_address), abi=FACTORY_ABI)

    def get_pair_address(self, tokenA, tokenB):
        return self.factory.functions.getPair(
            Web3.toChecksumAddress(tokenA), Web3.toChecksumAddress(tokenB)
        ).call()

    def get_reserves(self, pair_address):
        if pair_address == "0x0000000000000000000000000000000000000000":
            return None
        pair = web3.eth.contract(address=Web3.toChecksumAddress(pair_address), abi=PAIR_ABI)
        try:
            reserves = pair.functions.getReserves().call()
            token0 = pair.functions.token0().call()
            token1 = pair.functions.token1().call()
            return reserves, token0, token1
        except Exception as e:
            print(f"[{self.name}] Ошибка получения резервов пары {pair_address}: {e}")
            return None

    def get_price(self, tokenA, tokenB):
        pair_address = self.get_pair_address(tokenA, tokenB)
        res = self.get_reserves(pair_address)
        if res is None:
            return None
        (reserve0, reserve1, _), token0, token1 = res
        if token0.lower() == tokenA.lower():
            if reserve0 == 0:
                return None
            return reserve1 / reserve0
        else:
            if reserve1 == 0:
                return None
            return reserve0 / reserve1

uniswap = Dex("Uniswap", UNISWAP_FACTORY)
sushiswap = Dex("SushiSwap", SUSHISWAP_FACTORY)
DEXES = [uniswap, sushiswap]

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except Exception as e:
        print(f"Ошибка отправки Telegram-сообщения: {e}")

# Простейшая ML модель на основе линейной регрессии для прогноза цены (пример)
def train_simple_model(df, token):
    # df должен содержать колонки: ['timestamp', 'price']
    df = df.dropna()
    if len(df) < 10:
        return None
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['time_int'] = df['timestamp'].astype(np.int64) // 10**9  # в секундах
    X = df[['time_int']].values
    y = df['price'].values
    model = LinearRegression()
    model.fit(X, y)
    return model

def predict_price(model, future_seconds=120):
    if model is None:
        return None
    now = int(datetime.utcnow().timestamp())
    future_time = np.array([[now + future_seconds]])
    pred = model.predict(future_time)
    return float(pred[0])

def fetch_historical_prices(token_symbol):
    # Заглушка - загружаем csv из файла (в формате timestamp, price)
    fname = f"historical_{token_symbol}.csv"
    if not os.path.isfile(fname):
        print(f"Файл с историческими данными {fname} не найден")
        return None
    df = pd.read_csv(fname)
    return df

def main():
    send_telegram_message("🚀 Бот запущен и начал мониторинг.")

    while True:
        for base, quote in MONITORED_PAIRS:
            prices = {}
            for dex in DEXES:
                price = dex.get_price(TOKENS[base], TOKENS[quote])
                if price is not None:
                    prices[dex.name] = price

            if len(prices) < 2:
                # Не все цены получили
                continue

            # Пример арбитража — если цены на разных DEX отличаются больше чем на 0.5%
            price_values = list(prices.values())
            max_price = max(price_values)
            min_price = min(price_values)
            diff_pct = (max_price - min_price) / min_price * 100

            # Прогноз цены с ML
            hist_df = fetch_historical_prices(quote)
            model = None
            if hist_df is not None:
                model = train_simple_model(hist_df, quote)
            pred_price = predict_price(model, future_seconds=180) if model else None

            if diff_pct > 0.5:
                text = f"⚡️ Арбитражный сигнал для {base}/{quote}\n"
                text += f"Цены:\n"
                for dex_name, p in prices.items():
                    text += f"- {dex_name}: {p:.6f}\n"
                text += f"Разница: {diff_pct:.2f}%\n"
                if pred_price:
                    text += f"Прогноз цены {quote} через 3 мин: {pred_price:.6f}\n"
                text += f"Время: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
                send_telegram_message(text)

        time.sleep(30)


if __name__ == "__main__":
    main()
    
