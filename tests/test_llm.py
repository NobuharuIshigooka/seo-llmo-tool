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

    assert result == ["サンプルタイトル", "メイン見出し", "小見出し1", "小見出し2"]


async def test_extract_keywords_falls_back_on_invalid_json():
    mock_create = AsyncMock(return_value=_mock_response("not json"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.extract_keywords(_page())

    assert result == ["サンプルタイトル", "メイン見出し", "小見出し1", "小見出し2"]


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


async def test_sales_summary_returns_text():
    mock_create = AsyncMock(return_value=_mock_response("総合スコアは72点でした。"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.sales_summary(72, "fair", ["表示速度", "構造化データ"])

    assert result == "総合スコアは72点でした。"


async def test_sales_summary_falls_back_on_failure():
    mock_create = AsyncMock(side_effect=Exception("api error"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.sales_summary(72, "fair", ["表示速度", "構造化データ"])

    assert "72点" in result
    assert "表示速度" in result


async def test_sales_summary_excludes_backlink_wording_is_caller_responsibility():
    # weak_points に被リンク関連を含めない前提で呼び出す（scoring.py 側の責務）
    mock_create = AsyncMock(return_value=_mock_response("被リンクには触れない要約文です。"))
    with patch.object(llm, "_client", return_value=SimpleNamespace(messages=SimpleNamespace(create=mock_create))):
        result = await llm.sales_summary(50, "weak", ["表示速度"])

    assert "被リンク" not in result or "被リンクには触れない" in result
