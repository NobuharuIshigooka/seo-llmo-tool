"""診断パイプライン：7工程を統括する。

各工程は try/except で包み、失敗しても warnings に積んで続行する
（唯一の致命エラーは工程1 crawl のみ。CLAUDE.md「最重要ルール」参照）。
"""

import asyncio
import re
from collections import Counter
from urllib.parse import urlparse

from app import db, scoring
from app.clients import llm, psi, serp
from app.clients.crawler import CrawlError, CrawlResult, fetch as crawl_fetch
from app.config import settings
from app.jobs import Job

STAGE_LABELS = {
    "crawl": "サイトを読み込み中",
    "keywords": "対策キーワードを推定中",
    "serp": "検索順位とAI Overviewを取得中",
    "psi": "表示速度を計測中",
    "competitors": "競合サイトを分析中",
    "score": "スコア算出・要約生成中",
}

# requirements.md 4.5 の比較指標。
COMPARISON_METRIC_LABELS = {
    "char_count": "文字数",
    "h2_count": "H2数",
    "qa_heading_count": "Q&A見出し数",
    "has_faq_schema": "FAQスキーマ",
    "structured_data_type_count": "構造化データ種類数",
    "list_table_count": "リスト・表数",
    "internal_link_count": "内部リンク数",
    "eeat_count": "E-E-A-T要素数",
}


def _domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


COMPETITOR_CANDIDATE_MAX_RANK = 5  # requirements.md 4.5：候補は検索結果1〜5位のみ


def _pick_competitors(
    own_domain: str,
    serp_results: list[serp.SerpResult],
    excluded_domains: set[str],
) -> list[str]:
    """検索結果1〜5位に入り、かつ自社より上位表示された回数が多い順に競合ドメインを最大 max_competitors 件抽出する。"""
    counter: Counter[str] = Counter()
    for result in serp_results:
        own_rank = result.rank
        for position, domain in enumerate(result.organic_domains, start=1):
            if position > COMPETITOR_CANDIDATE_MAX_RANK:
                break
            if domain == own_domain or domain in excluded_domains or not domain:
                continue
            if own_rank is None or position < own_rank:
                counter[domain] += 1

    ranked = [domain for domain, _ in counter.most_common(settings.max_competitors)]
    return ranked


async def _run_crawl(job: Job, url: str) -> CrawlResult:
    job.emit_progress("crawl", STAGE_LABELS["crawl"])
    page = await crawl_fetch(url)
    job.emit_progress("crawl", STAGE_LABELS["crawl"], done=True)
    return page


async def _run_keywords(
    job: Job, page: CrawlResult, warnings: list[str], override_keywords: list[str] | None = None
) -> list[str]:
    job.emit_progress("keywords", STAGE_LABELS["keywords"])

    if override_keywords is not None:
        keywords = override_keywords
    else:
        try:
            keywords = await llm.extract_keywords(page)
        except Exception as exc:
            warnings.append(f"キーワード推定に失敗したため見出しから簡易抽出しました: {exc}")
            keywords = []

    job.emit_progress("keywords", STAGE_LABELS["keywords"], done=True, keywords=keywords)
    return keywords


async def _run_serp(
    job: Job, keywords: list[str], own_domain: str, devices: list[str], warnings: list[str]
) -> list[serp.SerpResult]:
    job.emit_progress("serp", STAGE_LABELS["serp"])
    try:
        results = await serp.search_all(keywords, own_domain, devices)
        if not results and keywords:
            warnings.append("検索順位の取得にすべて失敗したため、順位項目は空です。")
    except Exception as exc:
        warnings.append(f"検索順位の取得に失敗しました: {exc}")
        results = []
    job.emit_progress("serp", STAGE_LABELS["serp"], done=True)
    return results


