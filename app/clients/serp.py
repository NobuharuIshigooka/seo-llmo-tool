import asyncio
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app import cache
from app.config import settings

TIMEOUT_SECONDS = 30

_semaphore = asyncio.Semaphore(settings.serp_concurrency)


class SerpError(Exception):
    pass


@dataclass
class SerpResult:
    keyword: str
    device: str
    rank: int | None = None  # 20位以内に自社が入らなければ None
    ai_overview_shown: bool = False
    ai_overview_cited: bool = False
    organic_domains: list[str] = field(default_factory=list)  # 上位表示ドメイン（競合抽出用）


class SerpProvider(Protocol):
    async def search(self, keyword: str, device: str, own_domain: str) -> SerpResult: ...


def _normalize_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _rank_of(own_domain: str, organic_results: list[dict]) -> int | None:
    for item in organic_results:
        link = item.get("link", "")
        if _normalize_domain(link) == own_domain:
            return item.get("position")
    return None


def _ai_overview_cited(own_domain: str, ai_overview: dict) -> bool:
    references = ai_overview.get("references", [])
    for ref in references:
        link = ref.get("link", "")
        if link and _normalize_domain(link) == own_domain:
            return True
    return False


class SerpApiProvider:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.serpapi_key

    async def search(self, keyword: str, device: str, own_domain: str) -> SerpResult:
        cache_key = f"serp:{device}:jp:{keyword}"
        cached = cache.get(cache_key)
        if cached is not None:
            data = cached
        else:
            data = await self._fetch(keyword, device)
            cache.set(cache_key, data, ttl_seconds=settings.serp_cache_ttl)

        organic_results = data.get("organic_results", [])
        ai_overview = data.get("ai_overview")

        return SerpResult(
            keyword=keyword,
            device=device,
            rank=_rank_of(own_domain, organic_results),
            ai_overview_shown=ai_overview is not None,
            ai_overview_cited=_ai_overview_cited(own_domain, ai_overview) if ai_overview else False,
            organic_domains=[_normalize_domain(item.get("link", "")) for item in organic_results],
        )

    async def _fetch(self, keyword: str, device: str) -> dict:
        params = {
            "engine": "google",
            "q": keyword,
            "hl": "ja",
            "gl": "jp",
            "location": "Japan",
            "device": device,
            "num": 20,
            "api_key": self._api_key,
        }
        async with _semaphore:
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    resp = await client.get("https://serpapi.com/search.json", params=params)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as exc:
                raise SerpError(f"SerpApi 呼び出しに失敗しました: {keyword} ({device}) ({exc})") from exc


def get_provider() -> SerpProvider:
    if settings.serp_provider == "serpapi":
        return SerpApiProvider()
    raise SerpError(f"未対応の SERP_PROVIDER です: {settings.serp_provider}")


ACCOUNT_CACHE_TTL = 3600  # 残枠は頻繁に変わらないため1時間キャッシュ（管理画面表示用、従量課金の温存）


async def get_account_info() -> dict | None:
    """SerpApi の残枠情報（管理画面表示用）。取得失敗時は None。"""
    cache_key = "serp:account"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(
                "https://serpapi.com/account.json", params={"api_key": settings.serpapi_key}
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    info = {
        "plan_searches_left": data.get("plan_searches_left"),
        "searches_per_month": data.get("searches_per_month"),
        "this_month_usage": data.get("this_month_usage"),
        "account_email": data.get("account_email"),
    }
    cache.set(cache_key, info, ttl_seconds=ACCOUNT_CACHE_TTL)
    return info


async def search_all(keywords: list[str], own_domain: str, devices: list[str]) -> list[SerpResult]:
    """複数キーワード×デバイスを並行取得する。個々の失敗は握りつぶし、成功分のみ返す。"""
    provider = get_provider()

    async def _one(keyword: str, device: str) -> SerpResult | None:
        try:
            return await provider.search(keyword, device, own_domain)
        except SerpError:
            return None

    tasks = [_one(keyword, device) for keyword in keywords for device in devices]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]
