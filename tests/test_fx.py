import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from fastapi.testclient import TestClient

from app.main import app
from app import fx_service
from app.fx_service import fetch_rate
from app.fx_service import _rate_cache_clear


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_rate_cache():
    """Wipe the in-memory rate cache before (and after) every test.

    This prevents cache hits from leaking between tests that use the
    real fetch_rate with a fake transport.
    """
    _rate_cache_clear()
    yield
    _rate_cache_clear()


# ---------------------------------------------------------------------------
# Shared expected error bodies
# ---------------------------------------------------------------------------

INVALID_AMOUNT_RESPONSE = {
    "error": "invalid_amount",
    "message": "Amount must be greater than zero and have at most two decimal places.",
}

INVALID_CURRENCY_RESPONSE = {
    "error": "invalid_currency",
    "message": "Currency codes must be three-letter ISO-style codes.",
}

SAME_CURRENCY_RESPONSE = {
    "error": "same_currency",
    "message": "Source and target currencies must be different.",
}

FUTURE_DATE_RESPONSE = {
    "error": "future_date",
    "message": "Exchange rates are not available for future dates.",
}

INVALID_REQUEST_RESPONSE = {
    "error": "invalid_request",
    "message": "The request parameters are invalid.",
}


def _mock_fetch_rate(rate: Decimal, rate_date: str):
    """Return a context manager that patches fetch_rate with a fixed return value."""
    return patch(
        "app.main.fetch_rate",
        new=AsyncMock(return_value=(rate, rate_date)),
    )


def test_valid_convert_request_returns_200() -> None:
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 250
    assert body["from"] == "EUR"
    assert body["to"] == "TRY"
    assert Decimal(str(body["rate"])) == Decimal("47.1234")
    assert Decimal(str(body["result"])) == Decimal("11780.85")
    assert body["rate_date"] == "2026-08-28"
    assert body["asked_date"] == "2026-08-28"
    assert body["source"] == "ECB via frankfurter.dev"


