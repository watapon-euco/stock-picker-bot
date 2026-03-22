"""Gemini出力バリデーション関数のテスト"""
import pytest

from src.utils.helpers import validate_candidates, validate_themes


# ─────────────────────────────────────────────────────────────────────────────
# validate_themes
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_themes_empty():
    assert validate_themes([]) == []


def test_validate_themes_valid():
    themes = validate_themes([{
        "name": "防衛・宇宙テック",
        "total_score": 30,
        "scores": {"policy_impact": 8, "market_size": 7, "novelty": 9, "sustainability": 6},
        "summary": "テーマ要約",
        "keywords": ["防衛", "宇宙"],
        "icon": "🚀",
        "source_articles": [1, 5, 12],
        "investment_angle": "防衛関連に注目",
    }])
    assert len(themes) == 1
    assert themes[0]["name"] == "防衛・宇宙テック"
    assert themes[0]["source_articles"] == [1, 5, 12]


def test_validate_themes_skips_non_dict():
    result = validate_themes(["not_a_dict", 42, None])
    assert result == []


def test_validate_themes_skips_missing_name():
    result = validate_themes([{"summary": "no name field"}])
    assert result == []


def test_validate_themes_fills_defaults():
    themes = validate_themes([{"name": "テーマ"}])
    assert len(themes) == 1
    t = themes[0]
    assert t["icon"] == "💹"
    assert t["source_articles"] == []
    assert t["investment_angle"] == ""
    assert isinstance(t["keywords"], list)


def test_validate_themes_filters_invalid_source_articles():
    themes = validate_themes([{
        "name": "テーマ",
        "source_articles": [1, "bad", None, 5, 3.14],
    }])
    assert themes[0]["source_articles"] == [1, 5]


# ─────────────────────────────────────────────────────────────────────────────
# validate_candidates
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_candidates_empty():
    assert validate_candidates([]) == []


def test_validate_candidates_valid():
    result = validate_candidates([{"code": "6758", "name": "ソニー", "relation": "direct"}])
    assert len(result) == 1
    assert result[0]["code"] == "6758"


def test_validate_candidates_pads_short_code():
    result = validate_candidates([{"code": "998"}])
    assert result[0]["code"] == "0998"


def test_validate_candidates_skips_alphabetic_code():
    result = validate_candidates([{"code": "SONY"}])
    assert result == []


def test_validate_candidates_skips_empty_code():
    result = validate_candidates([{"name": "no code"}])
    assert result == []


def test_validate_candidates_skips_non_dict():
    result = validate_candidates(["string", 123, None])
    assert result == []


def test_validate_candidates_fills_defaults():
    result = validate_candidates([{"code": "7013"}])
    assert result[0]["relation"] == "indirect"
    assert result[0]["reason"] == ""
    assert result[0]["name"] == "7013"
