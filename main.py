import os
import time
import datetime
import requests
import pandas as pd
from web3 import Web3
from dotenv import load_dotenv
import pytz

load_dotenv("secrets.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LONDON_TZ = pytz.timezone("Europe/London")

RPC_LIST = [
    os.getenv("POLYGON_RPC"),
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor.publicnode.com",
    "https://1rpc.io/matic"
]

def get_working_web3():
    for rpc in RPC_LIST:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc))
            if w3.is_connected():
                print(f"[RPC CONNECTED] {rpc}")
                return w3
        except:
            continue
    raise Exception("No working RPC")

web3 = get_working_web3()

GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
GET_PAIR_ABI = '[{"constant":true,"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"pair","type":"address"}],"payable":false,"stateMutability":"view","type":"function"}]'

def checksum(addr): return Web3.to_checksum_address(addr)

TOKENS = {
    "USDT": checksum("0xc2132D05D31C914a87C6611C10748AaCbA6cD43E"),
    "USDC": checksum("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "DAI":  checksum("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"),
    "FRAX": checksum("0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89"),
    "SAND": checksum("0xbbba073C31bF03b8ACf7c28EF0738DeCF3695683"),
    "AAVE": checksum("0xD6DF932A45C0f255f85145f286eA0B292B21C90B"),
    "LDO":  checksum("0xC3C7d422809852031b44ab29eec9f1eff2a58756"),
    "LINK": checksum("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"),
    "POL":  checksum("0xE256Cf79a8f3bFbE427A0c57a6B5a278eC2acDC1"),
    "WPOL": checksum("0xf62f05d5De64AbD38eDd17A8fCfBF8336fB9f2c2"),
}

DECIMALS = {
    "USDT": 6,
    "USDC": 6,
    "DAI": 18,
    "FRAX": 18,
    "SAND": 18,
    "AAVE": 18,
    "LDO": 18,
    "LINK": 18,
    "POL": 18,
    "WPOL": 18,
}

BASE = "USDC"  # базовый токен для маршрутов

ROUTERS = {
    "SushiSwap": {
        "router": checksum("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory": checksum("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "key": "SushiSwap"
    },
    "Quickswap": {
        "router": checksum("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": checksum("0x5757371414417b8c6caad45baef941abc7d3ab32"),
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}",
        "key": "Quickswap"
    }
}

def send_telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[telegram] error {e}")

def check_pair(factory_addr, path):
    try:
        factory = web3.eth.contract(address=factory_addr, abi=GET_PAIR_ABI)
        for i in range(len(path) - 1):
            a = checksum(path[i])
            b = checksum(path[i+1])
            pair = factory.functions.getPair(a, b).call()
            print(f"[PAIR DEBUG] getPair({a}, {b}) = {pair}")
            if pair.lower() == "0x0000000000000000000000000000000000000000":
                return False
        return True
    except Exception as e:
        print(f"[check_pair] {e}")
        return False

def build_trade_path(input_symbol, output_symbol):
    in_tok = TOKENS[input_symbol]
    out_tok = TOKENS[output_symbol]
    if input_symbol == "USDT" and output_symbol == BASE:
        return [in_tok, TOKENS["USDC"]]
    if input_symbol == BASE and output_symbol == "USDT":
        return [in_tok, TOKENS["USDT"]]
    if input_symbol == "USDT":
        return [in_tok, TOKENS["USDC"], out_tok]
    if output_symbol == "USDT":
        return [in_tok, TOKENS["USDC"], TOKENS["USDT"]]
    return [in_tok, out_tok]

def calculate_profit(router_addr, factory_addr, token_symbol, platform):
    try:
        base = TOKENS[BASE]
        token = TOKENS[token_symbol]
        bridges = [TOKENS[x] for x in ["USDC", "DAI", "FRAX", "POL", "WPOL"]]

        contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10 ** DECIMALS[BASE]

        paths = [[base, token, base]] + [[base, b, token, base] for b in bridges]
        for path in paths:
            if not check_pair(factory_addr, path):
                continue
            try:
                res = contract.functions.getAmountsOut(amount_in, path).call()
                out = res[-1]
                if out == 0:
                    continue
                profit = (out / amount_in - 1) * 100
                print(f"[PROFIT] {token_symbol} via {platform}: {profit:.2f}%")
                return profit
            except Exception as e:
                print(f"[ERROR] getAmountsOut for {path}: {e}")
                continue

        print(f"[DEBUG] Нет пары {BASE}↔{token_symbol} на {platform}")
        return None
    except Exception as e:
        print(f"[calculate_profit] {e}")
        return None

def build_url(platform, token_symbol):
    base = TOKENS[BASE]
    tok = TOKENS[token_symbol]
    return ROUTERS[platform]["url"].format(base, tok)

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def main():
    print("💡 Bot started")
    send_telegram("🤖 Bot launched")
    tracked = {}
    min_profit = 0.1
    trade_dur = 4 * 60
    last_hb = None

    while True:
        now = get_local_time()
        if last_hb is None or (now - last_hb).total_seconds() >= 30 * 60:
            send_telegram(f"🟢 Bot alive: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            last_hb = now

        for token in TOKENS:
            if token == BASE:
                continue
            for platform, info in ROUTERS.items():
                profit = calculate_profit(info["router"], info["factory"], token, platform)
                if profit is None or profit < min_profit:
                    continue
                key = (token, platform)
                if key in tracked and (now - tracked[key]["start"]).total_seconds() < trade_dur + 60:
                    continue
                url = build_url(platform, token)
                send_telegram(f"📉 {BASE}→{token}→{BASE}\nPlatform: {platform}\nEst. profit: {profit:.2f}% 💸\n{url}")
                tracked[key] = {"start": now, "profit": profit, "token": token, "platform": platform, "url": url}

        for key, inf in list(tracked.items()):
            if (get_local_time() - inf["start"]).total_seconds() >= trade_dur:
                rp = calculate_profit(ROUTERS[inf["platform"]]["router"], ROUTERS[inf["platform"]]["factory"], inf["token"], inf["platform"])
                if rp is not None:
                    send_telegram(f"✅ Done {inf['token']} on {inf['platform']}\nPredicted: {inf['profit']:.2f}%\nActual: {rp:.2f}%\n{inf['url']}")
                else:
                    send_telegram(f"⚠️ Could not fetch actual for {inf['token']} on {inf['platform']}")
                tracked.pop(key)
        time.sleep(10)

if __name__ == "__main__":
    main()
    
