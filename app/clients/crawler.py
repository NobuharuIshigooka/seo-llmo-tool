import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app import cache
from app.config import settings

TIMEOUT_SECONDS = 25
MAX_CONCURRENT_PER_DOMAIN = 3

_domain_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(MAX_CONCURRENT_PER_DOMAIN)
)


@dataclass
class CrawlResult:
    url: str
    title: str = ""
    meta_description: str = ""
    h1_list: list[str] = field(default_factory=list)
    h2_list: list[str] = field(default_factory=list)
    body_text: str = ""
    first_paragraph: str = ""
    has_viewport: bool = False
    json_ld_types: list[str] = field(default_factory=list)
    is_noindex: bool = False
    has_canonical: bool = False
    is_https: bool = False
    list_table_count: int = 0
    fetched_via: str = "playwright"  # playwright / httpx


class CrawlError(Exception):
    pass


def _domain_of(url: str) -> str:
    return urlparse(url).netloc


def _default_user_agent() -> str:
    return settings.user_agent or "SEO-LLMO-Diagnostic-Bot/1.0 (+https://example.co.jp/contact)"


async def _robots_allows(url: str, user_agent: str) -> bool:
    if not settings.respect_robots:
        return True

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(robots_url, headers={"User-Agent": user_agent})
        if resp.status_code >= 400:
            return True  # 取得不可時は許可扱い
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(user_agent, url)
    except (httpx.HTTPError, Exception):
        return True  # 取得不可時は許可扱い


def _parse_html(url: str, html: str, fetched_via: str) -> CrawlResult:
    soup = BeautifulSoup(html, "html.parser")
    result = CrawlResult(url=url, fetched_via=fetched_via)

    result.is_https = url.startswith("https://")

    if soup.title and soup.title.string:
        result.title = soup.title.string.strip()

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        result.meta_description = meta_desc["content"].strip()

    viewport = soup.find("meta", attrs={"name": "viewport"})
    result.has_viewport = viewport is not None

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta and robots_meta.get("content"):
        result.is_noindex = "noindex" in robots_meta["content"].lower()

    canonical = soup.find("link", attrs={"rel": "canonical"})
    result.has_canonical = canonical is not None and bool(canonical.get("href"))

    result.h1_list = [h.get_text(strip=True) for h in soup.find_all("h1")]
    result.h2_list = [h.get_text(strip=True) for h in soup.find_all("h2")]

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@type" in item:
                type_value = item["@type"]
                types = type_value if isinstance(type_value, list) else [type_value]
                result.json_ld_types.extend(types)

    result.list_table_count = len(soup.find_all(["ul", "ol", "table"]))

    body = soup.find("body")
    if body:
        for tag in body.find_all(["script", "style", "noscript"]):
            tag.decompose()
        result.body_text = body.get_text(separator="\n", strip=True)

        first_p = body.find("p")
        if first_p:
            result.first_paragraph = first_p.get_text(strip=True)

    return result


async def _fetch_with_playwright(url: str, user_agent: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(user_agent=user_agent)
            await page.goto(url, timeout=TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
            return await page.content()
        finally:
            await browser.close()


async def _fetch_with_httpx(url: str, user_agent: str) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": user_agent})
        resp.raise_for_status()
        return resp.text


async def fetch(url: str) -> CrawlResult:
    cache_key = f"crawl:{url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return CrawlResult(**cached)

    user_agent = _default_user_agent()

    if not await _robots_allows(url, user_agent):
        raise CrawlError(f"robots.txt により診断対象URLへのアクセスが禁止されています: {url}")

    domain = _domain_of(url)
    semaphore = _domain_semaphores[domain]

    async with semaphore:
        try:
            html = await _fetch_with_playwright(url, user_agent)
            result = _parse_html(url, html, fetched_via="playwright")
        except Exception:
            try:
                html = await _fetch_with_httpx(url, user_agent)
                result = _parse_html(url, html, fetched_via="httpx")
            except Exception as exc:
                raise CrawlError(f"クロールに失敗しました: {url} ({exc})") from exc

    cache.set(cache_key, result.__dict__, ttl_seconds=settings.crawl_cache_ttl)
    return result
