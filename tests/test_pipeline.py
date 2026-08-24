from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline
from app.clients.crawler import CrawlError, CrawlResult
from app.clients.psi import PsiResult
from app.clients.serp import SerpResult
from app.jobs import Job


def _page() -> CrawlResult:
    return CrawlResult(
        url="https://example.co.jp/",
        title="サンプルタイトル",
        meta_description="説明文です。" * 10,
        h1_list=["メイン見出し"],
        h2_list=["小見出し"],
        body_text="本文です。" * 300,
        first_paragraph="これは冒頭段落です。40文字から200文字の範囲に収まるように調整した文章です。",
        has_viewport=True,
        json_ld_types=["FAQPage"],
        is_https=True,
        has_canonical=True,
        list_table_count=3,
    )


def test_pick_competitor_candidates_ranks_by_outrank_count():
    serp_results = [
        SerpResult(
            keyword="k1", device="desktop", rank=5,
            organic_domains=["a.co.jp", "b.co.jp", "example.co.jp", "c.co.jp"],
        ),
        SerpResult(
            keyword="k2", device="desktop", rank=3,
            organic_domains=["a.co.jp", "example.co.jp"],
        ),
    ]
    result = pipeline._pick_competitor_candidates("example.co.jp", serp_results, excluded_domains=set())
    assert result[0] == "a.co.jp"  # 2キーワードとも自社より上位
    assert "example.co.jp" not in result


def test_pick_competitor_candidates_excludes_domains():
    serp_results = [
        SerpResult(keyword="k1", device="desktop", rank=5, organic_domains=["a.co.jp", "example.co.jp"]),
    ]
    result = pipeline._pick_competitor_candidates("example.co.jp", serp_results, excluded_domains={"a.co.jp"})
    assert result == []


def test_pick_competitor_candidates_counts_all_when_own_rank_missing():
    serp_results = [
        SerpResult(keyword="k1", device="desktop", rank=None, organic_domains=["a.co.jp", "example.co.jp"]),
    ]
    result = pipeline._pick_competitor_candidates("example.co.jp", serp_results, excluded_domains=set())
    assert result == ["a.co.jp"]


def test_pick_competitor_candidates_excludes_domains_ranked_below_5th():
    serp_results = [
        SerpResult(
            keyword="k1", device="desktop", rank=None,
            organic_domains=["a.co.jp", "b.co.jp", "c.co.jp", "d.co.jp", "e.co.jp", "f.co.jp", "example.co.jp"],
        ),
    ]
    result = pipeline._pick_competitor_candidates("example.co.jp", serp_results, excluded_domains=set(), pool_size=3)
    assert "f.co.jp" not in result
    assert set(result) == {"a.co.jp", "b.co.jp", "c.co.jp"}  # pool_size=3 で上位3件のみ


def test_pick_competitor_candidates_pool_size_larger_than_max_competitors():
    serp_results = [
        SerpResult(
            keyword="k1", device="desktop", rank=None,
            organic_domains=["a.co.jp", "b.co.jp", "c.co.jp", "d.co.jp", "e.co.jp"],
        ),
    ]
    result = pipeline._pick_competitor_candidates("example.co.jp", serp_results, excluded_domains=set())
    assert set(result) == {"a.co.jp", "b.co.jp", "c.co.jp", "d.co.jp", "e.co.jp"}  # 既定pool_size=10なので全件残る


def test_is_auto_excluded_domain_matches_official_and_platform_patterns():
    assert pipeline._is_auto_excluded_domain("city.example.go.jp") is True
    assert pipeline._is_auto_excluded_domain("support.google.com") is True
    assert pipeline._is_auto_excluded_domain("docomo.ne.jp") is True
    assert pipeline._is_auto_excluded_domain("youtube.com") is True
    assert pipeline._is_auto_excluded_domain("normal-competitor.co.jp") is False


def test_is_auto_excluded_page_by_internal_link_count():
    page = CrawlResult(url="https://x.co.jp/", body_text="十分な本文。" * 50, internal_link_count=100)
    assert pipeline._is_auto_excluded_page(page) is True


def test_is_auto_excluded_page_by_short_body():
    page = CrawlResult(url="https://x.co.jp/", body_text="短い", internal_link_count=5)
    assert pipeline._is_auto_excluded_page(page) is True


def test_is_auto_excluded_page_false_for_normal_page():
    page = CrawlResult(url="https://x.co.jp/", body_text="十分な本文です。" * 30, internal_link_count=20)
    assert pipeline._is_auto_excluded_page(page) is False


async def test_run_audit_emits_error_and_stops_on_crawl_failure():
    job = Job(id="job1", created_by="user@example.co.jp")
    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(side_effect=CrawlError("boom"))):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"])

    assert job.status == "error"
    assert job.events[-1]["type"] == "error"


