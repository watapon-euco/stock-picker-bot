"""
theme_history.json への推奨銘柄追記ロジックのテスト

_record_theme_history の動作を atomic_write_json をモックして検証する。
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call

from src.step1_research import _record_theme_history, _load_full_theme_history


# ─────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────────

def _make_structured_themes(theme_name: str, stocks: list) -> list:
    return [{"theme_name": theme_name, "stocks": stocks, "failed_codes": []}]


def _make_stock(code: str, name: str, current_price: float, rank: int = None, market: str = None) -> dict:
    s = {"code": code, "name": name, "current_price": current_price}
    if rank is not None:
        s["rank"] = rank
    if market is not None:
        s["market"] = market
    return s


# ─────────────────────────────────────────────────────────────────────────────
# テスト 1: 新規月のエントリが正しく追記される
# ─────────────────────────────────────────────────────────────────────────────

def test_new_month_entry_appended(tmp_path):
    """既存データにない年月のエントリが正しく追加される"""
    existing_history = {
        "themes": [
            {"name": "旧テーマ", "year_month": "2026-03", "icon": "📈"}
        ]
    }

    structured = _make_structured_themes(
        "新テーマ",
        [_make_stock("7011", "三菱重工業", 12345.67, rank=1)]
    )
    themes_meta = [{"name": "新テーマ", "icon": "🚀"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value=existing_history), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    themes = written["data"]["themes"]
    names = [t["name"] for t in themes]
    assert "旧テーマ" in names
    assert "新テーマ" in names

    new_entry = next(t for t in themes if t["name"] == "新テーマ")
    assert new_entry["year_month"] == "2026-05"
    assert new_entry["icon"] == "🚀"
    assert len(new_entry["stocks"]) == 1
    assert new_entry["stocks"][0]["code"] == "7011"
    assert new_entry["stocks"][0]["price_at_pick"] == pytest.approx(12345.67)


# ─────────────────────────────────────────────────────────────────────────────
# テスト 2: 同月・同テーマの再実行で重複追加されない（上書き）
# ─────────────────────────────────────────────────────────────────────────────

def test_same_month_same_theme_overwrites_not_duplicates(tmp_path):
    """同じ年月・同じテーマ名で再実行すると既存エントリが置換される"""
    existing_history = {
        "themes": [
            {
                "name": "防衛テーマ",
                "year_month": "2026-05",
                "icon": "🛡️",
                "stocks": [{"code": "7011", "name": "旧データ", "rank": 1, "price_at_pick": 999.0}]
            }
        ]
    }

    structured = _make_structured_themes(
        "防衛テーマ",
        [_make_stock("7011", "三菱重工業", 12000.0, rank=1)]
    )
    themes_meta = [{"name": "防衛テーマ", "icon": "🛡️"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value=existing_history), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    themes = written["data"]["themes"]
    # 重複なし: 同テーマのエントリは1件だけ
    matching = [t for t in themes if t["name"] == "防衛テーマ" and t["year_month"] == "2026-05"]
    assert len(matching) == 1
    # 新しいデータで上書きされている
    assert matching[0]["stocks"][0]["price_at_pick"] == pytest.approx(12000.0)
    assert matching[0]["stocks"][0]["name"] == "三菱重工業"


# ─────────────────────────────────────────────────────────────────────────────
# テスト 3: 旧形式（stocks フィールドなし）のエントリが消えない
# ─────────────────────────────────────────────────────────────────────────────

def test_legacy_entries_without_stocks_preserved():
    """stocks フィールドを持たない旧エントリはそのまま残る"""
    existing_history = {
        "themes": [
            {"name": "旧テーマA", "year_month": "2026-03", "icon": "📊"},
            {"name": "旧テーマB", "year_month": "2026-04", "icon": "💹"},
        ]
    }

    structured = _make_structured_themes(
        "新テーマC",
        [_make_stock("7203", "トヨタ", 2500.0)]
    )
    themes_meta = [{"name": "新テーマC", "icon": "🚗"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value=existing_history), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    names = [t["name"] for t in written["data"]["themes"]]
    assert "旧テーマA" in names
    assert "旧テーマB" in names
    assert "新テーマC" in names


# ─────────────────────────────────────────────────────────────────────────────
# テスト 4: 銘柄リストが最大10件で切られる
# ─────────────────────────────────────────────────────────────────────────────

def test_stocks_capped_at_10():
    """11件以上の銘柄が渡されても、記録されるのは上位10件だけ"""
    stocks_15 = [_make_stock(str(1000 + i), f"銘柄{i}", float(100 + i)) for i in range(15)]

    structured = _make_structured_themes("テーマX", stocks_15)
    themes_meta = [{"name": "テーマX", "icon": "💡"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    entry = written["data"]["themes"][0]
    assert len(entry["stocks"]) == 10


# ─────────────────────────────────────────────────────────────────────────────
# テスト 5: price_at_pick に current_price が入る
# ─────────────────────────────────────────────────────────────────────────────

def test_price_at_pick_uses_current_price():
    """stock の current_price が price_at_pick として記録される"""
    structured = _make_structured_themes(
        "テーマP",
        [_make_stock("8035", "東京エレクトロン", 34567.89)]
    )
    themes_meta = [{"name": "テーマP", "icon": "⚡"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    stock = written["data"]["themes"][0]["stocks"][0]
    assert stock["price_at_pick"] == pytest.approx(34567.89)
    assert stock["code"] == "8035"
    assert stock["name"] == "東京エレクトロン"


# ─────────────────────────────────────────────────────────────────────────────
# テスト 6: 書き込み失敗時に例外が伝播しない
# ─────────────────────────────────────────────────────────────────────────────

def test_write_failure_does_not_raise():
    """atomic_write_json が例外を投げてもメインパイプラインに影響しない"""
    structured = _make_structured_themes(
        "テーマE",
        [_make_stock("9984", "ソフトバンク", 7000.0)]
    )
    themes_meta = [{"name": "テーマE", "icon": "📱"}]

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=OSError("disk full")), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        # 例外が外に漏れないこと
        _record_theme_history(structured, themes_meta)


# ─────────────────────────────────────────────────────────────────────────────
# テスト 7: rank フィールドがない場合にインデックス+1 が使われる
# ─────────────────────────────────────────────────────────────────────────────

def test_rank_defaults_to_index_plus_one():
    """rank フィールドが stock に存在しない場合、0始まりのインデックス+1 が使われる"""
    stocks = [
        {"code": "1001", "name": "銘柄A", "current_price": 100.0},
        {"code": "1002", "name": "銘柄B", "current_price": 200.0},
    ]
    structured = [{"theme_name": "テーマR", "stocks": stocks, "failed_codes": []}]
    themes_meta = [{"name": "テーマR", "icon": "🎯"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    entry_stocks = written["data"]["themes"][0]["stocks"]
    assert entry_stocks[0]["rank"] == 1
    assert entry_stocks[1]["rank"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# テスト 8: テーマメタにない場合のデフォルト icon
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_icon_in_meta_uses_default():
    """themes_meta にテーマが存在しない場合、icon はデフォルト 📊 になる"""
    structured = _make_structured_themes(
        "未知テーマ",
        [_make_stock("9999", "謎の銘柄", 500.0)]
    )
    themes_meta = []  # icon 情報なし

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    entry = written["data"]["themes"][0]
    assert entry["icon"] == "📊"


# ─────────────────────────────────────────────────────────────────────────────
# テスト 9: JP 銘柄の market フィールドが保持される
# ─────────────────────────────────────────────────────────────────────────────

def test_market_field_preserved_jp():
    """JP 銘柄の market フィールドが theme_history.json に正しく保存される"""
    structured = _make_structured_themes(
        "日本AI関連",
        [_make_stock("6758", "ソニーグループ", 12500.0, rank=1, market="JP")]
    )
    themes_meta = [{"name": "日本AI関連", "icon": "🤖"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    stock = written["data"]["themes"][0]["stocks"][0]
    assert stock["market"] == "JP"
    assert stock["code"] == "6758"


# ─────────────────────────────────────────────────────────────────────────────
# テスト 10: US 銘柄の market フィールドが保持される
# ─────────────────────────────────────────────────────────────────────────────

def test_market_field_preserved_us():
    """US 銘柄の market フィールドが theme_history.json に正しく保存される"""
    structured = _make_structured_themes(
        "米国EV関連",
        [_make_stock("TSLA", "Tesla Inc.", 185.50, rank=1, market="US")]
    )
    themes_meta = [{"name": "米国EV関連", "icon": "⚡"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    stock = written["data"]["themes"][0]["stocks"][0]
    assert stock["market"] == "US"
    assert stock["code"] == "TSLA"
    assert stock["price_at_pick"] == pytest.approx(185.50)


# ─────────────────────────────────────────────────────────────────────────────
# テスト 11: market フィールドがない場合は "JP" がデフォルト値になる
# ─────────────────────────────────────────────────────────────────────────────

def test_market_field_defaults_to_jp():
    """market フィールドがない銘柄は "JP" がデフォルト値として保存される"""
    structured = _make_structured_themes(
        "テーマZ",
        [{"code": "7203", "name": "トヨタ自動車", "current_price": 2800.0}]
    )
    themes_meta = [{"name": "テーマZ", "icon": "🚗"}]

    written = {}

    def fake_atomic_write(path, data):
        written["data"] = data

    with patch("src.step1_research._load_full_theme_history", return_value={"themes": []}), \
         patch("src.step1_research.atomic_write_json", side_effect=fake_atomic_write), \
         patch("src.step1_research.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-05"
        _record_theme_history(structured, themes_meta)

    stock = written["data"]["themes"][0]["stocks"][0]
    assert stock["market"] == "JP"
