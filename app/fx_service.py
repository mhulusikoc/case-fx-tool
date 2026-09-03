import os
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

FX_UPSTREAM_BASE = os.environ.get(
    "FX_UPSTREAM_BASE",
    "https://api.frankfurter.dev",
).rstrip("/")

# Earliest date covered by the ECB historical series.
ECB_START_DATE = date(1999, 1, 4)

# Hard timeout for every upstream request (seconds).
_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Domain exceptions — main.py maps these to HTTP responses.
# ---------------------------------------------------------------------------


class UpstreamTimeout(Exception):
    """Upstream did not respond within the configured timeout."""


class UpstreamError(Exception):
    """Network failure or upstream HTTP 5xx."""


class UnsupportedCurrency(Exception):
    """Currency code is syntactically valid but not supported by upstream."""


class InvalidUpstreamResponse(Exception):
    """Upstream replied but the payload is missing, malformed, or untrustworthy."""


# ---------------------------------------------------------------------------
# Service function
# ---------------------------------------------------------------------------


async def fetch_rate(
    from_currency: str,
    to_currency: str,
    asked_date: date,
    *,
    _client: httpx.AsyncClient | None = None,
) -> tuple[Decimal, str]:
    """Fetch exchange rate from Frankfurter v1 API.

    Returns (rate, rate_date) where rate_date is the actual trading date
    returned by upstream, which may be earlier than asked_date
    (e.g. weekends / public holidays).

    Raises:
        UpstreamTimeout: provider did not reply in time.
        UpstreamError: network failure or HTTP 5xx from upstream.
        UnsupportedCurrency: upstream does not recognise the currency pair.
        InvalidUpstreamResponse: payload is absent, malformed, or untrustworthy.

    _client is a private injection point for unit testing only.
    """
    url = f"{FX_UPSTREAM_BASE}/v1/{asked_date}"
    params = {"base": from_currency, "symbols": to_currency}

    # ---- HTTP request -------------------------------------------------------
    try:
        if _client is not None:
            response = await _client.get(url, params=params)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout() from exc
    except httpx.HTTPError as exc:
        raise UpstreamError() from exc

    # ---- HTTP status --------------------------------------------------------
    # 400/404/422: Frankfurter uses these for unknown/invalid currency symbols.
    if response.status_code in (400, 404, 422):
        raise UnsupportedCurrency()

    # 429, 401, 403 and other 4xx are provider-side problems (rate-limit,
    # auth, etc.) — not a currency issue.  Report as upstream unavailable.
    if 400 <= response.status_code < 500:
        raise UpstreamError()

    if response.status_code >= 500:
        raise UpstreamError()

    # ---- JSON parsing -------------------------------------------------------
    try:
        data = response.json()
    except Exception as exc:
        raise InvalidUpstreamResponse() from exc

    if not isinstance(data, dict):
        raise InvalidUpstreamResponse()

    # ---- Validate "date" field ----------------------------------------------
    raw_date = data.get("date")
    if not isinstance(raw_date, str):
        raise InvalidUpstreamResponse()
    try:
        rate_date_parsed = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise InvalidUpstreamResponse() from exc

    # rate_date must not be in the future relative to asked_date
    # (earlier is fine: weekends / holidays are shifted back)
    if rate_date_parsed > asked_date:
        raise InvalidUpstreamResponse()

    # ---- Validate "rates" field ---------------------------------------------
    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise InvalidUpstreamResponse()

    if to_currency not in rates:
        raise UnsupportedCurrency()

    # ---- Validate the rate value itself -------------------------------------
    try:
        rate = Decimal(str(rates[to_currency]))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidUpstreamResponse() from exc

    if not rate.is_finite() or rate <= 0:
        raise InvalidUpstreamResponse()

    return rate, raw_date
