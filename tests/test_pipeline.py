from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline
from app.clients.backlinks import BacklinksResult
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


def test_pick_competitors_ranks_by_outrank_count():
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
    result = pipeline._pick_competitors("example.co.jp", serp_results, excluded_domains=set())
    assert result[0] == "a.co.jp"  # 2キーワードとも自社より上位
    assert "example.co.jp" not in result


def test_pick_competitors_excludes_domains():
    serp_results = [
        SerpResult(keyword="k1", device="desktop", rank=5, organic_domains=["a.co.jp", "example.co.jp"]),
    ]
    result = pipeline._pick_competitors("example.co.jp", serp_results, excluded_domains={"a.co.jp"})
    assert result == []


def test_pick_competitors_counts_all_when_own_rank_missing():
    serp_results = [
        SerpResult(keyword="k1", device="desktop", rank=None, organic_domains=["a.co.jp", "example.co.jp"]),
    ]
    result = pipeline._pick_competitors("example.co.jp", serp_results, excluded_domains=set())
    assert result == ["a.co.jp"]


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
         patch.object(pipeline.backlinks, "get_summaries", new=AsyncMock(return_value={
             "https://example.co.jp/": BacklinksResult(domain="example.co.jp", referring_domains=20, rank=25),
             "a.co.jp": None,
         })), \
         patch.object(pipeline.llm, "sales_summary", new=AsyncMock(return_value="要約文です。")), \
         patch.object(pipeline.llm, "explain_competitors", new=AsyncMock(return_value="解説文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"])

    assert job.status == "done"
    done_event = job.events[-1]
    assert done_event["type"] == "done"
    result = done_event["result"]
    assert result["domain"] == "example.co.jp"
    assert result["competitors"] == ["a.co.jp"]
    assert result["sales_summary"] == "要約文です。"
    assert any("PageSpeed Insights（モバイル）" in w for w in result["warnings"])
    assert result["score"]["total_score"] > 0
    assert result["score"]["rubric_version"]
    assert result["serp_results"] == [
        {"keyword": "キーワード1", "device": "desktop", "rank": 2, "ai_overview_shown": False, "ai_overview_cited": False}
    ]
    assert result["competitor_backlinks"] == {"a.co.jp": None}
    assert set(result["competitor_comparison"].keys()) == {"example.co.jp", "a.co.jp"}
    assert result["competitor_comparison"]["a.co.jp"]["referring_domains"] is None
    assert result["competitor_explanation"] == "解説文です。"


async def test_run_audit_continues_when_serp_fails_entirely():
    job = Job(id="job3", created_by="user@example.co.jp")
    page = _page()

    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(return_value=page)), \
         patch.object(pipeline.llm, "extract_keywords", new=AsyncMock(return_value=["キーワード1"])), \
         patch.object(pipeline.serp, "search_all", new=AsyncMock(side_effect=Exception("serp down"))), \
         patch.object(pipeline.psi, "analyze_both", new=AsyncMock(return_value={"mobile": None, "desktop": None})), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()), \
         patch.object(pipeline.backlinks, "get_summaries", new=AsyncMock(return_value={
             "https://example.co.jp/": None,
         })), \
         patch.object(pipeline.llm, "sales_summary", new=AsyncMock(return_value="要約文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"])

    assert job.status == "done"
    result = job.events[-1]["result"]
    assert result["competitors"] == []
    assert any("検索順位の取得に失敗しました" in w for w in result["warnings"])
    assert result["score"]["categories"][3]["measured"] is False  # authority


async def test_run_audit_skips_llm_keyword_extraction_when_keywords_given():
    job = Job(id="job4", created_by="user@example.co.jp")
    page = _page()

    mock_extract = AsyncMock(return_value=["自動推定キーワード"])
    with patch.object(pipeline, "crawl_fetch", new=AsyncMock(return_value=page)), \
         patch.object(pipeline.llm, "extract_keywords", new=mock_extract), \
         patch.object(pipeline.serp, "search_all", new=AsyncMock(return_value=[])), \
         patch.object(pipeline.psi, "analyze_both", new=AsyncMock(return_value={"mobile": None, "desktop": None})), \
         patch.object(pipeline.db, "get_excluded_domains", return_value=set()), \
         patch.object(pipeline.backlinks, "get_summaries", new=AsyncMock(return_value={
             "https://example.co.jp/": None,
         })), \
         patch.object(pipeline.llm, "sales_summary", new=AsyncMock(return_value="要約文です。")):
        await pipeline.run_audit(job, "https://example.co.jp/", ["desktop"], keywords=["手動キーワード"])

    mock_extract.assert_not_called()
    result = job.events[-1]["result"]
    assert result["keywords"] == ["手動キーワード"]
