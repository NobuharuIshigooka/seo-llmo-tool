import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_connection


def get(key: str) -> Any | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()

    if row is None:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        return None

    return json.loads(row["value"])


def set(key: str, value: Any, ttl_seconds: int) -> None:
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cache(key, value, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at
            """,
            (key, json.dumps(value, ensure_ascii=False), expires_at),
        )
        conn.commit()
