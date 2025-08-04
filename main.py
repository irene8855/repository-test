import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"

LONDON_TZ = pytz.timezone("Europe/London")

# Базовые токены (Polygon)
TOKENS = {
    "USDT": "0xc2132D05D31C914a87C6611C10748AaCbA6cD43E",
    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "DAI":  "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "FRAX": "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
    "SAND": "0xbbba073C31bF03b8ACf7c28EF0738DeCF3695683",
    "AAVE": "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
    "LDO":  "0xC3C7d422809852031b44ab29eec9f1eff2a58756",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "POL":  "0xE256Cf79a8f3bFbE427A0c57a6B5a278eC2acDC1",
    "WPOL": "0xf62f05d5De64AbD38eDd17A8fCfBF8336fB9f2c2",
    "wstETH": "0x7f39c581f595b53c5cb5bbf5b5f27aa49a3a7e3d",
    "BET":  "0x3183a3f59e18beb3214be625e4eb2a49ac03df06",
    "tBTC": "0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede",
    "EMT":  "0x1e3a602906a749c6c07127dd3f2d97accb3fda3a",
    "GMT":  "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419",
}

PLATFORMS = {
    "1inch": "1inch",
    "SushiSwap": "SushiSwap",
    "Uniswap": "UniswapV3",
}

MAX_REQUESTS_PER_SECOND = 5
REQUEST_INTERVAL = 1 / MAX_REQUESTS_PER_SECOND

API_URL = "https://polygon.api.0x.org/swap/v1/quote"

MIN_AMOUNT_USD = 100  # Минимум 100 USDT или USDC
DECIMALS = 6

def send_telegram(msg: str):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
        if resp.status_code != 200 and DEBUG_MODE:
            print(f"[Telegram] Ошибка отправки: {resp.text}")
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Telegram] Исключение: {e}")

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def query_0x_quote(sell_token: str, buy_token: str, sell_amount: int):
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            error_text = f"[0x API] Ошибка {resp.status_code}: {resp.text}"
            if DEBUG_MODE:
                print(error_text)
            # Отправляем ошибку в Telegram
            send_telegram(error_text)
            return None
    except Exception as e:
        error_text = f"[0x API] Исключение запроса: {e}"
        if DEBUG_MODE:
            print(error_text)
        send_telegram(error_text)
        return None

def extract_platforms(protocols):
    found = set()
    for segment in protocols:
        for route in segment:
            dex = route[0].lower()
            for platform_key, platform_name in PLATFORMS.items():
                if platform_key.lower() in dex:
                    found.add(platform_name)
    return list(found)

def main():
    print("🚀 Bot started")
    send_telegram("🤖 Бот запущен и готов отслеживать сделки")

    base_tokens = ["USDT", "USDC"]
    tracked = {}

    sell_amount_min = MIN_AMOUNT_USD * (10 ** DECIMALS)  # минимум 100 USDT/USDC в мин единицах

    min_profit_percent = 0.5

    last_request_time = 0

    while True:
        now = get_local_time()

        for base_token in base_tokens:
            base_addr = TOKENS[base_token]

            for token_symbol, token_addr in TOKENS.items():
                if token_symbol == base_token:
                    continue

                # Лимитируем частоту запросов
                elapsed = time.time() - last_request_time
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                last_request_time = time.time()

                # Используем минимум 100 USDT/USDC как sellAmount
                sell_amount = sell_amount_min

                quote = query_0x_quote(sell_token=base_addr, buy_token=token_addr, sell_amount=sell_amount)
                if quote is None:
                    continue

                buy_amount = int(quote.get("buyAmount", "0"))
                if buy_amount == 0:
                    continue

                profit = (buy_amount / sell_amount - 1) * 100

                if profit < min_profit_percent:
                    if DEBUG_MODE:
                        print(f"Низкий профит {profit:.4f}% для {base_token}->{token_symbol}")
                    continue

                protocols = quote.get("protocols", [])
                platforms_found = extract_platforms(protocols)
                platforms_used = [p for p in platforms_found if p in PLATFORMS.values()]
                if not platforms_used:
                    if DEBUG_MODE:
                        print(f"Платформа сделки не из списка для {base_token}->{token_symbol}: {platforms_found}")
                    continue

                key = (base_token, token_symbol)
                last_time = tracked.get(key, 0)
                if (time.time() - last_time) < 600:
                    continue
                tracked[key] = time.time()

                url = f"https://app.1inch.io/#/polygon/swap/{base_addr}/{token_addr}"

                msg = (
                    f"📈 Потенциально выгодная сделка:\n"
                    f"{base_token} → {token_symbol}\n"
                    f"Профит: {profit:.2f}%\n"
                    f"Платформы: {', '.join(platforms_used)}\n"
                    f"Ссылка: {url}\n"
                    f"Время: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                )

                send_telegram(msg)
                if DEBUG_MODE:
                    print(msg)

        time.sleep(60)

if __name__ == "__main__":
    main()
    
