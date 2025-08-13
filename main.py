# -*- coding: utf-8 -*-
"""
Arb Scanner (Polygon) — full deploy-ready version
- Direct DEX quoting (Uniswap V3 QuoterV2, UniswapV2/Sushi reserves)
- Realistic round-trip PnL (fees, gas in USD, safety margin)
- RSI filter via Dexscreener (best-effort, soft)
- 0x v2 permit2/price check with rate budgeting
- Telegram notifications: signals always, plus "skipped: reason"
- Ban list: 2m for No liquidity, 15m for other errors (429/5xx/timeouts)
"""

import os
import time
import math
import json
import datetime
import pytz
import requests
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# ========= ENV & SETTINGS =========

load_dotenv()

# Telegram
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# RPC / chain
POLYGON_RPC        = os.getenv("POLYGON_RPC", "")  # e.g. https://polygon-rpc.com or your provider
CHAIN_ID           = int(os.getenv("CHAIN_ID", "137"))
TZ_NAME            = os.getenv("TZ_NAME", "Europe/London")
LONDON_TZ          = pytz.timezone(TZ_NAME)

# Trading controls
REAL_TRADING       = os.getenv("REAL_TRADING", "false").lower() == "true"  # execution (not implemented here)
SEND_SKIPPED       = os.getenv("SEND_SKIPPED", "true").lower() == "true"   # send "пропущено" reasons
SELL_AMOUNT_USD    = float(os.getenv("SELL_AMOUNT_USD", "50"))             # base amount per scan in USD (USDT-6d)
MIN_PROFIT_PCT     = float(os.getenv("MIN_PROFIT_PCT", "1.0"))             # 1.0–1.2%
SAFETY_SLIPPAGE_BP = float(os.getenv("SAFETY_SLIPPAGE_BP", "20"))          # extra safety margin in basis points (20 = 0.20%)

# RSI
RSI_ENABLED        = os.getenv("RSI_ENABLED", "true").lower() == "true"
RSI_OVERBOUGHT     = float(os.getenv("RSI_OVERBOUGHT", "70"))

# 0x
USE_ZEROX          = os.getenv("USE_ZEROX", "true").lower() == "true"
ZEROX_API_KEY      = os.getenv("ZEROX_API_KEY", "")
ZEROX_SKIP_VALID   = os.getenv("ZEROX_SKIP_VALIDATION", "true").lower() == "true"  # if true: pass skipValidation+slippage params
ZEROX_MAX_CALLS    = int(os.getenv("ZEROX_MAX_CALLS_PER_15M", "16"))
ZEROX_PRICE_URL    = os.getenv("ZEROX_PRICE_URL", "https://api.0x.org/swap/permit2/price")
DEFAULT_SLIPPAGE   = float(os.getenv("SLIPPAGE_PERCENTAGE", "0.01"))  # 1% for 0x price checks

# Bans & timings
NO_LIQ_BAN_SEC     = int(os.getenv("NO_LIQ_BAN_SEC", "120"))    # 2 min
OTHER_BAN_SEC      = int(os.getenv("OTHER_BAN_SEC", "900"))     # 15 min
REPORT_INTERVAL    = int(os.getenv("REPORT_INTERVAL_SEC", "900"))  # 15 min
LOOP_SLEEP_SEC     = float(os.getenv("LOOP_SLEEP_SEC", "0.3"))

# Rate limiting (global)
MAX_REQ_PER_SEC    = float(os.getenv("MAX_REQUESTS_PER_SECOND", "5"))
REQUEST_INTERVAL   = 1.0 / MAX_REQ_PER_SEC

# Sanity filters
MAX_REALISTIC_PROFIT_PCT = float(os.getenv("MAX_REALISTIC_PROFIT_PCT", "10.0"))  # anything > 10% flagged anomalous
MIN_POOL_LIQ_USD   = float(os.getenv("MIN_POOL_LIQ_USD", "50000"))   # filter out tiny pools
MIN_VOL24_USD      = float(os.getenv("MIN_VOL24_USD", "10000"))      # min 24h volume

DEBUG_MODE         = os.getenv("DEBUG_MODE", "true").lower() == "true"

# ========= TOKENS / ADDRS / ABIs =========

