from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.auth import create_session_cookie
from app.jobs import Job


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test_audit.db")
    test_settings = replace(db.settings, db_path=db_path)
    db.init_db(db_path)

    with patch.object(db, "settings", test_settings), patch.object(main, "settings", test_settings):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO users(email, name) VALUES (?, ?)",
                ("user@example.co.jp", "テストユーザー"),
            )
            conn.commit()

        with TestClient(main.app) as c:
            yield c


def _auth_cookie() -> dict:
    value = create_session_cookie("user@example.co.jp", "テストユーザー")
    return {"session": value}


def test_create_audit_requires_auth(client):
    resp = client.post("/api/audits", json={"url": "example.co.jp"})
    assert resp.status_code == 401


def test_create_audit_rejects_invalid_devices(client):
    resp = client.post(
        "/api/audits",
        json={"url": "example.co.jp", "devices": ["tablet"]},
        cookies=_auth_cookie(),
    )
    assert resp.status_code == 422


async def _fake_run_audit(job: Job, url: str, devices: list[str], keywords: list[str] | None = None) -> None:
    job.emit_progress("crawl", "サイトを読み込み中")
    job.emit_done({
        "url": url,
        "domain": "example.co.jp",
        "devices": devices,
        "keywords": [],
        "competitors": [],
        "score": {"total_score": 50, "band": "weak", "rubric_version": "test", "categories": [], "improvements": []},
        "sales_summary": "テスト要約",
        "warnings": [],
    })


def test_create_audit_normalizes_url_and_returns_job_id(client):
    with patch.object(main, "run_audit", new=AsyncMock(side_effect=_fake_run_audit)):
        resp = client.post(
            "/api/audits",
            json={"url": "example.co.jp", "devices": ["desktop"]},
            cookies=_auth_cookie(),
        )

    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert job_id

    with db.get_connection() as conn:
        row = conn.execute("SELECT url, domain, status FROM audits WHERE id = ?", (job_id,)).fetchone()
    assert row["url"] == "https://example.co.jp"
    assert row["domain"] == "example.co.jp"


def test_get_audit_result_after_completion(client):
    with patch.object(main, "run_audit", new=AsyncMock(side_effect=_fake_run_audit)):
        create_resp = client.post(
            "/api/audits",
            json={"url": "https://example.co.jp/"},
            cookies=_auth_cookie(),
        )
    job_id = create_resp.json()["job_id"]

    resp = client.get(f"/api/audits/{job_id}", cookies=_auth_cookie())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["result"]["sales_summary"] == "テスト要約"


def test_create_audit_forwards_manual_keywords_to_pipeline(client):
    mock_run_audit = AsyncMock(side_effect=_fake_run_audit)
    with patch.object(main, "run_audit", new=mock_run_audit):
        resp = client.post(
            "/api/audits",
            json={"url": "https://example.co.jp/", "keywords": ["手動キーワード1", "手動キーワード2"]},
            cookies=_auth_cookie(),
        )

    assert resp.status_code == 200
    mock_run_audit.assert_awaited_once()
    assert mock_run_audit.call_args.kwargs["keywords"] == ["手動キーワード1", "手動キーワード2"]


def test_get_audit_returns_404_for_unknown_id(client):
    resp = client.get("/api/audits/does-not-exist", cookies=_auth_cookie())
    assert resp.status_code == 404


def test_create_audit_rejects_when_daily_limit_reached(tmp_path):
    db_path = str(tmp_path / "test_rate_limit.db")
    limited_settings = replace(db.settings, db_path=db_path, daily_audit_limit=1)
    db.init_db(db_path)

    with patch.object(db, "settings", limited_settings), patch.object(main, "settings", limited_settings):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO users(email, name) VALUES (?, ?)", ("user@example.co.jp", "テストユーザー"))
            conn.commit()

        with TestClient(main.app) as c:
            with patch.object(main, "run_audit", new=AsyncMock(side_effect=_fake_run_audit)):
                first = c.post("/api/audits", json={"url": "example.co.jp"}, cookies=_auth_cookie())
                assert first.status_code == 200

                second = c.post("/api/audits", json={"url": "example.co.jp"}, cookies=_auth_cookie())
                assert second.status_code == 429


def test_sse_events_endpoint_streams_progress(client):
    with patch.object(main, "run_audit", new=AsyncMock(side_effect=_fake_run_audit)):
        create_resp = client.post(
            "/api/audits",
            json={"url": "https://example.co.jp/"},
            cookies=_auth_cookie(),
        )
    job_id = create_resp.json()["job_id"]

    resp = client.get(f"/api/audits/{job_id}/events", cookies=_auth_cookie())
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"
    assert "data:" in resp.text


def test_sse_events_returns_404_for_unknown_job(client):
    resp = client.get("/api/audits/unknown/events", cookies=_auth_cookie())
    assert resp.status_code == 404
