import os
import time
import datetime
import json
import requests
import pandas as pd
from web3 import Web3
from dotenv import load_dotenv

# Flask и threading
from flask import Flask
import threading

# Загрузка переменных окружения из .env
load_dotenv()
POLYGON_RPC = os.getenv("POLYGON_RPC")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Инициализация Web3
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

ROUTERS = {
    "Uniswap": {
        "url": "https://app.uniswap.org/#/swap?inputCurrency={}&outputCurrency={}",
        "router_address": web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564"),
    },
    "SushiSwap": {
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "router_address": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
    },
    "1inch": {
        "url": "https://app.1inch.io/#/137/swap/{}-{}",
        "router_address": web3.to_checksum_address("0x1111111254fb6c44bac0bed2854e76f90643097d"),
    }
}

TOKENS = {
    "USDT": web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"),
    "FRAX": web3.to_checksum_address("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"),
    "AAVE": web3.to_checksum_address("0xd6df932a45c0f255f85145f286ea0b292b21c90b"),
    "LDO": web3.to_checksum_address("0xC3C7d422809852031b44ab29EEC9F1EfF2A58756"),
    "BET": web3.to_checksum_address("0x46e6b214b524310239732D51387075E0e70970bf"),
    "wstETH": web3.to_checksum_address("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"),
    "GMT": web3.to_checksum_address("0x5fE80d2CD054645b9419657d3d10d26391780A7B"),
    "Link": web3.to_checksum_address("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39"),
    "SAND": web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "EMT": web3.to_checksum_address("0x6bE7E4A2202cB6E60ef3F94d27a65b906FdA7D86"),

    "WMATIC": web3.to_checksum_address("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"),
    "DAI": web3.to_checksum_address("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"),
    "USDC": web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "tBTC": web3.to_checksum_address("0x2d8e5b2b51f5c64d760a2cfc0f29f13f4ebf17a1"),
    "SUSHI": web3.to_checksum_address("0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a")
}

GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'

SWAP_EVENT_SIGNATURE = web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Telegram send error: {e}")

def graphql_query(query, variables=None):
    url = "https://api.thegraph.com/subgraphs/name/sushiswap/matic-exchange"
    headers = {"Content-Type": "application/json"}
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return response.json()
    except Exception as e:
        print(f"GraphQL request error: {e}")
        return None

def get_price_history_volatility(token_symbol):
    token_addr = TOKENS[token_symbol].lower()
    query = """
    query($token: String!) {
      token(id: $token) {
        tokenDayData(first: 10, orderBy: date, orderDirection: desc) {
          priceUSD
        }
      }
    }
    """
    variables = {"token": token_addr}
    data = graphql_query(query, variables)

    try:
        prices = [float(p["priceUSD"]) for p in data["data"]["token"]["tokenDayData"] if p["priceUSD"]]
        if len(prices) > 1:
            return pd.Series(prices).std()
    except Exception as e:
        print(f"Volatility fetch error: {e}")
    return 0

