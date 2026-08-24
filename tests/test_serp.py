from unittest.mock import AsyncMock, patch

import pytest

from app.clients import serp

SAMPLE_RESPONSE = {
    "organic_results": [
        {"position": 1, "link": "https://competitor-a.co.jp/page"},
        {"position": 2, "link": "https://www.example.co.jp/"},
        {"position": 3, "link": "https://competitor-b.co.jp/"},
    ],
    "ai_overview": {
        "references": [
            {"link": "https://www.example.co.jp/"},
        ]
    },
}

SAMPLE_RESPONSE_NO_AI_OVERVIEW = {
    "organic_results": [
        {"position": 5, "link": "https://competitor-a.co.jp/"},
    ],
}


@pytest.fixture(autouse=True)
def _isolated_cache():
    store: dict[str, object] = {}

    def fake_get(key):
        return store.get(key)

    def fake_set(key, value, ttl_seconds):
        store[key] = value

    with patch.object(serp.cache, "get", side_effect=fake_get), \
         patch.object(serp.cache, "set", side_effect=fake_set):
        yield


def test_normalize_domain_strips_www():
    assert serp._normalize_domain("https://www.example.co.jp/path") == "example.co.jp"
    assert serp._normalize_domain("https://example.co.jp/path") == "example.co.jp"


async def test_search_finds_own_rank_and_ai_overview_citation():
    provider = serp.SerpApiProvider(api_key="dummy")
    with patch.object(provider, "_fetch", new=AsyncMock(return_value=SAMPLE_RESPONSE)):
        result = await provider.search("キーワード", "desktop", "example.co.jp")

    assert result.rank == 2
    assert result.ai_overview_shown is True
    assert result.ai_overview_cited is True
    assert result.organic_domains == ["competitor-a.co.jp", "example.co.jp", "competitor-b.co.jp"]


async def test_search_handles_no_ai_overview():
    provider = serp.SerpApiProvider(api_key="dummy")
    with patch.object(provider, "_fetch", new=AsyncMock(return_value=SAMPLE_RESPONSE_NO_AI_OVERVIEW)):
        result = await provider.search("キーワード", "mobile", "example.co.jp")

    assert result.rank is None
    assert result.ai_overview_shown is False
    assert result.ai_overview_cited is False


async def test_search_uses_cache_on_second_call():
    provider = serp.SerpApiProvider(api_key="dummy")
    with patch.object(provider, "_fetch", new=AsyncMock(return_value=SAMPLE_RESPONSE)) as mock_fetch:
        await provider.search("キーワード", "desktop", "example.co.jp")
        await provider.search("キーワード", "desktop", "example.co.jp")

    assert mock_fetch.call_count == 1


async def test_fetch_raises_serp_error_on_http_failure():
    provider = serp.SerpApiProvider(api_key="dummy")
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("network error"))):
        with pytest.raises(serp.SerpError):
            await provider._fetch("キーワード", "desktop")


async def test_search_all_skips_failed_and_returns_successful():
    async def fake_search(keyword, device, own_domain):
        if keyword == "fail":
            raise serp.SerpError("boom")
        return serp.SerpResult(keyword=keyword, device=device, rank=1)

    with patch.object(serp, "get_provider", return_value=type("P", (), {"search": staticmethod(fake_search)})()):
        results = await serp.search_all(["ok", "fail"], "example.co.jp", ["desktop"])

    assert len(results) == 1
    assert results[0].keyword == "ok"


def test_get_provider_raises_on_unknown_provider():
    from dataclasses import replace

    unknown_settings = replace(serp.settings, serp_provider="unknown")
    with patch.object(serp, "settings", unknown_settings):
        with pytest.raises(serp.SerpError):
            serp.get_provider()


def test_get_provider_returns_dataforseo_provider():
    from dataclasses import replace

    dataforseo_settings = replace(serp.settings, serp_provider="dataforseo")
    with patch.object(serp, "settings", dataforseo_settings):
        provider = serp.get_provider()

    assert isinstance(provider, serp.DataForSeoProvider)


