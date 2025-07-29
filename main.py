from web3 import Web3
import requests
import json
import pandas as pd

web3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

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

ROUTERS = {
    "QuickSwap": {
        "router_address": web3.to_checksum_address("0xa5E0829CaCED8fFdd4De3c43696c57F7D7A678ff")
    },
    "SushiSwap": {
        "router_address": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506")
    },
    "DFYN": {
        "router_address": web3.to_checksum_address("0xA102072A4C07F06EC3B4900FDC4C7B80b6c57429")
    },
    "Polycat": {
        "router_address": web3.to_checksum_address("0x7c5a0ce9267ed19b22f8cae653f198e3e8daf098")
    }
}

GET_AMOUNTS_OUT_ABI = json.loads('[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]')

def get_profit_on_dex(router_address, token_symbol):
    try:
        contract = web3.eth.contract(address=router_address, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10**6
        usdt = TOKENS["USDT"]
        token = TOKENS[token_symbol]
        wmatic = TOKENS["WMATIC"]

        paths = [
            [usdt, token, usdt],
            [usdt, token],
            [token, usdt],
            [usdt, wmatic, token, usdt],
            [usdt, token, wmatic, usdt],
            [usdt, wmatic, token],
            [token, wmatic, usdt]
        ]

        for path in paths:
            try:
                print(f"[DEBUG] ➡️ Проверка маршрута: {[web3.to_checksum_address(addr) for addr in path]}")
                result = contract.functions.getAmountsOut(amount_in, path).call()

                if result[-1] > 0:
                    profit_percent = (result[-1] / amount_in - 1) * 100
                    print(f"[DIAG] 📈 profit_percent по маршруту {path}: {profit_percent:.4f}%")

                    # Раскомментируй при желании отправлять сообщения в Telegram
                    # if profit_percent > 0.5:
                    #     send_telegram(f"🚨 Тестовый сигнал\nТокен: {token_symbol}\nПуть: {path}\nПрофит: {profit_percent:.2f}%")

                    if profit_percent > 0:
                        return profit_percent

            except Exception as e:
                print(f"[SKIP] ⛔ Маршрут не работает: {path} — {e}")
                continue

        print(f"[DIAG] ⚠️ Все маршруты не дали результата для {token_symbol}")
        return None

    except Exception as e:
        print(f"[ERROR] ❌ get_profit_on_dex() ошибка: {e}")
        return None

def get_profits(token_symbol):
    profits = {}
    print(f"[DIAG] 🔍 Старт get_profits() для {token_symbol}")
    for dex_name, dex_info in ROUTERS.items():
        print(f"[DIAG] Запрос через getAmountsOut: {dex_name}")
        profit = get_profit_on_dex(dex_info["router_address"], token_symbol)
        if profit is not None:
            print(f"[DIAG] ✅ Прибыль на {dex_name} для {token_symbol}: {round(profit, 2)}%")
            profits[dex_name] = profit
        else:
            print(f"[DIAG] ⚠️ Нет прибыли на {dex_name} для {token_symbol}")
    return profits

# Запуск проверки для всех токенов
for token in TOKENS.keys():
    print("\n" + "="*40)
    profits = get_profits(token)
    print(f"Результаты для {token}: {profits}")
    
