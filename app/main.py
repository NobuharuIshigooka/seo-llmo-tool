from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.auth import read_session_cookie, router as auth_router, SESSION_COOKIE
from app.config import settings
from app.db import init_db

# 認証を要求しないパス（/healthz はヘルスチェック、/auth/* はログインフロー自体）
PUBLIC_PATHS = {"/healthz"}
PUBLIC_PREFIXES = ("/auth/",)

app = FastAPI(title="SEO/LLMO診断ツール")

# authlib の OAuth state 保存に使用
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

app.include_router(auth_router)


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)

    raw = request.cookies.get(SESSION_COOKIE)
    session = read_session_cookie(raw) if raw else None
    if session is None:
        if request.method == "GET":
            return RedirectResponse(url="/auth/login")
        return JSONResponse({"detail": "ログインが必要です"}, status_code=401)

    request.state.user = session
    return await call_next(request)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
