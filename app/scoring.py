"""スコア配点の唯一の定義。docs/scoring.md の配点をそのまま実装する。

配点・閾値はこのファイルのみに定義し、他のファイルにハードコードしない
（CLAUDE.md「最重要ルール」・docs/scoring.md 参照）。
"""

import re
from dataclasses import dataclass, field

from app.clients.crawler import CrawlResult
from app.clients.psi import PsiResult
from app.clients.serp import SerpResult

RUBRIC_VERSION = "2026.08.3"

# ---- カテゴリ配点 ----
CATEGORY_WEIGHTS = {
    "technical_seo": 30,
    "content_seo": 30,
    "llmo": 35,
}

# ---- ①テクニカルSEO ----
TECHNICAL_MOBILE_SPEED_POINTS = 40
TECHNICAL_DESKTOP_SPEED_POINTS = 10
TECHNICAL_MOBILE_FRIENDLY_POINTS = 15
TECHNICAL_STRUCTURED_DATA_POINTS = 20
TECHNICAL_CRAWLABILITY_POINTS = 15
TECHNICAL_NOINDEX_POINTS = 8
TECHNICAL_CANONICAL_POINTS = 4
TECHNICAL_HTTPS_POINTS = 3
TECHNICAL_TOTAL = (
    TECHNICAL_MOBILE_SPEED_POINTS
    + TECHNICAL_DESKTOP_SPEED_POINTS
    + TECHNICAL_MOBILE_FRIENDLY_POINTS
    + TECHNICAL_STRUCTURED_DATA_POINTS
    + TECHNICAL_CRAWLABILITY_POINTS
)  # 40+10+15+20+15=100

# ---- ②コンテンツSEO ----
CONTENT_BODY_LENGTH_POINTS = 30
CONTENT_BODY_LENGTH_FULL_CHARS = 2500
CONTENT_KEYWORD_COVERAGE_POINTS = 25
CONTENT_TITLE_HEADING_POINTS = 25
CONTENT_EEAT_POINTS = 20
CONTENT_TOTAL = (
    CONTENT_BODY_LENGTH_POINTS + CONTENT_KEYWORD_COVERAGE_POINTS + CONTENT_TITLE_HEADING_POINTS + CONTENT_EEAT_POINTS
)  # 30+25+25+20=100

TITLE_LENGTH_FULL_POINTS = 8
TITLE_LENGTH_PARTIAL_POINTS = 4
TITLE_LENGTH_MIN = 20
TITLE_LENGTH_MAX = 40
H1_SINGLE_POINTS = 6
H1_MULTIPLE_POINTS = 2
H2_POINTS_PER = 1.5
H2_POINTS_MAX = 6
META_DESC_FULL_POINTS = 5
META_DESC_PARTIAL_POINTS = 2
META_DESC_MIN_CHARS = 60

EEAT_FULL_COUNT = 4  # 4種以上で満点
EEAT_PATTERNS = {
    "運営者情報": [r"運営(会社|者)", r"会社概要", r"company"],
    "著者・監修情報": [r"監修", r"著者", r"執筆者", r"ライター"],
    "連絡先": [r"お問い合わせ", r"問合せ", r"contact", r"電話番号"],
    "実績表記": [r"導入実績", r"実績", r"事例", r"受賞"],
    "資格・許認可": [r"資格", r"許認可", r"認定", r"免許", r"登録番号"],
}

# ---- ③LLMO対応度 ----
LLMO_FAQ_POINTS = 25
LLMO_QA_HEADING_POINTS = 20
LLMO_QA_HEADING_POINTS_PER = 4
LLMO_INTRO_POINTS = 20
LLMO_LIST_TABLE_POINTS = 15
LLMO_LIST_TABLE_POINTS_PER = 2.5
LLMO_AI_OVERVIEW_POINTS = 20

