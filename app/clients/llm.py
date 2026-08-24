import json
import re
from collections import Counter

from anthropic import AsyncAnthropic
from fugashi import Tagger

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


_KEYWORD_SPLIT_PATTERN = r"[|｜\-–—:：/／、,，。・()（）\[\]【】]"

# タイトル・見出し等の主要情報源から得た候補は、本文中の頻度だけで埋もれないよう
# 優先度スコアに下駄を履かせる（本文出現1回 = 1点、主要情報源からの出現 = このボーナス点を加算）。
# 現実的な本文の繰り返し回数（数十回程度）でも逆転しない値にする。
_PRIMARY_SOURCE_BONUS = 1000

_NOUN_POS = "名詞"
_NOUN_MIN_LENGTH = 2  # 1文字名詞（「的」「化」等の断片）をノイズとして除外
_STOPWORD_NOUNS = {"こと", "もの", "ため", "よう", "これ", "それ", "あれ", "ここ", "そこ"}

_tagger = Tagger()


def _split_into_pieces(text: str) -> list[str]:
    return [piece.strip() for piece in re.split(_KEYWORD_SPLIT_PATTERN, text) if piece.strip()]


def _extract_body_nouns(body_text: str) -> list[str]:
    """本文から名詞のみを抽出する（1文字名詞・ストップワードは除外）。"""
    nouns = []
    for word in _tagger(body_text):
        if word.feature.pos1 != _NOUN_POS:
            continue
        surface = word.surface.strip()
        if len(surface) < _NOUN_MIN_LENGTH or surface in _STOPWORD_NOUNS:
            continue
        nouns.append(surface)
    return nouns


def _fallback_keywords(page: CrawlResult) -> list[str]:
    """ルールベースのキーワード候補生成。

    1. タイトル・meta description・見出し(h1〜h3)・meta keywordsを区切り文字で分割 → 主要候補
    2. 本文から形態素解析で頻出名詞を抽出 → 候補に追加
    3. alt属性・内部リンクの文字列も区切り文字で分割し、補助的に候補へ追加
    4. 全候補の出現回数を集計し、主要候補には優先度ボーナスを加算した上で、
       スコアが高い順に並べて重複のないキーワードリストを返す
    """
    primary_sources: list[str] = []
    if page.title:
        primary_sources.append(page.title)
    if page.meta_description:
        primary_sources.append(page.meta_description)
    primary_sources.extend(page.h1_list)
    primary_sources.extend(page.h2_list)
    primary_sources.extend(page.h3_list)
    primary_sources.extend(page.meta_keywords)

    secondary_sources: list[str] = [*page.alt_texts, *page.internal_link_texts]

    scores: Counter[str] = Counter()
    order: dict[str, int] = {}  # 同スコア時の安定した並び順（先に出現した候補を優先）

    def _add(pieces: list[str], weight: int) -> None:
        for piece in pieces:
            if piece not in order:
                order[piece] = len(order)
            scores[piece] += weight

    for text in primary_sources:
        _add(_split_into_pieces(text), _PRIMARY_SOURCE_BONUS)

    if page.body_text:
        _add(_extract_body_nouns(page.body_text), 1)

    for text in secondary_sources:
        _add(_split_into_pieces(text), 1)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], order[item[0]]))
    return [keyword for keyword, _ in ranked[: settings.max_keywords]]


async def extract_keywords(page: CrawlResult) -> list[str]:
    """title/meta description/見出し/本文からキーワードを推定する。失敗時は簡易抽出にフォールバック。"""
    prompt = f"""以下はWebページから抽出した情報です。この情報だけを根拠に、\
このページが検索エンジンで上位表示を狙うべき主要キーワードを最大{settings.max_keywords}個推定してください。

タイトル: {page.title}
meta description: {page.meta_description}
meta keywords: {", ".join(page.meta_keywords)}
見出し(h1): {", ".join(page.h1_list)}
見出し(h2): {", ".join(page.h2_list)}
見出し(h3): {", ".join(page.h3_list)}
画像alt属性: {", ".join(page.alt_texts[:30])}
内部リンクの文字列: {", ".join(page.internal_link_texts[:30])}
本文冒頭: {page.body_text[:1500]}

{_NO_HALLUCINATION_NOTE}
タイトル・meta description・見出し(h1〜h3)・meta keywordsが「・」「、」「/」「|」などの\
区切り文字や括弧で複数の語句を並べている場合は、それを1つのキーワードとしてまとめず、\
区切られた語句それぞれを個別のキーワードとして抽出してください。
（例：「包茎手術・亀頭増大術・長茎術・ED・性病検査」というタイトルなら、\
「包茎手術」「亀頭増大術」「長茎術」「ED」「性病検査」の5個として扱う）
画像alt属性・内部リンクの文字列・本文はキーワードの裏付けを取る補助情報として使い、\
タイトルや見出しに現れない語句でも本文中で繰り返し使われているものがあれば候補に含めてください。
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
文章は300字程度で、サイト運営者にそのまま提示できる自然な文章にしてください。"""

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


