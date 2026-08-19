from unittest.mock import AsyncMock, patch

import pytest

from app.clients import crawler

SAMPLE_HTML = """
<html>
<head>
  <title>サンプルタイトル</title>
  <meta name="description" content="これはテスト用の説明文です。">
  <meta name="keywords" content="キーワード1, キーワード2 ,キーワード3">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="canonical" href="https://example.co.jp/">
  <script type="application/ld+json">{"@type": "FAQPage"}</script>
</head>
<body>
  <h1>メイン見出し</h1>
  <h2>小見出し1</h2>
  <h2>小見出し2</h2>
  <p>これは最初の段落です。</p>
  <ul><li>項目1</li></ul>
  <table><tr><td>表</td></tr></table>
  <img src="/a.jpg" alt="施術風景の写真">
  <img src="/b.jpg" alt="">
  <a href="/about">会社概要</a>
  <a href="https://example.co.jp/contact">お問い合わせ</a>
  <a href="https://other.co.jp/">外部サイト</a>
  <a href="#section">ページ内リンク</a>
  <a href="mailto:info@example.co.jp">メール</a>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _isolated_cache():
    """crawler.py が使うキャッシュをテスト間で分離するプロセス内スタブ。"""
    store: dict[str, object] = {}

    def fake_get(key):
        return store.get(key)

    def fake_set(key, value, ttl_seconds):
        store[key] = value

    with patch.object(crawler.cache, "get", side_effect=fake_get), \
         patch.object(crawler.cache, "set", side_effect=fake_set):
        yield


async def test_fetch_parses_html_via_playwright():
    with patch.object(crawler, "_fetch_with_playwright", new=AsyncMock(return_value=SAMPLE_HTML)):
        result = await crawler.fetch("https://example.co.jp/")

    assert result.fetched_via == "playwright"
    assert result.title == "サンプルタイトル"
    assert result.meta_description == "これはテスト用の説明文です。"
    assert result.has_viewport is True
    assert result.has_canonical is True
    assert result.is_https is True
    assert result.h1_list == ["メイン見出し"]
    assert result.h2_list == ["小見出し1", "小見出し2"]
    assert result.json_ld_types == ["FAQPage"]
    assert result.list_table_count == 2
    assert result.first_paragraph == "これは最初の段落です。"
    assert result.internal_link_count == 2
    assert result.meta_keywords == ["キーワード1", "キーワード2", "キーワード3"]
    assert result.alt_texts == ["施術風景の写真"]
    assert result.internal_link_texts == ["会社概要", "お問い合わせ"]


async def test_fetch_falls_back_to_httpx_when_playwright_fails():
    with patch.object(crawler, "_fetch_with_playwright", new=AsyncMock(side_effect=Exception("boom"))), \
         patch.object(crawler, "_fetch_with_httpx", new=AsyncMock(return_value=SAMPLE_HTML)):
        result = await crawler.fetch("https://example.co.jp/")

    assert result.fetched_via == "httpx"
    assert result.title == "サンプルタイトル"


async def test_fetch_raises_crawl_error_when_both_fail():
    with patch.object(crawler, "_fetch_with_playwright", new=AsyncMock(side_effect=Exception("pw"))), \
         patch.object(crawler, "_fetch_with_httpx", new=AsyncMock(side_effect=Exception("httpx"))):
        with pytest.raises(crawler.CrawlError):
            await crawler.fetch("https://example.co.jp/")


async def test_fetch_uses_cache_on_second_call():
    with patch.object(crawler, "_fetch_with_playwright", new=AsyncMock(return_value=SAMPLE_HTML)) as mock_pw:
        await crawler.fetch("https://example.co.jp/")
        await crawler.fetch("https://example.co.jp/")

    assert mock_pw.call_count == 1


async def test_robots_disallow_blocks_fetch():
    class FakeResp:
        status_code = 200
        text = "User-agent: *\nDisallow: /"

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResp())):
        with pytest.raises(crawler.CrawlError):
            await crawler.fetch("https://blocked.example.co.jp/")


async def test_robots_fetch_failure_defaults_to_allowed():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("network error"))):
        allowed = await crawler._robots_allows("https://example.co.jp/", "TestBot/1.0")

    assert allowed is True