FAQ_JSON_LD_TYPES = {"FAQPage", "QAPage", "HowTo"}
QA_HEADING_KEYWORDS = ["?", "？", "とは", "なぜ", "どう", "方法", "できる", "違い", "いくら", "おすすめ"]
INTRO_MIN_CHARS = 40
INTRO_MAX_CHARS = 200
INTRO_PARTIAL_POINTS = 10
AI_OVERVIEW_NEUTRAL_RATIO = 0.5  # AI Overview が1件も表示されない場合の中立評価

# ---- ③LLMO対応度：情報構造・補足情報・発見巡回（2026-08-24追加） ----
# 既存5項目（FAQ〜AI Overview、素点合計100）とは別枠として素点を積み増し、
# LLMO_STRUCTURE_TOTAL を合算した素点でカテゴリ配点（CATEGORY_WEIGHTS["llmo"]）に正規化する。
LLMO_MAIN_TAG_POINTS = 5
LLMO_HEADING_COUNT_POINTS = 5
LLMO_HEADING_COUNT_FULL = 5  # 5個以上で満点
LLMO_HEADING_COUNT_PARTIAL = 2  # 2〜4個
LLMO_CONTENT_BLOCK_POINTS = 5
LLMO_CONTENT_BLOCK_FULL = 3  # article+section 3個以上で満点
LLMO_CONTENT_BLOCK_PARTIAL = 2  # 1〜2個
LLMO_STRUCTURED_DATA_VALID_POINTS = 8
LLMO_STRUCTURED_DATA_ERROR_POINTS = 3  # JSON-LDはあるが構文エラーを含む
LLMO_ROBOTS_TXT_POINTS = 5
LLMO_SITEMAP_POINTS = 5
LLMO_LLMS_TXT_POINTS = 7

LLMO_STRUCTURE_TOTAL = (
    LLMO_MAIN_TAG_POINTS
    + LLMO_HEADING_COUNT_POINTS
    + LLMO_CONTENT_BLOCK_POINTS
    + LLMO_STRUCTURED_DATA_VALID_POINTS
    + LLMO_ROBOTS_TXT_POINTS
    + LLMO_SITEMAP_POINTS
    + LLMO_LLMS_TXT_POINTS
)  # 5+5+5+8+5+5+7=40

LLMO_TOTAL = (
    LLMO_FAQ_POINTS
    + LLMO_QA_HEADING_POINTS
    + LLMO_INTRO_POINTS
    + LLMO_LIST_TABLE_POINTS
    + LLMO_AI_OVERVIEW_POINTS
    + LLMO_STRUCTURE_TOTAL
)  # 25+20+20+15+20+40=140

# ---- 評価バンド ----
BAND_THRESHOLDS = [
    (80, "good"),
    (60, "fair"),
    (40, "weak"),
]
BAND_DEFAULT = "poor"

IMPROVEMENT_TOP_N = 3


@dataclass
class CategoryScore:
    key: str
    label: str
    score: float  # 0〜100（カテゴリ内正規化後）
    weight: int
    measured: bool = True
    items: list["ScoreItem"] = field(default_factory=list)
    advice: str = ""  # カテゴリ別の抽象アドバイス（pipeline.py が診断完了後に付与）


@dataclass
class ScoreItem:
    label: str
    score: float
    max_score: float
    evidence: str  # 実測値の根拠（例: "本文 約1,240文字"）


@dataclass
class ImprovementPoint:
    label: str
    category: str
    impact: float  # 失点 ÷ 100 × カテゴリ重み


@dataclass
class ScoreResult:
    total_score: int
    band: str
    categories: list[CategoryScore]
    improvements: list[ImprovementPoint]
    rubric_version: str = RUBRIC_VERSION


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _ratio_score(value: float, full_value: float, max_points: float) -> float:
    if full_value <= 0:
        return 0.0
    return _clamp(value / full_value, 0, 1) * max_points


# ---------------------------------------------------------------------------
# ①テクニカルSEO
# ---------------------------------------------------------------------------


