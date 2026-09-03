from fastapi import FastAPI

app = FastAPI(
    title="FX Conversion Tool",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
