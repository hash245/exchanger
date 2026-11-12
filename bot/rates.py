"""Курсы с CoinGecko с кэшем, чтобы не упираться в лимиты API."""
import time
import aiohttp

from config import CURRENCIES, FEE_PERCENT, FEE_RUB_PERCENT, FIAT

_CACHE: dict[str, float] = {}
_CACHE_TS = 0.0
CACHE_SECONDS = 60


async def get_usd_prices() -> dict[str, float]:
    """Цены в USD для всех валют, включая RUB (цена 1 рубля в долларах)."""
    global _CACHE, _CACHE_TS
    if _CACHE and time.time() - _CACHE_TS < CACHE_SECONDS:
        return _CACHE

    ids = ",".join(v[0] for v in CURRENCIES.values() if v[0])
    url = "https://api.coingecko.com/api/v3/simple/price"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"ids": ids, "vs_currencies": "usd,rub"},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            r.raise_for_status()
            data = await r.json()

    prices = {}
    for ticker, (cg_id, *_rest) in CURRENCIES.items():
        if cg_id and cg_id in data and "usd" in data[cg_id]:
            prices[ticker] = float(data[cg_id]["usd"])
    # Курс рубля берём через USDT: сколько долларов стоит 1 рубль
    usdt = data.get("tether", {})
    if usdt.get("usd") and usdt.get("rub"):
        prices["RUB"] = float(usdt["usd"]) / float(usdt["rub"])
    if prices:
        _CACHE, _CACHE_TS = prices, time.time()
    return prices or _CACHE


def fee_for(frm: str) -> float:
    return FEE_RUB_PERCENT if frm in FIAT else FEE_PERCENT


async def get_rate(frm: str, to: str) -> float:
    """Сколько `to` клиент получит за 1 `frm` с учётом комиссии."""
    p = await get_usd_prices()
    market = p[frm] / p[to]
    return market * (1 - fee_for(frm) / 100)
