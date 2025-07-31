import os, time, datetime, requests, pandas as pd
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

TOKENS = {
    "USDT": {"symbol": "USDT", "decimals": 6, "sushi": "0xc2132D05D31c914a87C6611C10748AaCbA6cD43E", "quick": "0xc2132D05D31c914a87C6611C10748AaCbA6cD43E"},
    "DAI": {"symbol": "DAI", "decimals": 18, "sushi": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063", "quick": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"},
    "USDC": {"symbol": "USDC", "decimals": 6, "sushi": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "quick": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"},
    "FRAX": {"symbol": "FRAX", "decimals": 18, "sushi": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89", "quick": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"},
    "wstETH": {"symbol": "wstETH", "decimals": 18, "sushi": "0x7f39c581f595b53c5cb19bcd5f5cf9b136097b5a", "quick": "0x7f39c581f595b53c5cb19bcd5f5cf9b136097b5a"},
    "BET": {"symbol": "BET", "decimals": 18, "sushi": "0x3183a3f59e18beb3214be625e4eb2a49ac03df06", "quick": "0x3183a3f59e18beb3214be625e4eb2a49ac03df06"},
    "tBTC": {"symbol": "tBTC", "decimals": 18, "sushi": "0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede", "quick": "0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede"},
    "EMT": {"symbol": "EMT", "decimals": 18, "sushi": "0x1e3a602906a749c6c07127dd3f2d97accb3fda3a", "quick": "0x1e3a602906a749c6c07127dd3f2d97accb3fda3a"},
    "GMT": {"symbol": "GMT", "decimals": 18, "sushi": "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419", "quick": "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"}
}

ROUTERS = {
    "SushiSwap": {
        "router": web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory": web3.to_checksum_address("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}",
        "platform_key": "sushi"
    },
    "Quickswap": {
        "router": web3.to_checksum_address("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": web3.to_checksum_address("0x5757371414417b8c6caad45baef941abc7d3ab32"),
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
            a, b = path[i], path[i+1]
            pair = factory.functions.getPair(a, b).call()
            if pair == "0x0000000000000000000000000000000000000000":
                return False
        return True
    except Exception as e:
        print(f"[check_pair] {e}")
        return False

def calculate_profit(router_addr, factory_addr, token_symbol, platform):
    try:
        platform_key = ROUTERS[platform]["platform_key"]
        token = TOKENS[token_symbol][platform_key]
        usdt = TOKENS["USDT"][platform_key]
        bridge = TOKENS["USDC"][platform_key]  # Bridge token

        token = Web3.to_checksum_address(token)
        usdt = Web3.to_checksum_address(usdt)
        bridge = Web3.to_checksum_address(bridge)

        paths = [[usdt, token, usdt], [usdt, bridge, token, usdt]]
        contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)
        amount_in = 10 ** TOKENS["USDT"]["decimals"]

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
            except:
                continue
        print(f"[DEBUG] Нет пары USDT↔{token_symbol} (даже через мост) на {platform}")
        return None
    except Exception as e:
        print(f"[DEBUG] Ошибка check_profit для {token_symbol} на {platform}: {e}")
        return None

def build_url(platform, token_symbol):
    key = ROUTERS[platform]["platform_key"]
    if not key:
        return ""
    template = ROUTERS[platform]["url"]
    usdt = TOKENS["USDT"][key]
    token = TOKENS[token_symbol][key]
    return template.format(usdt, token)

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
        if last_hb is None or (now - last_hb).total_seconds() >= 30 * 60:
            send_telegram(f"🗢 Bot alive: {now.strftime('%Y-%m-%d %H:%M:%S')}")
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
                    send_telegram(f"❌ Could not fetch actual for {info['token']} on {info['platform']}")
                tracked.pop(key)
        time.sleep(10)

if __name__ == "__main__":
    main()
