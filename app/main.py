import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.auth import CurrentUserDep, read_session_cookie, router as auth_router, SESSION_COOKIE
from app.config import settings
from app.db import count_audits_today, get_connection, init_db
from app.jobs import job_manager, sse_stream
from app.pipeline import run_audit

templates = Jinja2Templates(directory="app/templates")

# 認証を要求しないパス（/healthz はヘルスチェック、/auth/* はログインフロー自体、/static/* は静的アセット）
PUBLIC_PATHS = {"/healthz"}
PUBLIC_PREFIXES = ("/auth/", "/static/")

ALLOWED_DEVICES = {"desktop", "mobile"}

app = FastAPI(title="SEO/LLMO診断ツール")

# authlib の OAuth state 保存に使用
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

app.include_router(auth_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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


@app.get("/")
async def index(request: Request, user=CurrentUserDep):
    return templates.TemplateResponse(
        "index.html", {"request": request, "user_name": user.name}
    )


class CreateAuditRequest(BaseModel):
    url: str
    devices: list[str] = ["desktop", "mobile"]
    keywords: list[str] | None = None  # 指定時はキーワード推定をスキップ（結果画面での手動編集→再診断用）

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URLを入力してください")
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    @field_validator("devices")
    @classmethod
    def _validate_devices(cls, value: list[str]) -> list[str]:
        if not value or not set(value) <= ALLOWED_DEVICES:
            raise ValueError("devices は desktop / mobile のみ指定できます")
        return value


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _save_audit_started(audit_id: str, url: str, devices: list[str], created_by: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audits(id, url, domain, created_by, status, devices)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (audit_id, url, _domain_of(url), created_by, json.dumps(devices, ensure_ascii=False)),
        )
        conn.commit()


def _save_audit_result(audit_id: str, status: str, result: dict | None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audits SET status = ?, result = ?, finished_at = datetime('now')
            WHERE id = ?
            """,
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None, audit_id),
        )
        conn.commit()


async def _run_and_persist(job, url: str, devices: list[str], keywords: list[str] | None) -> None:
    """pipeline.run_audit を実行し、完了/エラーいずれの場合も audits テーブルへ結果を保存する。"""
    await run_audit(job, url, devices, keywords=keywords)

    if job.status == "done":
        result = job.events[-1]["result"]
        _save_audit_result(job.id, "done", result)
    else:
        _save_audit_result(job.id, "error", None)


@app.post("/api/audits")
async def create_audit(payload: CreateAuditRequest, request: Request, user=CurrentUserDep):
    audits_today = count_audits_today(user.email)
    if audits_today >= settings.daily_audit_limit:
        raise HTTPException(
            status_code=429,
            detail=f"1日の診断上限（{settings.daily_audit_limit}件）に達しました。明日また実行してください。",
        )

    async def runner(job):
        await _run_and_persist(job, payload.url, payload.devices, payload.keywords)

    job = job_manager.create_job(created_by=user.email, runner=runner)
    _save_audit_started(job.id, payload.url, payload.devices, user.email)

    return {"job_id": job.id}


@app.get("/api/audits/{audit_id}/events")
async def audit_events(audit_id: str, user=CurrentUserDep):
    job = job_manager.get(audit_id)
    if job is None:
        raise HTTPException(status_code=404, detail="診断ジョブが見つかりません")

    return StreamingResponse(
        sse_stream(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/audits/{audit_id}")
async def get_audit(audit_id: str, user=CurrentUserDep):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, url, domain, created_by, created_at, finished_at, status, devices, result FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="診断結果が見つかりません")

    return {
        "id": row["id"],
        "url": row["url"],
        "domain": row["domain"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "devices": json.loads(row["devices"]),
        "result": json.loads(row["result"]) if row["result"] else None,
    }
