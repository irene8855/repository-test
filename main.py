import os, time, datetime, requests
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

# ABIs
GET_AMOUNTS_OUT_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
GET_PAIR_ABI = '[{"constant":true,"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"pair","type":"address"}],"payable":false,"stateMutability":"view","type":"function"}]'

# Tokens
def checksum(addr):
    return Web3.to_checksum_address(addr)

TOKENS = {
    "USDT":   {"symbol": "USDT",   "decimals": 6,  "sushi": checksum("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"), "quick": checksum("0xc2132D05D31c914a87C6611C10748AaCbA6cD43E")},
    "USDC":   {"symbol": "USDC",   "decimals": 6,  "sushi": checksum("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), "quick": checksum("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")},
    "DAI":    {"symbol": "DAI",    "decimals": 18, "sushi": checksum("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"), "quick": checksum("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063")},
    "FRAX":   {"symbol": "FRAX",   "decimals": 18, "sushi": checksum("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"), "quick": checksum("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89")},
    "WMATIC": {"symbol": "WMATIC", "decimals": 18, "sushi": checksum("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"), "quick": checksum("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270")},
    "WETH":   {"symbol": "WETH",   "decimals": 18, "sushi": checksum("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"), "quick": checksum("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619")},
    "POL":    {"symbol": "POL",    "decimals": 18, "sushi": checksum("0x0000000000000000000000000000000000001010"), "quick": checksum("0x0000000000000000000000000000000000001010")},
    "SAND":   {"symbol": "SAND",   "decimals": 18, "sushi": checksum("0xbbba073c31bf03b8acf7c28ef0738decf3695683"), "quick": checksum("0xbbba073c31bf03b8acf7c28ef0738decf3695683")},
    "AAVE":   {"symbol": "AAVE",   "decimals": 18, "sushi": checksum("0xd6df932a45c0f255f85145f286ea0b292b21c90b"), "quick": checksum("0xd6df932a45c0f255f85145f286ea0b292b21c90b")},
    "LDO":    {"symbol": "LDO",    "decimals": 18, "sushi": checksum("0xc3c7d422809852031b44ab29eec9f1eff2a58756"), "quick": checksum("0xc3c7d422809852031b44ab29eec9f1eff2a58756")},
    "LINK":   {"symbol": "LINK",   "decimals": 18, "sushi": checksum("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"), "quick": checksum("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39")},
}

ROUTERS = {
    "SushiSwap": {
        "router": checksum("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory": checksum("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "platform_key": "sushi"
    },
    "Quickswap": {
        "router": checksum("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": checksum("0x5757371414417b8c6caad45baef941abc7d3ab32"),
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}",
        "platform_key": "quick"
    }
}

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[telegram] error {e}")

def check_pair(factory_addr, path):
    try:
        factory = web3.eth.contract(address=factory_addr, abi=GET_PAIR_ABI)
        for i in range(len(path)-1):
            a = Web3.to_checksum_address(path[i])
            b = Web3.to_checksum_address(path[i+1])
            pair = factory.functions.getPair(a, b).call()
            if pair == "0x0000000000000000000000000000000000000000":
                return False
        return True
    except Exception as e:
        print(f"[check_pair] {e}")
        return False

def calculate_profit(router_addr, factory_addr, token_symbol, platform):
    try:
        pk = ROUTERS[platform]["platform_key"]
        token = TOKENS[token_symbol][pk]
        usdt = TOKENS["USDT"][pk]

        bridges = [
            TOKENS["USDC"][pk],
            TOKENS["DAI"][pk],
            TOKENS["FRAX"][pk],
            TOKENS["WMATIC"][pk],
            TOKENS["WETH"][pk],
            TOKENS["POL"][pk]
        ]

        contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10 ** TOKENS["USDT"]["decimals"]

        paths = [[usdt, token, usdt]]
        for bridge in bridges:
            paths.append([usdt, bridge, token, usdt])

        for path in paths:
            if not check_pair(factory_addr, path):
                continue
            try:
                result = contract.functions.getAmountsOut(amount_in, path).call()
                amount_out = result[-1]
                if amount_out == 0:
                    continue
                profit = (amount_out / amount_in - 1) * 100
                print(f"[PROFIT] {token_symbol} via {platform}: {profit:.2f}%")
                return profit
            except Exception as e:
                print(f"[ERROR] getAmountsOut failed for {path}: {e}")
                continue

        print(f"[DEBUG] Нет пары USDT↔{token_symbol} (даже через мост) на {platform}")
        return None

    except Exception as e:
        print(f"[DEBUG] Ошибка calculate_profit для {token_symbol} на {platform}: {e}")
        return None

def build_url(platform, token_symbol):
    pk = ROUTERS[platform]["platform_key"]
    usdt = TOKENS["USDT"][pk]
    token = TOKENS[token_symbol][pk]
    return ROUTERS[platform]["url"].format(usdt, token)

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

def main():
    print("⚡ Bot started")
    send_telegram("🤖 Bot launched")
    tracked = {}
    min_profit = 0.1
    trade_dur = 4 * 60
    last_hb = None

    while True:
        now = get_local_time()
        if last_hb is None or (now - last_hb).total_seconds() >= 1800:
            send_telegram(f"📢 Bot alive: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            last_hb = now

        for token in TOKENS:
            if token == "USDT":
                continue
            for platform, info in ROUTERS.items():
                profit = calculate_profit(info["router"], info["factory"], token, platform)
                if profit is None or profit < min_profit:
                    continue
                key = (token, platform)
                if key in tracked and (now - tracked[key]["start"]).total_seconds() < trade_dur + 60:
                    continue
                url = build_url(platform, token)
                send_telegram(f"📉 USDT→{token}→USDT\nPlatform: {platform}\nEst. profit: {profit:.2f}% 💸\n{url}")
                tracked[key] = {
                    "start": now,
                    "profit": profit,
                    "token": token,
                    "platform": platform,
                    "url": url
                }

        for key, info in list(tracked.items()):
            if (get_local_time() - info["start"]).total_seconds() >= trade_dur:
                rp = calculate_profit(
                    ROUTERS[info["platform"]]["router"],
                    ROUTERS[info["platform"]]["factory"],
                    info["token"],
                    info["platform"]
                )
                if rp is not None:
                    send_telegram(f"✅ Done {info['token']} on {info['platform']}\nPredicted: {info['profit']:.2f}%\nActual: {rp:.2f}%\n{info['url']}")
                else:
                    send_telegram(f"⚠️ Could not fetch actual for {info['token']} on {info['platform']}")
                tracked.pop(key)
        time.sleep(10)

if __name__ == "__main__":
    main()
    
