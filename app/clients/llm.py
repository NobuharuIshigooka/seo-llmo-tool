import json
import re

from anthropic import AsyncAnthropic

from app.clients.crawler import CrawlResult
from app.config import settings

_NO_HALLUCINATION_NOTE = (
    "与えられた情報に含まれる事実のみを扱ってください。"
    "与えられていない数値やスコアを推測で作らないでください。"
    "出力はプレーンテキストのみとし、Markdown記法（#, *, ` など）は使わないでください。"
)


class LlmError(Exception):
    pass


def _client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _fallback_keywords(page: CrawlResult) -> list[str]:
    candidates: list[str] = []
    if page.title:
        candidates.append(page.title)
    candidates.extend(page.h1_list)
    candidates.extend(page.h2_list)

    keywords: list[str] = []
    for text in candidates:
        cleaned = re.split(r"[|｜\-–—:：/／]", text)[0].strip()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
        if len(keywords) >= settings.max_keywords:
            break

    return keywords[: settings.max_keywords]


async def extract_keywords(page: CrawlResult) -> list[str]:
    """title/meta description/見出し/本文からキーワードを推定する。失敗時は簡易抽出にフォールバック。"""
    prompt = f"""以下はWebページから抽出した情報です。この情報だけを根拠に、\
このページが検索エンジンで上位表示を狙うべき主要キーワードを最大{settings.max_keywords}個推定してください。

タイトル: {page.title}
meta description: {page.meta_description}
見出し(h1): {", ".join(page.h1_list)}
見出し(h2): {", ".join(page.h2_list)}
本文冒頭: {page.body_text[:1500]}

{_NO_HALLUCINATION_NOTE}
出力は日本語のキーワードのJSON配列のみとしてください（例: ["キーワード1", "キーワード2"]）。\
配列以外の説明文は一切出力しないでください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _strip_code_fence(response.content[0].text)
        keywords = json.loads(raw)
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise LlmError("キーワード抽出の出力形式が不正です")
        return keywords[: settings.max_keywords]
    except Exception:
        return _fallback_keywords(page)


async def explain_competitors(own_domain: str, comparison: list[dict]) -> str | None:
    """自社と競合の数値差分を自然文化する。失敗時は None（呼び出し側で非表示扱い）。"""
    prompt = f"""以下は自社サイト（{own_domain}）と競合サイトのSEO関連の実測データの比較です。\
この数値のみを根拠に、自社が競合に対してどう劣っている／優れているかを日本語の自然な文章で説明してください。

比較データ（JSON）:
{json.dumps(comparison, ensure_ascii=False)}

{_NO_HALLUCINATION_NOTE}
文章は300字程度で、営業担当が商談中にそのまま読み上げられる自然な口調にしてください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return None


def _fallback_sales_summary(total_score: int, band: str, weak_points: list[str]) -> str:
    band_text = {
        "good": "全体として良好な状態です。",
        "fair": "改善の余地はありますが基礎はできています。",
        "weak": "いくつかの重要な項目で改善が必要です。",
        "poor": "多くの項目で改善が必要な状態です。",
    }.get(band, "")

    points = "、".join(weak_points[:3]) if weak_points else "特定の弱点"
    return (
        f"診断の結果、総合スコアは{total_score}点でした。{band_text}"
        f"特に{points}の改善によって、検索エンジンおよびAI検索での見え方の向上が期待できます。"
        "まずは着手しやすい項目から順に改善していくことをおすすめします。"
    )


async def sales_summary(total_score: int, band: str, weak_points: list[str]) -> str:
    """営業トーク用の要約（4〜5文・300字以内）。失敗時はテンプレート文にフォールバック。"""
    prompt = f"""以下はWebサイトのSEO/LLMO診断結果です。この数値のみを根拠に、\
営業担当が商談中にそのまま話せる要約文を作成してください。

総合スコア: {total_score}点（100点満点）
評価: {band}
弱点項目（スコアが低い順）: {", ".join(weak_points)}

{_NO_HALLUCINATION_NOTE}
被リンク施策のような短期間で改善できない項目は「最初に着手すべきこと」として挙げないでください。
4〜5文・300字以内で、日本語の自然な口語文にしてください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if not text:
            raise LlmError("営業トーク要約が空です")
        return text
    except Exception:
        return _fallback_sales_summary(total_score, band, weak_points)
