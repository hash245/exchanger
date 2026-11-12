"""Курсы с CoinGecko с кэшем, чтобы не упираться в лимиты API."""
import time
import aiohttp

from config import CURRENCIES, FEE_PERCENT

_CACHE: dict[str, float] = {}
_CACHE_TS = 0.0
CACHE_SECONDS = 60


async def get_usd_prices() -> dict[str, float]:
    global _CACHE, _CACHE_TS
    if _CACHE and time.time() - _CACHE_TS < CACHE_SECONDS:
        return _CACHE

    ids = ",".join(v[0] for v in CURRENCIES.values())
    url = "https://api.coingecko.com/api/v3/simple/price"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"ids": ids, "vs_currencies": "usd"},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            r.raise_for_status()
            data = await r.json()

    prices = {}
    for ticker, (cg_id, *_rest) in CURRENCIES.items():
        if cg_id in data and "usd" in data[cg_id]:
            prices[ticker] = float(data[cg_id]["usd"])
    if prices:
        _CACHE, _CACHE_TS = prices, time.time()
    return prices or _CACHE


async def get_rate(frm: str, to: str) -> float:
    """Сколько `to` клиент получит за 1 `frm` с учётом комиссии."""
    p = await get_usd_prices()
    market = p[frm] / p[to]
    return market * (1 - FEE_PERCENT / 100)