def _speed_score(psi: PsiResult | None, max_points: float) -> tuple[float, str]:
    if psi is None or psi.performance_score is None:
        return 0.0, "表示速度の計測に失敗したため0点"
    score = psi.performance_score * max_points
    return score, f"表示速度スコア {round(psi.performance_score * 100)}／100"


def score_technical_seo(page: CrawlResult, psi_mobile: PsiResult | None, psi_desktop: PsiResult | None) -> CategoryScore:
    items: list[ScoreItem] = []

    mobile_score, mobile_evidence = _speed_score(psi_mobile, TECHNICAL_MOBILE_SPEED_POINTS)
    items.append(ScoreItem("表示速度（モバイル）", mobile_score, TECHNICAL_MOBILE_SPEED_POINTS, mobile_evidence))

    desktop_score, desktop_evidence = _speed_score(psi_desktop, TECHNICAL_DESKTOP_SPEED_POINTS)
    items.append(ScoreItem("表示速度（PC）", desktop_score, TECHNICAL_DESKTOP_SPEED_POINTS, desktop_evidence))

    mobile_friendly_score = TECHNICAL_MOBILE_FRIENDLY_POINTS if page.has_viewport else 0.0
    items.append(ScoreItem(
        "モバイル対応", mobile_friendly_score, TECHNICAL_MOBILE_FRIENDLY_POINTS,
        "viewportメタタグ あり" if page.has_viewport else "viewportメタタグ なし",
    ))

    structured_data_score = TECHNICAL_STRUCTURED_DATA_POINTS if page.json_ld_types else 0.0
    items.append(ScoreItem(
        "構造化データ", structured_data_score, TECHNICAL_STRUCTURED_DATA_POINTS,
        f"JSON-LD {len(page.json_ld_types)}件検出" if page.json_ld_types else "JSON-LD 未検出",
    ))

    crawlability_score = 0.0
    crawlability_score += TECHNICAL_NOINDEX_POINTS if not page.is_noindex else 0.0
    crawlability_score += TECHNICAL_CANONICAL_POINTS if page.has_canonical else 0.0
    crawlability_score += TECHNICAL_HTTPS_POINTS if page.is_https else 0.0
    items.append(ScoreItem(
        "クロール可能性・HTTPS", crawlability_score, TECHNICAL_CRAWLABILITY_POINTS,
        f"noindex={page.is_noindex} canonical={page.has_canonical} https={page.is_https}",
    ))

    raw_total = sum(i.score for i in items)
    normalized = raw_total / TECHNICAL_TOTAL * CATEGORY_WEIGHTS["technical_seo"]
    return CategoryScore("technical_seo", "テクニカルSEO", normalized, CATEGORY_WEIGHTS["technical_seo"], items=items)


# ---------------------------------------------------------------------------
# ②コンテンツSEO
# ---------------------------------------------------------------------------


def _body_length_score(page: CrawlResult) -> tuple[float, str]:
    char_count = len(re.sub(r"\s+", "", page.body_text))
    score = _ratio_score(char_count, CONTENT_BODY_LENGTH_FULL_CHARS, CONTENT_BODY_LENGTH_POINTS)
    return score, f"本文 約{char_count:,}文字"


def _keyword_coverage_score(page: CrawlResult, keywords: list[str]) -> tuple[float, str]:
    if not keywords:
        return 0.0, "推定キーワードなし"
    haystack = f"{page.title} {page.meta_description} {' '.join(page.h1_list)} {' '.join(page.h2_list)} {page.body_text}"
    hit_count = sum(1 for kw in keywords if kw and kw in haystack)
    ratio = hit_count / len(keywords)
    score = ratio * CONTENT_KEYWORD_COVERAGE_POINTS
    return score, f"推定キーワード{len(keywords)}件中{hit_count}件がページ内に出現"


