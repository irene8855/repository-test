import os
import asyncio
import aiohttp
import json
from datetime import datetime
from telegram import Bot
from web3 import Web3
import pytz
import traceback

# --- Настройки из env ---
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
POLYGON_RPC = os.getenv("POLYGON_RPC")

# --- Параметры ---
CHECK_SEC = 15
SLIPPAGE_THRESHOLD = 0.005   # 0.5% минимальная разница для арбитража
GAS_LIMIT = 300_000
ESTIMATED_GAS_GWEI = 60  # примерная цена газа в gwei
ETH_USDT_PRICE = 1600    # Заглушка, можно расширить динамически получать

LONDON = pytz.timezone("Europe/London")

# --- Список токенов (токен -> адрес) ---
TOKENS = {
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740"
}

# --- Пулы Uniswap и SushiSwap для этих токенов (адреса пулов) ---
UNISWAP_POOLS = {
    "SUSHI": "0x3e2d3c1e052c481832c1082d7f6a3ceef24502f7",
    "wstETH": "0x817f7c0c764f74e6b0a67f1185c907c0eb6f39f3",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740"
}

SUSHISWAP_POOLS = {
    "SUSHI": "0x3e2d3c1e052c481832c1082d7f6a3ceef24502f7",
    "wstETH": "0x817f7c0c764f74e6b0a67f1185c907c0eb6f39f3",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740"
}

# --- ABI для вызова getReserves ---
PAIR_ABI = json.loads("""[
    {
      "constant":true,
      "inputs":[],
      "name":"getReserves",
      "outputs":[
        {"internalType":"uint112","name":"_reserve0","type":"uint112"},
        {"internalType":"uint112","name":"_reserve1","type":"uint112"},
        {"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}
      ],
      "payable":false,
      "stateMutability":"view",
      "type":"function"
    },
    {
      "constant":true,
      "inputs":[],
      "name":"token0",
      "outputs":[{"internalType":"address","name":"","type":"address"}],
      "payable":false,
      "stateMutability":"view",
      "type":"function"
    },
    {
      "constant":true,
      "inputs":[],
      "name":"token1",
      "outputs":[{"internalType":"address","name":"","type":"address"}],
      "payable":false,
      "stateMutability":"view",
      "type":"function"
    }
]""")

# --- Web3 подключение ---
w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

bot = Bot(TG_TOKEN)

def ts(dt=None):
    return (dt or datetime.now(LONDON)).strftime("%H:%M:%S")

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

def log(msg):
    print(f"{datetime.now().isoformat()} {msg}")

# Получить резервы и адреса токенов в пуле
def get_reserves(pool_addr):
    try:
        contract = w3.eth.contract(address=pool_addr, abi=PAIR_ABI)
        reserves = contract.functions.getReserves().call()
        token0 = contract.functions.token0().call()
        token1 = contract.functions.token1().call()
        return reserves, token0.lower(), token1.lower()
    except Exception as e:
        log(f"Error get_reserves {pool_addr}: {e}")
        return None, None, None

# Расчет цены токена из резервов: price = reserve_token1 / reserve_token0 (если token0 — искомый токен)
def calc_price(reserves, token0, token1, target_token_addr):
    r0, r1, _ = reserves
    target_token_addr = target_token_addr.lower()
    if token0 == target_token_addr:
        if r0 == 0:
            return None
        return r1 / r0
    elif token1 == target_token_addr:
        if r1 == 0:
            return None
        return r0 / r1
    return None

async def monitor_token(sym, token_addr):
    while True:
        try:
            uni_pool = UNISWAP_POOLS.get(sym)
            sushi_pool = SUSHISWAP_POOLS.get(sym)

            price_uni = None
            price_sushi = None

            if uni_pool:
                reserves, t0, t1 = get_reserves(uni_pool)
                if reserves:
                    price_uni = calc_price(reserves, t0, t1, token_addr)

            if sushi_pool:
                reserves, t0, t1 = get_reserves(sushi_pool)
                if reserves:
                    price_sushi = calc_price(reserves, t0, t1, token_addr)

            if price_uni and price_sushi:
                gas_cost_eth = ESTIMATED_GAS_GWEI * 1e-9 * GAS_LIMIT
                gas_cost_usdt = gas_cost_eth * ETH_USDT_PRICE

                diff = abs(price_uni - price_sushi)
                avg_price = (price_uni + price_sushi) / 2
                diff_perc = diff / avg_price

                if diff_perc > SLIPPAGE_THRESHOLD:
                    if price_uni < price_sushi:
                        buy_from = "Uniswap"
                        sell_to = "SushiSwap"
                        buy_price = price_uni
                        sell_price = price_sushi
                    else:
                        buy_from = "SushiSwap"
                        sell_to = "Uniswap"
                        buy_price = price_sushi
                        sell_price = price_uni

                    # Прибыль после учёта газа и слippage
                    profit_perc = (sell_price - buy_price) / buy_price - SLIPPAGE_THRESHOLD - (gas_cost_usdt / buy_price)

                    if profit_perc > 0:
                        now = datetime.now(LONDON)
                        msg = (
                            f"⚡️ *Arbitrage Opportunity*\n"
                            f"{sym} token\n"
                            f"Buy from: {buy_from} at {buy_price:.6f}\n"
                            f"Sell to: {sell_to} at {sell_price:.6f}\n"
                            f"Potential profit: +{profit_perc*100:.2f}% (net after fees)\n"
                            f"⏰ {ts(now)}"
                        )
                        await send(msg)
                    else:
                        log(f"{sym} no profitable arbitrage after fees")
                else:
                    log(f"{sym} price difference below threshold")

            else:
                log(f"{sym} price data incomplete: uni={price_uni}, sushi={price_sushi}")

            await asyncio.sleep(CHECK_SEC)

        except Exception as e:
            log(f"Error in monitor_token {sym}: {e}")
            traceback.print_exc()
            await asyncio.sleep(CHECK_SEC)

async def main():
    await send("✅ Crypto Arbitrage Bot запущен и подключен к Polygon RPC")
    tasks = []
    for sym, addr in TOKENS.items():
        tasks.append(asyncio.create_task(monitor_token(sym, addr)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
