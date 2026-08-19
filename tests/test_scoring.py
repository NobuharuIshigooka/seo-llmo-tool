from app import scoring
from app.clients.crawler import CrawlResult
from app.clients.psi import PsiResult
from app.clients.serp import SerpResult


def _good_page() -> CrawlResult:
    body = "SEO対策についての解説です。" * 200  # 十分な文字数
    return CrawlResult(
        url="https://example.co.jp/",
        title="SEO対策の完全ガイド｜自社サービス",
        meta_description="このページではSEO対策の基本から応用までを60文字以上でしっかり解説しています。",
        h1_list=["SEO対策の完全ガイドとは"],
        h2_list=["SEO対策の方法", "費用はいくら？", "他社との違い"],
        body_text=body,
        first_paragraph="SEO対策とは、検索エンジンでの表示順位を高めるための一連の施策のことです。",
        has_viewport=True,
        json_ld_types=["FAQPage"],
        is_noindex=False,
        has_canonical=True,
        is_https=True,
        list_table_count=6,
    )


def _empty_page() -> CrawlResult:
    return CrawlResult(url="https://weak.example.co.jp/")


def _psi(score: float) -> PsiResult:
    return PsiResult(strategy="mobile", performance_score=score)


def test_score_technical_seo_full_marks_when_all_signals_present():
    page = _good_page()
    result = scoring.score_technical_seo(page, _psi(1.0), _psi(1.0))
    assert result.score == 30


def test_score_technical_seo_only_crawlability_when_psi_missing_and_no_signals():
    # is_noindex=False（未検出=noindexタグなし）が唯一加点される項目
    page = _empty_page()
    result = scoring.score_technical_seo(page, None, None)
    assert result.score == 8 / scoring.TECHNICAL_TOTAL * 30


def test_ai_overview_neutral_when_not_shown():
    score, evidence = scoring._ai_overview_score([])
    assert score == 10  # 20点の50%
    assert "中立評価" in evidence


def test_ai_overview_full_when_all_cited():
    results = [
        SerpResult(keyword="k1", device="desktop", ai_overview_shown=True, ai_overview_cited=True),
        SerpResult(keyword="k2", device="desktop", ai_overview_shown=True, ai_overview_cited=True),
    ]
    score, _ = scoring._ai_overview_score(results)
    assert score == 20


def test_compute_total_score_excludes_unmeasured_category():
    categories = [
        scoring.CategoryScore("technical_seo", "テクニカルSEO", 30, 30),
        scoring.CategoryScore("content_seo", "コンテンツSEO", 30, 30),
        scoring.CategoryScore("llmo", "LLMO対応度", 0, 25, measured=False),
    ]
    total, band = scoring.compute_total_score(categories)
    assert total == 100
    assert band == "good"


def test_band_thresholds():
    assert scoring._band_of(80) == "good"
    assert scoring._band_of(60) == "fair"
    assert scoring._band_of(40) == "weak"
    assert scoring._band_of(39) == "poor"


def test_compute_improvements_picks_top3():
    categories = [
        scoring.CategoryScore("technical_seo", "テクニカルSEO", 0, 30, items=[
            scoring.ScoreItem("表示速度（モバイル）", 0, 40 / 100 * 30, "PSI 0点"),
        ]),
        scoring.CategoryScore("content_seo", "コンテンツSEO", 0, 30, items=[
            scoring.ScoreItem("本文の情報量", 0, 30 / 100 * 30, "本文0文字"),
        ]),
    ]
    improvements = scoring.compute_improvements(categories)
    assert len(improvements) <= 3


def test_compute_score_is_deterministic_for_same_input():
    page = _good_page()
    keywords = ["SEO対策", "検索エンジン"]
    serp_results = [SerpResult(keyword="SEO対策", device="desktop", rank=3, ai_overview_shown=True, ai_overview_cited=True)]
    psi_mobile = _psi(0.8)
    psi_desktop = _psi(0.9)

    result1 = scoring.compute_score(page, keywords, serp_results, psi_mobile, psi_desktop)
    result2 = scoring.compute_score(page, keywords, serp_results, psi_mobile, psi_desktop)

    assert result1.total_score == result2.total_score
    assert result1.band == result2.band
    assert [c.score for c in result1.categories] == [c.score for c in result2.categories]
    assert result1.rubric_version == scoring.RUBRIC_VERSION


def test_compute_score_includes_rubric_version():
    page = _empty_page()
    result = scoring.compute_score(page, [], [], None, None)
    assert result.rubric_version == scoring.RUBRIC_VERSION
    assert len(result.categories) == 3
