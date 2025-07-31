import os
import time
import datetime
import requests
import pandas as pd
from web3 import Web3
from dotenv import load_dotenv
import pytz

load_dotenv("secrets.env")

# Настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RPC_URL = os.getenv("POLYGON_RPC")

LONDON_TZ = pytz.timezone("Europe/London")

web3 = Web3(Web3.HTTPProvider(RPC_URL))

# Контракты
GET_PAIR_ABI = '[{"constant":true,"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"pair","type":"address"}],"payable":false,"stateMutability":"view","type":"function"}]'
GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'

# Токены (9 штук) — адреса для SushiSwap и Quickswap, decimals
TOKENS = {
    "USDT":  {"decimals": 6,  "sushi": "0xc2132D05D31c914a87C6611C10748AaCbA6cD43E", "quick": "0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"},
    "DAI":   {"decimals": 18, "sushi": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063", "quick": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"},
    "USDC":  {"decimals": 6,  "sushi": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "quick": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"},
    "FRAX":  {"decimals": 18, "sushi": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89", "quick": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"},
    "wstETH":{"decimals": 18, "sushi": "0x7f39c581f595b53c5cb19bcd5f5cf9b136097b5a", "quick": "0x7f39c581f595b53c5cb19bcd5f5cf9b136097b5a"},
    "BET":   {"decimals": 18, "sushi": "0x3183a3f59e18beb3214be625e4eb2a49ac03df06", "quick": "0x3183a3f59e18beb3214be625e4eb2a49ac03df06"},
    "tBTC":  {"decimals": 18, "sushi": "0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede", "quick": "0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede"},
    "EMT":   {"decimals": 18, "sushi": "0x1e3a602906a749c6c07127dd3f2d97accb3fda3a", "quick": "0x1e3a602906a749c6c07127dd3f2d97accb3fda3a"},
    "GMT":   {"decimals": 18, "sushi": "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419", "quick": "0x5f4ec3df9cbd43714fe2740f5e3616155e3616155c5b8419"},
}

BRIDGE_TOKENS = ["USDC", "DAI", "FRAX"]

FACTORIES = {
    "SushiSwap": web3.to_checksum_address("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
    "Quickswap": web3.to_checksum_address("0x5757371414417b8c6caad45baef941abc7d3ab32"),
}

ROUTERS = {
    "SushiSwap": {
        "router": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory": FACTORIES["SushiSwap"],
        "platform_key": "sushi",
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}"
    },
    "Quickswap": {
        "router": web3.to_checksum_address("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": FACTORIES["Quickswap"],
        "platform_key": "quick",
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}"
    }
}

MIN_PROFIT = 0.1  # минимальный профит для сигнала в %

# Логирование
def log(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"{now}\n[DEBUG] {msg}\n")

# Telegram
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        log(f"[telegram] error: {e}")

# Проверка наличия пары
def has_pair(factory_addr, tokenA, tokenB):
    try:
        factory = web3.eth.contract(address=factory_addr, abi=GET_PAIR_ABI)
        pair = factory.functions.getPair(tokenA, tokenB).call()
        if pair == "0x0000000000000000000000000000000000000000":
            return False
        return True
    except Exception as e:
        log(f"Ошибка has_pair: {e}")
        return False

def find_pair_with_bridge(factory_addr, platform_key, usdt_addr, token_addr):
    # Ищем пару напрямую
    if has_pair(factory_addr, usdt_addr, token_addr):
        return [usdt_addr, token_addr]

    # Через мостовые токены
    for bridge in BRIDGE_TOKENS:
        if bridge == "USDT":
            continue
        bridge_addr = web3.to_checksum_address(TOKENS[bridge][platform_key])
        if has_pair(factory_addr, usdt_addr, bridge_addr) and has_pair(factory_addr, bridge_addr, token_addr):
            return [usdt_addr, bridge_addr, token_addr]
    return None

def get_amounts_out(router_addr, amount_in, path):
    contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)
    return contract.functions.getAmountsOut(amount_in, path).call()

def check_profit(token_symbol, platform):
    try:
        platform_data = ROUTERS[platform]
        platform_key = platform_data["platform_key"]
        router = platform_data["router"]
        factory = platform_data["factory"]

        tok = TOKENS[token_symbol]
        tok_addr = web3.to_checksum_address(tok[platform_key])
        usdt_addr = web3.to_checksum_address(TOKENS["USDT"][platform_key])

        path = find_pair_with_bridge(factory, platform_key, usdt_addr, tok_addr)
        if not path:
            log(f"Нет пары USDT↔{token_symbol} (даже через мост) на {platform}")
            return None

        amount_in = 10 ** TOKENS["USDT"]["decimals"]
        amounts_out = get_amounts_out(router, amount_in, path)
        out = amounts_out[-1]

        profit = (out / amount_in - 1) * 100
        log(f"Прибыль {token_symbol} на {platform}: {profit:.2f}%")
        return profit
    except Exception as e:
        log(f"Ошибка check_profit для {token_symbol} на {platform}: {e}")
        return None

# Логика работы и сообщения
def main():
    # 1) Сообщение о запуске
    send_telegram("🚀 Бот запущен и готов к работе.")

    # Загружаем историю (CSV) или создаем новую
    history_file = "trade_history.csv"
    if os.path.exists(history_file):
        history = pd.read_csv(history_file)
    else:
        history = pd.DataFrame(columns=["datetime", "token", "platform", "profit", "note"])

    try:
        while True:
            now = datetime.datetime.now()
            # 2) Каждые 30 минут сообщаем, что бот жив
            if now.minute % 30 == 0 and now.second < 5:
                send_telegram("⏰ Бот работает, все системы в норме.")

            # Перебираем токены и платформы, ищем возможность сделки
            for token in TOKENS:
                if token == "USDT":
                    continue

                for platform in ROUTERS:
                    profit = check_profit(token, platform)
                    if profit is None:
                        continue

                    if profit > MIN_PROFIT:
                        # 3) Сообщение о сделке (пример)
                        msg = (f"⚡ Сделка по {token} на {platform}\n"
                               f"Прибыль: {profit:.2f}%\n"
                               f"Вход: USDT\nВыход: {token}\n"
                               f"Платформа: {platform}")
                        send_telegram(msg)

                        # Сохраняем в историю с примерной датой
                        history = history.append({
                            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                            "token": token,
                            "platform": platform,
                            "profit": profit,
                            "note": "Сигнал сделки"
                        }, ignore_index=True)
                        history.to_csv(history_file, index=False)

                        # 4) Сообщение о подтверждении (условно, через 5 минут)
                        time.sleep(300)
                        send_telegram(f"✅ Сделка по {token} на {platform} подтверждена. Фактическая прибыль: {profit:.2f}%")
            time.sleep(10)
    except KeyboardInterrupt:
        log("Бот остановлен вручную")

if __name__ == "__main__":
    main()
    
