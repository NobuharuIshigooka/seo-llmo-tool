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


def init_db(db_path: str | None = None) -> None:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(str(path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_excluded_domains() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT domain FROM excluded_domains").fetchall()
    return {row["domain"] for row in rows}


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