def test_zero_amount_returns_invalid_amount() -> None:
    response = client.get("/tools/convert?amount=0&from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_negative_amount_returns_invalid_amount() -> None:
    response = client.get("/tools/convert?amount=-10&from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_amount_with_more_than_two_decimal_places_returns_invalid_amount() -> None:
    response = client.get(
        "/tools/convert?amount=1.1234567890&from=EUR&to=TRY&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_oversized_amount_returns_invalid_amount() -> None:
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            f"/tools/convert?amount={'9' * 40}&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_amount",
        "message": "Amount is too large to convert safely.",
    }


def test_malformed_currency_returns_invalid_currency() -> None:
    response = client.get(
        "/tools/convert?amount=250&from=EURO&to=TRY&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == INVALID_CURRENCY_RESPONSE


def test_lowercase_currency_codes_pass_validation() -> None:
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=eur&to=try&date=2026-08-28"
        )

    assert response.status_code == 200


def test_same_currency_returns_same_currency_error() -> None:
    response = client.get(
        "/tools/convert?amount=250&from=eur&to=EUR&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == SAME_CURRENCY_RESPONSE


def test_future_date_returns_future_date_error() -> None:
    future_date = date.today() + timedelta(days=1)

    response = client.get(
        f"/tools/convert?amount=250&from=EUR&to=TRY&date={future_date}"
    )

    assert response.status_code == 400
    assert response.json() == FUTURE_DATE_RESPONSE


def test_missing_amount_returns_invalid_request() -> None:
    response = client.get("/tools/convert?from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_REQUEST_RESPONSE


def test_invalid_date_format_returns_invalid_request() -> None:
    response = client.get("/tools/convert?amount=250&from=EUR&to=TRY&date=2026/08/28")

    assert response.status_code == 400
    assert response.json() == INVALID_REQUEST_RESPONSE


# ---------------------------------------------------------------------------
# Upstream integration tests (network-free)
# ---------------------------------------------------------------------------


def test_upstream_rate_date_differs_from_asked_date() -> None:
    """When upstream returns a different date (e.g. weekend), asked_date and
    rate_date must both be preserved independently."""
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-30"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asked_date"] == "2026-08-30"
    assert body["rate_date"] == "2026-08-28"


def test_result_uses_full_upstream_precision() -> None:
    """result must be computed as amount * rate (full precision), then rounded
    to 2 decimal places — NOT rate-rounded-first."""
    # 250 * 47.1234 = 11780.85 exactly at 2dp
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 200
    assert Decimal(str(response.json()["result"])) == Decimal("11780.85")


# ---------------------------------------------------------------------------
# fetch_rate unit tests — real function, no mock.patch, no network
# ---------------------------------------------------------------------------

_FAKE_UPSTREAM_BODY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-08-28",
    "rates": {"TRY": 47.1234},
}


class _FakeFrankfurterTransport(httpx.AsyncBaseTransport):
    """Intercepts every request and returns a fixed JSON payload.

    Records the last request so tests can assert on path and query params.
    """

    def __init__(self, body: dict, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.last_request: httpx.Request | None = None
        self.request_count: int = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        self.request_count += 1
        return httpx.Response(
            status_code=self.status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self.body).encode(),
            request=request,
        )


class _RawTransport(httpx.AsyncBaseTransport):
    """Returns arbitrary raw bytes with a configurable status and content-type.

    Useful for simulating non-JSON upstream responses.
    """

    def __init__(
        self,
        content: bytes,
        status_code: int = 200,
        content_type: str = "text/html",
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.content_type = content_type

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=self.status_code,
            headers={"content-type": self.content_type},
            content=self.content,
            request=request,
        )


class _ErrorTransport(httpx.AsyncBaseTransport):
    """Raises a given httpx network-level exception on every request.

    exc_factory is a zero-arg callable that returns the exception to raise,
    so each request gets a fresh exception object.
    """

    def __init__(self, exc_factory) -> None:
        self._exc_factory = exc_factory

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._exc_factory()


def _svc_client(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """Return an AsyncClient wired to *transport* and the current upstream base."""
    return httpx.AsyncClient(
        base_url=fx_service.FX_UPSTREAM_BASE,
        transport=transport,
    )


_ASKED = date(2026, 8, 28)


@pytest.mark.anyio
async def test_fetch_rate_request_path_and_params() -> None:
    """fetch_rate must call /v1/<date> with base=FROM and symbols=TO."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=fx_service.FX_UPSTREAM_BASE,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    assert transport.last_request is not None
    assert transport.last_request.url.path == "/v1/2026-08-28"
    assert transport.last_request.url.params["base"] == "EUR"
    assert transport.last_request.url.params["symbols"] == "TRY"


@pytest.mark.anyio
async def test_fetch_rate_returns_decimal_and_date() -> None:
    """fetch_rate must parse the upstream JSON into Decimal rate and string date."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=fx_service.FX_UPSTREAM_BASE,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"


@pytest.mark.anyio
async def test_fetch_rate_uses_fx_upstream_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """FX_UPSTREAM_BASE env var must control the base URL used by fetch_rate."""
    custom_base = "https://custom.fx.example.com"
    monkeypatch.setattr(fx_service, "FX_UPSTREAM_BASE", custom_base)

    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=custom_base,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    # Verify the URL built inside fetch_rate used the patched base
    assert transport.last_request is not None
    assert custom_base in str(transport.last_request.url)
    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"


# ---------------------------------------------------------------------------
# Error-handling tests — endpoint level, no real network
# ---------------------------------------------------------------------------
# Helpers that make fetch_rate raise a specific exception.

from app.fx_service import (  # noqa: E402
    InvalidUpstreamResponse,
    UnsupportedCurrency,
    UpstreamError,
    UpstreamTimeout,
)


def _raise_fetch_rate(exc: Exception):
    """Patch fetch_rate so it raises *exc* unconditionally."""
    return patch(
        "app.main.fetch_rate",
        new=AsyncMock(side_effect=exc),
    )


# 1. Date before ECB series start -> 400 date_out_of_range (no upstream call)

def test_date_before_ecb_start_returns_date_out_of_range() -> None:
    """asked_date earlier than 1999-01-04 must be rejected before any upstream call."""
    response = client.get(
        "/tools/convert?amount=100&from=EUR&to=USD&date=1998-12-31"
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "date_out_of_range",
        "message": "Exchange rate data is unavailable for this date.",
    }


def test_ecb_start_date_itself_is_allowed() -> None:
    """1999-01-04 (ECB_START_DATE) must pass the range check and reach upstream."""
    with _mock_fetch_rate(Decimal("1.1865"), "1999-01-04"):
        response = client.get(
            "/tools/convert?amount=100&from=EUR&to=USD&date=1999-01-04"
        )

    assert response.status_code == 200


# 2. Timeout -> 504 upstream_timeout

def test_upstream_timeout_returns_504() -> None:
    with _raise_fetch_rate(UpstreamTimeout()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 504
    assert response.json() == {
        "error": "upstream_timeout",
        "message": "The exchange-rate provider did not respond in time.",
    }


# 3. Connection / network error -> 502 upstream_error

def test_upstream_connection_error_returns_502() -> None:
    with _raise_fetch_rate(UpstreamError()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": "upstream_error",
        "message": "The exchange-rate provider is currently unavailable.",
    }


# 4. Upstream HTTP 500 -> 502 upstream_error (same exception path as network error)

def test_upstream_5xx_returns_502() -> None:
    with _raise_fetch_rate(UpstreamError()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"


# 5. Non-JSON response -> 502 invalid_upstream_response

def test_non_json_upstream_returns_502() -> None:
    with _raise_fetch_rate(InvalidUpstreamResponse()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": "invalid_upstream_response",
        "message": "The exchange-rate provider returned an invalid response.",
    }


# 6. Missing "rates" or "date" fields -> 502 invalid_upstream_response

def test_missing_rates_field_returns_502() -> None:
    with _raise_fetch_rate(InvalidUpstreamResponse()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"


# 7. Zero or negative rate -> 502 invalid_upstream_response

def test_zero_rate_from_upstream_returns_502() -> None:
    with _raise_fetch_rate(InvalidUpstreamResponse()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"


# 8. Unsupported (but format-valid) currency -> 400 unsupported_currency

def test_unsupported_currency_returns_400() -> None:
    with _raise_fetch_rate(UnsupportedCurrency()):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=ZZZ&date=2026-08-28"
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": "unsupported_currency",
        "message": "The requested currency is not supported.",
    }


# 9. Weekend / holiday: upstream rate_date earlier than asked_date -> still 200

def test_weekend_asked_date_returns_200_with_earlier_rate_date() -> None:
    """Frankfurter shifts weekends back to Friday; this must remain a success."""
    # asked_date = Sunday 2026-08-30, upstream returns Friday 2026-08-28
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-30"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asked_date"] == "2026-08-30"
    assert body["rate_date"] == "2026-08-28"


# 10. rate_date > asked_date -> 502 invalid_upstream_response

def test_rate_date_in_future_relative_to_asked_date_returns_502() -> None:
    """Upstream must never return a rate_date that is after asked_date."""
    with _raise_fetch_rate(InvalidUpstreamResponse()):
        response = client.get(
            "/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"


# ---------------------------------------------------------------------------
# fetch_rate service-level tests — real fetch_rate, fake httpx transport
# No mock.patch. No real network.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_svc_http_500_raises_upstream_error() -> None:
    """HTTP 500 from upstream must raise UpstreamError."""
    transport = _FakeFrankfurterTransport({}, status_code=500)
    with pytest.raises(UpstreamError):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_connection_error_raises_upstream_error() -> None:
    """Network-level connection failure must raise UpstreamError."""
    transport = _ErrorTransport(lambda: httpx.ConnectError("refused"))
    with pytest.raises(UpstreamError):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_timeout_raises_upstream_timeout() -> None:
    """Read timeout must raise UpstreamTimeout, not UpstreamError."""
    transport = _ErrorTransport(lambda: httpx.ReadTimeout("timed out"))
    with pytest.raises(UpstreamTimeout):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_non_json_response_raises_invalid_upstream_response() -> None:
    """A non-JSON body (e.g. HTML error page) must raise InvalidUpstreamResponse."""
    transport = _RawTransport(b"<html>Service Unavailable</html>", status_code=200)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_missing_date_field_raises_invalid_upstream_response() -> None:
    """JSON without 'date' field must raise InvalidUpstreamResponse."""
    body = {"base": "EUR", "rates": {"TRY": 47.1234}}  # no "date"
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_missing_rates_field_raises_invalid_upstream_response() -> None:
    """JSON without 'rates' field must raise InvalidUpstreamResponse."""
    body = {"base": "EUR", "date": "2026-08-28"}  # no "rates"
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_target_currency_absent_from_rates_raises_unsupported_currency() -> None:
    """Rate for the requested target currency absent from rates dict → UnsupportedCurrency."""
    body = {"base": "EUR", "date": "2026-08-28", "rates": {"USD": 1.08}}  # TRY missing
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(UnsupportedCurrency):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_rate_zero_raises_invalid_upstream_response() -> None:
    """rate = 0 is economically impossible; must raise InvalidUpstreamResponse."""
    body = {"base": "EUR", "date": "2026-08-28", "rates": {"TRY": 0}}
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_rate_negative_raises_invalid_upstream_response() -> None:
    """Negative rate is invalid; must raise InvalidUpstreamResponse."""
    body = {"base": "EUR", "date": "2026-08-28", "rates": {"TRY": -1}}
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_rate_nan_raises_invalid_upstream_response() -> None:
    """'NaN' string rate cannot be converted to a finite Decimal."""
    body = {"base": "EUR", "date": "2026-08-28", "rates": {"TRY": "NaN"}}
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_rate_date_after_asked_date_raises_invalid_upstream_response() -> None:
    """upstream date later than asked_date is untrustworthy; raise InvalidUpstreamResponse."""
    # asked_date = 2026-08-28, upstream claims 2026-08-29
    body = {"base": "EUR", "date": "2026-08-29", "rates": {"TRY": 47.1234}}
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_rate_date_before_asked_date_is_valid() -> None:
    """upstream date earlier than asked_date (weekend/holiday) is perfectly valid."""
    # asked_date = Sunday 2026-08-30, upstream returns Friday 2026-08-28
    asked = date(2026, 8, 30)
    body = {"base": "EUR", "date": "2026-08-28", "rates": {"TRY": 47.1234}}
    transport = _FakeFrankfurterTransport(body)
    rate, rate_date = await fetch_rate("EUR", "TRY", asked, _client=_svc_client(transport))

    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"


@pytest.mark.anyio
async def test_svc_http_4xx_currency_codes_raise_unsupported_currency() -> None:
    """HTTP 400 and 404 from upstream must raise UnsupportedCurrency."""
    for status in (400, 404, 422):
        transport = _FakeFrankfurterTransport({}, status_code=status)
        with pytest.raises(UnsupportedCurrency):
            await fetch_rate("EUR", "ZZZ", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_http_429_raises_upstream_error_not_unsupported_currency() -> None:
    """HTTP 429 (rate-limit) must raise UpstreamError, never UnsupportedCurrency."""
    transport = _FakeFrankfurterTransport({}, status_code=429)
    with pytest.raises(UpstreamError):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


# ---------------------------------------------------------------------------
# Cache behaviour tests — real fetch_rate, fake transport, request_count
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_same_params_hits_upstream_only_once() -> None:
    """Two identical calls (from/to/date) must produce exactly one HTTP request."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    c = _svc_client(transport)

    rate1, date1 = await fetch_rate("EUR", "TRY", _ASKED, _client=c)
    rate2, date2 = await fetch_rate("EUR", "TRY", _ASKED, _client=c)

    assert transport.request_count == 1
    assert rate1 == rate2
    assert date1 == date2


@pytest.mark.anyio
async def test_cache_different_date_hits_upstream_twice() -> None:
    """Different asked_date means a different cache key → two upstream calls."""
    # Each call uses a body whose upstream date matches the asked_date.
    call_count = 0

    class _CountingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Return a body whose date echoes whatever date is in the URL path.
            path_date = str(request.url).split("/v1/")[1].split("?")[0]
            body = {"base": "EUR", "date": path_date, "rates": {"TRY": 47.12}}
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(body).encode(),
                request=request,
            )

    c = httpx.AsyncClient(base_url=fx_service.FX_UPSTREAM_BASE, transport=_CountingTransport())
    await fetch_rate("EUR", "TRY", date(2026, 8, 27), _client=c)
    await fetch_rate("EUR", "TRY", date(2026, 8, 28), _client=c)

    assert call_count == 2


@pytest.mark.anyio
async def test_cache_different_target_currency_hits_upstream_twice() -> None:
    """Different to_currency means a different cache key → two upstream calls."""
    body_usd = {"base": "EUR", "date": "2026-08-28", "rates": {"USD": 1.08}}
    transport_usd = _FakeFrankfurterTransport(body_usd)
    transport_try = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)

    await fetch_rate("EUR", "USD", _ASKED, _client=_svc_client(transport_usd))
    await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport_try))

    assert transport_usd.request_count == 1
    assert transport_try.request_count == 1


@pytest.mark.anyio
async def test_cache_preserves_upstream_rate_date_for_weekend() -> None:
    """Cache must store the real rate_date (e.g. Friday) for a weekend asked_date.

    Second call with the same Sunday asked_date must return the Friday rate_date
    from cache without hitting upstream again.
    """
    sunday = date(2026, 8, 30)
    friday_body = {"base": "EUR", "date": "2026-08-28", "rates": {"TRY": 47.1234}}
    transport = _FakeFrankfurterTransport(friday_body)
    c = _svc_client(transport)

    rate1, rd1 = await fetch_rate("EUR", "TRY", sunday, _client=c)
    rate2, rd2 = await fetch_rate("EUR", "TRY", sunday, _client=c)

    assert transport.request_count == 1     # second call came from cache
    assert rd1 == rd2 == "2026-08-28"       # Friday rate_date preserved
    assert rate1 == rate2 == Decimal("47.1234")


@pytest.mark.anyio
async def test_cache_error_is_not_cached() -> None:
    """A failed upstream call must not populate the cache.

    After an UpstreamError, the next call for the same params must retry upstream.
    """
    error_transport = _ErrorTransport(lambda: httpx.ConnectError("refused"))
    success_transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)

    # First call fails — must not be cached.
    with pytest.raises(UpstreamError):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(error_transport))

    # Second call with fresh transport should succeed and hit upstream.
    rate, rate_date = await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(success_transport))

    assert success_transport.request_count == 1
    assert rate == Decimal("47.1234")


@pytest.mark.anyio
async def test_cache_amount_is_not_part_of_cache_key() -> None:
    """amount is not a parameter of fetch_rate, so it never influences the cache key.

    Calling fetch_rate twice for the same from/to/date (regardless of what
    amount the caller will later multiply against the rate) must result in
    exactly one upstream request.
    """
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    c = _svc_client(transport)

    # Simulate what main.py does for amount=100 and amount=250 on the same pair/date.
    rate_for_100, _ = await fetch_rate("EUR", "TRY", _ASKED, _client=c)
    rate_for_250, _ = await fetch_rate("EUR", "TRY", _ASKED, _client=c)

    assert transport.request_count == 1          # only one upstream call
    assert rate_for_100 == rate_for_250          # same rate returned both times


# ---------------------------------------------------------------------------
# Base field validation tests — real fetch_rate, fake transport
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_svc_missing_base_raises_invalid_upstream_response() -> None:
    """Upstream payload without a 'base' field must raise InvalidUpstreamResponse."""
    body = {"date": "2026-08-28", "rates": {"TRY": 47.1234}}  # no "base"
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_wrong_base_raises_invalid_upstream_response() -> None:
    """Upstream payload whose 'base' differs from the requested from_currency
    must raise InvalidUpstreamResponse — a USD rate must never be used for an
    EUR-based conversion."""
    body = {"base": "USD", "date": "2026-08-28", "rates": {"TRY": 38.5}}
    transport = _FakeFrankfurterTransport(body)
    with pytest.raises(InvalidUpstreamResponse):
        await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))


@pytest.mark.anyio
async def test_svc_correct_base_succeeds() -> None:
    """When 'base' matches from_currency, the existing success path is unchanged."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)  # base="EUR"
    rate, rate_date = await fetch_rate("EUR", "TRY", _ASKED, _client=_svc_client(transport))

    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"
