import os
from datetime import date
from decimal import Decimal

import httpx

FX_UPSTREAM_BASE = os.environ.get(
    "FX_UPSTREAM_BASE",
    "https://api.frankfurter.dev",
).rstrip("/")


async def fetch_rate(
    from_currency: str,
    to_currency: str,
    asked_date: date,
    *,
    _client: httpx.AsyncClient | None = None,
) -> tuple[Decimal, str]:
    """Fetch exchange rate from Frankfurter v1 API.

    Returns (rate, rate_date) where rate_date comes from upstream payload,
    which may differ from asked_date (e.g. weekends/holidays).

    _client is a private injection point for testing only; callers should
    never pass it in production code.
    """
    url = f"{FX_UPSTREAM_BASE}/v1/{asked_date}"
    params = {"base": from_currency, "symbols": to_currency}

    if _client is not None:
        response = await _client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

    rate = Decimal(str(data["rates"][to_currency]))
    rate_date: str = data["date"]

    return rate, rate_date
