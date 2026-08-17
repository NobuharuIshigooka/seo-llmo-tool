import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import backup


@pytest.fixture
def sqlite_db(tmp_path):
    db_path = str(tmp_path / "audit.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE audits (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO audits(id) VALUES ('a1')")
    conn.commit()
    conn.close()
    return db_path


def test_backup_blob_name_uses_jst_date():
    # 2026-08-06 15:30 UTC = 2026-08-07 00:30 JST
    now = datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc)
    assert backup._backup_blob_name(now) == "audit-db-backups/2026-08-07/audit.db"


def test_run_backup_raises_when_bucket_not_configured():
    empty_settings = replace(backup.settings, backup_gcs_bucket="")
    with patch.object(backup, "settings", empty_settings):
        with pytest.raises(backup.BackupError):
            backup.run_backup()


def test_run_backup_uploads_snapshot_to_gcs(sqlite_db):
    test_settings = replace(backup.settings, backup_gcs_bucket="test-bucket", db_path=sqlite_db)

    captured_rows = []

    def fake_upload(path):
        conn = sqlite3.connect(path)
        captured_rows.extend(conn.execute("SELECT id FROM audits").fetchall())
        conn.close()

    mock_blob = MagicMock()
    mock_blob.upload_from_filename.side_effect = fake_upload
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    now = datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc)

    with patch.object(backup, "settings", test_settings), \
         patch.object(backup.storage, "Client", return_value=mock_client):
        destination = backup.run_backup(now=now)

    assert destination == "gs://test-bucket/audit-db-backups/2026-08-07/audit.db"
    mock_bucket.blob.assert_called_once_with("audit-db-backups/2026-08-07/audit.db")
    mock_blob.upload_from_filename.assert_called_once()
    assert captured_rows == [("a1",)]
