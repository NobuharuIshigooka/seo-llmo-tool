from fastapi import FastAPI

app = FastAPI(title="SEO/LLMO診断ツール")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
