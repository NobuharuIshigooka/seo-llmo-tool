import asyncio
from dataclasses import dataclass

import httpx

from app import cache
from app.config import settings

TIMEOUT_SECONDS = 90

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PsiError(Exception):
    pass


@dataclass
class PsiResult:
    strategy: str  # mobile / desktop
    performance_score: float | None = None  # 0.0〜1.0（取得不可時は None）
    lcp_ms: float | None = None
    cls: float | None = None
    tbt_ms: float | None = None
    has_field_data: bool = False


def _extract(strategy: str, data: dict) -> PsiResult:
    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})

    performance_score = None
    perf_category = categories.get("performance")
    if perf_category and perf_category.get("score") is not None:
        performance_score = perf_category["score"]

    def _audit_value(key: str) -> float | None:
        audit = audits.get(key)
        if audit is None:
            return None
        return audit.get("numericValue")

    field_data = data.get("loadingExperience", {})

    return PsiResult(
        strategy=strategy,
        performance_score=performance_score,
        lcp_ms=_audit_value("largest-contentful-paint"),
        cls=_audit_value("cumulative-layout-shift"),
        tbt_ms=_audit_value("total-blocking-time"),
        has_field_data=bool(field_data.get("metrics")),
    )


async def _fetch(url: str, strategy: str) -> dict:
    params = {
        "url": url,
        "strategy": strategy,
        "category": "PERFORMANCE",
    }
    if settings.psi_api_key:
        params["key"] = settings.psi_api_key

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(PSI_ENDPOINT, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise PsiError(f"PageSpeed Insights 呼び出しに失敗しました: {url} ({strategy}) ({exc})") from exc


async def analyze(url: str, strategy: str) -> PsiResult:
    """指定ストラテジー（mobile/desktop）で表示速度を取得する。キャッシュ経由。"""
    cache_key = f"psi:{strategy}:{url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return PsiResult(**cached)

    data = await _fetch(url, strategy)
    result = _extract(strategy, data)
    cache.set(cache_key, result.__dict__, ttl_seconds=settings.psi_cache_ttl)
    return result


async def analyze_both(url: str) -> dict[str, PsiResult | None]:
    """mobile/desktop の両方を並行取得する。個々の失敗は None にして続行する（表示速度は0点扱い）。"""

    async def _one(strategy: str) -> PsiResult | None:
        try:
            return await analyze(url, strategy)
        except PsiError:
            return None

    mobile, desktop = await asyncio.gather(_one("mobile"), _one("desktop"))
    return {"mobile": mobile, "desktop": desktop}
