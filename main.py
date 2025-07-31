import os
import time
import datetime
import requests
import json
import pandas as pd
from web3 import Web3
from dotenv import load_dotenv

# Загрузка .env
load_dotenv("secrets.env")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# RPC лист
RPC_LIST = [
    os.getenv("POLYGON_RPC"),
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor.publicnode.com",
    "https://1rpc.io/matic",
]

# Получение доступного RPC
def get_working_web3():
    for rpc in RPC_LIST:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc))
            if w3.is_connected():
                print(f"[RPC CONNECTED] {rpc}")
                return w3
            else:
                print(f"[RPC FAILED] {rpc}")
        except Exception as e:
            print(f"[RPC ERROR] {rpc} - {e}")
    raise Exception("❌ No working RPC found")

web3 = get_working_web3()

# ABI
GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
GET_PAIR_ABI = '[{"constant":true,"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"pair","type":"address"}],"payable":false,"stateMutability":"view","type":"function"}]'

# Токены
TOKENS = {
    "USDT": web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"),
    "DAI": web3.to_checksum_address("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"),
    "USDC": web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "FRAX": web3.to_checksum_address("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"),
    "AAVE": web3.to_checksum_address("0xd6df932a45c0f255f85145f286ea0b292b21c90b"),
    "LDO": web3.to_checksum_address("0xC3C7d422809852031b44ab29EEC9F1EfF2A58756"),
    "SUSHI": web3.to_checksum_address("0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a"),
    "LINK": web3.to_checksum_address("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39"),
    "SAND": web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "wstETH": web3.to_checksum_address("0x7f39c581f595b53c5cb5bbf5b5f27aa49a3a7e3d"),
    "BET": web3.to_checksum_address("0x3B48d0d7c77f8e96DDC8e741Fd9e4b140b24ceC1"),
    "tBTC": web3.to_checksum_address("0x2d60e239b36ba4EcA8EcE4dD9E0B632d1478b67B"),
    "EMT": web3.to_checksum_address("0x2e49c2d4cfa0833f2ccfa79d057f183d6ffdb66c"),
    "GMT": web3.to_checksum_address("0x5fEaf6fAD2315be2EfEEd3c207cF2eE0A60D83Ee"),
}

# Платформы
ROUTERS = {
    "SushiSwap": {
        "router_address": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory_address": web3.to_checksum_address("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}"
    },
    "Quickswap": {
        "router_address": web3.to_checksum_address("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory_address": web3.to_checksum_address("0x5757371414417b8c6caad45baef941abc7d3ab32"),
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}"
    },
    "1inch": {
        "router_address": None,
        "factory_address": None,
        "url": "https://app.1inch.io/#/137/swap/USDT/{}"
    }
}

# Telegram
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"[telegram] ❌ Exception: {e}")

# Проверка пары
def has_pair(factory_address, tokenA, tokenB):
    try:
        factory = web3.eth.contract(address=factory_address, abi=GET_PAIR_ABI)
        pair = factory.functions.getPair(tokenA, tokenB).call()
        return pair != "0x0000000000000000000000000000000000000000"
    except Exception as e:
        print(f"[has_pair] ❌ {tokenA} ↔ {tokenB} — {e}")
        return False

# Расчет прибыли
def calculate_profit(router_address, factory_address, token):
    try:
        if not has_pair(factory_address, TOKENS["USDT"], TOKENS[token]) or not has_pair(factory_address, TOKENS[token], TOKENS["USDT"]):
            print(f"[DEBUG] ❌ Нет пары USDT↔{token}")
            return None

        contract = web3.eth.contract(address=router_address, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10**6  # 1 USDT

        path = [TOKENS["USDT"], TOKENS[token], TOKENS["USDT"]]
        result = contract.functions.getAmountsOut(amount_in, path).call()
        amount_out = result[-1]
        if amount_out == 0:
            return None
        profit = (amount_out / amount_in - 1) * 100
        return profit
    except Exception as e:
        print(f"[calculate_profit] ❌ Ошибка расчета {token}: {e}")
        return None

def build_url(platform, token):
    return ROUTERS[platform]["url"].format("USDT", TOKENS[token])

def log_trade(data):
    file = "historical.csv"
    df = pd.DataFrame([data])
    if os.path.exists(file):
        df_old = pd.read_csv(file)
        df = pd.concat([df_old, df], ignore_index=True)
    df.to_csv(file, index=False)

# MAIN
def main():
    print("✅ Бот запущен")
    send_telegram("🤖 Бот запущен")

    tracked = {}
    min_profit = 0.1
    trade_duration = 4 * 60
    last_heartbeat = None
    heartbeat_interval = 30 * 60

    while True:
        try:
            now = datetime.datetime.now()

            if (last_heartbeat is None) or ((now - last_heartbeat).total_seconds() >= heartbeat_interval):
                send_telegram(f"🟢 Бот жив: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                last_heartbeat = now

            for token in TOKENS:
                if token == "USDT":
                    continue

                for platform, info in ROUTERS.items():
                    print(f"[DEBUG] Проверка {token} через {platform}")
                    if not info["router_address"]:
                        continue  # 1inch не участвует в расчетах

                    profit = calculate_profit(info["router_address"], info["factory_address"], token)
                    if profit is None or profit < min_profit:
                        print(f"[DEBUG] ❌ Недостаточная или нулевая прибыль по {token} ({platform})")
                        continue

                    last = tracked.get((token, platform))
                    if last and (now - last["start"]).total_seconds() < trade_duration + 60:
                        continue

                    url = build_url(platform, token)
                    send_telegram(
                        f"📉 USDT→{token}→USDT 📈\n"
                        f"Платформа: {platform}\n"
                        f"Старт: {now.strftime('%H:%M')}\n"
                        f"Завершение: {(now + datetime.timedelta(seconds=trade_duration)).strftime('%H:%M')}\n"
                        f"Прибыль: {round(profit, 2)}% 💸\n{url}"
                    )
                    tracked[(token, platform)] = {
                        "start": now,
                        "profit": profit,
                        "platform": platform,
                        "url": url,
                        "token": token,
                    }

            # Завершение сделок
            for key, info in list(tracked.items()):
                now = datetime.datetime.now()
                if (now - info["start"]).total_seconds() >= trade_duration:
                    platform = info["platform"]
                    token = info["token"]
                    router = ROUTERS[platform]["router_address"]
                    factory = ROUTERS[platform]["factory_address"]

                    real_profit = calculate_profit(router, factory, token)
                    if real_profit is not None:
                        send_telegram(
                            f"✅ Сделка завершена ({token} на {platform})\n"
                            f"Предсказано: {round(info['profit'],2)}%\n"
                            f"Фактически: {round(real_profit,2)}%\n{info['url']}"
                        )
                    else:
                        send_telegram(f"⚠️ Не удалось получить фактическую прибыль по {token} ({platform})")

                    log_trade({
                        "timestamp": now.isoformat(),
                        "token": token,
                        "platform": platform,
                        "predicted_profit": round(info["profit"], 4),
                        "real_profit": round(real_profit, 4) if real_profit else None
                    })
                    tracked.pop(key)

            time.sleep(10)

        except Exception as e:
            send_telegram(f"[CRITICAL ERROR] {e}")
            print(f"[CRITICAL ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
    
