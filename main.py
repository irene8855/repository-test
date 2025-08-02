import os
import time
import datetime
import pytz
import requests
from dotenv import load_dotenv
from web3 import Web3

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"
WEB3_WS = os.getenv("WEB3_WS")

web3 = Web3(Web3.WebsocketProvider(WEB3_WS))

# Временная зона
LONDON_TZ = pytz.timezone("Europe/London")
ROUTE_CHECK_INTERVAL_HOURS = 3

# ABI
GET_AMOUNTS_OUT_ABI = [{
    "name": "getAmountsOut",
    "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
    "inputs": [
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "address[]", "name": "path", "type": "address[]"}
    ],
    "stateMutability": "view",
    "type": "function"
}]

GET_PAIR_ABI = [{
    "name": "getPair",
    "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
    "inputs": [
        {"internalType": "address", "name": "tokenA", "type": "address"},
        {"internalType": "address", "name": "tokenB", "type": "address"}
    ],
    "stateMutability": "view",
    "type": "function"
}]

# Хелпер для адресов
def checksum(addr): return Web3.toChecksumAddress(addr)

# Токены
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

DECIMALS = {
    "USDC": 6
}

ROUTERS = {
    "QuickSwap": {
        "router": checksum("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"),
        "factory": checksum("0x5757371414417b8c6caad45baef941abc7d3ab32"),
        "url": "https://quickswap.exchange/#/swap?inputCurrency={}&outputCurrency={}"
    }
}

# Telegram
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except Exception as e:
        print(f"[telegram] error {e}")

# Проверка маршрутов
def check_pair(factory_addr, path):
    try:
        factory = web3.eth.contract(address=factory_addr, abi=GET_PAIR_ABI)
        for i in range(len(path) - 1):
            pair = factory.functions.getPair(path[i], path[i + 1]).call()
            if pair.lower() == "0x0000000000000000000000000000000000000000":
                return False
        return True
    except:
        return False

# Построение маршрутов
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

# Расчет прибыли
def calculate_profit(router_addr, factory_addr, token_symbol, platform):
    try:
        base = TOKENS["USDC"]
        amount_in = 10 ** DECIMALS["USDC"]
        contract = web3.eth.contract(address=router_addr, abi=GET_AMOUNTS_OUT_ABI)
        all_routes = build_all_routes(token_symbol)

        for route in all_routes:
            path = [TOKENS[s] for s in route] + [base]
            if not check_pair(factory_addr, path): continue
            try:
                res = contract.functions.getAmountsOut(amount_in, path).call()
                amt = res[-1]
                if amt <= 0: continue
                profit = (amt / amount_in - 1) * 100
                return profit
            except Exception as e:
                if DEBUG_MODE:
                    send_telegram(f"[ERROR] calculate_profit({token_symbol}): {str(e)}")
        return None
    except Exception as e:
        if DEBUG_MODE:
            send_telegram(f"[ERROR] calculate_profit({token_symbol}): {str(e)}")
        return None

# Построение URL
def build_url(platform, token_symbol):
    base = TOKENS["USDC"]
    tok = TOKENS[token_symbol]
    return ROUTERS[platform]["url"].format(base, tok)

# Время
def get_local_time():
    return datetime.datetime.now(LONDON_TZ)

# Обновление валидных токенов
valid_tokens = {}

def update_valid_tokens():
    global valid_tokens
    updated = {}
    if DEBUG_MODE:
        send_telegram("🔍 Режим проверки пар запущен")

    for token in TOKENS:
        if token == "USDC": continue
        for platform, info in ROUTERS.items():
            routes = build_all_routes(token)
            valid = sum(1 for route in routes if check_pair(info["factory"], [TOKENS[s] for s in route] + [TOKENS["USDC"]]))
            if DEBUG_MODE:
                send_telegram(f"✔️ {token} on {platform}: {valid}/{len(routes)} маршрутов валидны")
            if valid > 0:
                updated.setdefault(platform, set()).add(token)
                if platform not in valid_tokens or token not in valid_tokens[platform]:
                    send_telegram(f"🆕 {token} стал валиден на {platform}")
    valid_tokens = updated

    if DEBUG_MODE:
        send_telegram("✅ Проверка пар завершена")

# Основной цикл
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
                tracked[key] = {
                    "start": now,
                    "profit": profit,
                    "url": url,
                    "token": token,
                    "platform": platform
                }

        for key, info in list(tracked.items()):
            if (now - info["start"]).total_seconds() >= trade_dur:
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

# Запуск
if __name__ == "__main__":
    main()
    