TOKENS: Dict[str, str] = {
    "USDT":  "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    "USDC":  "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "DAI":   "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
    "FRAX":  "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
    "wstETH":"0x03b54A6e9a984069379fae1a4fC4dBAE93B3bCCD",
    "BET":   "0xbF7970D56a150cD0b60BD08388A4A75a27777777",
    "WPOL":  "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # Wrapped POL (ex-WMATIC)
    "tBTC":  "0x236aa50979d5f3de3bd1eeb40e81137f22ab794b",
    "SAND":  "0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683",
    "GMT":   "0x714DB550b574b3E927af3D93E26127D15721D4C2",
    "LINK":  "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",
    "EMT":   "0x708383ae0e80E75377d664E4D6344404dede119A",
    "AAVE":  "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
    "LDO":   "0xc3c7d422809852031b44ab29eec9f1eff2a58756",
    "POL":   "0x0000000000000000000000000000000000001010",  # native POL (formerly MATIC)
    "WETH":  "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "SUSHI": "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",
}

DECIMALS: Dict[str, int] = {
    "USDT": 6, "USDC": 6, "DAI": 18, "FRAX": 18, "wstETH": 18,
    "BET": 18, "WPOL": 18, "tBTC": 18, "SAND": 18, "GMT": 8,
    "LINK": 18, "EMT": 18, "AAVE": 18, "LDO": 18, "POL": 18,
    "WETH": 18, "SUSHI": 18
}

RSI_TOKENS = {"AAVE","LINK","EMT","LDO","SUSHI","GMT","SAND","tBTC","wstETH","WETH"}

# Uniswap V3 QuoterV2 (Polygon)
UNISWAP_V3_QUOTER = Web3.to_checksum_address("0x61fFE014bA17989E743c5F6cB21bF9697530B21e")
UNISWAP_V3_QUOTER_ABI = json.loads("""
[
  {
    "inputs": [
      {
        "components": [
          {"internalType":"address","name":"tokenIn","type":"address"},
          {"internalType":"address","name":"tokenOut","type":"address"},
          {"internalType":"uint24","name":"fee","type":"uint24"},
          {"internalType":"address","name":"recipient","type":"address"},
          {"internalType":"uint256","name":"amountIn","type":"uint256"},
          {"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}
        ],
        "internalType":"struct IQuoterV2.QuoteExactInputSingleParams",
        "name":"params","type":"tuple"
      }
    ],
    "name":"quoteExactInputSingle",
    "outputs":[
      {"internalType":"uint256","name":"amountOut","type":"uint256"},
      {"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},
      {"internalType":"uint32","name":"initializedTicksCrossed","type":"uint32"},
      {"internalType":"uint256","name":"gasEstimate","type":"uint256"}
    ],
    "stateMutability":"nonpayable",
    "type":"function"
  }
]
""")

# UniswapV2/Sushi Pair ABI (getReserves, token0, token1)
V2_PAIR_ABI = json.loads("""
[
  {"inputs":[],"name":"getReserves","outputs":[
    {"internalType":"uint112","name":"_reserve0","type":"uint112"},
    {"internalType":"uint112","name":"_reserve1","type":"uint112"},
    {"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],
    "stateMutability":"view","type":"function"
  },
  {"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"token1","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
]
""")

# ========= GLOBAL STATE =========

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC)) if POLYGON_RPC else None

ban_list: Dict[Tuple[str,str], Dict] = {}
tracked_trades: Dict[Tuple[str,str], float] = {}
last_report_time = 0.0
last_request_time = 0.0

# 0x token bucket (15m)
zerox_bucket = {
    "capacity": ZEROX_MAX_CALLS,
    "tokens": ZEROX_MAX_CALLS,
    "refill_interval": 900.0,
    "last_refill": time.time()
}

# profiler counters (per cycle)
@dataclass
class CycleStats:
    total_checked: int = 0
    successful_trades: int = 0
    skipped_lowprofit: List[Tuple[str,str]] = None
    skipped_misc: List[Tuple[str,str]] = None
    rsi_skipped: List[Tuple[str,str]] = None
    ds_skipped: List[Tuple[str,str]] = None

    def __post_init__(self):
        self.skipped_lowprofit = []
        self.skipped_misc = []
        self.rsi_skipped = []
        self.ds_skipped = []

# ========= UTILS =========

def now_local():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(LONDON_TZ)

def rate_limit_sleep():
    global last_request_time
    elapsed = time.time() - last_request_time
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    last_request_time = time.time()

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if DEBUG_MODE:
            print("[Telegram disabled]", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text}
        )
    except Exception as e:
        if DEBUG_MODE:
            print("[Telegram] error:", e)

