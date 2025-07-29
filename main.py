import time
from datetime import datetime, timedelta
from web3 import Web3
import json
import 
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, data=payload)

def check_profit():
    try:
        router = web3.eth.contract(address=ROUTER, abi=ABI)
        amount_in = 10 ** 6  # 1 USDT

        path = [USDT, FRAX, USDT]
        result = router.functions.getAmountsOut(amount_in, path).call()
        amount_out = result[-1]
        profit_percent = (amount_out / amount_in - 1) * 100

        print(f"[LOG] ➡️ Profit: {profit_percent:.2f}%")

        if profit_percent > 1.5:
            now = datetime.utcnow() + timedelta(hours=3)
            time_start = now.strftime("%H:%M")
            time_end = (now + timedelta(minutes=4)).strftime("%H:%M")

            message = (
                f"📉USDT->FRAX->USDT📈\n"
                f"TIMING: 4 MIN ⌛️\n"
                f"TIME FOR START: {time_start}\n"
                f"TIME FOR SELL: {time_end}\n"
                f"PROFIT: {profit_percent:.2f}% 💸\n"
                f"PLATFORMS:\n"
                f"1) https://trustwallet.com/ru\n"
                f"2) https://www.sushi.com\n"
                f"3) https://trustwallet.com/ru"
            )
            send_telegram(message)

    except Exception as e:
        print(f"[ERROR] {e}")

# 🔁 Цикл проверки каждые 2 минуты
while True:
    check_profit()
    time.sleep(120)
    
