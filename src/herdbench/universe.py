"""Fixed classification of the ``xyz`` builder-dex markets in the archive (94 markets: 93 classified below;
``xyz:PURRDAT``, a venue-native token, classifies as ``other``).

Classes come from FINRA's short-interest issue names (settlement 2026-08-14), which name every US-listed
security and its type, checked against each perp's price: a perp tracks the instrument it names only if
its mark matches that instrument's price (``xyz:GOLD`` trades near $4,500, so it is gold, not the NYSE
stock with ticker GOLD). Only US-listed common stocks enter the stock transfer panel; ADRs, ETFs and
preferred shares are excluded, as are non-US and unlisted names.
"""

from __future__ import annotations

US_STOCKS: tuple[str, ...] = (
    "AAPL",
    "AMAT",
    "AMD",
    "AMZN",
    "AVGO",
    "BB",
    "BE",
    "BIRD",
    "BOT",
    "BX",
    "CBRS",
    "COIN",
    "COST",
    "CRCL",
    "CRWV",
    "DELL",
    "DKNG",
    "EBAY",
    "GEV",
    "GME",
    "GOOGL",
    "HIMS",
    "HOOD",
    "IBM",
    "INTC",
    "LITE",
    "LLY",
    "META",
    "MRVL",
    "MSFT",
    "MSTR",
    "MU",
    "NBIS",
    "NFLX",
    "NOW",
    "NVDA",
    "ORCL",
    "PLTR",
    "QCOM",
    "QNT",
    "RIVN",
    "RKLB",
    "SHAZ",
    "SNDK",
    "SPCX",
    "TSLA",
    "USAR",
    "WDC",
    "ZM",
)
ETFS: tuple[str, ...] = ("DRAM", "EWJ", "EWT", "EWY", "EWZ", "KORU", "LYTE", "NCLD", "SMH", "SOXL", "URNM", "XLE")
ADRS: tuple[str, ...] = ("ARM", "ASML", "BABA", "NOK", "TSM")
PREFERRED: tuple[str, ...] = ("STRC",)
INDICES: tuple[str, ...] = ("JP225", "KR200", "SP500", "XYZ100")
COMMODITIES: tuple[str, ...] = ("BRENTOIL", "CL", "COPPER", "GOLD", "NATGAS", "PALLADIUM", "PLATINUM", "SILVER")
FX: tuple[str, ...] = ("EUR", "GBP", "JPY")
NON_US_OR_UNLISTED: tuple[str, ...] = (
    "CXMT",
    "GIGADEV",
    "HYUNDAI",
    "KIOXIA",
    "MINIMAX",
    "SKHX",
    "SKHY",
    "SMSN",
    "SOFTBANK",
    "UNITREE",
    "ZHIPU",
)

_CLASSES = {
    "us_stock": US_STOCKS,
    "etf": ETFS,
    "adr": ADRS,
    "preferred": PREFERRED,
    "index": INDICES,
    "commodity": COMMODITIES,
    "fx": FX,
    "non_us_or_unlisted": NON_US_OR_UNLISTED,
}
_LOOKUP = {ticker: cls for cls, tickers in _CLASSES.items() for ticker in tickers}


def classify(market: str) -> str:
    """Class of a market: ``crypto`` for the main dex, a class above for ``xyz``, ``other`` otherwise."""
    if ":" not in market:
        return "crypto"
    dex, ticker = market.split(":", 1)
    if dex != "xyz":
        return "other"
    return _LOOKUP.get(ticker, "other")


def stock_markets() -> list[str]:
    return [f"xyz:{t}" for t in US_STOCKS]
