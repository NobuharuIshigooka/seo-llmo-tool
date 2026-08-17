from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import db, main


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test_backup_endpoint.db")
    test_settings = replace(db.settings, db_path=db_path, backup_secret="test-secret", backup_gcs_bucket="test-bucket")
    db.init_db(db_path)

    with patch.object(db, "settings", test_settings), patch.object(main, "settings", test_settings):
        with TestClient(main.app) as c:
            yield c


def test_backup_requires_no_session_cookie(client):
    with patch("app.backup.run_backup", return_value="gs://test-bucket/audit-db-backups/2026-08-07/audit.db"):
        resp = client.post("/internal/backup", headers={"X-Backup-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_backup_rejects_wrong_secret(client):
    resp = client.post("/internal/backup", headers={"X-Backup-Secret": "wrong"})
    assert resp.status_code == 401


def test_backup_rejects_missing_secret_header(client):
    resp = client.post("/internal/backup")
    assert resp.status_code == 401


def test_backup_returns_503_when_secret_not_configured(tmp_path):
    db_path = str(tmp_path / "test_backup_unconfigured.db")
    unconfigured_settings = replace(db.settings, db_path=db_path, backup_secret="", backup_gcs_bucket="")
    db.init_db(db_path)

    with patch.object(db, "settings", unconfigured_settings), patch.object(main, "settings", unconfigured_settings):
        with TestClient(main.app) as c:
            resp = c.post("/internal/backup", headers={"X-Backup-Secret": "anything"})
    assert resp.status_code == 503


def test_backup_returns_500_on_backup_error(client):
    from app.backup import BackupError

    with patch("app.backup.run_backup", side_effect=BackupError("upload failed")):
        resp = client.post("/internal/backup", headers={"X-Backup-Secret": "test-secret"})
    assert resp.status_code == 500