def ban_pair(key: Tuple[str,str], reason: str, short=False):
    duration = NO_LIQ_BAN_SEC if short else OTHER_BAN_SEC
    ban_list[key] = {"time": time.time(), "reason": reason, "duration": duration}
    if DEBUG_MODE:
        print(f"[BAN] {key} - {reason} ({duration}s)")

def clean_bans():
    now = time.time()
    to_del = [k for k,v in ban_list.items() if now - v["time"] > v["duration"]]
    for k in to_del:
        ban_list.pop(k, None)

def fmt_rsi(rsi: Optional[float]) -> str:
    return f"{rsi:.2f}" if isinstance(rsi, (int,float)) else "N/A"

def bps_to_pct(bp: float) -> float:
    return bp / 100.0

def refill_zerox_bucket():
    now = time.time()
    if now - zerox_bucket["last_refill"] >= zerox_bucket["refill_interval"]:
        zerox_bucket["tokens"] = zerox_bucket["capacity"]
        zerox_bucket["last_refill"] = now

def try_consume_zerox():
    refill_zerox_bucket()
    if zerox_bucket["tokens"] > 0:
        zerox_bucket["tokens"] -= 1
        return True
    return False

# ========= DEXSCREENER / RSI =========

def fetch_dexscreener(token_addr: str) -> Optional[dict]:
    try:
        rate_limit_sleep()
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        if DEBUG_MODE:
            print("[Dexscreener] exception:", e)
        return None

def pick_best_pair(ds_json: dict) -> Optional[dict]:
    pairs = ds_json.get("pairs", []) if ds_json else []
    if not pairs:
        return None
    # sort by liquidity USD desc
    pairs = [p for p in pairs if p.get("liquidity", {}).get("usd")]
    if not pairs:
        return None
    pairs.sort(key=lambda p: float(p["liquidity"]["usd"]), reverse=True)
    return pairs[0]

def ds_price_usd_of_token(ds_json: dict, want_addr: str) -> Optional[float]:
    best = pick_best_pair(ds_json)
    if not best:
        return None
    # token price is in "priceUsd"
    price = best.get("priceUsd")
    try:
        return float(price) if price else None
    except:
        return None

def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def fetch_rsi(ds_json: dict) -> Optional[float]:
    # Dexscreener "candles" не всегда присутствуют. Пробуем 1h candles если есть.
    pairs = ds_json.get("pairs", []) if ds_json else []
    if not pairs:
        return None
    candles = pairs[0].get("candles") or []
    prices = []
    for c in candles:
        try:
            prices.append(float(c.get("close")))
        except:
            pass
    if len(prices) < 15:
        return None
    return calculate_rsi(prices, 14)

# ========= DEX QUOTES =========

def v3_quote_exact_in(token_in: str, token_out: str, amount_in: int, fee: int = 3000) -> Optional[int]:
    """ Quote Uniswap V3 via QuoterV2; returns amountOut or None """
    if not w3:
        return None
    try:
        quoter = w3.eth.contract(address=UNISWAP_V3_QUOTER, abi=UNISWAP_V3_QUOTER_ABI)
        params = (Web3.to_checksum_address(token_in),
                  Web3.to_checksum_address(token_out),
                  fee,
                  "0x0000000000000000000000000000000000000000",
                  int(amount_in),
                  0)
        rate_limit_sleep()
        out, _, _, _ = quoter.functions.quoteExactInputSingle(params).call()
        return int(out)
    except ContractLogicError:
        return None
    except Exception as e:
        if DEBUG_MODE:
            print("[V3 quote] error:", e)
        return None

