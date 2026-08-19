from urllib.parse import urlencode

from authlib.integrations.base_client.errors import MismatchingStateError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import RedirectResponse

from app.config import settings
from app.db import get_connection

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "auth_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14日

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="session")


def create_session_cookie(email: str, name: str) -> str:
    return _serializer().dumps({"email": email, "name": name})


def read_session_cookie(value: str) -> dict | None:
    try:
        return _serializer().loads(value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _is_allowed(email: str) -> bool:
    if email in settings.allowed_emails:
        return True
    if settings.allowed_hd and email.lower().endswith("@" + settings.allowed_hd.lower()):
        return True
    return False


def _upsert_user(email: str, name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users(email, name) VALUES (?, ?)
            ON CONFLICT(email) DO UPDATE SET name = excluded.name
            """,
            (email, name),
        )
        conn.commit()


@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    extra_params = {}
    if settings.allowed_hd:
        extra_params["hd"] = settings.allowed_hd
    return await oauth.google.authorize_redirect(request, redirect_uri, **extra_params)


@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except MismatchingStateError:
        # ブラウザがGoogleのサイレント再認証（prompt=none）等でコールバックを複数回
        # 送ってくることがある。1回目で成功しstateが消費済みの状態で2回目が来ると
        # ここに来るため、既にログイン済みならエラーを見せずトップへ流す。
        raw = request.cookies.get(SESSION_COOKIE)
        if raw is not None and read_session_cookie(raw) is not None:
            return RedirectResponse(url="/")
        raise HTTPException(status_code=401, detail="ログインの有効期限が切れました。再度ログインしてください")
    userinfo = token.get("userinfo")
    if userinfo is None:
        raise HTTPException(status_code=401, detail="Google認証に失敗しました")

    if not userinfo.get("email_verified", False):
        raise HTTPException(status_code=401, detail="メールアドレスが確認されていません")

    email = userinfo["email"]
    name = userinfo.get("name", email)

    if not _is_allowed(email):
        raise HTTPException(status_code=403, detail="このアカウントには利用権限がありません")

    _upsert_user(email, name)

    response = RedirectResponse(url="/")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_cookie(email, name),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie(SESSION_COOKIE)
    return response


class CurrentUser:
    def __init__(self, email: str, name: str):
        self.email = email
        self.name = name


async def require_auth(request: Request) -> CurrentUser:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw is None:
        raise HTTPException(status_code=401, detail="ログインが必要です")

    session = read_session_cookie(raw)
    if session is None:
        raise HTTPException(status_code=401, detail="セッションが無効です。再度ログインしてください")

    # Cloud Run のファイルシステムは揮発性のため、再デプロイでSQLiteのusersテーブルが
    # 空になっても、署名付きセッションCookieは有効期限内であれば残り続ける。
    # その場合でも audits 等の外部キー制約に落ちないよう、都度存在を保証しておく
    # （docs/deployment.md「データの永続化」参照）。
    _upsert_user(session["email"], session["name"])

    return CurrentUser(email=session["email"], name=session["name"])


CurrentUserDep = Depends(require_auth)
