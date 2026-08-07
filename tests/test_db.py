from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import db


@pytest.fixture
def isolated_db(tmp_path):
    db_path = str(tmp_path / "test_rate_limit.db")
    test_settings = replace(db.settings, db_path=db_path)
    db.init_db(db_path)

    with patch.object(db, "settings", test_settings):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO users(email, name) VALUES (?, ?)", ("user@example.co.jp", "テスト"))
            conn.commit()
        yield


def _insert_audit(audit_id: str, created_by: str, created_at_utc: datetime) -> None:
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audits(id, url, domain, created_by, created_at, status, devices)
            VALUES (?, 'https://example.co.jp/', 'example.co.jp', ?, ?, 'done', '["desktop"]')
            """,
            (audit_id, created_by, created_at_utc.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def test_count_audits_today_counts_only_same_jst_day(isolated_db):
    # 2026-08-07 00:30 JST = 2026-08-06 15:30 UTC
    now = datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc)

    _insert_audit("a1", "user@example.co.jp", now)  # 同じJST日（08-07 00:30 JST）
    _insert_audit("a2", "user@example.co.jp", now - timedelta(hours=1))  # JST前日（08-06 23:30 JST）
    _insert_audit("a3", "user@example.co.jp", now + timedelta(hours=24))  # 翌JST日（08-08 00:30 JST）

    count = db.count_audits_today("user@example.co.jp", now=now)
    assert count == 1


def test_count_audits_today_scopes_by_user(isolated_db):
    now = datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO users(email, name) VALUES (?, ?)", ("other@example.co.jp", "他ユーザー"))
        conn.commit()

    _insert_audit("a1", "user@example.co.jp", now)
    _insert_audit("a2", "other@example.co.jp", now)

    assert db.count_audits_today("user@example.co.jp", now=now) == 1
    assert db.count_audits_today("other@example.co.jp", now=now) == 1


def test_count_audits_today_returns_zero_when_no_audits(isolated_db):
    now = datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc)
    assert db.count_audits_today("user@example.co.jp", now=now) == 0


def test_count_audits_today_boundary_just_before_jst_midnight(isolated_db):
    # 2026-08-06 23:59:59 JST = 2026-08-06 14:59:59 UTC
    boundary = datetime(2026, 8, 6, 14, 59, 59, tzinfo=timezone.utc)
    _insert_audit("a1", "user@example.co.jp", boundary)

    # 問い合わせ基準時刻を同じJST日にする
    now = datetime(2026, 8, 6, 10, 0, 0, tzinfo=timezone.utc)
    assert db.count_audits_today("user@example.co.jp", now=now) == 1

    # 翌日側から見ると含まれない
    next_day_now = datetime(2026, 8, 6, 16, 0, 0, tzinfo=timezone.utc)  # 2026-08-07 01:00 JST
    assert db.count_audits_today("user@example.co.jp", now=next_day_now) == 0