def v2_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int = 30) -> int:
    # x*y=k with fee: amountOut = amountIn*(1-fee) * reserveOut / (reserveIn + amountIn*(1-fee))
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amount_in_with_fee = amount_in * (10000 - fee_bps) // 10000
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in + amount_in_with_fee
    return int(numerator // denominator)

def v2_reserves(pair_addr: str) -> Optional[Tuple[int,int,str,str]]:
    """ returns reserves aligned to token0/token1, along with token0, token1 """
    if not w3:
        return None
    try:
        pair = w3.eth.contract(address=Web3.to_checksum_address(pair_addr), abi=V2_PAIR_ABI)
        rate_limit_sleep()
        r = pair.functions.getReserves().call()
        t0 = pair.functions.token0().call()
        t1 = pair.functions.token1().call()
        return int(r[0]), int(r[1]), Web3.to_checksum_address(t0), Web3.to_checksum_address(t1)
    except Exception as e:
        if DEBUG_MODE:
            print("[V2 reserves] error:", e)
        return None

@dataclass
class LegQuote:
    dex: str           # "UniswapV3-3000" | "UniswapV2" | "Sushi"
    amount_out: int    # raw out
    fee_bps: int       # V3: 500/3000/10000; V2: 30 bps
    gas_estimate: int  # rough swap gas estimate

def best_entry_exit_quotes(base_addr: str, token_addr: str, sell_amount_raw: int, token_decimals: int) -> Tuple[Optional[LegQuote], Optional[LegQuote]]:
    """ Find best entry (base->token) and exit (token->base) across V3/V2/Sushi """
    candidates_in: List[LegQuote] = []
    candidates_out: List[LegQuote] = []

    # Uniswap V3 tiers
    for fee in [500, 3000, 10000]:
        out_v3 = v3_quote_exact_in(base_addr, token_addr, sell_amount_raw, fee)
        if out_v3 and out_v3 > 0:
            candidates_in.append(LegQuote(dex=f"UniswapV3-{fee}", amount_out=out_v3, fee_bps=fee, gas_estimate=220000))
        # exit side
        out_v3_back = v3_quote_exact_in(token_addr, base_addr, out_v3 if out_v3 else 0, fee) if out_v3 else None
        if out_v3_back and out_v3_back > 0:
            candidates_out.append(LegQuote(dex=f"UniswapV3-{fee}", amount_out=out_v3_back, fee_bps=fee, gas_estimate=220000))

    # Dexscreener V2-like pairs: try SUSHI/UNI if available
    ds = fetch_dexscreener(token_addr)
    if ds:
        pairs = ds.get("pairs", [])
        for p in pairs[:5]:  # check top 5 by liquidity
            dex_id = (p.get("dexId") or "").lower()
            pair_addr = p.get("pairAddress")
            if not pair_addr:
                continue
            if "sushi" in dex_id or "uniswap" in dex_id:
                rv = v2_reserves(pair_addr)
                if not rv: 
                    continue
                r0, r1, t0, t1 = rv
                # entry: base -> token
                try:
                    if Web3.to_checksum_address(base_addr) == t0 and Web3.to_checksum_address(token_addr) == t1:
                        out = v2_amount_out(sell_amount_raw, r0, r1, 30)
                        if out > 0:
                            candidates_in.append(LegQuote(dex="V2Like", amount_out=out, fee_bps=30, gas_estimate=150000))
                    elif Web3.to_checksum_address(base_addr) == t1 and Web3.to_checksum_address(token_addr) == t0:
                        out = v2_amount_out(sell_amount_raw, r1, r0, 30)
                        if out > 0:
                            candidates_in.append(LegQuote(dex="V2Like", amount_out=out, fee_bps=30, gas_estimate=150000))
                    # exit: token -> base (using out from best in, rough — we’ll recompute below after best in known)
                except Exception:
                    pass

    # pick best entry
    best_in = max(candidates_in, key=lambda c: c.amount_out) if candidates_in else None

    # for exit we need amount_in = amount_out from entry
    if best_in:
        # try V3 tiers for exit with amount_in = best_in.amount_out
        for fee in [500, 3000, 10000]:
            out_back = v3_quote_exact_in(token_addr, base_addr, best_in.amount_out, fee)
            if out_back and out_back > 0:
                candidates_out.append(LegQuote(dex=f"UniswapV3-{fee}", amount_out=out_back, fee_bps=fee, gas_estimate=220000))

        # try V2-like exit using reserves (same top pairs if present)
        if ds:
            pairs = ds.get("pairs", [])
            for p in pairs[:5]:
                dex_id = (p.get("dexId") or "").lower()
                pair_addr = p.get("pairAddress")
                if not pair_addr: 
                    continue
                if "sushi" in dex_id or "uniswap" in dex_id:
                    rv = v2_reserves(pair_addr)
                    if not rv:
                        continue
                    r0, r1, t0, t1 = rv
                    try:
                        if Web3.to_checksum_address(token_addr) == t0 and Web3.to_checksum_address(base_addr) == t1:
                            out = v2_amount_out(best_in.amount_out, r0, r1, 30)
                            if out > 0:
                                candidates_out.append(LegQuote(dex="V2Like", amount_out=out, fee_bps=30, gas_estimate=150000))
                        elif Web3.to_checksum_address(token_addr) == t1 and Web3.to_checksum_address(base_addr) == t0:
                            out = v2_amount_out(best_in.amount_out, r1, r0, 30)
                            if out > 0:
                                candidates_out.append(LegQuote(dex="V2Like", amount_out=out, fee_bps=30, gas_estimate=150000))
                    except Exception:
                        pass

    best_out = max(candidates_out, key=lambda c: c.amount_out) if candidates_out else None
    return best_in, best_out

# ========= GAS & USD PRICING =========

def polygon_gas_price_wei() -> Optional[int]:
    if not w3:
        return None
    try:
        rate_limit_sleep()
        return int(w3.eth.gas_price)
    except Exception as e:
        if DEBUG_MODE:
            print("[Gas price] error:", e)
        return None

def wpol_price_usd() -> Optional[float]:
    # Use Dexscreener WPOL (wrapped POL) price
    ds = fetch_dexscreener(TOKENS["WPOL"])
    return ds_price_usd_of_token(ds, TOKENS["WPOL"]) if ds else None

def gas_cost_usd(gas_units: int) -> Optional[float]:
    gp = polygon_gas_price_wei()
    px = wpol_price_usd()
    if gp is None or px is None:
        return None
    # 1 wei * gas_units = wei; convert to POL: /1e18, then to USD
    return (gp * gas_units / 1e18) * px

# ========= 0x CHECK (ECONOMICAL) =========

def zerox_price(sell_token: str, buy_token: str, sell_amount: int) -> Optional[dict]:
    if not USE_ZEROX:
        return None
    if not try_consume_zerox():
        return None
    params = {
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": str(sell_amount),
        "chainId": CHAIN_ID
    }
    if ZEROX_SKIP_VALID:
        params["skipValidation"] = "true"
        params["slippagePercentage"] = str(DEFAULT_SLIPPAGE)
        params["enableSlippageProtection"] = "true"

    headers = {"0x-version": "v2"}
    if ZEROX_API_KEY:
        headers["0x-api-key"] = ZEROX_API_KEY

    try:
        rate_limit_sleep()
        r = requests.get(ZEROX_PRICE_URL, params=params, headers=headers, timeout=12)
    except requests.exceptions.RequestException as e:
        return {"error": f"Request exception: {e}", "code": 0}

    if r.status_code == 200:
        try:
            data = r.json()
        except Exception:
            return {"error": "Invalid JSON", "code": 0}
        if data.get("liquidityAvailable") is False:
            return {"error": "No liquidity", "code": 404}
        return data
    elif r.status_code == 404:
        return {"error": "No liquidity", "code": 404}
    else:
        # capture body snippet
        snippet = r.text[:220].replace("\n"," ")
        return {"error": f"HTTP {r.status_code} - {snippet}", "code": r.status_code}

# ========= MAIN LOGIC =========

def run():
    global last_report_time
    send_telegram("🤖 Bot started.")
    base_symbol = "USDT"
    base_addr = TOKENS[base_symbol]
    base_dec = DECIMALS[base_symbol]

    while True:
        cycle_start = time.time()
        clean_bans()
        stats = CycleStats()

        # compute base sell amount raw
        sell_amount_raw = int(SELL_AMOUNT_USD * (10 ** base_dec))

        for sym, addr in TOKENS.items():
            if sym == base_symbol:
                continue
            stats.total_checked += 1
            key = (base_symbol, sym)

            # skip banned
            if key in ban_list:
                info = ban_list[key]
                left = int(info["duration"] - (time.time() - info["time"]))
                if left > 0:
                    continue
                else:
                    ban_list.pop(key, None)

            # cooldown after attempt/trade
            if time.time() - tracked_trades.get(key, 0) < OTHER_BAN_SEC:
                continue

            # Dexscreener prefilter
            ds_json = fetch_dexscreener(addr)
            if not ds_json:
                if SEND_SKIPPED:
                    reason = "Dexscreener failed"
                    stats.ds_skipped.append((sym, reason))
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: {reason}")
                continue

            best_pair = pick_best_pair(ds_json)
            if not best_pair:
                ban_pair(key, "No liquidity (ds)", short=True)
                if SEND_SKIPPED:
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: нет ликвидности")
                continue

            liq_usd = float(best_pair.get("liquidity", {}).get("usd") or 0.0)
            vol24 = float(best_pair.get("volume", {}).get("h24") or 0.0)
            if liq_usd < MIN_POOL_LIQ_USD or vol24 < MIN_VOL24_USD:
                reason = f"низкая ликвидность/объём (liq ${liq_usd:,.0f}, vol24 ${vol24:,.0f})"
                stats.skipped_misc.append((sym, reason))
                if SEND_SKIPPED:
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: {reason}")
                continue

            # RSI
            rsi = None
            if RSI_ENABLED and sym in RSI_TOKENS:
                rsi = fetch_rsi(ds_json)
                if isinstance(rsi, (int,float)) and rsi > RSI_OVERBOUGHT:
                    stats.rsi_skipped.append((sym, f"RSI={rsi:.2f}"))
                    if SEND_SKIPPED:
                        send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: RSI={rsi:.2f}")
                    continue

            # direct DEX quotes (entry+exit)
            entry, exitq = best_entry_exit_quotes(base_addr, addr, sell_amount_raw, DECIMALS[sym])
            if not entry or not exitq:
                ban_pair(key, "No route (dex)", short=True)
                if SEND_SKIPPED:
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: нет маршрута на DEX")
                continue

            gross_in_token = entry.amount_out     # token units
            gross_out_base = exitq.amount_out     # base units (USDT raw)

            # fees: already priced-in for quotes; но закладываем safety margin
            safety_pct = bps_to_pct(SAFETY_SLIPPAGE_BP) / 100.0  # convert bp->%->fraction
            # gas: rough double-swap gas
            total_gas_units = entry.gas_estimate + exitq.gas_estimate
            gas_usd = gas_cost_usd(total_gas_units)
            if gas_usd is None:
                # fallback: assume modest gas $0.05 on Polygon
                gas_usd = 0.05

            # convert base raw to USD (USDT ≈ 1$)
            sell_usd = SELL_AMOUNT_USD
            final_back_usd = (gross_out_base / (10 ** base_dec))  # ≈ USD
            # safety subtract
            final_back_usd *= (1 - safety_pct)

            profit_usd = final_back_usd - sell_usd - gas_usd
            profit_pct = (profit_usd / sell_usd) * 100.0

            # sanity
            if abs(profit_pct) > MAX_REALISTIC_PROFIT_PCT:
                reason = f"аномалия profit {profit_pct:.2f}%"
                stats.skipped_misc.append((sym, reason))
                if SEND_SKIPPED:
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: {reason}")
                continue

            if profit_pct < MIN_PROFIT_PCT:
                stats.skipped_lowprofit.append((sym, f"Profit {profit_pct:.2f}% < {MIN_PROFIT_PCT}%"))
                if SEND_SKIPPED:
                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: Profit {profit_pct:.2f}% < {MIN_PROFIT_PCT}%")
                continue

            # optional 0x cross-check (very economical)
            zerox_note = "—"
            if USE_ZEROX:
                z = zerox_price(base_addr, addr, sell_amount_raw)
                if isinstance(z, dict) and "error" in z:
                    code = z.get("code", 0)
                    err = z["error"]
                    if code in (404,):
                        ban_pair(key, "0x: No liquidity", short=True)
                        if SEND_SKIPPED:
                            send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: 0x нет ликвидности")
                        continue
                    elif code in (429, 500, 502, 503, 504):
                        ban_pair(key, f"0x error {code}", short=False)
                        if SEND_SKIPPED:
                            send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: 0x ошибка {code}")
                        continue
                    else:
                        zerox_note = f"0x warn: {err[:80]}"
                elif isinstance(z, dict) and "buyAmount" in z:
                    # round-trip compare: 0x buyAmount for entry only; light sanity
                    try:
                        z_buy = int(z["buyAmount"])
                        # if 0x differs > 2% from our entry tokenOut
                        if z_buy > 0:
                            diff = abs(z_buy - gross_in_token)/max(z_buy, gross_in_token)*100
                            if diff > 2.0:
                                reason = f"0x расходится {diff:.2f}%"
                                stats.skipped_misc.append((sym, reason))
                                if SEND_SKIPPED:
                                    send_telegram(f"⏭️ Пропущено {base_symbol}->{sym}: {reason}")
                                ban_pair(key, "0x mismatch", short=True)
                                continue
                            else:
                                zerox_note = f"0x ok (Δ~{diff:.2f}%)"
                    except Exception:
                        zerox_note = "0x parse err"

            # SIGNAL — always send
            time_start = now_local().strftime("%H:%M")
            timing_min = 3
            time_sell = (now_local() + datetime.timedelta(minutes=timing_min)).strftime("%H:%M")

            platforms = []
            platforms.append(entry.dex)
            if exitq.dex != entry.dex:
                platforms.append(exitq.dex)

            msg = (
                f"{base_symbol} -> {sym} -> {base_symbol} 📈\n"
                f"TIMING: {timing_min} MIN ⌛️\n"
                f"TIME FOR START: {time_start}\n"
                f"TIME FOR SELL: {time_sell}\n"
                f"PROFIT ESTIMATE: {profit_pct:.2f}%  (~${profit_usd:.2f}) 💸\n"
                f"RSI: {fmt_rsi(rsi)}\n"
                f"PLATFORMS: {', '.join(platforms)} 📊\n"
                f"Gas est: ${gas_usd:.3f} | Safety: {bps_to_pct(SAFETY_SLIPPAGE_BP):.3f}% | 0x: {zerox_note}\n"
                f"Liq: ${liq_usd:,.0f} | Vol24: ${vol24:,.0f}\n"
                f"https://app.uniswap.org/swap?chain=polygon"
            )
            send_telegram(msg)

            # mark attempt/cooldown
            stats.successful_trades += 1
            tracked_trades[key] = time.time()
            ban_pair(key, "Post-attempt cooldown", short=False)  # 15m

        # periodic report
        now_ts = time.time()
        if now_ts - last_report_time >= REPORT_INTERVAL:
            clean_bans()

            # ban details
            banned_lines = []
            for pair, info in ban_list.items():
                left = int(info["duration"] - (now_ts - info["time"]))
                if left < 0: left = 0
                banned_lines.append(f"  - {pair[0]} -> {pair[1]}: причина - {info['reason']}, осталось: {left}s")

            report = (
                "===== PROFILER REPORT =====\n"
                f"⏱ Время полного цикла: {time.time() - cycle_start:.2f} сек\n"
                f"🚫 Пар в бан-листе: {len(ban_list)}\n"
            )
            if banned_lines:
                report += "Бан-лист детали:\n" + "\n".join(banned_lines) + "\n"

            if stats.skipped_lowprofit:
                report += "💰 Пар с прибылью > {0}% (но не отправлены): {1}\n".format(MIN_PROFIT_PCT, len(stats.skipped_lowprofit))
                for sym, reason in stats.skipped_lowprofit[:16]:
                    report += f"   - {sym}: {reason}\n"
            else:
                report += "💰 Все пары с прибылью были отправлены.\n"

            if stats.ds_skipped:
                report += "🔎 Пропущенные (dexscreener/price issues):\n"
                for sym, reason in stats.ds_skipped[:16]:
                    report += f"   - {sym}: {reason}\n"

            if stats.rsi_skipped:
                report += "📉 Пропущенные по RSI:\n"
                for sym, reason in stats.rsi_skipped[:16]:
                    report += f"   - {sym}: {reason}\n"

            if stats.skipped_misc:
                report += "⚠️ Прочие пропуски:\n"
                for sym, reason in stats.skipped_misc[:16]:
                    report += f"   - {sym}: {reason}\n"

            report += f"✔️ Успешных сигналов за цикл: {stats.successful_trades}\n"
            report += f"🔍 Всего проверено пар: {stats.total_checked}\n"
            report += f"0x budget left: {zerox_bucket['tokens']}/{zerox_bucket['capacity']} (reset in ≤{int(zerox_bucket['refill_interval'] - (time.time()-zerox_bucket['last_refill']))}s)\n"
            report += "==========================="
            send_telegram(report)
            last_report_time = now_ts

        time.sleep(LOOP_SLEEP_SEC)

# ========= ENTRY =========

if __name__ == "__main__":
    try:
        if not POLYGON_RPC:
            print("❗ POLYGON_RPC is not set. Please set RPC endpoint in ENV.")
        run()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        send_telegram(f"❗ Bot crashed with exception: {e}")
        if DEBUG_MODE:
            raise
            