def get_profit_on_dex(router_address, token_symbol):
    try:
        contract = web3.eth.contract(address=router_address, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10**6  # 1 USDT
        usdt = TOKENS["USDT"]
        token = TOKENS[token_symbol]
        wmatic = TOKENS["WMATIC"]

        paths = [
            [usdt, token, usdt],               # основной маршрут
            [usdt, token],                     # прямой
            [token, usdt],                     # обратный
            [usdt, wmatic, token, usdt],       # через WMATIC
            [usdt, token, wmatic, usdt],       # токен через WMATIC обратно
            [usdt, wmatic, token],             # прямой через WMATIC
            [token, wmatic, usdt]              # обратный через WMATIC
        ]

        for path in paths:
            try:
                print(f"[DEBUG] ➡️ Проверка маршрута: {path}")
                result = contract.functions.getAmountsOut(amount_in, path).call()

                if result[-1] > 0:
                    profit_percent = (result[-1] / amount_in - 1) * 100

                    # ✅ Тестовая отправка в Telegram при профите больше 0.5%
                    if profit_percent > 0.5:
                        message = (
                            f"🚨 Тестовый сигнал\n"
                            f"Токен: {token_symbol}\n"
                            f"Путь: {path}\n"
                            f"Профит: {profit_percent:.2f}%"
                        )
                        send_telegram_message(message)

                    return profit_percent

            except Exception as e:
                print(f"[SKIP] ⛔ Маршрут не работает: {path} — {e}")
                continue

        print(f"[DIAG] ⚠️ Все маршруты не дали результата для {token_symbol}")
        return None

    except Exception as e:
        print(f"[ERROR] ❌ get_profit_on_dex() ошибка: {e}")
        return None

def get_volume_volatility(router_address, token_symbol):
    now_block = web3.eth.block_number
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
    except Exception:
        return 0, 0
    volume = len(logs)
    volatility = get_price_history_volatility(token_symbol)
    return volume, volatility

def get_profits(token_symbol):
    profits = {}
    print(f"[DIAG] 🔍 Старт get_profits() для {token_symbol}")
    
    for dex_name, dex_info in ROUTERS.items():
        try:
            print(f"[DIAG] Запрос через getAmountsOut: {dex_name}")
            profit = get_profit_on_dex(dex_info["router_address"], token_symbol)

            if profit is not None:
                print(f"[DIAG] ✅ Прибыль на {dex_name} для {token_symbol}: {round(profit, 2)}%")
                profits[dex_name] = profit
            else:
                print(f"[DIAG] ⚠️ Нет прибыли на {dex_name} для {token_symbol}")
        except Exception as e:
            print(f"[ERROR] ❌ Ошибка получения прибыли на {dex_name}: {e}")
    
    print(f"[DIAG] 🧾 Итог по {token_symbol}: {profits}")
    return profits

def build_url(platform, token_symbol):
    if platform == "1inch":
        return ROUTERS["1inch"]["url"].format("USDT", token_symbol)
    elif platform == "SushiSwap":
        return ROUTERS["SushiSwap"]["url"].format("USDT", TOKENS[token_symbol])
    else:
        return ROUTERS["Uniswap"]["url"].format("USDT", TOKENS[token_symbol])

def save_to_csv(data):
    filename = "historical.csv"
    df = pd.DataFrame([data])
    if os.path.exists(filename):
        df_existing = pd.read_csv(filename)
        df = pd.concat([df_existing, df], ignore_index=True)
    df.to_csv(filename, index=False)

# ... начало файла без изменений

import numpy as np  # перенесён в начало

# ========== Flask ==========
app = Flask(__name__)

@app.route("/")
def healthcheck():
    return "✅ Bot is running", 200

# ========== Main Logic ==========

def main_loop():
    notified = {}
    trade_records = {}

    print("[DEBUG] main_loop стартовал")
    send_telegram("🤖 Бот запущен. Диагностический режим активирован.")

    while True:
        try:
            now = datetime.datetime.now()
            print(f"[DEBUG] Цикл запущен в {now.strftime('%H:%M:%S')}")

            for token in TOKENS:
                if token == "USDT":
                    continue

                print(f"[DEBUG] Проверка токена: {token}")

                try:
                    profits = get_profits(token)
                except Exception as e:
                    msg = f"❗️Ошибка в get_profits({token}): {e}"
                    print(msg)
                    send_telegram(msg)
                    continue

                debug_lines = [
                    f"[DEBUG] {token} на {dex}: {round(profit, 2)}%" if profit is not None else f"[DEBUG] {token} на {dex}: нет данных"
                    for dex, profit in profits.items()
                ]
                debug_message = "\n".join(debug_lines)
                if debug_message:
                    print(debug_message)

                if not profits:
                    continue

                max_platform = max(profits, key=profits.get)
                max_profit = profits[max_platform]

                try:
                    _, volatility = get_volume_volatility(ROUTERS[max_platform]["router_address"], token)
                except Exception as e:
                    msg = f"❗️Ошибка в get_volume_volatility({token}): {e}"
                    print(msg)
                    send_telegram(msg)
                    volatility = 0.01

                base_threshold = 1.0
                volatility_factor = volatility * 8
                adaptive_threshold = round(base_threshold + volatility_factor, 2)

                print(
                    f"[DEBUG] {token} → {max_platform}: "
                    f"profit={round(max_profit,2)}%, "
                    f"adaptive_threshold={adaptive_threshold}%"
                )

                if max_profit >= adaptive_threshold:
                    last_sent = notified.get(token, now - datetime.timedelta(minutes=10))
                    if (now - last_sent).total_seconds() < 300:
                        continue

                    volume, _ = get_volume_volatility(ROUTERS[max_platform]["router_address"], token)

                    timing = 4
                    delay_notice = 3

                    start_time_dt = now + datetime.timedelta(minutes=delay_notice)
                    end_time_dt = start_time_dt + datetime.timedelta(minutes=timing)
                    start_time = start_time_dt.strftime("%H:%M")
                    end_time = end_time_dt.strftime("%H:%M")

                    url = build_url(max_platform, token)

                    msg = (
                        f"📉USDT→{token}→USDT📈\n"
                        f"PLATFORM: {max_platform}\n"
                        f"TIMING: {timing} MIN⌛️\n"
                        f"START TIME: {start_time}\n"
                        f"SELL TIME: {end_time}\n"
                        f"ESTIMATED PROFIT: {round(max_profit,2)}% 💸\n"
                        f"ADAPTIVE THRESHOLD: {adaptive_threshold}%\n"
                        f"VOLUME (events): {volume}\n"
                        f"VOLATILITY: {volatility:.4f}\n"
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

                    save_to_csv({
                        "datetime": now.isoformat(),
                        "token": token,
                        "platform": max_platform,
                        "profit_percent": max_profit,
                        "volume": volume,
                        "volatility": volatility
                    })

                elif max_profit >= adaptive_threshold * 0.8:
                    send_telegram(
                        f"⚠️ Почти сигнал по {token} ({max_platform})\n"
                        f"Прибыль: {round(max_profit,2)}% (порог {adaptive_threshold}%)"
                    )

            # Проверка завершения сделок
            to_remove = []
            for token, info in trade_records.items():
                elapsed = (now - info["start"]).total_seconds()
                if elapsed >= 60 * 4:
                    real_profit = get_profit_on_dex(ROUTERS[info["platform"]]["router_address"], token)
                    if real_profit is not None:
                        msg = (
                            f"✅ Результат сделки по {token} на {info['platform']}:\n"
                            f"Предсказанная прибыль: {round(info['profit_estimated'], 2)}%\n"
                            f"Реальная прибыль: {round(real_profit, 2)}%\n"
                            f"Время сделки: {info['start_time']} – {info['end_time']}\n"
                            f"Объём (events): {info['volume']}\n"
                            f"Волатильность: {info['volatility']:.4f}\n"
                            f"Ссылка: {info['url']}"
                        )
                    else:
                        msg = (
                            f"⚠️ Сделка по {token} на {info['platform']} завершилась, но реальная прибыль не определена.\n"
                            f"Предсказанная: {round(info['profit_estimated'], 2)}%\n"
                            f"Время сделки: {info['start_time']} – {info['end_time']}\n"
                            f"Ссылка: {info['url']}"
                        )
                    send_telegram(msg)
                    to_remove.append(token)

            for token in to_remove:
                trade_records.pop(token, None)

            time.sleep(5)

        except Exception as e:
            err = f"❗️Ошибка в main_loop: {e}"
            print(err)
            send_telegram(err)

def start_background_loop():
    print("[DEBUG] 🔁 Вызов start_background_loop()")
    threading.Thread(target=main_loop, daemon=True).start()
    