DATAFORSEO_SAMPLE_RESULT = {
    "items": [
        {
            "type": "ai_overview",
            "items": [
                {
                    "type": "ai_overview_element",
                    "references": [
                        {"type": "ai_overview_reference", "domain": "www.example.co.jp", "url": "https://www.example.co.jp/"},
                    ],
                },
            ],
        },
        {"type": "organic", "rank_absolute": 1, "url": "https://competitor-a.co.jp/page", "domain": "competitor-a.co.jp"},
        {"type": "organic", "rank_absolute": 2, "url": "https://www.example.co.jp/", "domain": "www.example.co.jp"},
        {"type": "organic", "rank_absolute": 3, "url": "https://competitor-b.co.jp/", "domain": "competitor-b.co.jp"},
    ],
}

DATAFORSEO_SAMPLE_RESULT_NO_AI_OVERVIEW = {
    "items": [
        {"type": "organic", "rank_absolute": 5, "url": "https://competitor-a.co.jp/", "domain": "competitor-a.co.jp"},
    ],
}


def _dataforseo_body(result: dict) -> dict:
    return {
        "status_code": 20000,
        "status_message": "Ok.",
        "tasks": [{"status_code": 20000, "status_message": "Ok.", "result": [result]}],
    }


async def test_dataforseo_search_finds_own_rank_and_ai_overview_citation():
    provider = serp.DataForSeoProvider(login="user", password="pass")
    with patch.object(provider, "_fetch", new=AsyncMock(return_value=DATAFORSEO_SAMPLE_RESULT)):
        result = await provider.search("キーワード", "desktop", "example.co.jp")

    assert result.rank == 2
    assert result.ai_overview_shown is True
    assert result.ai_overview_cited is True
    assert result.organic_domains == ["competitor-a.co.jp", "example.co.jp", "competitor-b.co.jp"]


async def test_dataforseo_search_handles_no_ai_overview():
    provider = serp.DataForSeoProvider(login="user", password="pass")
    with patch.object(provider, "_fetch", new=AsyncMock(return_value=DATAFORSEO_SAMPLE_RESULT_NO_AI_OVERVIEW)):
        result = await provider.search("キーワード", "mobile", "example.co.jp")

    assert result.rank is None
    assert result.ai_overview_shown is False
    assert result.ai_overview_cited is False


async def test_dataforseo_fetch_raises_serp_error_on_task_error():
    provider = serp.DataForSeoProvider(login="user", password="pass")

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "status_code": 20000,
                "status_message": "Ok.",
                "tasks": [{"status_code": 40501, "status_message": "Invalid Field"}],
            }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResp())):
        with pytest.raises(serp.SerpError):
            await provider._fetch("キーワード", "desktop")


async def test_dataforseo_fetch_raises_serp_error_on_http_failure():
    provider = serp.DataForSeoProvider(login="user", password="pass")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("network error"))):
        with pytest.raises(serp.SerpError):
            await provider._fetch("キーワード", "desktop")


async def test_get_account_info_returns_parsed_fields():
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "plan_searches_left": 1234,
                "searches_per_month": 5000,
                "this_month_usage": 3766,
                "account_email": "ops@example.co.jp",
            }

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResp())):
        info = await serp.get_account_info()

    assert info == {
        "plan_searches_left": 1234,
        "searches_per_month": 5000,
        "this_month_usage": 3766,
        "account_email": "ops@example.co.jp",
    }


async def test_get_account_info_returns_none_on_failure():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("network error"))):
        info = await serp.get_account_info()

    assert info is None


async def test_get_account_info_uses_cache_on_second_call():
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"plan_searches_left": 1, "searches_per_month": 1, "this_month_usage": 1, "account_email": "a@b.co.jp"}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResp())) as mock_get:
        await serp.get_account_info()
        await serp.get_account_info()

    assert mock_get.call_count == 1
