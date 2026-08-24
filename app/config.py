import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [v.strip() for v in raw.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # 認証
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    allowed_hd: str = os.getenv("ALLOWED_HD", "")
    allowed_emails: list[str] = field(default_factory=lambda: _list("ALLOWED_EMAILS"))
    session_secret: str = os.getenv("SESSION_SECRET", "")

    # 外部API
    serp_provider: str = os.getenv("SERP_PROVIDER", "serpapi")
    serpapi_key: str = os.getenv("SERPAPI_KEY", "")
    dataforseo_login: str = os.getenv("DATAFORSEO_LOGIN", "")
    dataforseo_password: str = os.getenv("DATAFORSEO_PASSWORD", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    psi_api_key: str = os.getenv("PSI_API_KEY", "")

    # 動作設定
    max_keywords: int = int(os.getenv("MAX_KEYWORDS", "10"))
    max_competitors: int = int(os.getenv("MAX_COMPETITORS", "3"))
    daily_audit_limit: int = int(os.getenv("DAILY_AUDIT_LIMIT", "30"))
    serp_concurrency: int = int(os.getenv("SERP_CONCURRENCY", "4"))
    serp_cache_ttl: int = int(os.getenv("SERP_CACHE_TTL", "86400"))
    crawl_cache_ttl: int = int(os.getenv("CRAWL_CACHE_TTL", "21600"))
    psi_cache_ttl: int = int(os.getenv("PSI_CACHE_TTL", "43200"))
    fetch_ai_overview_detail: bool = _bool("FETCH_AI_OVERVIEW_DETAIL", "false")
    respect_robots: bool = _bool("RESPECT_ROBOTS", "true")
    user_agent: str = os.getenv("USER_AGENT", "")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    db_path: str = os.getenv("DB_PATH", "data/audit.db")

    # バックアップ（docs/deployment.md 4章）
    backup_secret: str = os.getenv("BACKUP_SECRET", "")
    backup_gcs_bucket: str = os.getenv("BACKUP_GCS_BUCKET", "")


settings = Settings()
