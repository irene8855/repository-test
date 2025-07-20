import os, asyncio, aiohttp, json
from datetime import datetime, timedelta
from collections import deque
from telegram import Bot
import pytz
import traceback
from web3 import Web3
from eth_abi import decode_abi

# Настройки
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))
POLYGON_RPC = os.getenv("POLYGON_RPC")

CHECK_SEC = 15
LEAD_WINDOW = 2
VOLATILITY_WINDOW = 5
TREND_WINDOW = 3

PREDICT_THRESH = 1.2
CONFIRM_THRESH = 2.0
CONFIDENCE_THRESH = 1.5

LONDON = pytz.timezone("Europe/London")
web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

# Токены
TOKENS = {
    "BET": "0x47da42124a67ef2d2fcea8f53c937b83e9f58fce",
    "LDO": "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "SAND": "0xbbba073c31bf03b8acf7c28ef0738decf3695683",
    "GMT": "0xe3c408bd53c31c085a1746af401a4042954ff740",
    "FRAX": "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89",
    "LINK": "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39",
    "SUSHI": "0x0b3f868e0be5597d5db7feb59e1cadbb0fdda50a",
    "wstETH": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
    "AAVE": "0xd6df932a45c0f255f85145f286ea0b292b21c90b"
}

UNI_POOLS = {
    "LDO": "0xd4ca396007c5d043fae4d14f95b9ed581055264d",
    "SAND": "0x49aa71c4f44c2d60c285346071cf0413deec1877",
    "FRAX": "0x43e59f7ddbe2c2ad8e51c29112ee8e473b31f4f3",
    "LINK": "0xa3f558aeb1f5f60c36f6ee62bfb9a1dbb5fc7c53",
    "AAVE": "0xe0c4cf8c7a2ec3edfaf57e32b8ffdc0dd4d5c77c"
}

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"

bot = Bot(TG_TOKEN)
history = {s: deque(maxlen=600) for s in TOKENS}
entries = {}
sem = asyncio.Semaphore(10)

def ts(dt=None): return (dt or datetime.now(LONDON)).strftime("%H:%M")

def log(msg: str):
    with open("logs.txt", "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")

async def send(msg): 
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    log(msg.replace("\n", " | "))

def get_reserves(pool_address: str):
    try:
        pool_address = Web3.to_checksum_address(pool_address)
        abi = [{"inputs":[],"name":"getReserves","outputs":[
            {"internalType":"uint112","name":"_reserve0","type":"uint112"},
            {"internalType":"uint112","name":"_reserve1","type":"uint112"},
            {"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],
            "stateMutability":"view","type":"function"}]
        contract = web3.eth.contract(address=pool_address, abi=abi)
        reserves = contract.functions.getReserves().call()
        return reserves
    except Exception as e:
        log(f"Error get_reserves {pool_address}: {e}")
        return None

async def price_dex(sess, addr):
    try:
        js = await (await sess.get(DEX_URL + addr, timeout=10)).json()
        pools = js.get("pairs") or []
        best = max(pools, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        return float(best["priceUsd"]), best["dexId"].capitalize(), best.get("url", "")
    except: return None

async def best_price(sess, sym, addr):
    dex = await price_dex(sess, addr)
    uni_price = None
    if sym in UNI_POOLS:
        reserves = get_reserves(UNI_POOLS[sym])
        if reserves:
            r0, r1, _ = reserves
            if r1 != 0:
                uni_price = r0 / r1
    if dex and dex[0] and uni_price:
        return max([(dex[0], dex[1], dex[2]), (uni_price, "Uniswap", "")], key=lambda x: x[0])
    elif dex and dex[0]:
        return dex
    elif uni_price:
        return uni_price, "Uniswap", ""
    else:
        log(f"{sym} price data incomplete: uni={uni_price}, dex={dex}")
        return None

def check_volatility(prices):
    try:
        return max(prices) / min(prices) - 1 if len(prices) >= 2 else 0
    except: return 0

def check_trend(prices):
    return prices[-1] > prices[0] if len(prices) >= 2 else False

async def monitor(sess, sym, addr):
    async with sem:
        try:
            res = await best_price(sess, sym, addr)
            if not res: return
            price, source, url = res

            now = datetime.now(LONDON)
            history[sym].append((now, price))

            lead = [p for t, p in history[sym] if now - t <= timedelta(minutes=LEAD_WINDOW)]
            vol_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=VOLATILITY_WINDOW)]
            trend_window = [p for t, p in history[sym] if now - t <= timedelta(minutes=TREND_WINDOW)]

            if sym in entries:
                entry_time, _ = entries[sym]
                if now >= entry_time and entries[sym][1] is None:
                    entries[sym] = (entry_time, price)
                    await send(f"🚀 *ENTRY ALERT*\n{sym} → USDT\n💰 Цена входа: {price:.4f}\n📡 Источник: {source}\n🔗 [Купить]({url})\n🕒 {ts(now)}")

            if len(lead) >= 3 and all(p is not None for p in lead):
                min_lead = min(lead)
                speed = (price / min_lead - 1) * 100
                volatility = check_volatility(vol_window)
                confidence = speed / volatility if volatility > 0 else 0
                proj = speed * (3 / LEAD_WINDOW)
                entry = now + timedelta(minutes=2)
                exit_ = entry + timedelta(minutes=3)

                if (
                    speed >= PREDICT_THRESH and proj >= CONFIRM_THRESH and sym not in entries and
                    check_trend(trend_window) and confidence >= CONFIDENCE_THRESH
                ):
                    entries[sym] = (entry, None)
                    await send(f"🔮 *PREDICTIVE ALERT*\n💡 _Ожидается рост_\n{sym} → USDT\n⏱ Вход: {ts(entry)} | Выход: {ts(exit_)}\n📈 Прогноз: +{proj:.2f}%\n📡 Источник: {source}\n🔗 [Купить]({url})\n🕒 {ts(now)}")

            if sym in entries:
                entry_time, entry_price = entries[sym]
                if entry_price and now >= entry_time + timedelta(minutes=3):
                    growth = (price / entry_price - 1) * 100
                    await send(f"✅ *CONFIRMED ALERT*\n📊 _Сделка завершена_\n{sym} → USDT\n📈 Результат: {'+' if growth >= 0 else ''}{growth:.2f}% за 3м\n📡 Источник: {source}\n🔗 [Купить]({url})\n🕒 {ts(now)}")
                    del entries[sym]

        except Exception as e:
            log(f"[MONITOR ERROR] {sym}: {e}")
            traceback.print_exc()

async def main():
    await send("✅ Crypto Arbitrage Bot запущен. Мониторинг цен и арбитражных возможностей начался.")
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                await asyncio.gather(*(monitor(sess, sym, addr) for sym, addr in TOKENS.items()))
            except Exception as e:
                log(f"[MAIN LOOP ERROR] {e}")
                traceback.print_exc()
            await asyncio.sleep(CHECK_SEC)

if __name__ == "__main__":
    asyncio.run(main())
