import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.config import settings

JST = timezone(timedelta(hours=9))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(email),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL,
    devices TEXT NOT NULL,
    result TEXT
);

CREATE INDEX IF NOT EXISTS idx_audits_domain ON audits(domain);
CREATE INDEX IF NOT EXISTS idx_audits_created_by ON audits(created_by);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS excluded_domains (
    domain TEXT PRIMARY KEY,
    reason TEXT,
    created_by TEXT NOT NULL REFERENCES users(email),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# docs/requirements.md 4.5「除外リストの初期値」。ドメインが一意に特定できるもののみ登録する
# （「求人媒体各社」「まとめ系メディア」は個別サイト名の指定がないため対象外。管理画面から追加する）。
_SEED_EXCLUDED_DOMAINS: list[tuple[str, str]] = [
    ("tabelog.com", "ポータルサイト（初期リスト）"),
    ("hotpepper.jp", "ポータルサイト（初期リスト）"),
    ("ekiten.jp", "ポータルサイト（初期リスト）"),
    ("gnavi.co.jp", "ポータルサイト（初期リスト）"),
    ("ja.wikipedia.org", "比較対象として不適切（初期リスト）"),
    ("wikipedia.org", "比較対象として不適切（初期リスト）"),
    ("amazon.co.jp", "ポータルサイト（初期リスト）"),
    ("rakuten.co.jp", "ポータルサイト（初期リスト）"),
    ("shopping.yahoo.co.jp", "ポータルサイト（初期リスト）"),
]

_SEED_USER_EMAIL = "system@internal"


def init_db(db_path: str | None = None) -> None:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(str(path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        _seed_excluded_domains(conn)


def _seed_excluded_domains(conn: sqlite3.Connection) -> None:
    """excluded_domains が空の場合のみ、requirements.md の初期リストを投入する（初回起動時のみ）。"""
    count = conn.execute("SELECT COUNT(*) AS count FROM excluded_domains").fetchone()["count"]
    if count > 0:
        return

    conn.execute(
        "INSERT INTO users(email, name) VALUES (?, ?) ON CONFLICT(email) DO NOTHING",
        (_SEED_USER_EMAIL, "システム（初期除外リスト）"),
    )
    conn.executemany(
        """
        INSERT INTO excluded_domains(domain, reason, created_by) VALUES (?, ?, ?)
        ON CONFLICT(domain) DO NOTHING
        """,
        [(domain, reason, _SEED_USER_EMAIL) for domain, reason in _SEED_EXCLUDED_DOMAINS],
    )
    conn.commit()


def get_excluded_domains() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT domain FROM excluded_domains").fetchall()
    return {row["domain"] for row in rows}


def list_excluded_domains() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT domain, reason, created_by, created_at FROM excluded_domains ORDER BY created_at DESC"
        ).fetchall()


def add_excluded_domain(domain: str, reason: str | None, created_by: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO excluded_domains(domain, reason, created_by) VALUES (?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET reason = excluded.reason
            """,
            (domain, reason, created_by),
        )
        conn.commit()


def remove_excluded_domain(domain: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM excluded_domains WHERE domain = ?", (domain,))
        conn.commit()


def list_audits(*, limit: int = 100) -> list[sqlite3.Row]:
    """診断履歴一覧（新しい順）。ドメイン単位での一覧表示に使う。"""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT id, url, domain, created_by, created_at, finished_at, status, devices, result
            FROM audits
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_audit(audit_id: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, url, domain, created_by, created_at, finished_at, status, devices, result FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()


def count_audits_today(created_by: str, *, now: datetime | None = None) -> int:
    """JST基準の当日0時〜翌0時に created_by が起票した診断件数を数える。"""
    now_jst = (now or datetime.now(timezone.utc)).astimezone(JST)
    day_start_jst = datetime.combine(now_jst.date(), time.min, tzinfo=JST)
    day_end_jst = day_start_jst + timedelta(days=1)

    day_start_utc = day_start_jst.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    day_end_utc = day_end_jst.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count FROM audits
            WHERE created_by = ? AND created_at >= ? AND created_at < ?
            """,
            (created_by, day_start_utc, day_end_utc),
        ).fetchone()
    return row["count"]


@contextmanager
def get_connection(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