def _title_heading_score(page: CrawlResult) -> tuple[float, str]:
    score = 0.0
    evidences = []

    title_len = len(page.title)
    if TITLE_LENGTH_MIN <= title_len <= TITLE_LENGTH_MAX:
        score += TITLE_LENGTH_FULL_POINTS
    elif page.title:
        score += TITLE_LENGTH_PARTIAL_POINTS
    evidences.append(f"title {title_len}文字")

    h1_count = len(page.h1_list)
    if h1_count == 1:
        score += H1_SINGLE_POINTS
    elif h1_count >= 2:
        score += H1_MULTIPLE_POINTS
    evidences.append(f"H1 {h1_count}個")

    h2_score = min(len(page.h2_list) * H2_POINTS_PER, H2_POINTS_MAX)
    score += h2_score
    evidences.append(f"H2 {len(page.h2_list)}個")

    meta_len = len(page.meta_description)
    if meta_len >= META_DESC_MIN_CHARS:
        score += META_DESC_FULL_POINTS
    elif meta_len > 0:
        score += META_DESC_PARTIAL_POINTS
    evidences.append(f"meta description {meta_len}文字")

    return score, "、".join(evidences)


def detect_eeat(page: CrawlResult) -> list[str]:
    """E-E-A-T要素の検出（運営者情報／著者情報／連絡先／実績表記／資格・許認可）。

    配点（score_content_seo）と競合比較（pipeline.py）の両方から参照するため、
    検出ロジックのみここに切り出す。配点自体はこのファイル内に閉じる。
    """
    haystack = f"{page.body_text} {page.title}".lower()
    return [
        label for label, patterns in EEAT_PATTERNS.items()
        if any(re.search(p, haystack, flags=re.IGNORECASE) for p in patterns)
    ]


def _eeat_score(page: CrawlResult) -> tuple[float, str]:
    detected = detect_eeat(page)
    score = _ratio_score(len(detected), EEAT_FULL_COUNT, CONTENT_EEAT_POINTS)
    evidence = f"検出: {', '.join(detected)}" if detected else "該当要素なし"
    return score, evidence


def score_content_seo(page: CrawlResult, keywords: list[str]) -> CategoryScore:
    items: list[ScoreItem] = []

    body_score, body_evidence = _body_length_score(page)
    items.append(ScoreItem("本文の情報量", body_score, CONTENT_BODY_LENGTH_POINTS, body_evidence))

    keyword_score, keyword_evidence = _keyword_coverage_score(page, keywords)
    items.append(ScoreItem("キーワード網羅性", keyword_score, CONTENT_KEYWORD_COVERAGE_POINTS, keyword_evidence))

    title_score, title_evidence = _title_heading_score(page)
    items.append(ScoreItem("タイトル・見出し構造", title_score, CONTENT_TITLE_HEADING_POINTS, title_evidence))

    eeat_score, eeat_evidence = _eeat_score(page)
    items.append(ScoreItem("E-E-A-T要素", eeat_score, CONTENT_EEAT_POINTS, eeat_evidence))

    raw_total = sum(i.score for i in items)
    normalized = raw_total / CONTENT_TOTAL * CATEGORY_WEIGHTS["content_seo"]
    return CategoryScore("content_seo", "コンテンツSEO", normalized, CATEGORY_WEIGHTS["content_seo"], items=items)


# ---------------------------------------------------------------------------
# ③LLMO対応度
# ---------------------------------------------------------------------------


def is_qa_heading(heading: str) -> bool:
    return any(kw in heading for kw in QA_HEADING_KEYWORDS)


def has_faq_schema(page: CrawlResult) -> bool:
    return bool(FAQ_JSON_LD_TYPES & set(page.json_ld_types))


def _ai_overview_score(serp_results: list[SerpResult]) -> tuple[float, str]:
    shown = [r for r in serp_results if r.ai_overview_shown]
    if not shown:
        return AI_OVERVIEW_NEUTRAL_RATIO * LLMO_AI_OVERVIEW_POINTS, "AI Overview 表示なし（中立評価）"

    cited = [r for r in shown if r.ai_overview_cited]
    ratio = len(cited) / len(shown)
    score = ratio * LLMO_AI_OVERVIEW_POINTS
    return score, f"AI Overview表示{len(shown)}件中{len(cited)}件で自社が引用"


