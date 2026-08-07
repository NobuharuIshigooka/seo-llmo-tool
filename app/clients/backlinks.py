import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app import cache
from app.config import settings

TIMEOUT_SECONDS = 30

BACKLINKS_ENDPOINT = "https://api.dataforseo.com/v3/backlinks/summary/live"


class BacklinksError(Exception):
    pass


@dataclass
class BacklinksResult:
    domain: str
    referring_domains: int  # 主指標
    rank: int  # ドメインランク
    backlinks: int = 0  # 参考表示のみ
    dofollow_ratio: float | None = None  # 参考表示のみ


def _domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower() or url.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _dofollow_ratio(result: dict) -> float | None:
    backlinks = result.get("backlinks") or 0
    dofollow = result.get("backlinks_dofollow")
    if not backlinks or dofollow is None:
        return None
    return dofollow / backlinks


def _parse(domain: str, response: dict) -> BacklinksResult:
    try:
        result = response["tasks"][0]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise BacklinksError(f"DataForSEO のレスポンス形式が不正です: {domain}") from exc

    if result is None:
        raise BacklinksError(f"DataForSEO にデータがありません: {domain}")

    return BacklinksResult(
        domain=domain,
        referring_domains=result.get("referring_domains", 0),
        rank=result.get("rank", 0),
        backlinks=result.get("backlinks", 0),
        dofollow_ratio=_dofollow_ratio(result),
    )


async def _fetch_one(domain: str) -> dict:
    if not settings.dataforseo_login or not settings.dataforseo_password:
        raise BacklinksError("DataForSEO の認証情報が未設定です")

    body = [{"target": domain}]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                BACKLINKS_ENDPOINT,
                json=body,
                auth=(settings.dataforseo_login, settings.dataforseo_password),
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise BacklinksError(f"DataForSEO 呼び出しに失敗しました: {domain} ({exc})") from exc


async def get_summary(url_or_domain: str) -> BacklinksResult:
    """自社/競合1件分の被リンクサマリーを取得する。未契約・失敗時は BacklinksError を送出する。"""
    domain = _domain_of(url_or_domain)
    cache_key = f"backlinks:{domain}"
    cached = cache.get(cache_key)
    if cached is not None:
        return BacklinksResult(**cached)

    response = await _fetch_one(domain)
    result = _parse(domain, response)
    cache.set(cache_key, result.__dict__, ttl_seconds=settings.backlink_cache_ttl)
    return result


async def get_summaries(urls_or_domains: list[str]) -> dict[str, BacklinksResult | None]:
    """自社1件＋競合最大3社分を並行取得する。個別の失敗は None にして続行する（権威性カテゴリは未計測扱い）。"""

    async def _one(target: str) -> BacklinksResult | None:
        try:
            return await get_summary(target)
        except BacklinksError:
            return None

    values = await asyncio.gather(*(_one(target) for target in urls_or_domains))
    return dict(zip(urls_or_domains, values))
