from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.fx_service import (
    ECB_START_DATE,
    InvalidUpstreamResponse,
    UnsupportedCurrency,
    UpstreamError,
    UpstreamTimeout,
    fetch_rate,
)

app = FastAPI(
    title="FX Conversion Tool",
    version="0.1.0",
)


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "message": message,
        },
    )


def validate_amount(amount: Decimal) -> JSONResponse | None:
    if (
        not amount.is_finite()
        or amount <= 0
        or amount.as_tuple().exponent < -2
    ):
        return error_response(
            status_code=400,
            error="invalid_amount",
            message="Amount must be greater than zero and have at most two decimal places.",
        )

    return None


def normalize_currency_code(currency_code: str) -> str | JSONResponse:
    normalized = currency_code.strip().upper()
    if len(normalized) != 3 or not all("A" <= char <= "Z" for char in normalized):
        return error_response(
            status_code=400,
            error="invalid_currency",
            message="Currency codes must be three-letter ISO-style codes.",
        )

    return normalized


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        status_code=400,
        error="invalid_request",
        message="The request parameters are invalid.",
    )


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/tools/convert")
async def convert(
    amount: Decimal,
    from_currency: str = Query(alias="from"),
    to_currency: str = Query(alias="to"),
    asked_date: date = Query(alias="date"),
):
    amount_error = validate_amount(amount)
    if amount_error is not None:
        return amount_error

    normalized_from = normalize_currency_code(from_currency)
    if isinstance(normalized_from, JSONResponse):
        return normalized_from

    normalized_to = normalize_currency_code(to_currency)
    if isinstance(normalized_to, JSONResponse):
        return normalized_to

    if normalized_from == normalized_to:
        return error_response(
            status_code=400,
            error="same_currency",
            message="Source and target currencies must be different.",
        )

    if asked_date > date.today():
        return error_response(
            status_code=400,
            error="future_date",
            message="Exchange rates are not available for future dates.",
        )

    if asked_date < ECB_START_DATE:
        return error_response(
            status_code=400,
            error="date_out_of_range",
            message="Exchange rate data is unavailable for this date.",
        )

    try:
        rate, rate_date = await fetch_rate(normalized_from, normalized_to, asked_date)
    except UpstreamTimeout:
        return error_response(
            status_code=504,
            error="upstream_timeout",
            message="The exchange-rate provider did not respond in time.",
        )
    except UpstreamError:
        return error_response(
            status_code=502,
            error="upstream_error",
            message="The exchange-rate provider is currently unavailable.",
        )
    except UnsupportedCurrency:
        return error_response(
            status_code=400,
            error="unsupported_currency",
            message="The requested currency is not supported.",
        )
    except InvalidUpstreamResponse:
        return error_response(
            status_code=502,
            error="invalid_upstream_response",
            message="The exchange-rate provider returned an invalid response.",
        )

    try:
        result = (amount * rate).quantize(Decimal("0.01"))
    except InvalidOperation:
        return error_response(
            status_code=400,
            error="invalid_amount",
            message="Amount is too large to convert safely.",
        )

    return {
        "amount": amount,
        "from": normalized_from,
        "to": normalized_to,
        "rate": rate,
        "result": result,
        "rate_date": rate_date,
        "asked_date": str(asked_date),
        "source": "ECB via frankfurter.dev",
    }
