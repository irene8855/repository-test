import os
import time
import logging
from web3 import Web3
from dotenv import load_dotenv
import requests

# Загрузка переменных из .env
load_dotenv("secrets.env")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# RPC список (Alchemy + fallback)
RPC_LIST = [
    os.getenv("POLYGON_RPC"),
    "https://polygon-rpc.com",
    "https://rpc-mainnet.maticvigil.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor.publicnode.com",
    "https://1rpc.io/matic",
]

# ABI
GET_AMOUNTS_OUT_ABI = [{
    "name": "getAmountsOut",
    "outputs": [{"name": "", "type": "uint256[]"}],
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"},
    ],
    "stateMutability": "view",
    "type": "function",
}]

# Токены (можно расширить)
TOKENS = {
    "USDT": Web3.to_checksum_address("0x3813e82e6f7098b9583FC0F33a962D02018B6803"),
    "USDC": Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "DAI": Web3.to_checksum_address("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"),
    "SUSHI": Web3.to_checksum_address("0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a"),
    "LINK": Web3.to_checksum_address("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"),
    "SAND": Web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "BET": Web3.to_checksum_address("0xF491e7B69E4244ad4002BC14e878a34207E38c29"),
}

# Роутеры
ROUTERS = {
    "Uniswap": Web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
    "SushiSwap": Web3.to_checksum_address("0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"),
    "1inch": Web3.to_checksum_address("0x1111111254fb6c44bac0bed2854e76f90643097d"),
}

# Подключение к рабочему RPC
def get_working_web3():
    for rpc in RPC_LIST:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc))
            if w3.is_connected():
                logging.info(f"[RPC CONNECTED] {rpc}")
                return w3
            else:
                logging.warning(f"[RPC FAILED] {rpc}")
        except Exception as e:
            logging.error(f"[RPC ERROR] {rpc} - {e}")
    raise Exception("❌ No working RPC found")

# Telegram уведомление
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logging.info("✅ Telegram message sent")
        else:
            logging.warning("⚠️ Telegram failed")
    except Exception as e:
        logging.error(f"[Telegram Error] {e}")

# Расчет прибыли
def calculate_profit(router_address, token):
    global web3
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
        logging.error(f"[ERROR calculate_profit] {token} - {e}")
        try:
            web3 = get_working_web3()
        except:
            logging.critical("No RPCs available")
        return None

# Основной цикл
def main():
    logging.info("✅ Bot started")
    send_telegram("🤖 Bot started")

    while True:
        for platform, router in ROUTERS.items():
            for token in TOKENS:
                if token == "USDT":
                    continue
                profit = calculate_profit(router, token)
                if profit and profit > 0.5:  # порог
                    msg = f"💰 {platform} - {token}: {profit:.2f}%"
                    send_telegram(msg)
        time.sleep(10)

if __name__ == "__main__":
    web3 = get_working_web3()
    try:
        main()
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped by user")
