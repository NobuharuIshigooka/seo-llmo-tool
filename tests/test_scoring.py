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
        main_count=1,
        heading_count=5,
        content_block_count=3,
        robots_txt_status="allowed",
        has_sitemap=True,
        has_llms_txt=True,
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
    assert result.score == scoring.TECHNICAL_NOINDEX_POINTS / scoring.TECHNICAL_TOTAL * 30


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
        scoring.CategoryScore("llmo", "AI検索対応度", 0, 35, measured=False),
    ]
    total, band = scoring.compute_total_score(categories)
    assert total == 100
    assert band == "good"


def test_band_thresholds():
    assert scoring._band_of(80) == "good"
    assert scoring._band_of(60) == "fair"
    assert scoring._band_of(40) == "weak"
    assert scoring._band_of(39) == "poor"


def test_category_band_uses_achievement_ratio():
    assert scoring.category_band(scoring.CategoryScore("k", "l", 24, 30)) == "good"  # 80%
    assert scoring.category_band(scoring.CategoryScore("k", "l", 18, 30)) == "fair"  # 60%
    assert scoring.category_band(scoring.CategoryScore("k", "l", 12, 30)) == "weak"  # 40%
    assert scoring.category_band(scoring.CategoryScore("k", "l", 6, 30)) == "poor"  # 20%


def test_category_band_defaults_to_poor_when_unmeasured():
    assert scoring.category_band(scoring.CategoryScore("k", "l", 0, 25, measured=False)) == "poor"


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


def test_score_llmo_new_structure_items_full_marks_when_present():
    page = _good_page()  # main_count=1, heading_count=5, content_block_count=3, robots/sitemap/llms すべて設置済み
    result = scoring.score_llmo(page, [])
    items_by_label = {item.label: item for item in result.items}
    assert items_by_label["mainタグ"].score == scoring.LLMO_MAIN_TAG_POINTS
    assert items_by_label["見出し数"].score == scoring.LLMO_HEADING_COUNT_POINTS
    assert items_by_label["本文ブロック"].score == scoring.LLMO_CONTENT_BLOCK_POINTS
    assert items_by_label["robots.txt"].score == scoring.LLMO_ROBOTS_TXT_POINTS
    assert items_by_label["sitemap.xml"].score == scoring.LLMO_SITEMAP_POINTS
    assert items_by_label["llms.txt"].score == scoring.LLMO_LLMS_TXT_POINTS
    assert "構造化データ（LLM可読性）" not in items_by_label


def test_score_llmo_new_structure_items_zero_when_absent():
    page = _empty_page()  # main_count=0, heading_count=0, content_block_count=0, robots_txt_status="missing" 等
    result = scoring.score_llmo(page, [])
    items_by_label = {item.label: item for item in result.items}
    assert items_by_label["mainタグ"].score == 0
    assert items_by_label["見出し数"].score == 0
    assert items_by_label["本文ブロック"].score == 0
    assert items_by_label["robots.txt"].score == 0
    assert items_by_label["sitemap.xml"].score == 0
    assert items_by_label["llms.txt"].score == 0


def test_score_llmo_structure_items_partial_scores():
    page = _empty_page()
    page.main_count = 2  # 2個以上は0点（1個のみが満点）
    page.heading_count = 3  # 2〜4個は部分点
    page.content_block_count = 1  # 1〜2個は部分点
    page.robots_txt_status = "disallowed_all"

    result = scoring.score_llmo(page, [])
    items_by_label = {item.label: item for item in result.items}
    assert items_by_label["mainタグ"].score == 0
    assert items_by_label["見出し数"].score == scoring.LLMO_HEADING_COUNT_PARTIAL
    assert items_by_label["本文ブロック"].score == scoring.LLMO_CONTENT_BLOCK_PARTIAL
    assert items_by_label["robots.txt"].score == 0


def test_faq_qa_structured_data_covers_json_ld_type_detection():
    # 旧「構造化データ（LLM可読性）」は廃止し、FAQ/Q&A構造化データに配点を統合したため、
    # JSON-LD型の有無判定はこの項目のみで行う
    page = _empty_page()
    page.json_ld_types = ["FAQPage"]
    result = scoring.score_llmo(page, [])
    items_by_label = {item.label: item for item in result.items}
    assert items_by_label["FAQ / Q&A構造化データ"].score == scoring.LLMO_FAQ_POINTS


def test_llmo_total_matches_sum_of_all_item_max_scores():
    assert scoring.LLMO_TOTAL == 140
    assert scoring.LLMO_STRUCTURE_TOTAL == 32
    assert scoring.LLMO_FAQ_POINTS == 33


def test_compute_improvements_normalizes_by_category_raw_total():
    # LLMO（素点合計140）の1項目を全失点させた場合の impact が、
    # 素点100前提の旧実装（lost/100*35）ではなく lost/140*35 になっていることを確認する
    categories = [
        scoring.CategoryScore("llmo", "AI検索対応度", 0, 35, items=[
            scoring.ScoreItem("llms.txt", 0, scoring.LLMO_LLMS_TXT_POINTS, "未設置"),
        ]),
    ]
    improvements = scoring.compute_improvements(categories)
    assert len(improvements) == 1
    expected_impact = (scoring.LLMO_LLMS_TXT_POINTS / scoring.LLMO_LLMS_TXT_POINTS) * 35
    assert improvements[0].impact == expected_impact