def score_llmo(page: CrawlResult, serp_results: list[SerpResult]) -> CategoryScore:
    items: list[ScoreItem] = []

    faq_score = LLMO_FAQ_POINTS if has_faq_schema(page) else 0.0
    items.append(ScoreItem(
        "FAQ / QA構造化データ", faq_score, LLMO_FAQ_POINTS,
        f"JSON-LD種別: {', '.join(page.json_ld_types)}" if page.json_ld_types else "JSON-LD なし",
    ))

    qa_headings = [h for h in (page.h1_list + page.h2_list) if is_qa_heading(h)]
    qa_score = min(len(qa_headings) * LLMO_QA_HEADING_POINTS_PER, LLMO_QA_HEADING_POINTS)
    items.append(ScoreItem("Q&A形式の見出し", qa_score, LLMO_QA_HEADING_POINTS, f"Q&A形式見出し {len(qa_headings)}個"))

    intro_len = len(page.first_paragraph)
    if INTRO_MIN_CHARS <= intro_len <= INTRO_MAX_CHARS:
        intro_score = LLMO_INTRO_POINTS
    elif intro_len > 0:
        intro_score = INTRO_PARTIAL_POINTS
    else:
        intro_score = 0.0
    items.append(ScoreItem("冒頭の定義文", intro_score, LLMO_INTRO_POINTS, f"冒頭段落 {intro_len}文字"))

    list_table_score = min(page.list_table_count * LLMO_LIST_TABLE_POINTS_PER, LLMO_LIST_TABLE_POINTS)
    items.append(ScoreItem("リスト・表による整理", list_table_score, LLMO_LIST_TABLE_POINTS, f"リスト・表 {page.list_table_count}個"))

    ai_overview_score, ai_overview_evidence = _ai_overview_score(serp_results)
    items.append(ScoreItem("AI Overview での引用", ai_overview_score, LLMO_AI_OVERVIEW_POINTS, ai_overview_evidence))

    main_score = LLMO_MAIN_TAG_POINTS if page.main_count == 1 else 0.0
    items.append(ScoreItem("mainタグ", main_score, LLMO_MAIN_TAG_POINTS, f"main要素 {page.main_count}個"))

    if page.heading_count >= LLMO_HEADING_COUNT_FULL:
        heading_score = LLMO_HEADING_COUNT_POINTS
    elif page.heading_count >= 2:
        heading_score = LLMO_HEADING_COUNT_PARTIAL
    else:
        heading_score = 0.0
    items.append(ScoreItem("見出し数", heading_score, LLMO_HEADING_COUNT_POINTS, f"見出し（h1〜h6）合計 {page.heading_count}個"))

    if page.content_block_count >= LLMO_CONTENT_BLOCK_FULL:
        content_block_score = LLMO_CONTENT_BLOCK_POINTS
    elif page.content_block_count >= 1:
        content_block_score = LLMO_CONTENT_BLOCK_PARTIAL
    else:
        content_block_score = 0.0
    items.append(ScoreItem(
        "本文ブロック", content_block_score, LLMO_CONTENT_BLOCK_POINTS,
        f"article・section 合計 {page.content_block_count}個",
    ))

    if page.json_ld_types and not page.has_json_ld_parse_error:
        structured_data_score = LLMO_STRUCTURED_DATA_VALID_POINTS
        structured_data_evidence = f"JSON-LDが有効に存在（{len(page.json_ld_types)}件）"
    elif page.has_json_ld_parse_error:
        structured_data_score = LLMO_STRUCTURED_DATA_ERROR_POINTS
        structured_data_evidence = "JSON-LDに構文エラーを検出"
    else:
        structured_data_score = 0.0
        structured_data_evidence = "JSON-LD 未検出"
    items.append(ScoreItem("構造化データ（LLM可読性）", structured_data_score, LLMO_STRUCTURED_DATA_VALID_POINTS, structured_data_evidence))

    robots_txt_score = LLMO_ROBOTS_TXT_POINTS if page.robots_txt_status == "allowed" else 0.0
    robots_txt_evidence = {
        "allowed": "robots.txt 設置あり・許可",
        "disallowed_all": "robots.txt が全ページを拒否",
        "missing": "robots.txt 未設置",
    }[page.robots_txt_status]
    items.append(ScoreItem("robots.txt", robots_txt_score, LLMO_ROBOTS_TXT_POINTS, robots_txt_evidence))

    sitemap_score = LLMO_SITEMAP_POINTS if page.has_sitemap else 0.0
    items.append(ScoreItem("sitemap.xml", sitemap_score, LLMO_SITEMAP_POINTS, "設置あり" if page.has_sitemap else "未設置"))

    llms_txt_score = LLMO_LLMS_TXT_POINTS if page.has_llms_txt else 0.0
    items.append(ScoreItem("llms.txt", llms_txt_score, LLMO_LLMS_TXT_POINTS, "設置あり" if page.has_llms_txt else "未設置"))

    raw_total = sum(i.score for i in items)
    normalized = raw_total / LLMO_TOTAL * CATEGORY_WEIGHTS["llmo"]
    return CategoryScore("llmo", "AI検索対応度", normalized, CATEGORY_WEIGHTS["llmo"], items=items)


