# pipeline_web3.py
import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()
print("DEBUG ALCHEMY_RPC:", os.getenv("ALCHEMY_POLYGON_RPC"))

# RPC через Alchemy (Polygon)
ALCHEMY_RPC = os.getenv("ALCHEMY_POLYGON_RPC")
w3 = Web3(Web3.HTTPProvider(ALCHEMY_RPC))
from web3.middleware import geth_poa_middleware
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# Пример: пул USDT–MATIC на QuickSwap (Polygon)
PAIR_ADDRESS = Web3.to_checksum_address("0x4d6b2b90fdb1c68b9b37c58d93d6309d63f03bc0")

PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

pair = w3.eth.contract(address=PAIR_ADDRESS, abi=PAIR_ABI)

def get_quote_web3(src_symbol, dst_symbol, amount_units):
    """
    Возвращает данные в формате, как get_quote_ds / get_quote_1inch
    Пока только для пула USDT–MATIC (пример).
    """
    try:
        if not w3.is_connected():
            return None

        reserves = pair.functions.getReserves().call()
        reserve0, reserve1 = reserves[0], reserves[1]

        # Упрощение: считаем, что token0 = USDT (6 знаков), token1 = MATIC (18 знаков)
        # В реальном коде нужно будет проверять порядок!
        amount_in = amount_units * 10**6  # USDT -> wei
        amount_out = (amount_in * reserve1) // (reserve0 + amount_in)

        price = amount_out / 1e18 / (amount_units)  # цена MATIC в USDT
        liquidity_usd = reserve0 / 1e6 + (reserve1 / 1e18 * price)

        return {
            "src_symbol": src_symbol,
            "dst_symbol": dst_symbol,
            "amount_in": amount_units,
            "amount_out": amount_out / 1e18,
            "price": price,
            "liquidity_usd": liquidity_usd,
            "source": "Web3",
        }

    except Exception as e:
        print("Web3 error:", e)
        return None

if __name__ == "__main__":
    print("Connected:", w3.is_connected())
    print("Block number:", w3.eth.block_number)