async def _run_psi(job: Job, url: str, warnings: list[str]) -> dict[str, psi.PsiResult | None]:
    job.emit_progress("psi", STAGE_LABELS["psi"])
    results = await psi.analyze_both(url)
    if results.get("mobile") is None:
        warnings.append("PageSpeed Insights（モバイル）の取得に失敗したため、表示速度は0点として算出しました。")
    if results.get("desktop") is None:
        warnings.append("PageSpeed Insights（PC）の取得に失敗したため、表示速度は0点として算出しました。")
    job.emit_progress("psi", STAGE_LABELS["psi"], done=True)
    return results


async def _run_competitors(
    job: Job,
    own_domain: str,
    serp_results: list[serp.SerpResult],
    warnings: list[str],
) -> list[str]:
    job.emit_progress("competitors", STAGE_LABELS["competitors"])
    try:
        excluded = db.get_excluded_domains()
        competitor_domains = _pick_competitors(own_domain, serp_results, excluded)
        if not competitor_domains:
            warnings.append("競合サイトを検出できなかったため、競合比較セクションは表示されません。")
    except Exception as exc:
        warnings.append(f"競合サイトの分析に失敗しました: {exc}")
        competitor_domains = []
    job.emit_progress("competitors", STAGE_LABELS["competitors"], done=True, competitors=competitor_domains)
    return competitor_domains


def _comparison_metrics(page: CrawlResult) -> dict:
    char_count = len(re.sub(r"\s+", "", page.body_text))
    qa_heading_count = sum(1 for h in (page.h1_list + page.h2_list) if scoring.is_qa_heading(h))
    return {
        "char_count": char_count,
        "h2_count": len(page.h2_list),
        "qa_heading_count": qa_heading_count,
        "has_faq_schema": scoring.has_faq_schema(page),
        "structured_data_type_count": len(set(page.json_ld_types)),
        "list_table_count": page.list_table_count,
        "internal_link_count": page.internal_link_count,
        "eeat_count": len(scoring.detect_eeat(page)),
    }


async def _fetch_competitor_page(domain: str) -> CrawlResult | None:
    try:
        return await crawl_fetch(f"https://{domain}/")
    except CrawlError:
        return None


async def _run_competitor_comparison(
    own_page: CrawlResult,
    own_domain: str,
    competitor_domains: list[str],
    warnings: list[str],
) -> tuple[dict, str | None]:
    """競合3社を実クロールし、requirements.md 4.5 の比較指標を算出する。

    自社クロールと異なりここでの失敗は致命的ではないため、個別に握りつぶして
    取得できたサイトのみで比較表を組み立てる（CLAUDE.md「部分成功」原則）。
    """
    if not competitor_domains:
        return {}, None

    pages = await asyncio.gather(*(_fetch_competitor_page(d) for d in competitor_domains))

    own_metrics = _comparison_metrics(own_page)
    comparison: dict[str, dict] = {own_domain: own_metrics}

    failed_domains: list[str] = []
    for domain, page in zip(competitor_domains, pages):
        if page is None:
            failed_domains.append(domain)
            continue
        comparison[domain] = _comparison_metrics(page)

    if failed_domains:
        warnings.append(f"競合サイトのクロールに失敗したため比較対象から除外しました: {', '.join(failed_domains)}")

    explanation = None
    if len(comparison) >= 2:
        localized = [
            {"ドメイン": domain, **{COMPARISON_METRIC_LABELS.get(k, k): v for k, v in metrics.items()}}
            for domain, metrics in comparison.items()
        ]
        try:
            explanation = await llm.explain_competitors(own_domain, localized)
        except Exception as exc:
            warnings.append(f"競合比較の解説文生成に失敗しました: {exc}")

    return comparison, explanation


