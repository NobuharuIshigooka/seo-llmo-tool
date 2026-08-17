from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.auth import create_session_cookie
from app.jobs import Job


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test_report.db")
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


async def _fake_run_audit(job: Job, url: str, devices: list[str], keywords: list[str] | None = None) -> None:
    job.emit_progress("crawl", "サイトを読み込み中")
    job.emit_done({
        "url": url,
        "domain": "example.co.jp",
        "devices": devices,
        "keywords": ["キーワード1"],
        "competitors": ["a.co.jp"],
        "competitor_backlinks": {"a.co.jp": {"domain": "a.co.jp", "referring_domains": 30, "rank": 40, "backlinks": 100, "dofollow_ratio": 0.8}},
        "competitor_comparison": {
            "example.co.jp": {
                "char_count": 1000, "h2_count": 3, "qa_heading_count": 1, "has_faq_schema": True,
                "structured_data_type_count": 1, "list_table_count": 2, "internal_link_count": 10,
                "eeat_count": 2, "referring_domains": 20,
            },
            "a.co.jp": {
                "char_count": 2000, "h2_count": 5, "qa_heading_count": 2, "has_faq_schema": False,
                "structured_data_type_count": 0, "list_table_count": 4, "internal_link_count": 20,
                "eeat_count": 3, "referring_domains": 30,
            },
        },
        "competitor_comparison_labels": {},
        "competitor_explanation": "解説文です。",
        "serp_results": [
            {"keyword": "キーワード1", "device": "desktop", "rank": 3, "ai_overview_shown": True, "ai_overview_cited": True},
        ],
        "score": {
            "total_score": 62, "band": "fair", "rubric_version": "test",
            "categories": [
                {"key": "technical_seo", "label": "テクニカルSEO", "score": 20.0, "weight": 30, "measured": True, "items": [
                    {"label": "表示速度（モバイル）", "score": 20.0, "max_score": 40, "evidence": "PageSpeedスコア 50／100"},
                ]},
            ],
            "improvements": [{"label": "表示速度（モバイル）", "category": "テクニカルSEO", "impact": 6.0}],
        },
        "sales_summary": "テスト要約",
        "warnings": ["PageSpeed Insights（PC）の取得に失敗しました。"],
    })


def _create_done_audit(client) -> str:
    with patch.object(main, "run_audit", new=AsyncMock(side_effect=_fake_run_audit)):
        resp = client.post(
            "/api/audits",
            json={"url": "https://example.co.jp/"},
            cookies=_auth_cookie(),
        )
    return resp.json()["job_id"]


def test_report_requires_auth(client):
    audit_id = _create_done_audit(client)
    resp = client.get(f"/report/{audit_id}", follow_redirects=False)
    assert resp.status_code in (302, 401, 307)


def test_report_renders_for_completed_audit(client):
    audit_id = _create_done_audit(client)
    resp = client.get(f"/report/{audit_id}", cookies=_auth_cookie())
    assert resp.status_code == 200
    assert "example.co.jp" in resp.text
    assert "テスト要約" in resp.text
    assert "解説文です。" in resp.text


def test_report_returns_404_for_unknown_audit(client):
    resp = client.get("/report/does-not-exist", cookies=_auth_cookie())
    assert resp.status_code == 404


def test_history_api_lists_audits(client):
    audit_id = _create_done_audit(client)
    resp = client.get("/api/history", cookies=_auth_cookie())
    assert resp.status_code == 200
    audits = resp.json()["audits"]
    assert any(a["id"] == audit_id and a["score"] == 62 for a in audits)


def test_history_page_requires_auth(client):
    resp = client.get("/history", follow_redirects=False)
    assert resp.status_code in (302, 401, 307)


def test_excluded_domains_crud_via_api(client):
    add_resp = client.post(
        "/api/excluded-domains",
        json={"domain": "Example.com", "reason": "ポータル"},
        cookies=_auth_cookie(),
    )
    assert add_resp.status_code == 200

    list_resp = client.get("/api/excluded-domains", cookies=_auth_cookie())
    domains = list_resp.json()["domains"]
    assert any(d["domain"] == "example.com" for d in domains)

    delete_resp = client.delete("/api/excluded-domains/example.com", cookies=_auth_cookie())
    assert delete_resp.status_code == 200

    list_resp_after = client.get("/api/excluded-domains", cookies=_auth_cookie())
    assert list_resp_after.json()["domains"] == []


def test_excluded_domains_page_requires_auth(client):
    resp = client.get("/admin/excluded-domains", follow_redirects=False)
    assert resp.status_code in (302, 401, 307)
