from datetime import date
from decimal import Decimal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI(
    title="FX Conversion Tool",
    version="0.1.0",
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
    return JSONResponse(
        status_code=501,
        content={
            "error": "not_implemented",
            "message": "Currency conversion is not implemented yet.",
        },
    )
