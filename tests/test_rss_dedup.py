"""ニュース近重複除去（rss_fetcher.deduplicate_articles）のテスト"""
from src.utils.rss_fetcher import (
    _normalize_title,
    deduplicate_articles,
)


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_title
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeTitle:
    def test_strips_source_suffix(self):
        assert _normalize_title("トヨタが最高益 - 日本経済新聞") == _normalize_title("トヨタが最高益")

    def test_removes_punctuation_and_space(self):
        a = _normalize_title("日本株、上昇！")
        b = _normalize_title("日本株 上昇")
        assert a == b

    def test_short_title_not_emptied(self):
        # サフィックス除去で全部消える場合は元を使う
        assert _normalize_title("速報 - NHK") != ""

    def test_lowercase(self):
        assert _normalize_title("SONY Group") == _normalize_title("sony group")


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate_articles
# ─────────────────────────────────────────────────────────────────────────────

def _arts(*titles):
    return [{"title": t, "link": f"https://ex.com/{i}"} for i, t in enumerate(titles)]


class TestDeduplicateArticles:
    def test_empty(self):
        assert deduplicate_articles([]) == []

    def test_exact_duplicate_removed(self):
        out = deduplicate_articles(_arts("日本株が上昇", "日本株が上昇"))
        assert len(out) == 1

    def test_same_story_different_source_removed(self):
        out = deduplicate_articles(_arts(
            "トヨタが通期最高益を更新 - 日本経済新聞",
            "トヨタが通期最高益を更新 - ロイター",
        ))
        assert len(out) == 1

    def test_distinct_stories_kept(self):
        out = deduplicate_articles(_arts(
            "トヨタが最高益を更新",
            "ソニーが新型センサーを発表",
            "日銀が金利を据え置き",
        ))
        assert len(out) == 3

    def test_preserves_input_order(self):
        out = deduplicate_articles(_arts(
            "ソニーが新型センサーを発表",
            "日銀が金利を据え置き",
        ))
        assert out[0]["title"].startswith("ソニー")
        assert out[1]["title"].startswith("日銀")

    def test_keeps_first_occurrence(self):
        out = deduplicate_articles(_arts(
            "半導体株が急騰 - 日経",
            "半導体株が急騰 - 朝日",
        ))
        assert len(out) == 1
        assert "日経" in out[0]["title"]

    def test_near_duplicate_containment_removed(self):
        # 同一見出しに追記（詳報）が付いただけのものは重複として除去
        out = deduplicate_articles(_arts(
            "日経平均が3万9000円台を回復",
            "日経平均が3万9000円台を回復、半年ぶりの高値 - 日経",
        ))
        assert len(out) == 1

    def test_missing_title_skipped(self):
        out = deduplicate_articles([{"title": ""}, {"title": "有効な記事タイトル"}])
        assert len(out) == 1
