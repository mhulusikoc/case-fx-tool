from datetime import date
from decimal import Decimal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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

    return error_response(
        status_code=501,
        error="not_implemented",
        message="Currency conversion is not implemented yet.",
    )