async def _run_score_and_narrative(
    job: Job,
    page: CrawlResult,
    own_domain: str,
    keywords: list[str],
    serp_results: list[serp.SerpResult],
    psi_results: dict[str, psi.PsiResult | None],
    competitor_domains: list[str],
    warnings: list[str],
) -> tuple[scoring.ScoreResult, str, dict, str | None]:
    job.emit_progress("score", STAGE_LABELS["score"])

    score_result = scoring.compute_score(
        page,
        keywords,
        serp_results,
        psi_results.get("mobile"),
        psi_results.get("desktop"),
    )

    weak_points = [imp.label for imp in score_result.improvements]

    # llm.improvement_advice() / llm.category_advice() は失敗時に自前でテンプレート文へ
    # フォールバックするため例外は送出しない。未計測カテゴリはアドバイス自体が不自然なため対象外。
    measured_categories = [c for c in score_result.categories if c.measured]
    category_advice_tasks = [
        llm.category_advice(c.label, scoring.category_band(c)) for c in measured_categories
    ]
    advice, category_advices, (comparison, explanation) = await asyncio.gather(
        llm.improvement_advice(score_result.total_score, score_result.band, weak_points),
        asyncio.gather(*category_advice_tasks),
        _run_competitor_comparison(page, own_domain, competitor_domains, warnings),
    )
    for category, category_advice_text in zip(measured_categories, category_advices):
        category.advice = category_advice_text

    job.emit_progress("score", STAGE_LABELS["score"], done=True)
    return score_result, advice, comparison, explanation


def _serp_results_to_list(serp_results: list[serp.SerpResult]) -> list[dict]:
    return [
        {
            "keyword": r.keyword,
            "device": r.device,
            "rank": r.rank,
            "ai_overview_shown": r.ai_overview_shown,
            "ai_overview_cited": r.ai_overview_cited,
        }
        for r in serp_results
    ]


def _score_result_to_dict(score_result: scoring.ScoreResult) -> dict:
    return {
        "total_score": score_result.total_score,
        "band": score_result.band,
        "rubric_version": score_result.rubric_version,
        "categories": [
            {
                "key": c.key,
                "label": c.label,
                "score": round(c.score, 1),
                "weight": c.weight,
                "measured": c.measured,
                "advice": c.advice,
                "items": [
                    {
                        "label": i.label,
                        "score": round(i.score, 1),
                        "max_score": round(i.max_score, 1),
                        "evidence": i.evidence,
                    }
                    for i in c.items
                ],
            }
            for c in score_result.categories
        ],
        "improvements": [
            {"label": i.label, "category": i.category, "impact": round(i.impact, 2)}
            for i in score_result.improvements
        ],
    }


async def run_audit(job: Job, url: str, devices: list[str], keywords: list[str] | None = None) -> None:
    """診断ジョブのランナー本体。JobManager.create_job() の runner として渡す想定。

    工程5（competitors）は自社SERP結果からの抽出に依存するため、serp → competitors
    の順に実行する。serp と psi は競合ドメインに依存しないため並行実行できる。

    keywords を指定した場合、工程2（keywords）はLLM推定をスキップしてそのまま使う
    （結果画面でのキーワード手動編集→再診断向け。requirements.md 4.2）。
    """
    warnings: list[str] = []
    own_domain = _domain_of(url)

    try:
        page = await _run_crawl(job, url)
    except CrawlError as exc:
        job.emit_error(str(exc))
        return

    keywords = await _run_keywords(job, page, warnings, override_keywords=keywords)

    serp_results, psi_results = await asyncio.gather(
        _run_serp(job, keywords, own_domain, devices, warnings),
        _run_psi(job, url, warnings),
    )

    competitor_domains = await _run_competitors(job, own_domain, serp_results, warnings)

    score_result, advice, comparison, explanation = await _run_score_and_narrative(
        job, page, own_domain, keywords, serp_results, psi_results,
        competitor_domains, warnings,
    )

    result = {
        "url": url,
        "domain": own_domain,
        "devices": devices,
        "keywords": keywords,
        "competitors": competitor_domains,
        "competitor_comparison": comparison,
        "competitor_comparison_labels": COMPARISON_METRIC_LABELS,
        "competitor_explanation": explanation,
        "serp_results": _serp_results_to_list(serp_results),
        "score": _score_result_to_dict(score_result),
        "improvement_advice": advice,
        "warnings": warnings,
    }
    job.emit_done(result)
