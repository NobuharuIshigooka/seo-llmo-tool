from unittest.mock import AsyncMock, patch

from authlib.integrations.base_client.errors import MismatchingStateError
from fastapi.testclient import TestClient

from app import auth, db, main
from app.auth import SESSION_COOKIE, create_session_cookie


def _client(tmp_path):
    db_path = str(tmp_path / "test_auth.db")
    db.init_db(db_path)
    from dataclasses import replace

    test_settings = replace(db.settings, db_path=db_path)
    return test_settings


def test_callback_redirects_home_on_mismatching_state_when_already_logged_in(tmp_path):
    test_settings = _client(tmp_path)
    with patch.object(db, "settings", test_settings), patch.object(main, "settings", test_settings):
        with TestClient(main.app) as c:
            cookie_value = create_session_cookie("user@example.co.jp", "テストユーザー")
            with patch.object(
                auth.oauth.google, "authorize_access_token",
                new=AsyncMock(side_effect=MismatchingStateError()),
            ):
                resp = c.get(
                    "/auth/callback?state=stale&code=x",
                    cookies={SESSION_COOKIE: cookie_value},
                    follow_redirects=False,
                )

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/"


def test_callback_returns_401_on_mismatching_state_when_not_logged_in(tmp_path):
    test_settings = _client(tmp_path)
    with patch.object(db, "settings", test_settings), patch.object(main, "settings", test_settings):
        with TestClient(main.app) as c:
            with patch.object(
                auth.oauth.google, "authorize_access_token",
                new=AsyncMock(side_effect=MismatchingStateError()),
            ):
                resp = c.get("/auth/callback?state=stale&code=x", follow_redirects=False)

    assert resp.status_code == 401


def test_require_auth_recreates_missing_user_row(tmp_path):
    """Cloud Run再デプロイでSQLiteが揮発してもusersテーブルが空になった状態で
    有効なセッションCookieだけが残るケース（docs/deployment.md記載の既知の制約）。
    require_auth() が該当ユーザーを自動的に作り直し、外部キー制約に落ちないことを確認する。
    """
    test_settings = _client(tmp_path)
    with patch.object(db, "settings", test_settings), patch.object(main, "settings", test_settings):
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE email = ?", ("ghost@example.co.jp",)
            ).fetchone()
        assert row is None

        with TestClient(main.app) as c:
            cookie_value = create_session_cookie("ghost@example.co.jp", "消えたユーザー")
            with patch.object(main, "run_audit", new=AsyncMock()):
                resp = c.post(
                    "/api/audits",
                    json={"url": "https://example.co.jp/"},
                    cookies={SESSION_COOKIE: cookie_value},
                )

        assert resp.status_code == 200

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM users WHERE email = ?", ("ghost@example.co.jp",)
            ).fetchone()
        assert row is not None
        assert row["name"] == "消えたユーザー"
