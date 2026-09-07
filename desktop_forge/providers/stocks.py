"""Stock quotes from Yahoo's chart endpoint.

This endpoint is free and needs no key, but it is unofficial and can change
without notice. That is the entire reason providers are a separate layer: if
it breaks, a replacement lands here and the extension does not change.
"""
from __future__ import annotations

from typing import Any

from .base import Provider, ProviderError, get_json

QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
MAX_SYMBOLS = 12


class StocksProvider(Provider):
    name = "stocks"

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        symbols = [s.strip().upper() for s in options.get("symbols") or [] if s.strip()]
        if not symbols:
            raise ProviderError("No symbols configured")

        quotes: list[dict[str, Any]] = []
        errors: list[str] = []

        # Deliberately one request per symbol against the per-symbol endpoint,
        # rather than the batch /v7/finance/quote endpoint, which now requires
        # a session crumb. A handful of small requests is a fair price for not
        # having to manage cookies.
        for symbol in symbols[:MAX_SYMBOLS]:
            try:
                quotes.append(self._quote(symbol))
            except ProviderError as exc:
                errors.append(f"{symbol}: {exc}")

        # A single bad ticker in a watchlist must not blank the whole widget --
        # only a total failure is worth raising.
        if not quotes:
            raise ProviderError(errors[0] if errors else "No quotes returned")

        return {"quotes": quotes, "errors": errors}

    def _quote(self, symbol: str) -> dict[str, Any]:
        payload = get_json(QUOTE_URL.format(symbol=symbol) + "?range=1d&interval=1d")
        chart = payload.get("chart") or {}
        results = chart.get("result") or []
        if not results:
            error = (chart.get("error") or {}).get("description") or "unknown symbol"
            raise ProviderError(error)

        meta = results[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")

        change = change_percent = None
        if price is not None and previous:
            change = round(price - previous, 4)
            change_percent = round(100.0 * change / previous, 2)

        return {
            "symbol": meta.get("symbol", symbol),
            "name": meta.get("shortName") or meta.get("longName") or symbol,
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_percent": change_percent,
            "currency": meta.get("currency", ""),
            "exchange": meta.get("exchangeName", ""),
            "market_state": meta.get("marketState", ""),
        }
