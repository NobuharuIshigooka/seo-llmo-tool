"""共有レポート画面（/report/{id}）・PDF向けの表示用データ組み立て。

audit.js の結果画面描画ロジック（rankText / aiOverviewText 等）と等価な変換を
サーバーサイドで行う。画面とPDFのレンダリングを1系統に保つため、この結果は
report.html からのみ参照する（docs/architecture.md 7章）。
"""

from datetime import datetime, timedelta, timezone

from app.pipeline import COMPARISON_METRIC_LABELS

JST = timezone(timedelta(hours=9))

BAND_LABELS = {"good": "良好", "fair": "標準", "weak": "要改善", "poor": "急ぎ改善"}

COMPARISON_METRIC_ORDER = [
    "char_count", "h2_count", "qa_heading_count", "has_faq_schema",
    "structured_data_type_count", "list_table_count", "internal_link_count",
    "eeat_count",
]

# 総合スコアのドーナツグラフ（frontend.md 5.4）のカテゴリ配色。scoring.py の category key に対応
CATEGORY_COLORS = {
    "technical_seo": "var(--category-technical-seo)",
    "content_seo": "var(--category-content-seo)",
    "llmo": "var(--category-llmo)",
}
CATEGORY_UNMEASURED_COLOR = "var(--mono-100)"


def build_score_donut_segments(categories: list[dict]) -> tuple[list[dict], str]:
    """各カテゴリの獲得点数を conic-gradient 用の累積%区間に変換する。

    合計は総合スコア（0〜100）と一致する。未計測カテゴリは0点として灰色の
    区間を作らない（conic-gradient に0幅の区間を含めても無害だが、見た目上不要なため除外する）。
    2つ目の戻り値は CSS の conic-gradient() にそのまま渡せる完成済みの引数文字列。
    """
    segments = []
    cursor = 0.0
    for c in categories:
        points = c["score"] if c["measured"] else 0.0
        if points <= 0:
            continue
        start = cursor
        cursor += points
        segments.append({
            "key": c["key"],
            "label": c["label"],
            "color": CATEGORY_COLORS.get(c["key"], CATEGORY_UNMEASURED_COLOR),
            "points": round(points, 1),
            "start_pct": round(start, 4),
            "end_pct": round(cursor, 4),
        })

    stops = [f"{s['color']} {s['start_pct']}% {s['end_pct']}%" for s in segments]
    stops.append(f"{CATEGORY_UNMEASURED_COLOR} {round(cursor, 4)}% 100%")
    gradient = f"conic-gradient({', '.join(stops)})"
    return segments, gradient


def format_jst(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    dt = datetime.strptime(iso_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(JST)
    return dt.strftime("%Y年%m月%d日")


def _rank_text(row: dict | None) -> str:
    if not row:
        return "-"
    return f"{row['rank']}位" if row.get("rank") else "20位圏外"


def _ai_overview_text(row: dict | None) -> str:
    if not row or not row.get("ai_overview_shown"):
        return "表示なし"
    return "表示あり・引用されている" if row.get("ai_overview_cited") else "表示あり"


def build_serp_rows(result: dict) -> list[dict]:
    by_keyword: dict[str, dict[str, dict]] = {}
    for r in result.get("serp_results", []):
        by_keyword.setdefault(r["keyword"], {})[r["device"]] = r

    rows = []
    for keyword in result.get("keywords", []):
        devices = by_keyword.get(keyword, {})
        any_ai_overview = next((r for r in devices.values() if r.get("ai_overview_shown")), None)
        rows.append({
            "keyword": keyword,
            "desktop_text": _rank_text(devices.get("desktop")),
            "mobile_text": _rank_text(devices.get("mobile")),
            "ai_overview_text": _ai_overview_text(any_ai_overview),
            "cited": bool(any_ai_overview and any_ai_overview.get("ai_overview_cited")),
        })
    return rows


def _format_metric(key: str, value) -> str:
    if value is None:
        return "未計測"
    if key == "has_faq_schema":
        return "あり" if value else "なし"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def build_comparison_rows(result: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    comparison = result.get("competitor_comparison") or {}
    own_domain = result.get("domain")
    if not comparison:
        return [], []

    metric_labels = [(key, COMPARISON_METRIC_LABELS.get(key, key)) for key in COMPARISON_METRIC_ORDER]

    rows = []
    if own_domain in comparison:
        metrics = comparison[own_domain]
        rows.append({
            "domain": own_domain,
            "is_own": True,
            "values": [_format_metric(key, metrics.get(key)) for key in COMPARISON_METRIC_ORDER],
        })
    for domain, metrics in comparison.items():
        if domain == own_domain:
            continue
        rows.append({
            "domain": domain,
            "is_own": False,
            "values": [_format_metric(key, metrics.get(key)) for key in COMPARISON_METRIC_ORDER],
        })
    return rows, metric_labels


def build_report_context(audit_row: dict, result: dict, base_url: str, is_pdf: bool = False) -> dict:
    comparison_rows, comparison_metric_labels = build_comparison_rows(result)
    score_donut_segments, score_donut_gradient = build_score_donut_segments(
        result.get("score", {}).get("categories", [])
    )
    return {
        "audit": {
            "id": audit_row["id"],
            "domain": audit_row["domain"],
            "devices": audit_row["devices"],
            "created_at_jst": format_jst(audit_row["created_at"]),
        },
        "result": result,
        "band_labels": BAND_LABELS,
        "score_donut_segments": score_donut_segments,
        "score_donut_gradient": score_donut_gradient,
        "serp_rows": build_serp_rows(result),
        "comparison_rows": comparison_rows,
        "comparison_metric_labels": comparison_metric_labels,
        "share_url": f"{base_url}/report/{audit_row['id']}",
        "is_pdf": is_pdf,
    }
