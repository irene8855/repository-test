import os
import time
import datetime
import requests
import json
import pandas as pd
from web3 import Web3
from dotenv import load_dotenv

# Загрузка переменных
load_dotenv("secrets.env")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# RPC-провайдеры
RPC_LIST = [
    os.getenv("POLYGON_RPC"),
    "https://polygon-rpc.com",
    "https://rpc-mainnet.maticvigil.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor.publicnode.com",
    "https://1rpc.io/matic",
]

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

# Список токенов
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
    "wstETH": web3.to_checksum_address("0x7f39c581f595b53c5cb19bcd5f5cf5c77c4e0534"),
    "BET": web3.to_checksum_address("0x0f1ebbf7b0adf7f96e04239a28f1a1b04d3f302e"),
    "tBTC": web3.to_checksum_address("0x2e1ad108ff1d8c782fcbbb89aad783ac49586756"),
    "EMT": web3.to_checksum_address("0x6e97a9b49c933e7f5f4373c75f9ed0eb57dcbe47"),
    "GMT": web3.to_checksum_address("0x99e221b76f5f43738d0ba3e6cce5c3d6db7b052f"),
}

# Платформы
ROUTERS = {
    "SushiSwap": {
        "router_address": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}"
    },
    "Uniswap": {
        "router_address": web3.to_checksum_address("0x93b2f27d628dee53a61b665d7b01689c6d1147c5"),  # примерный адрес
        "url": "https://app.uniswap.org/#/swap?inputCurrency={}&outputCurrency={}&chain=polygon"
    },
    "1inch": {
        "router_address": None,  # нет прямого вызова
        "url": "https://app.1inch.io/#/137/swap/{}"
    }
}

# ABI
GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print(f"[telegram] ✅ Sent: {message[:60]}...")
        else:
            print(f"[telegram] ❌ Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[telegram] ❌ Exception: {e}")

def calculate_profit(router_address, token):
    try:
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
        if "execution reverted" in str(e):
            print(f"[calculate_profit] ❌ USDT → {token} не сработал: execution reverted")
        else:
            print(f"[calculate_profit] ❌ Ошибка расчета {token}: {e}")
        return None

def build_url(platform, token):
    token_addr = TOKENS[token]
    if platform == "1inch":
        return ROUTERS[platform]["url"].format(token_addr)
    else:
        return ROUTERS[platform]["url"].format("USDT", token_addr)

def log_trade(data):
    file = "historical.csv"
    df = pd.DataFrame([data])
    if os.path.exists(file):
        df_old = pd.read_csv(file)
        df = pd.concat([df_old, df], ignore_index=True)
    df.to_csv(file, index=False)

def main():
    print("✅ Бот запущен")
    send_telegram("🤖 Бот запущен и отслеживает сделки")

    tracked = {}
    min_profit = 0.1  # %
    trade_duration = 4 * 60  # сек
    last_heartbeat = None
    heartbeat_interval = 30 * 60

    while True:
        try:
            now = datetime.datetime.now()
            if (last_heartbeat is None) or ((now - last_heartbeat).total_seconds() >= heartbeat_interval):
                send_telegram(f"🟢 Бот работает: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                last_heartbeat = now

            for token in TOKENS:
                if token == "USDT":
                    continue

                for platform, data in ROUTERS.items():
                    if platform == "1inch":
                        continue  # пока пропускаем, нет API-данных

                    print(f"[DEBUG] Проверка {token} через {platform}")
                    profit = calculate_profit(data["router_address"], token)

                    if profit is None or profit < min_profit:
                        print(f"[DEBUG] ❌ Недостаточная или нулевая прибыль по {token} ({platform})")
                        continue

                    last = tracked.get((token, platform))
                    if last and (now - last["start"]).total_seconds() < trade_duration + 60:
                        continue

                    start = now
                    end = now + datetime.timedelta(seconds=trade_duration)
                    url = build_url(platform, token)

                    send_telegram(
                        f"📉 USDT → {token} → USDT 📈\n"
                        f"PLATFORM: {platform}\n"
                        f"START: {start.strftime('%H:%M')}\n"
                        f"SELL: {end.strftime('%H:%M')}\n"
                        f"EST. PROFIT: {round(profit, 2)}% 💸\n"
                        f"{url}"
                    )

                    tracked[(token, platform)] = {
                        "start": start,
                        "profit": profit,
                        "platform": platform,
                        "url": url
                    }

            for key, info in list(tracked.items()):
                now = datetime.datetime.now()
                elapsed = (now - info["start"]).total_seconds()
                if elapsed >= trade_duration:
                    token, platform = key
                    real_profit = calculate_profit(ROUTERS[platform]["router_address"], token)

                    if real_profit is not None:
                        send_telegram(
                            f"✅ Сделка завершена ({token} на {platform})\n"
                            f"Предсказано: {round(info['profit'],2)}%\n"
                            f"Фактически: {round(real_profit,2)}%\n"
                            f"{info['url']}"
                        )
                    else:
                        send_telegram(
                            f"⚠️ Не удалось получить фактическую прибыль по {token} ({platform})"
                        )

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
            print(f"[CRITICAL ERROR] {e}")
            send_telegram(f"[CRITICAL ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
    
