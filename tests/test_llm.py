from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.clients import llm
from app.clients.crawler import CrawlResult


def _mock_response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _page(**overrides) -> CrawlResult:
    base = dict(
        url="https://example.co.jp/",
        title="サンプルタイトル | 会社名",
        meta_description="説明文です。",
        h1_list=["メイン見出し"],
        h2_list=["小見出し1", "小見出し2"],
        body_text="本文冒頭です。",
    )
    base.update(overrides)
    return CrawlResult(**base)


async def test_extract_keywords_parses_json_array():
    mock_create = AsyncMock(return_value=_mock_response('["キーワード1", "キーワード2"]'))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.extract_keywords(_page())

    assert result == ["キーワード1", "キーワード2"]


async def test_extract_keywords_strips_code_fence():
    mock_create = AsyncMock(return_value=_mock_response('```json\n["キーワード1"]\n```'))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.extract_keywords(_page())

    assert result == ["キーワード1"]


async def test_extract_keywords_falls_back_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.extract_keywords(_page())

    assert result == [
        "サンプルタイトル", "会社名", "説明文です", "メイン見出し", "小見出し1", "小見出し2", "本文", "冒頭",
    ]


async def test_extract_keywords_falls_back_on_invalid_json():
    mock_create = AsyncMock(return_value=_mock_response("not json"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.extract_keywords(_page())

    assert result == [
        "サンプルタイトル", "会社名", "説明文です", "メイン見出し", "小見出し1", "小見出し2", "本文", "冒頭",
    ]


async def test_fallback_keywords_splits_on_nakaguro_and_punctuation():
    page = _page(
        title="包茎手術・亀頭増大術・長茎術・ED・性病検査",
        meta_description="",
        h1_list=[],
        h2_list=[],
        body_text="",
    )
    result = llm._fallback_keywords(page)

    assert result == ["包茎手術", "亀頭増大術", "長茎術", "ED", "性病検査"]


async def test_fallback_keywords_includes_meta_description_and_h3():
    page = _page(
        title="",
        meta_description="腰痛治療・肩こり改善なら当院へ",
        h1_list=[],
        h2_list=[],
        h3_list=["初診の流れ・料金について"],
        body_text="",
    )
    result = llm._fallback_keywords(page)

    assert result == ["腰痛治療", "肩こり改善なら当院へ", "初診の流れ", "料金について"]


async def test_fallback_keywords_extracts_frequent_nouns_from_body():
    page = _page(
        title="",
        meta_description="",
        h1_list=[],
        h2_list=[],
        body_text="腰痛治療は当院の得意分野です。腰痛でお悩みの方は腰痛専門の当院へご相談ください。",
    )
    result = llm._fallback_keywords(page)

    assert result[0] == "腰痛"  # 3回出現し最頻出


async def test_fallback_keywords_prioritizes_primary_sources_over_body_frequency():
    page = _page(
        title="肩こり改善メニュー",
        meta_description="",
        h1_list=[],
        h2_list=[],
        body_text="腰痛腰痛腰痛腰痛腰痛腰痛。",  # 本文中でどれだけ頻出しても
    )
    result = llm._fallback_keywords(page)

    assert result[0] in ("肩こり改善メニュー",)  # タイトル由来が本文頻度に埋もれない


async def test_fallback_keywords_uses_alt_text_and_internal_link_text():
    page = _page(
        title="",
        meta_description="",
        h1_list=[],
        h2_list=[],
        body_text="",
        alt_texts=["訪問看護・介護サービス"],
        internal_link_texts=["料金プラン一覧"],
    )
    result = llm._fallback_keywords(page)

    assert "訪問看護" in result
    assert "介護サービス" in result
    assert "料金プラン一覧" in result


async def test_fallback_keywords_deduplicates_across_sources():
    page = _page(
        title="腰痛治療",
        meta_description="",
        h1_list=["腰痛治療"],
        h2_list=[],
        body_text="",
    )
    result = llm._fallback_keywords(page)

    assert result.count("腰痛治療") == 1


async def test_explain_competitors_returns_text():
    mock_create = AsyncMock(return_value=_mock_response("自社は競合より表示速度が遅いです。"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.explain_competitors("example.co.jp", [{"metric": "psi", "own": 40, "competitor": 80}])

    assert result == "自社は競合より表示速度が遅いです。"


async def test_explain_competitors_returns_none_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.explain_competitors("example.co.jp", [])

    assert result is None


async def test_improvement_advice_returns_text():
    mock_create = AsyncMock(return_value=_mock_response("総合スコアは72点でした。表示速度の改善をおすすめします。"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.improvement_advice(72, "fair", ["表示速度", "構造化データ"])

    assert result == "総合スコアは72点でした。表示速度の改善をおすすめします。"


async def test_improvement_advice_falls_back_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.improvement_advice(72, "fair", ["表示速度", "構造化データ"])

    assert "72点" in result
    assert "表示速度" in result


_CATEGORY_ITEMS = [
    {"label": "表示速度（モバイル）", "evidence": "表示速度スコア 40／100", "score": 15.0, "max_score": 40},
    {"label": "モバイル対応", "evidence": "viewportメタタグ なし", "score": 0.0, "max_score": 15},
]


async def test_category_advice_returns_text():
    mock_create = AsyncMock(return_value=_mock_response("表示速度とモバイル対応を改善すると、より多くのユーザーに見てもらえます。"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.category_advice("テクニカルSEO", "fair", _CATEGORY_ITEMS)

    assert result == "表示速度とモバイル対応を改善すると、より多くのユーザーに見てもらえます。"


async def test_category_advice_falls_back_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.category_advice("テクニカルSEO", "poor", _CATEGORY_ITEMS)

    assert "モバイル対応" in result


async def test_filter_same_industry_candidates_returns_matching_only():
    mock_create = AsyncMock(return_value=_mock_response("[true, false]"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.filter_same_industry_candidates(
            "〇〇病院｜内科・外科",
            [("real-competitor.co.jp", "△△クリニック"), ("kotobank.jp", "北野とは - コトバンク")],
        )

    assert result == ["real-competitor.co.jp"]


async def test_filter_same_industry_candidates_empty_input_returns_empty():
    result = await llm.filter_same_industry_candidates("〇〇病院", [])
    assert result == []


async def test_filter_same_industry_candidates_falls_back_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    candidates = [("a.co.jp", "A社"), ("b.co.jp", "B社")]
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.filter_same_industry_candidates("自社タイトル", candidates)

    assert result == ["a.co.jp", "b.co.jp"]  # 判定失敗時はフェイルオープンで全候補を通す


async def test_filter_same_industry_candidates_falls_back_on_length_mismatch():
    mock_create = AsyncMock(return_value=_mock_response("[true]"))  # 候補数2に対し要素数1（不正な出力）
    candidates = [("a.co.jp", "A社"), ("b.co.jp", "B社")]
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.filter_same_industry_candidates("自社タイトル", candidates)

    assert result == ["a.co.jp", "b.co.jp"]