async def test_run_audit_completes_with_partial_failures_and_warnings():
    job = Job(id="job2", created_by="user@example.co.jp")
    page = _page()
    competitor_page = _page()

    async def fake_crawl(url):
        if url == "https://example.co.jp/":
            return page
        return competitor_page

    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(side_effect=fake_crawl)), \
         patch.object(pipeline.llm, "extract_keywords", new=AsyncMock(return_value=["キーワード1"])), \
         patch.object(pipeline.serp, "search_all", new=AsyncMock(return_value=[
             SerpResult(keyword="キーワード1", device="desktop", rank=2, organic_domains=["a.co.jp", "example.co.jp"]),
         ])), \
         patch.object(pipeline.psi, "analyze_both", new=AsyncMock(return_value={"mobile": None, "desktop": PsiResult(strategy="desktop", performance_score=0.9)})), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()), \
         patch.object(pipeline.llm, "improvement_advice", new=AsyncMock(return_value="アドバイス文です。")), \
         patch.object(pipeline.llm, "category_advice", new=AsyncMock(return_value="カテゴリアドバイス文です。")), \
         patch.object(pipeline.llm, "explain_competitors", new=AsyncMock(return_value="解説文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"])

    assert job.status == "done"
    done_event = job.events[-1]
    assert done_event["type"] == "done"
    result = done_event["result"]
    assert result["domain"] == "example.co.jp"
    assert result["competitors"] == ["a.co.jp"]
    assert result["improvement_advice"] == "アドバイス文です。"
    assert any("PageSpeed Insights（モバイル）" in w for w in result["warnings"])
    assert result["score"]["total_score"] > 0
    assert result["score"]["rubric_version"]
    assert all(c["advice"] == "カテゴリアドバイス文です。" for c in result["score"]["categories"] if c["measured"])
    assert result["serp_results"] == [
        {"keyword": "キーワード1", "device": "desktop", "rank": 2, "ai_overview_shown": False, "ai_overview_cited": False}
    ]
    assert set(result["competitor_comparison"].keys()) == {"example.co.jp", "a.co.jp"}
    assert result["competitor_explanation"] == "解説文です。"


async def test_run_competitors_filters_out_auto_excluded_candidates():
    """候補にドメインパターン一致・内部リンク過多・本文極端に短いページが混じっていても、
    自動除外され、正常な候補のみが最終的な競合ドメインとして残ることを確認する。
    """
    job = Job(id="job-auto-exclude", created_by="user@example.co.jp")
    serp_results = [
        SerpResult(
            keyword="k1", device="desktop", rank=None,
            organic_domains=[
                "support.google.com",  # ドメインパターンで自動除外
                "portal-heavy.co.jp",  # 実クロール後、内部リンク過多で自動除外
                "empty-page.co.jp",    # 実クロール後、本文極端に短く自動除外
                "normal-competitor.co.jp",  # 正常な競合として残る
            ],
        ),
    ]

    async def fake_crawl(url):
        if "portal-heavy" in url:
            return CrawlResult(url=url, body_text="十分な本文。" * 50, internal_link_count=150)
        if "empty-page" in url:
            return CrawlResult(url=url, body_text="短い", internal_link_count=5)
        return CrawlResult(url=url, body_text="十分な本文です。" * 50, internal_link_count=20)

    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(side_effect=fake_crawl)), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()):
        result = await pipeline._run_competitors(job, "example.co.jp", serp_results, warnings=[])

    assert result == ["normal-competitor.co.jp"]


async def test_run_audit_continues_when_serp_fails_entirely():
    job = Job(id="job3", created_by="user@example.co.jp")
    page = _page()

    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(return_value=page)), \
         patch.object(pipeline.llm, "extract_keywords", new=AsyncMock(return_value=["キーワード1"])), \
         patch.object(pipeline.serp, "search_all", new=AsyncMock(side_effect=Exception("serp down"))), \
         patch.object(pipeline.psi, "analyze_both", new=AsyncMock(return_value={"mobile": None, "desktop": None})), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()), \
         patch.object(pipeline.llm, "improvement_advice", new=AsyncMock(return_value="アドバイス文です。")), \
         patch.object(pipeline.llm, "category_advice", new=AsyncMock(return_value="カテゴリアドバイス文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"])

    assert job.status == "done"
    result = job.events[-1]["result"]
    assert result["competitors"] == []
    assert any("検索順位の取得に失敗しました" in w for w in result["warnings"])
    assert len(result["score"]["categories"]) == 3


async def test_run_audit_skips_llm_keyword_extraction_when_keywords_given():
    job = Job(id="job4", created_by="user@example.co.jp")
    page = _page()

    mock_extract = AsyncMock(return_value=["自動推定キーワード"])
    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(return_value=page)), \
         patch.object(pipeline.llm, "extract_keywords", new=mock_extract), \
         patch.object(pipeline.serp, "search_all", new=AsyncMock(return_value=[])), \
         patch.object(pipeline.psi, "analyze_both", new=AsyncMock(return_value={"mobile": None, "desktop": None})), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()), \
         patch.object(pipeline.llm, "improvement_advice", new=AsyncMock(return_value="アドバイス文です。")), \
         patch.object(pipeline.llm, "category_advice", new=AsyncMock(return_value="カテゴリアドバイス文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"], keywords=["手動キーワード"])

    mock_extract.assert_not_called()
    result = job.events[-1]["result"]
    assert result["keywords"] == ["手動キーワード"]
