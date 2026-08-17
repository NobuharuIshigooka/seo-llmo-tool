import json
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.auth import CurrentUserDep, read_session_cookie, router as auth_router, SESSION_COOKIE
from app.config import settings
from app import db
from app.db import count_audits_today, get_connection, init_db
from app.jobs import job_manager, sse_stream
from app.pipeline import run_audit
from app.report_view import build_report_context

templates = Jinja2Templates(directory="app/templates")

# 認証を要求しないパス（/healthz はヘルスチェック、/auth/* はログインフロー自体、/static/* は静的アセット、
# /internal/backup は Cloud Scheduler からの呼び出し用でセッションCookieを持たないため、
# エンドポイント自体で共有シークレットヘッダーを検証する）
PUBLIC_PATHS = {"/healthz", "/internal/backup"}
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


def _load_finished_audit(audit_id: str) -> dict:
    row = db.get_audit(audit_id)
    if row is None or row["status"] != "done" or not row["result"]:
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
        "result": json.loads(row["result"]),
    }


@app.get("/report/{audit_id}")
async def report(audit_id: str, request: Request, user=CurrentUserDep):
    audit_row = _load_finished_audit(audit_id)
    is_pdf = request.query_params.get("pdf") == "1"
    context = build_report_context(audit_row, audit_row["result"], settings.base_url, is_pdf=is_pdf)
    return templates.TemplateResponse("report.html", {"request": request, **context})


@app.get("/report/{audit_id}.pdf")
async def report_pdf(audit_id: str, request: Request, user=CurrentUserDep):
    from playwright.async_api import async_playwright

    audit_row = _load_finished_audit(audit_id)

    session_cookie = request.cookies.get(SESSION_COOKIE)
    report_url = f"{settings.base_url}/report/{audit_id}?pdf=1"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            if session_cookie:
                cookie_url = settings.base_url
                await page.context.add_cookies([{
                    "name": SESSION_COOKIE,
                    "value": session_cookie,
                    "url": cookie_url,
                }])
            await page.goto(report_url, wait_until="networkidle", timeout=30000)
            pdf_bytes = await page.pdf(format="A4", print_background=True)
        finally:
            await browser.close()

    filename = f"{audit_row['domain']}_seo_llmo_report.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/history")
async def api_history(user=CurrentUserDep):
    rows = db.list_audits()
    return {
        "audits": [
            {
                "id": row["id"],
                "domain": row["domain"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "status": row["status"],
                "score": (json.loads(row["result"])["score"]["total_score"] if row["result"] else None),
            }
            for row in rows
        ]
    }


@app.get("/history")
async def history_page(request: Request, user=CurrentUserDep):
    return templates.TemplateResponse("history.html", {"request": request, "user_name": user.name})


class ExcludedDomainRequest(BaseModel):
    domain: str
    reason: str | None = None


@app.get("/api/excluded-domains")
async def api_list_excluded_domains(user=CurrentUserDep):
    rows = db.list_excluded_domains()
    return {
        "domains": [
            {"domain": row["domain"], "reason": row["reason"], "created_by": row["created_by"], "created_at": row["created_at"]}
            for row in rows
        ]
    }


@app.post("/api/excluded-domains")
async def api_add_excluded_domain(payload: ExcludedDomainRequest, user=CurrentUserDep):
    domain = payload.domain.strip().lower()
    if not domain:
        raise HTTPException(status_code=400, detail="ドメインを入力してください")
    db.add_excluded_domain(domain, payload.reason, user.email)
    return {"ok": True}


@app.delete("/api/excluded-domains/{domain}")
async def api_remove_excluded_domain(domain: str, user=CurrentUserDep):
    db.remove_excluded_domain(domain)
    return {"ok": True}


@app.get("/admin/excluded-domains")
async def excluded_domains_page(request: Request, user=CurrentUserDep):
    return templates.TemplateResponse("excluded_domains.html", {"request": request, "user_name": user.name})


@app.get("/api/serp-account")
async def api_serp_account(user=CurrentUserDep):
    from app.clients import serp

    info = await serp.get_account_info()
    if info is None:
        return {"available": False}
    return {"available": True, **info}


@app.post("/internal/backup")
async def internal_backup(request: Request):
    """Cloud Scheduler から日次で呼ばれ、SQLiteをCloud Storageへバックアップする。

    セッションCookie認証の対象外パスのため、共有シークレットヘッダーで検証する
    （docs/deployment.md 4章）。
    """
    if not settings.backup_secret:
        raise HTTPException(status_code=503, detail="バックアップ機能が設定されていません")

    provided = request.headers.get("X-Backup-Secret", "")
    if not secrets.compare_digest(provided, settings.backup_secret):
        raise HTTPException(status_code=401, detail="認証に失敗しました")

    from app.backup import BackupError, run_backup

    try:
        destination = run_backup()
    except BackupError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True, "destination": destination}
