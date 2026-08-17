"""SQLite を Cloud Storage へ日次バックアップする（docs/deployment.md 4章）。

Cloud Run は単体では定期実行できないため、Cloud Scheduler が
POST /internal/backup を日次で叩くことでこのモジュールを起動する
（app/main.py 側で共有シークレットヘッダーを検証してから呼び出す）。
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.cloud import storage

from app.config import settings

JST = timezone(timedelta(hours=9))


class BackupError(Exception):
    pass


def _backup_blob_name(now: datetime | None = None) -> str:
    now_jst = (now or datetime.now(timezone.utc)).astimezone(JST)
    return f"audit-db-backups/{now_jst.strftime('%Y-%m-%d')}/audit.db"


def _snapshot_sqlite(src_path: str, dst_path: str) -> None:
    """SQLite の Online Backup API で整合性のあるコピーを取る。

    起動中のプロセスが書き込み中でも安全に取得できるよう、
    ファイルコピーではなく sqlite3.Connection.backup() を使う。
    """
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def run_backup(now: datetime | None = None) -> str:
    """SQLiteのスナップショットを取得しGCSにアップロードする。戻り値はアップロード先のパス。"""
    if not settings.backup_gcs_bucket:
        raise BackupError("BACKUP_GCS_BUCKET が設定されていません")

    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = str(Path(tmp_dir) / "audit.db")
        _snapshot_sqlite(settings.db_path, snapshot_path)

        client = storage.Client()
        bucket = client.bucket(settings.backup_gcs_bucket)
        blob_name = _backup_blob_name(now)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(snapshot_path)

    return f"gs://{settings.backup_gcs_bucket}/{blob_name}"