# ---------------------------------------------------------------------------
# 総合スコア・評価バンド・改善インパクト
# ---------------------------------------------------------------------------


def _band_of(total_score: float) -> str:
    for threshold, band in BAND_THRESHOLDS:
        if total_score >= threshold:
            return band
    return BAND_DEFAULT


def category_band(category: CategoryScore) -> str:
    """カテゴリの達成率（score/weight）を総合スコアと同じ閾値でバンド判定する。"""
    if not category.measured or category.weight <= 0:
        return BAND_DEFAULT
    return _band_of(category.score / category.weight * 100)


def compute_total_score(categories: list[CategoryScore]) -> tuple[int, str]:
    measured = [c for c in categories if c.measured]
    weight_sum = sum(c.weight for c in measured)
    if weight_sum == 0:
        return 0, BAND_DEFAULT

    raw_total = sum(c.score for c in measured)
    total_score = raw_total / weight_sum * 100
    total_score = round(_clamp(total_score))
    return total_score, _band_of(total_score)


def compute_improvements(categories: list[CategoryScore]) -> list[ImprovementPoint]:
    """失点 ÷ カテゴリ内の素点合計 × カテゴリ重み を計算し、大きい順に上位3件を返す。"""
    points: list[ImprovementPoint] = []
    for category in categories:
        if not category.measured:
            continue
        category_raw_total = sum(i.max_score for i in category.items)
        if category_raw_total <= 0:
            continue
        for item in category.items:
            lost_points = item.max_score - item.score
            if lost_points <= 0:
                continue
            impact = (lost_points / category_raw_total) * category.weight
            points.append(ImprovementPoint(label=item.label, category=category.label, impact=impact))

    points.sort(key=lambda p: p.impact, reverse=True)
    return points[:IMPROVEMENT_TOP_N]


def compute_score(
    page: CrawlResult,
    keywords: list[str],
    serp_results: list[SerpResult],
    psi_mobile: PsiResult | None,
    psi_desktop: PsiResult | None,
) -> ScoreResult:
    categories = [
        score_technical_seo(page, psi_mobile, psi_desktop),
        score_content_seo(page, keywords),
        score_llmo(page, serp_results),
    ]
    total_score, band = compute_total_score(categories)
    improvements = compute_improvements(categories)

    return ScoreResult(
        total_score=total_score,
        band=band,
        categories=categories,
        improvements=improvements,
    )
