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
MAX_CONCURRENT_BROWSERS = 2  # Chromiumプロセスはメモリを多く使うため、ドメイン横断でも同時起動数を絞る

_domain_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(MAX_CONCURRENT_PER_DOMAIN)
)
_browser_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROWSERS)


@dataclass
class CrawlResult:
    url: str
    title: str = ""
    meta_description: str = ""
    h1_list: list[str] = field(default_factory=list)
    h2_list: list[str] = field(default_factory=list)
    h3_list: list[str] = field(default_factory=list)
    body_text: str = ""
    first_paragraph: str = ""
    meta_keywords: list[str] = field(default_factory=list)
    alt_texts: list[str] = field(default_factory=list)
    internal_link_texts: list[str] = field(default_factory=list)
    has_viewport: bool = False
    json_ld_types: list[str] = field(default_factory=list)
    has_json_ld_parse_error: bool = False  # application/ld+json はあるが json.loads に失敗したスクリプトが1つ以上ある
    is_noindex: bool = False
    has_canonical: bool = False
    is_https: bool = False
    list_table_count: int = 0
    internal_link_count: int = 0
    main_count: int = 0
    heading_count: int = 0  # h1〜h6の合計数
    content_block_count: int = 0  # article + section の合計数
    robots_txt_status: str = "missing"  # "allowed" / "disallowed_all" / "missing"
    has_sitemap: bool = False
    has_llms_txt: bool = False
    fetched_via: str = "playwright"  # playwright / httpx


class CrawlError(Exception):
    pass


def _domain_of(url: str) -> str:
    return urlparse(url).netloc


def _default_user_agent() -> str:
    return settings.user_agent or "SEO-LLMO-Diagnostic-Bot/1.0 (+https://example.co.jp/contact)"


async def _fetch_robots_txt(url: str, user_agent: str) -> tuple[bool, str]:
    """(診断対象URLへのアクセス可否, スコア用の設置状態) を返す。

    設置状態は "allowed"（設置あり・許可）/ "disallowed_all"（全面Disallow）/ "missing"（未設置・取得不可）の3種類。
    アクセス可否は取得不可時を許可扱いにする既存方針（docs/api-integrations.md 6章）を維持する。
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(robots_url, headers={"User-Agent": user_agent})
        if resp.status_code >= 400:
            return True, "missing"
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        allowed = parser.can_fetch(user_agent, url)
        # "*" に対して全パス拒否かどうかで全面Disallowを判定する
        all_disallowed = not parser.can_fetch(user_agent, f"{parsed.scheme}://{parsed.netloc}/")
        status = "disallowed_all" if all_disallowed else "allowed"
        return allowed, status
    except (httpx.HTTPError, Exception):
        return True, "missing"


async def _fetch_text_exists(url: str) -> bool:
    """sitemap.xml・llms.txt 用の軽量な存在確認。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
        return resp.status_code < 400
    except (httpx.HTTPError, Exception):
        return False


def _parse_html(url: str, html: str, fetched_via: str) -> CrawlResult:
    soup = BeautifulSoup(html, "html.parser")
    result = CrawlResult(url=url, fetched_via=fetched_via)

    result.is_https = url.startswith("https://")

    if soup.title and soup.title.string:
        result.title = soup.title.string.strip()

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        result.meta_description = meta_desc["content"].strip()

    meta_keywords = soup.find("meta", attrs={"name": "keywords"})
    if meta_keywords and meta_keywords.get("content"):
        result.meta_keywords = [
            kw.strip() for kw in meta_keywords["content"].split(",") if kw.strip()
        ]

    viewport = soup.find("meta", attrs={"name": "viewport"})
    result.has_viewport = viewport is not None

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta and robots_meta.get("content"):
        result.is_noindex = "noindex" in robots_meta["content"].lower()

    canonical = soup.find("link", attrs={"rel": "canonical"})
    result.has_canonical = canonical is not None and bool(canonical.get("href"))

    result.h1_list = [h.get_text(strip=True) for h in soup.find_all("h1")]
    result.h2_list = [h.get_text(strip=True) for h in soup.find_all("h2")]
    result.h3_list = [h.get_text(strip=True) for h in soup.find_all("h3")]
    result.heading_count = len(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]))

    result.main_count = len(soup.find_all("main"))
    result.content_block_count = len(soup.find_all(["article", "section"]))

    ld_json_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in ld_json_scripts:
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            result.has_json_ld_parse_error = True
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@type" in item:
                type_value = item["@type"]
                types = type_value if isinstance(type_value, list) else [type_value]
                result.json_ld_types.extend(types)

    result.list_table_count = len(soup.find_all(["ul", "ol", "table"]))

    page_domain = urlparse(url).netloc
    internal_links = 0
    internal_link_texts: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        link_domain = urlparse(href).netloc
        if not link_domain or link_domain == page_domain:
            internal_links += 1
            text = a.get_text(strip=True)
            if text:
                internal_link_texts.append(text)
    result.internal_link_count = internal_links
    result.internal_link_texts = internal_link_texts

    result.alt_texts = [
        img["alt"].strip() for img in soup.find_all("img", alt=True) if img["alt"].strip()
    ]

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
    async with _browser_semaphore:
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

    access_allowed, robots_txt_status = await _fetch_robots_txt(url, user_agent)
    if settings.respect_robots and not access_allowed:
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

    result.robots_txt_status = robots_txt_status

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    result.has_sitemap, result.has_llms_txt = await asyncio.gather(
        _fetch_text_exists(f"{origin}/sitemap.xml"),
        _fetch_text_exists(f"{origin}/llms.txt"),
    )

    cache.set(cache_key, result.__dict__, ttl_seconds=settings.crawl_cache_ttl)
    return result
