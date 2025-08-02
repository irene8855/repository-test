import os, time, datetime, requests, pandas as pd
from web3 import Web3
from dotenv import load_dotenv
import pytz

load_dotenv("secrets.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ROUTE_CHECK_INTERVAL_HOURS = int(os.getenv("ROUTE_CHECK_INTERVAL_HOURS", 3))
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"

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
    "USDT":  checksum("0xc2132D05D31C914a87C6611C10748AaCbA6cD43E"),
    "USDC":  checksum("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "DAI":   checksum("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"),
    "FRAX":  checksum("0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89"),
    "SAND":  checksum("0xbbba073C31bF03b8ACf7c28EF0738DeCF3695683"),
    "AAVE":  checksum("0xD6DF932A45C0f255f85145f286eA0B292B21C90B"),
    "LDO":   checksum("0xC3C7d422809852031b44ab29eec9f1eff2a58756"),
    "LINK":  checksum("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"),
    "POL":   checksum("0xE256Cf79a8f3bFbE427A0c57a6B5a278eC2acDC1"),
    "WPOL":  checksum("0xf62f05d5De64AbD38eDd17A8fCfBF8336fB9f2c2"),
    "wstETH":checksum("0x7f39c581f595b53c5cb5bbf5b5f27aa49a3a7e3d"),
    "BET":   checksum("0x3183a3f59e18beb3214be625e4eb2a49ac03df06"),
    "tBTC":  checksum("0x1c5db575e2fec81cbe6718df3b282e4ddbb2aede"),
    "EMT":   checksum("0x1e3a602906a749c6c07127dd3f2d97accb3fda3a"),
    "GMT":   checksum("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"),
}

DECIMALS = {sym: (6 if sym in ["USDT", "USDC"] else 18) for sym in TOKENS}

ROUTERS = {
    "SushiSwap": {
        "router": checksum("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
        "factory": checksum("0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
        "url": "https://www.sushi.com/swap?inputCurrency={}&outputCurrency={}"
    },
    "Quickswap": {
        "router": checksum("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": checksum("0x5757371414417b8c6caad45baef941abc7d3ab32"),
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}"
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
            b = checksum(path[i + 1])
            pair = factory.functions.getPair(a, b).call()
            if pair.lower() == "0x0000000000000000000000000000000000000000":
                return False
        return True
    except:
        return False

def build_all_routes(token_symbol):
    base_list = list(TOKENS.keys())
    routes = [[token_symbol]]
    for mid in base_list:
        if mid != token_symbol:
            routes.append([token_symbol, mid])
            for mid2 in base_list:
                if mid2 not in {token_symbol, mid}:
                    routes.append([token_symbol, mid, mid2])
    return routes

def calculate_profit(router_addr, factory_addr, token_symbol, platform):
    try:
        base = TOKENS["USDC"]
        amount_in = 10 ** DECIMALS["USDC"]
        contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)

        all_routes = build_all_routes(token_symbol)
        for route in all_routes:
            path = [TOKENS[s] for s in route] + [base]
            if not check_pair(factory_addr, path):
                continue
            res = contract.functions.getAmountsOut(amount_in, path).call()
            amt = res[-1]
            if amt <= 0:
                continue
            profit = (amt / amount_in - 1) * 100
            if DEBUG_MODE:
                send_telegram(f"[DEBUG] {platform}: USDC→{'→'.join(route)}→USDC ≈ {profit:.2f}%")
            return profit
        return None
    except Exception as e:
        if DEBUG_MODE:
            send_telegram(f"[ERROR] calculate_profit({token_symbol}): {e}")
        return None

def build_url(platform, token_symbol):
    base = TOKENS["USDC"]
    tok = TOKENS[token_symbol]
    return ROUTERS[platform]["url"].format(base, tok)

def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

valid_tokens = {}

def update_valid_tokens():
    global valid_tokens
    updated = {}
    send_telegram("🔍 Режим проверки пар запущен")
    for token in TOKENS:
        if token == "USDC": continue
        for platform, info in ROUTERS.items():
            routes = build_all_routes(token)
            valid = sum(1 for route in routes if check_pair(info["factory"], [TOKENS[s] for s in route] + [TOKENS["USDC"]]))
            msg = f"✔️ {token} on {platform}: {valid}/{len(routes)} маршрутов валидны"
            if DEBUG_MODE:
                send_telegram(msg)
            if valid > 0:
                updated.setdefault(platform, set()).add(token)
                if platform not in valid_tokens or token not in valid_tokens[platform]:
                    send_telegram(f"🆕 {token} стал валиден на {platform}")
    valid_tokens = updated
    send_telegram("✅ Проверка пар завершена")

def main():
    print("🚀 Bot started")
    send_telegram("🤖 Bot запущен")

    min_profit = 0.1
    trade_dur = 4 * 60
    last_check = None
    last_hb = None
    tracked = {}

    while True:
        now = get_local_time()

        if last_check is None or (now - last_check).total_seconds() >= ROUTE_CHECK_INTERVAL_HOURS * 3600:
            update_valid_tokens()
            last_check = now

        if last_hb is None or (now - last_hb).total_seconds() >= 1800:
            send_telegram(f"🟢 Bot активен: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            last_hb = now

        for platform, info in ROUTERS.items():
            tokens = valid_tokens.get(platform, [])
            for token in tokens:
                profit = calculate_profit(info["router"], info["factory"], token, platform)
                if profit is None or profit < min_profit:
                    continue
                key = (token, platform)
                if key in tracked and (now - tracked[key]["start"]).total_seconds() < trade_dur + 60:
                    continue
                url = build_url(platform, token)
                send_telegram(f"📈 USDC→{token}→USDC\nPlatform: {platform}\nEst. profit: {profit:.2f}% 💸\n{url}")
                tracked[key] = {"start": now, "profit": profit, "url": url, "token": token, "platform": platform}

        for key, info in list(tracked.items()):
            if (now - info["start"]).total_seconds() >= trade_dur:
                rp = calculate_profit(ROUTERS[info["platform"]]["router"], ROUTERS[info["platform"]]["factory"], info["token"], info["platform"])
                if rp is not None:
                    send_telegram(f"✅ Done {info['token']} on {info['platform']}\nPredicted: {info['profit']:.2f}%\nActual: {rp:.2f}%\n{info['url']}")
                else:
                    send_telegram(f"⚠️ Could not fetch actual for {info['token']} on {info['platform']}")
                tracked.pop(key)

        time.sleep(10)

if __name__ == "__main__":
    main()
    
