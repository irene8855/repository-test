import os
import time
import pandas as pd
import numpy as np
import requests
import datetime
from web3 import Web3
from dotenv import load_dotenv

# Загрузка секретов
load_dotenv()
POLYGON_RPC = os.getenv("POLYGON_RPC")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Web3 и платформы
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

ROUTERS = {
    "Uniswap": {
        "url": "https://app.uniswap.org/#/swap?inputCurrency={}&outputCurrency={}",
        "router_address": Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564"),
    },
    "SushiSwap": {
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "router_address": Web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
    },
    "1inch": {
        "url": "https://app.1inch.io/#/137/swap/{}-{}/USDT",
        "api_url": "https://api.1inch.dev/swap/v5.2/137/quote",
    }
}

TOKENS = {
    "USDT": Web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"),
    "FRAX": Web3.to_checksum_address("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"),
    "AAVE": Web3.to_checksum_address("0xd6df932a45c0f255f85145f286ea0b292b21c90b"),
    "LDO": Web3.to_checksum_address("0xC3C7d422809852031b44ab29EEC9F1EfF2A58756"),
    "BET": Web3.to_checksum_address("0x46e6b214b524310239732D51387075E0e70970bf"),
    "wstETH": Web3.to_checksum_address("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"),
    "GMT": Web3.to_checksum_address("0x5fE80d2CD054645b9419657d3d10d26391780A7B"),
    "Link": Web3.to_checksum_address("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39"),
    "SAND": Web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "EMT": Web3.to_checksum_address("0x6bE7E4A2202cB6E60ef3F94d27a65b906FdA7D86")
}

# Telegram отправка
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Telegram send error: {e}")

# Получение прибыли по маршруту
def get_real_profit(token_symbol):
    try:
        router = ROUTERS["Uniswap"]["router_address"]
        abi = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
        contract = web3.eth.contract(address=router, abi=abi)
        result = contract.functions.getAmountsOut(10**6, [TOKENS["USDT"], TOKENS[token_symbol], TOKENS["USDT"]]).call()
        return (result[-1] / 1e6 - 1) * 100
    except:
        return None

# Построение ссылки
def build_url(platform, token_symbol):
    if platform == "1inch":
        return ROUTERS["1inch"]["url"].format("USDT", token_symbol)
    elif platform == "SushiSwap":
        return ROUTERS["SushiSwap"]["url"].format("USDT", TOKENS[token_symbol])
    else:
        return ROUTERS["Uniswap"]["url"].format("USDT", TOKENS[token_symbol])

# Главный цикл
if __name__ == "__main__":
    notified = {}  # токен -> время последней отправки
    send_telegram("🤖 Бот запущен. Ожидаем всплесков прибыли...")

    while True:
        now = datetime.datetime.now()
        for token in TOKENS:
            if token == "USDT":
                continue

            profit = get_real_profit(token)
            if profit and profit > 1.6:
                last_sent = notified.get(token, now - datetime.timedelta(minutes=10))
                if (now - last_sent).total_seconds() < 300:
                    continue  # Не спамим раньше 5 минут

                timing = 4  # минуты сделки
                delay_notice = 3  # за сколько минут уведомить

                start_time = (now + datetime.timedelta(minutes=delay_notice)).strftime("%H:%M")
                end_time = (now + datetime.timedelta(minutes=delay_notice + timing)).strftime("%H:%M")
                url = build_url("SushiSwap", token)  # можно поменять на динамический выбор

                msg = (
                    f"📉USDT->{token}->USDT📈\n"
                    f"TIMING: {timing} MIN⌛️\n"
                    f"TIME FOR START: {start_time}\n"
                    f"TIME FOR SELL: {end_time}\n"
                    f"PROFIT: {round(profit, 2)} 💸\n"
                    f"PLATFORMS:📊\n{url}"
                )
                send_telegram(msg)
                notified[token] = now

        time.sleep(60)
        