async def filter_same_industry_candidates(own_title: str, candidates: list[tuple[str, str]]) -> list[str]:
    """競合候補のうち、自社と同じ業種・サービスを扱っているとみられるドメインのみを返す。

    候補は (domain, title) のタプルのリスト。辞書・Q&Aポータル（kotobank.jp・Yahoo!知恵袋等）のように
    ドメインパターンや実測値の自動除外（pipeline.py）では弾けない「見た目は普通のサイトだが業種が
    無関係」なケースに対応する（2026-08-24追加）。スコアには一切使わない、競合候補の足切り専用。
    判定不能時（API失敗・出力形式不正）は全候補をそのまま通す（フェイルオープン。CLAUDE.md
    「部分成功」原則）。
    """
    if not candidates:
        return []

    candidates_text = "\n".join(f"{i + 1}. {domain}: {title}" for i, (domain, title) in enumerate(candidates))
    prompt = f"""自社サイトのタイトルと、検索結果で見つかった候補サイトのタイトル一覧です。\
各候補が自社と同じ業種・サービスを扱っているか（本当の競合関係にあるか）を判定してください。

自社サイトのタイトル: {own_title}

候補サイト一覧:
{candidates_text}

辞書サイト・Q&Aポータル・用語解説ページなど、自社と同じ業種・サービスを扱っていない候補は「同じ業種ではない」と判定してください。
タイトルの情報のみを根拠に判定し、推測で業種を決めつけないでください。
出力は各候補が同じ業種かどうかを表すJSON配列のみとしてください（例: [true, false, true]）。\
配列の要素数は候補の数と同じにし、説明文は一切出力しないでください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=256,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _strip_code_fence(response.content[0].text)
        flags = json.loads(raw)
        if not isinstance(flags, list) or len(flags) != len(candidates) or not all(isinstance(f, bool) for f in flags):
            raise LlmError("業種判定の出力形式が不正です")
        return [domain for (domain, _), is_same in zip(candidates, flags) if is_same]
    except Exception:
        return [domain for domain, _ in candidates]


def _fallback_improvement_advice(total_score: int, band: str, weak_points: list[str]) -> str:
    band_text = {
        "good": "全体として良好な状態です。",
        "fair": "改善の余地はありますが基礎はできています。",
        "weak": "いくつかの重要な項目で改善が必要です。",
        "poor": "多くの項目で改善が必要な状態です。",
    }.get(band, "")

    points = "、".join(weak_points[:3]) if weak_points else "特定の弱点"
    return (
        f"診断の結果、総合スコアは{total_score}点でした。{band_text}"
        f"特に{points}を改善することで、検索エンジンおよびAI検索での見え方の向上が期待できます。"
        "まずは着手しやすい項目から順に改善していくことをおすすめします。"
    )


async def improvement_advice(total_score: int, band: str, weak_points: list[str]) -> str:
    """顧客向けの改善アドバイス文（4〜5文・300字以内）。失敗時はテンプレート文にフォールバック。"""
    prompt = f"""以下はWebサイトのSEO/LLMO診断結果です。この数値のみを根拠に、\
サイトの運営者・担当者に向けた改善アドバイスの文章を作成してください。

総合スコア: {total_score}点（100点満点）
評価: {band}
弱点項目（スコアが低い順）: {", ".join(weak_points)}

{_NO_HALLUCINATION_NOTE}
「〜という状態です」で終わる現状説明ではなく、「〜を改善しましょう」「〜に取り組むことをおすすめします」のように、\
読み手が次に何をすべきか分かるアドバイスとして書いてください。
4〜5文・300字以内で、日本語の自然な文章にしてください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if not text:
            raise LlmError("改善アドバイスが空です")
        return text
    except Exception:
        return _fallback_improvement_advice(total_score, band, weak_points)


def _fallback_category_advice(band: str, weak_items: list[str]) -> str:
    band_text = {
        "good": "全体として良好な状態です。",
        "fair": "改善の余地はありますが基礎はできています。",
        "weak": "いくつかの項目で改善が必要です。",
        "poor": "多くの項目で改善が必要な状態です。",
    }.get(band, "")
    if weak_items:
        return f"{band_text}特に{weak_items[0]}を改善することをおすすめします。"
    return f"{band_text}改善の余地はあります。"


async def category_advice(category_label: str, band: str, items: list[dict]) -> str:
    """カテゴリ別スコアの下に添える改善アドバイス（右上の「改善アドバイス」と同様、実測値を根拠にした具体的な文章）。

    items はそのカテゴリ内の各評価項目（label・evidence・score・max_score）。サイト個別の
    実測値に依存するため improvement_advice() と同様にキャッシュしない。失敗時は評価点の低い
    項目名を根拠にした固定文にフォールバックする。
    """
    items_text = "\n".join(
        f"- {i['label']}: {i['evidence']}（{i['score']}/{i['max_score']}点）" for i in items
    )
    weak_items = [
        i["label"] for i in sorted(items, key=lambda i: (i["score"] / i["max_score"]) if i["max_score"] else 0)
    ]

    prompt = f"""以下はWebサイトのSEO/LLMO診断結果のうち、「{category_label}」カテゴリの実測データです。\
この数値のみを根拠に、サイトの運営者・担当者に向けた改善アドバイスの文章を作成してください。

カテゴリ: {category_label}
評価: {band}（good=良好, fair=標準, weak=要改善, poor=急ぎ改善）
項目別の実測値:
{items_text}

{_NO_HALLUCINATION_NOTE}
「〜という状態です」で終わる現状説明ではなく、「〜を改善しましょう」「〜に取り組むことをおすすめします」のように、\
読み手が次に何をすべきか分かるアドバイスとして書いてください。
1〜2文・100字以内で、日本語の自然な文章にしてください。"""

    try:
        response = await _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=256,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if not text:
            raise LlmError("カテゴリアドバイスが空です")
        return text
    except Exception:
        return _fallback_category_advice(band, weak_items)
