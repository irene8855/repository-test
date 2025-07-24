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
        "router_address": Web3.to_checksum_address("0x1111111254fb6c44bac0bed2854e76f90643097d"),  # 1inch router (пример)
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

# ABI для getAmountsOut (все роутеры используют одинаковый интерфейс)
GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'

# Для событий swap (примерный Event Signature для UniswapV3/SushiSwap V2)
# Для упрощения: смотрим логи по методу swap (у разных DEX он может отличаться)
# Для реального проекта нужно уточнить ABI каждого роутера. Здесь общий пример по событию Swap (Uniswap V2)

# Swap Event signature (keccak256("Swap(address,uint256,uint256,uint256,uint256,address)"))
SWAP_EVENT_SIGNATURE = web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()

# Телеграм отправка
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Telegram send error: {e}")

# Получение прибыли с конкретного DEX (используем getAmountsOut)
def get_profit_on_dex(router_address, token_symbol):
    try:
        contract = web3.eth.contract(address=router_address, abi=GET_AMOUNTS_OUT_ABI)
        path = [TOKENS["USDT"], TOKENS[token_symbol], TOKENS["USDT"]]
        amount_in = 10**6  # 1 USDT (6 decimals)
        result = contract.functions.getAmountsOut(amount_in, path).call()
        profit_percent = (result[-1] / 1e6 - 1) * 100
        return profit_percent
    except Exception as e:
        #print(f"Error getting profit for {token_symbol} on {router_address}: {e}")
        return None

# Сбор объёма и волатильности через события Swap за последние 5 минут на данном роутере
def get_volume_volatility(router_address, token_symbol):
    now_block = web3.eth.block_number
    # Оцениваем блоки за последние ~5 минут (примерно 4 блока в минуту на Polygon)
    blocks_to_check = 20  
    from_block = max(now_block - blocks_to_check, 0)
    to_block = now_block

    try:
        logs = web3.eth.get_logs({
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": router_address,
            "topics": [SWAP_EVENT_SIGNATURE]
        })
    except Exception as e:
        #print(f"Error getting logs for volume: {e}")
        return 0, 0

    # Извлечение объёмов из лога: парсим по топикам и data
    # Для простоты суммируем по token_symbol объемы входа и выхода
    # Реальный разбор зависит от точного ABI и формата события Swap
    # Здесь упрощенный пример (требуется адаптация под конкретные DEX)

    volumes = []
    # Пока не реализуем детальную декодировку (сложно без ABI)
    # Возьмём количество событий как proxy для объема, волатильность — пока 0 (нужно доработать)

    volume = len(logs)  # proxy для объёма
    volatility = 0  # placeholder, можно посчитать разброс цен с доп. API или графика

    return volume, volatility

# Сбор сигналов по всем DEX
def get_profits(token_symbol):
    profits = {}
    for dex_name, dex_info in ROUTERS.items():
        router_addr = dex_info["router_address"]
        profit = get_profit_on_dex(router_addr, token_symbol)
        if profit is not None:
            profits[dex_name] = profit
    return profits

# Построение ссылки на swap по платформе
def build_url(platform, token_symbol):
    if platform == "1inch":
        return ROUTERS["1inch"]["url"].format("USDT", token_symbol)
    elif platform == "SushiSwap":
        return ROUTERS["SushiSwap"]["url"].format("USDT", TOKENS[token_symbol])
    else:
        return ROUTERS["Uniswap"]["url"].format("USDT", TOKENS[token_symbol])

# Сохранение исторических данных в csv
def save_to_csv(data):
    filename = "historical.csv"
    df = pd.DataFrame([data])
    if os.path.exists(filename):
        df_existing = pd.read_csv(filename)
        df = pd.concat([df_existing, df], ignore_index=True)
    df.to_csv(filename, index=False)

if __name__ == "__main__":
    notified = {}  # token_symbol -> last send datetime
    trade_records = {}  # token_symbol -> {'start': datetime, 'profit': float, 'platform': str}

    send_telegram("🤖 Бот запущен. Ожидаем всплесков прибыли...")

    while True:
        now = datetime.datetime.now()
        for token in TOKENS:
            if token == "USDT":
                continue

            profits = get_profits(token)
            # Ищем максимальный профит среди платформ
            if not profits:
                continue

            max_platform = max(profits, key=profits.get)
            max_profit = profits[max_platform]

            # Фильтр на профит выше 1.6%
            if max_profit > 1.6:
                last_sent = notified.get(token, now - datetime.timedelta(minutes=10))
                if (now - last_sent).total_seconds() < 300:
                    continue  # не спамим чаще 5 минут

                # Собираем объем и волатильность по выбранной платформе
                volume, volatility = get_volume_volatility(ROUTERS[max_platform]["router_address"], token)

                timing = 4  # время сделки в минутах
                delay_notice = 3  # время оповещения до старта

                start_time_dt = now + datetime.timedelta(minutes=delay_notice)
                end_time_dt = start_time_dt + datetime.timedelta(minutes=timing)
                start_time = start_time_dt.strftime("%H:%M")
                end_time = end_time_dt.strftime("%H:%M")

                url = build_url(max_platform, token)

                msg = (
                    f"📉USDT->{token}->USDT📈\n"
                    f"PLATFORM: {max_platform}\n"
                    f"TIMING: {timing} MIN⌛️\n"
                    f"START TIME: {start_time}\n"
                    f"SELL TIME: {end_time}\n"
                    f"ESTIMATED PROFIT: {round(max_profit,2)} % 💸\n"
                    f"VOLUME (events): {volume}\n"
                    f"VOLATILITY: {volatility}\n"
                    f"TRADE LINK:\n{url}"
                )
                send_telegram(msg)

                notified[token] = now
                trade_records[token] = {
                    "start": now,
                    "profit_estimated": max_profit,
                    "platform": max_platform,
                    "volume": volume,
                    "volatility": volatility,
                    "start_time": start_time,
                    "end_time": end_time,
                    "url": url,
                }

        # Проверяем сделки для подтверждения реального результата через timing минут
        to_remove = []
        for token, info in trade_records.items():
            elapsed = (now - info["start"]).total_seconds()
            if elapsed >= 60*4:  # через 4 минуты считаем реальную прибыль
                real_profit = get_profit_on_dex(ROUTERS[info["platform"]]["router_address"], token)
                if real_profit is not None:
                    msg = (
                        f"✅ Результат сделки по {token} на {info['platform']}:\n"
                        f"Предсказанная прибыль: {round(info['profit_estimated'],2)} %\n"
                        f"Реальная прибыль: {round(real_profit,2)} %\n"
                        f"Время сделки: {info['start_time']} - {info['end_time']}\n"
                        f"Объём (events): {info['volume']}\n"
                        f"Волатильность: {info['volatility']}\n"
                        f"Ссылка: {info['url']}"
                    )
                    send_telegram(msg)

                    # Сохраняем в исторический файл
                    save_to_csv({
                        "token": token,
                        "platform": info["platform"],
                        "start_time": info["start_time"],
                        "end_time": info["end_time"],
                        "predicted_profit": info["profit_estimated"],
                        "real_profit": real_profit,
                        "volume": info["volume"],
                        "volatility": info["volatility"],
                        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "url": info["url"]
                    })

                to_remove.append(token)

        for token in to_remove:
            del trade_records[token]

        time.sleep(60)
        
