from unittest.mock import AsyncMock, patch

import pytest

from app.clients import backlinks

SAMPLE_RESPONSE = {
    "tasks": [
        {
            "result": [
                {
                    "referring_domains": 42,
                    "rank": 35,
                    "backlinks": 300,
                    "backlinks_dofollow": 210,
                }
            ]
        }
    ]
}

EMPTY_RESULT_RESPONSE = {"tasks": [{"result": [None]}]}


@pytest.fixture(autouse=True)
def _isolated_cache():
    store: dict[str, object] = {}

    def fake_get(key):
        return store.get(key)

    def fake_set(key, value, ttl_seconds):
        store[key] = value

    with patch.object(backlinks.cache, "get", side_effect=fake_get), \
         patch.object(backlinks.cache, "set", side_effect=fake_set):
        yield


def test_domain_of_strips_www_and_scheme():
    assert backlinks._domain_of("https://www.example.co.jp/path") == "example.co.jp"
    assert backlinks._domain_of("example.co.jp") == "example.co.jp"


async def test_get_summary_parses_response():
    with patch.object(backlinks, "_fetch_one", new=AsyncMock(return_value=SAMPLE_RESPONSE)):
        result = await backlinks.get_summary("https://example.co.jp/")

    assert result.domain == "example.co.jp"
    assert result.referring_domains == 42
    assert result.rank == 35
    assert result.backlinks == 300
    assert result.dofollow_ratio == pytest.approx(0.7)


async def test_get_summary_uses_cache_on_second_call():
    with patch.object(backlinks, "_fetch_one", new=AsyncMock(return_value=SAMPLE_RESPONSE)) as mock_fetch:
        await backlinks.get_summary("https://example.co.jp/")
        await backlinks.get_summary("https://example.co.jp/")

    assert mock_fetch.call_count == 1


async def test_get_summary_raises_on_empty_result():
    with patch.object(backlinks, "_fetch_one", new=AsyncMock(return_value=EMPTY_RESULT_RESPONSE)):
        with pytest.raises(backlinks.BacklinksError):
            await backlinks.get_summary("https://example.co.jp/")


async def test_fetch_one_raises_when_credentials_missing():
    from dataclasses import replace

    empty_settings = replace(backlinks.settings, dataforseo_login="", dataforseo_password="")
    with patch.object(backlinks, "settings", empty_settings):
        with pytest.raises(backlinks.BacklinksError):
            await backlinks._fetch_one("example.co.jp")


async def test_get_summaries_returns_none_for_failed_domain():
    async def fake_get_summary(target):
        if target == "https://fail.co.jp/":
            raise backlinks.BacklinksError("boom")
        return backlinks.BacklinksResult(domain="example.co.jp", referring_domains=10, rank=20)

    with patch.object(backlinks, "get_summary", side_effect=fake_get_summary):
        results = await backlinks.get_summaries(["https://example.co.jp/", "https://fail.co.jp/"])

    assert results["https://example.co.jp/"].referring_domains == 10
    assert results["https://fail.co.jp/"] is None
