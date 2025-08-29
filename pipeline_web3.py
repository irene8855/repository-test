# pipeline_web3.py
import os
from web3 import Web3
from web3.middleware import geth_poa_middleware

# RPC
ALCHEMY_RPC = os.getenv("ALCHEMY_POLYGON_RPC")
if not ALCHEMY_RPC:
    raise RuntimeError("ALCHEMY_POLYGON_RPC is not set")

w3 = Web3(Web3.HTTPProvider(ALCHEMY_RPC))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# QuickSwap v2 Router & Factory
QUICKSWAP_ROUTER = Web3.to_checksum_address("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff")
QUICKSWAP_FACTORY = Web3.to_checksum_address("0x5757371414417b8c6caad45baef941abc7d3ab32")

ROUTER_ABI = [{
    "name": "getAmountsOut",
    "type": "function",
    "stateMutability": "view",
    "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
    "outputs": [{"name": "amounts", "type": "uint256[]"}]
}]
FACTORY_ABI = [{
    "name": "getPair",
    "type": "function",
    "stateMutability": "view",
    "inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}],
    "outputs": [{"name": "pair", "type": "address"}]
}]
PAIR_ABI = [{
    "name": "getReserves",
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [
        {"name": "reserve0", "type": "uint112"},
        {"name": "reserve1", "type": "uint112"},
        {"name": "blockTimestampLast", "type": "uint32"}
    ]
}, {
    "name": "token0",
    "type": "function",
    "inputs": [],
    "outputs": [{"name": "token0", "type": "address"}]
}, {
    "name": "token1",
    "type": "function",
    "inputs": [],
    "outputs": [{"name": "token1", "type": "address"}]
}]

router = w3.eth.contract(address=QUICKSWAP_ROUTER, abi=ROUTER_ABI)
factory = w3.eth.contract(address=QUICKSWAP_FACTORY, abi=FACTORY_ABI)

# Адреса токенов (синхронизировано с Main.py)
TOKENS = {
    "USDT": Web3.to_checksum_address("0xc2132d05d31c914a87c6611c10748aeb04b58e8f"),
    "USDC": Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    "DAI":  Web3.to_checksum_address("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"),
    "FRAX": Web3.to_checksum_address("0x45c32fa6df82ead1e2ef74d17b76547eddfaff89"),
    "LINK": Web3.to_checksum_address("0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39"),
    "wstETH": Web3.to_checksum_address("0x7f39c581f595b53c5cb5bbf48fab85048e8c1b02"),
    "tBTC": Web3.to_checksum_address("0x236aa50979d5f3de3bd1eeb40e81137f22ab794b"),
    "SAND": Web3.to_checksum_address("0xbbba073c31bf03b8acf7c28ef0738decf3695683"),
    "GMT":  Web3.to_checksum_address("0xe3c408BD53c31C085a1746AF401A4042954ff740"),
    "BET":  Web3.to_checksum_address("0x2e28b9b74d6d99d4697e913b82b41ef1cac51c6c"),
    "WPOL": Web3.to_checksum_address("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"),  # WMATIC
    "POL":  Web3.to_checksum_address("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"),  # alias
}

# Порог ликвидности (в USD), можно задать через fly secrets или [env] в fly.toml
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "10000"))

def _norm_symbol(sym: str) -> str:
    s = sym.upper()
    if s in ("POL", "MATIC", "WMATIC", "WPOL"):
        return "WPOL"
    return s

def _check_liquidity(tokenA, tokenB):
    """Проверка ликвидности пары через getReserves"""
    pair_addr = factory.functions.getPair(tokenA, tokenB).call()
    if pair_addr == "0x0000000000000000000000000000000000000000":
        return 0

    pair = w3.eth.contract(address=pair_addr, abi=PAIR_ABI)
    reserves = pair.functions.getReserves().call()
    token0 = pair.functions.token0().call()
    token1 = pair.functions.token1().call()

    r0, r1 = reserves[0], reserves[1]

    # если в паре есть USDT или USDC — смотрим его резерв
    if token0 in (TOKENS["USDT"], TOKENS["USDC"]):
        return r0 / 1e6
    if token1 in (TOKENS["USDT"], TOKENS["USDC"]):
        return r1 / 1e6

    # если нет USDT/USDC — просто возвращаем min(reserve0,reserve1)
    return min(r0, r1)

def get_quote_web3(src_symbol: str, dst_symbol: str, amount_in_units: int):
    """Пробуем получить цену напрямую или через WPOL (WMATIC)."""
    src_symbol = _norm_symbol(src_symbol)
    dst_symbol = _norm_symbol(dst_symbol)

    if src_symbol not in TOKENS or dst_symbol not in TOKENS:
        raise ValueError(f"Web3 unsupported token: {src_symbol}->{dst_symbol}")

    # === Проверка ликвидности
    liq = _check_liquidity(TOKENS[src_symbol], TOKENS[dst_symbol])
    if liq < MIN_LIQ_USD:
        raise ValueError(f"Low liquidity: {liq:.2f} USD in {src_symbol}->{dst_symbol}")

    try:
        # Прямой маршрут
        path = [TOKENS[src_symbol], TOKENS[dst_symbol]]
        amounts = router.functions.getAmountsOut(int(amount_in_units), path).call()
        out_units = int(amounts[-1])
        return {"buyAmount": str(out_units), "protocols": [], "source": "Web3"}

    except Exception:
        # Через WPOL
        if src_symbol != "WPOL" and dst_symbol != "WPOL":
            liq = _check_liquidity(TOKENS[src_symbol], TOKENS["WPOL"])
            if liq < MIN_LIQ_USD:
                raise ValueError(f"Low liquidity via WPOL: {liq:.2f} USD")
            try:
                path = [TOKENS[src_symbol], TOKENS["WPOL"], TOKENS[dst_symbol]]
                amounts = router.functions.getAmountsOut(int(amount_in_units), path).call()
                out_units = int(amounts[-1])
                return {"buyAmount": str(out_units), "protocols": [], "source": "Web3"}
            except Exception as e:
                raise ValueError(f"Web3 no route for {src_symbol}->{dst_symbol}: {e}")
        else:
            raise ValueError(f"Web3 no direct pool for {src_symbol}->{dst_symbol}")

if __name__ == "__main__":
    print("Connected:", w3.is_connected())
    try:
        print("Block number:", w3.eth.block_number)
    except Exception as e:
        print("Error fetching block:", e)
