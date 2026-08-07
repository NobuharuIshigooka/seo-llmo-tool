"""診断パイプライン：7工程を統括する。

各工程は try/except で包み、失敗しても warnings に積んで続行する
（唯一の致命エラーは工程1 crawl のみ。CLAUDE.md「最重要ルール」参照）。
"""

import asyncio
from collections import Counter
from urllib.parse import urlparse

from app import db, scoring
from app.clients import backlinks, llm, psi, serp
from app.clients.crawler import CrawlError, CrawlResult, fetch as crawl_fetch
from app.config import settings
from app.jobs import Job

STAGE_LABELS = {
    "crawl": "サイトを読み込み中",
    "keywords": "対策キーワードを推定中",
    "serp": "検索順位とAI Overviewを取得中",
    "psi": "表示速度を計測中",
    "competitors": "競合サイトを分析中",
    "backlinks": "被リンクを調査中",
    "score": "スコア算出・要約生成中",
}


def _domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _pick_competitors(
    own_domain: str,
    serp_results: list[serp.SerpResult],
    excluded_domains: set[str],
) -> list[str]:
    """自社より上位表示回数が多い順に競合ドメインを最大 max_competitors 件抽出する。"""
    counter: Counter[str] = Counter()
    for result in serp_results:
        own_rank = result.rank
        for position, domain in enumerate(result.organic_domains, start=1):
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


async def _run_backlinks(
    job: Job, own_url: str, competitor_domains: list[str], warnings: list[str]
) -> dict[str, backlinks.BacklinksResult | None]:
    job.emit_progress("backlinks", STAGE_LABELS["backlinks"])
    targets = [own_url] + competitor_domains
    try:
        results = await backlinks.get_summaries(targets)
    except Exception as exc:
        warnings.append(f"被リンク調査に失敗したため、権威性・被リンクは未計測として算出しました: {exc}")
        results = {target: None for target in targets}

    if results.get(own_url) is None:
        warnings.append("自社サイトの被リンク調査に失敗したため、権威性・被リンクは未計測として算出しました。")

    job.emit_progress("backlinks", STAGE_LABELS["backlinks"], done=True)
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


async def _run_score_and_narrative(
    job: Job,
    page: CrawlResult,
    keywords: list[str],
    serp_results: list[serp.SerpResult],
    psi_results: dict[str, psi.PsiResult | None],
    own_backlinks: backlinks.BacklinksResult | None,
) -> tuple[scoring.ScoreResult, str]:
    job.emit_progress("score", STAGE_LABELS["score"])

    score_result = scoring.compute_score(
        page,
        keywords,
        serp_results,
        psi_results.get("mobile"),
        psi_results.get("desktop"),
        own_backlinks,
    )

    weak_points = [imp.label for imp in score_result.improvements]

    # llm.sales_summary() は失敗時に自前でテンプレート文へフォールバックするため例外は送出しない
    summary = await llm.sales_summary(score_result.total_score, score_result.band, weak_points)

    job.emit_progress("score", STAGE_LABELS["score"], done=True)
    return score_result, summary


def _backlinks_to_dict(result: backlinks.BacklinksResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "domain": result.domain,
        "referring_domains": result.referring_domains,
        "rank": result.rank,
        "backlinks": result.backlinks,
        "dofollow_ratio": result.dofollow_ratio,
    }


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

    工程5（competitors）は自社SERP結果からの抽出に依存し、工程6（backlinks）は
    competitors が確定した競合ドメインに依存するため、serp → competitors → backlinks
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
    backlink_results = await _run_backlinks(job, url, competitor_domains, warnings)

    score_result, summary = await _run_score_and_narrative(
        job, page, keywords, serp_results, psi_results, backlink_results.get(url)
    )

    result = {
        "url": url,
        "domain": own_domain,
        "devices": devices,
        "keywords": keywords,
        "competitors": competitor_domains,
        "competitor_backlinks": {
            domain: _backlinks_to_dict(backlink_results.get(domain)) for domain in competitor_domains
        },
        "serp_results": _serp_results_to_list(serp_results),
        "score": _score_result_to_dict(score_result),
        "sales_summary": summary,
        "warnings": warnings,
    }
    job.emit_done(result)
