from unittest.mock import AsyncMock, patch

import pytest

from app.clients import psi

SAMPLE_RESPONSE = {
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.62}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 2500.0},
            "cumulative-layout-shift": {"numericValue": 0.05},
            "total-blocking-time": {"numericValue": 150.0},
        },
    },
    "loadingExperience": {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {}}},
}


@pytest.fixture(autouse=True)
def _isolated_cache():
    store: dict[str, object] = {}

    def fake_get(key):
        return store.get(key)

    def fake_set(key, value, ttl_seconds):
        store[key] = value

    with patch.object(psi.cache, "get", side_effect=fake_get), \
         patch.object(psi.cache, "set", side_effect=fake_set):
        yield


async def test_analyze_extracts_metrics():
    with patch.object(psi, "_fetch", new=AsyncMock(return_value=SAMPLE_RESPONSE)):
        result = await psi.analyze("https://example.co.jp/", "mobile")

    assert result.strategy == "mobile"
    assert result.performance_score == 0.62
    assert result.lcp_ms == 2500.0
    assert result.cls == 0.05
    assert result.tbt_ms == 150.0
    assert result.has_field_data is True


async def test_analyze_uses_cache_on_second_call():
    with patch.object(psi, "_fetch", new=AsyncMock(return_value=SAMPLE_RESPONSE)) as mock_fetch:
        await psi.analyze("https://example.co.jp/", "mobile")
        await psi.analyze("https://example.co.jp/", "mobile")

    assert mock_fetch.call_count == 1


async def test_fetch_raises_psi_error_on_http_failure():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("timeout"))):
        with pytest.raises(psi.PsiError):
            await psi._fetch("https://example.co.jp/", "mobile")


async def test_analyze_both_returns_none_for_failed_strategy():
    async def fake_analyze(url, strategy):
        if strategy == "mobile":
            return psi.PsiResult(strategy="mobile", performance_score=0.5)
        raise psi.PsiError("boom")

    with patch.object(psi, "analyze", side_effect=fake_analyze):
        results = await psi.analyze_both("https://example.co.jp/")

    assert results["mobile"].performance_score == 0.5
    assert results["desktop"] is None
