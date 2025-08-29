# pipeline_web3.py
import os
from web3 import Web3
from web3.middleware import geth_poa_middleware

# 1) RPC из секретов (ALCHEMY_POLYGON_RPC)
ALCHEMY_RPC = os.getenv("ALCHEMY_POLYGON_RPC")
if not ALCHEMY_RPC:
    raise RuntimeError("ALCHEMY_POLYGON_RPC is not set")

w3 = Web3(Web3.HTTPProvider(ALCHEMY_RPC))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# 2) QuickSwap V2 Router (Polygon)
QUICKSWAP_ROUTER = Web3.to_checksum_address("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff")

# Минимальный ABI для getAmountsOut
ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"}
        ],
        "outputs": [
            {"name": "amounts", "type": "uint256[]"}
        ]
    }
]

# 3) Адреса и десятичные
TOKENS = {
    # те же адреса, что и в твоём Main.py
    "USDT": Web3.to_checksum_address("0xc2132d05d31c914a87c6611c10748aeb04b58e8f"),
    "WPOL": Web3.to_checksum_address("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"),  # WMATIC
    "POL":  Web3.to_checksum_address("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"),  # alias на WMATIC
}
DECIMALS = {"USDT": 6, "WPOL": 18, "POL": 18}

def _norm_symbol(sym: str) -> str:
    """Сводим POL/MATIC/WMATIC к одному обозначению для роутера."""
    s = sym.upper()
    if s in ("POL", "MATIC", "WMATIC", "WPOL"):
        return "WPOL"
    return s

def get_quote_web3(src_symbol: str, dst_symbol: str, amount_units: int):
    """
    Возвращает dict с ключом buyAmount (строкой), как у 1inch/Dexscreener-пути,
    либо выбрасывает ValueError для неподдерживаемых пар (чтобы это попало в отчёт).
    """
    src_symbol = _norm_symbol(src_symbol)
    dst_symbol = _norm_symbol(dst_symbol)

    # Минимальный демо-кейс: поддерживаем только USDT <-> WPOL
    supported = {("USDT", "WPOL"), ("WPOL", "USDT")}
    if (src_symbol, dst_symbol) not in supported:
        raise ValueError(f"Web3 unsupported pair in demo: {src_symbol}->{dst_symbol}")

    router = w3.eth.contract(address=QUICKSWAP_ROUTER, abi=ROUTER_ABI)

    path = [TOKENS[src_symbol], TOKENS[dst_symbol]]
    # amount_units уже в "вей" базового токена — бери как есть
    amounts = router.functions.getAmountsOut(int(amount_units), path).call()
    out_units = int(amounts[-1])

    # Возвращаем в привычном формате
    return {
        "buyAmount": str(out_units),
        "protocols": [],   # чтобы downstream код не ломался
        "source": "Web3"
    }

if __name__ == "__main__":
    print("Connected:", w3.is_connected())
    try:
        print("Block number:", w3.eth.block_number)
    except Exception as e:
        print("Error fetching block:", e)
