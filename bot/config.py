import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "support")

# Комиссия обменника в процентах (закладывается в курс)
FEE_PERCENT = float(os.getenv("FEE_PERCENT", "1.5"))
# Сколько минут действует зафиксированный курс
QUOTE_TTL_MIN = int(os.getenv("QUOTE_TTL_MIN", "15"))
DB_PATH = os.getenv("DB_PATH", "exchanger.db")
# Адрес Mini App на GitHub Pages, например https://hash245.github.io/exchanger/
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# Поддерживаемые монеты: тикер -> (id на CoinGecko, сеть, мин. сумма, адрес приёма)
CURRENCIES = {
    "BTC":  ("bitcoin",      "Bitcoin",     0.0005, os.getenv("WALLET_BTC", "")),
    "ETH":  ("ethereum",     "ERC-20",      0.01,   os.getenv("WALLET_ETH", "")),
    "USDT": ("tether",       "TRC-20",      20,     os.getenv("WALLET_USDT", "")),
    "TON":  ("the-open-network", "TON",     5,      os.getenv("WALLET_TON", "")),
    "LTC":  ("litecoin",     "Litecoin",    0.2,    os.getenv("WALLET_LTC", "")),
    "TRX":  ("tron",         "TRC-20",      100,    os.getenv("WALLET_TRX", "")),
}

# Проверка формата адресов получения (те же правила, что в index.html)
ADDRESS_RE = {
    "BTC":  r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$",
    "ETH":  r"^0x[a-fA-F0-9]{40}$",
    "USDT": r"^T[1-9A-HJ-NP-Za-km-z]{33}$",
    "TON":  r"^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$",
    "LTC":  r"^(ltc1|[LM3])[a-zA-HJ-NP-Z0-9]{25,62}$",
    "TRX":  r"^T[1-9A-HJ-NP-Za-km-z]{33}$",
}
