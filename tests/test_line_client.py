"""LINE クライアントの Flex Message 構造・送信ロジックのテスト"""
from unittest import mock

import pytest

from src.utils.line_client import LineClient


# ─────────────────────────────────────────────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    return LineClient(channel_token="test-token")


_THEMES = [
    {
        "name": "防衛・宇宙テック",
        "icon": "🚀",
        "summary": "防衛関連の政府支出増加と宇宙開発の民営化が追い風となるテーマ。",
        "sector_overlap_warning": False,
    },
    {
        "name": "生成AIインフラ",
        "icon": "🤖",
        "summary": "大規模言語モデルの普及を支えるデータセンター・半導体・電力関連。",
        "sector_overlap_warning": True,
    },
]

_STOCK_DATA_THEMES = [
    {
        "theme_name": "防衛・宇宙テック",
        "stocks": [
            {"code": "7011", "name": "三菱重工業", "current_price": 2850, "change_pct": 1.2},
            {"code": "6861", "name": "キーエンス", "current_price": 65000, "change_pct": -0.5},
            {"code": "6758", "name": "ソニー", "current_price": 12000, "change_pct": 0.3},
            {"code": "9984", "name": "SoftBank G", "current_price": 9000, "change_pct": 2.1},
        ],
    },
    {
        "theme_name": "生成AIインフラ",
        "stocks": [
            {"code": "6857", "name": "アドバンテスト", "current_price": 5500, "change_pct": 3.0},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# send_flex: ペイロード構造の検証
# ─────────────────────────────────────────────────────────────────────────────

def test_send_flex_payload_structure(client):
    """send_flex が LINE API に正しい Flex Message ペイロードを送ること。"""
    mock_response = mock.Mock()
    mock_response.status_code = 200

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        result = client.send_flex(
            to="C12345",
            alt_text="テスト通知",
            flex_contents={"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}},
        )

    assert result is True
    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["to"] == "C12345"
    assert len(payload["messages"]) == 1
    msg = payload["messages"][0]
    assert msg["type"] == "flex"
    assert msg["altText"] == "テスト通知"
    assert msg["contents"]["type"] == "bubble"


def test_send_flex_alt_text_truncated_to_400(client):
    """altText が 400 文字を超える場合に切り詰められること。"""
    long_text = "あ" * 500
    mock_response = mock.Mock()
    mock_response.status_code = 200

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        client.send_flex(to="U1", alt_text=long_text, flex_contents={"type": "bubble"})

    payload = mock_post.call_args[1]["json"]
    assert len(payload["messages"][0]["altText"]) == 400


def test_send_flex_returns_false_on_api_error(client):
    """LINE API が 400 を返した場合に False を返すこと。"""
    mock_response = mock.Mock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    with mock.patch("requests.post", return_value=mock_response):
        result = client.send_flex(to="U1", alt_text="x", flex_contents={})

    assert result is False


def test_send_flex_returns_false_on_network_error(client):
    """ネットワーク例外が発生した場合に False を返すこと。"""
    import requests as req

    with mock.patch("requests.post", side_effect=req.RequestException("timeout")):
        result = client.send_flex(to="U1", alt_text="x", flex_contents={})

    assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# send_flex_report_notification: カルーセル構造の検証
# ─────────────────────────────────────────────────────────────────────────────

def _capture_flex_payload(client, themes, stock_data_themes, report_url="https://example.com/report"):
    """send_flex_report_notification を呼び出し、LINE API に渡されたペイロードを返す。"""
    mock_response = mock.Mock()
    mock_response.status_code = 200

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        result = client.send_flex_report_notification(
            to="C99",
            year_month="2026年5月",
            themes=themes,
            stock_data_themes=stock_data_themes,
            report_url=report_url,
        )

    return result, mock_post.call_args[1]["json"]


def test_carousel_type_and_bubble_count(client):
    """カルーセルの type が carousel で、バブル数がテーマ数と一致すること。"""
    result, payload = _capture_flex_payload(client, _THEMES, _STOCK_DATA_THEMES)

    assert result is True
    msg = payload["messages"][0]
    assert msg["type"] == "flex"
    contents = msg["contents"]
    assert contents["type"] == "carousel"
    assert len(contents["contents"]) == len(_THEMES)


def test_bubble_header_contains_theme_name(client):
    """各バブルのヘッダーにテーマ名が含まれること。"""
    _, payload = _capture_flex_payload(client, _THEMES, _STOCK_DATA_THEMES)
    bubbles = payload["messages"][0]["contents"]["contents"]

    for i, theme in enumerate(_THEMES):
        header_text = bubbles[i]["header"]["contents"][0]["text"]
        assert theme["name"] in header_text


def test_bubble_body_contains_top3_stocks_only(client):
    """Top3 銘柄のみ表示され、4番目以降が除外されること。"""
    _, payload = _capture_flex_payload(client, _THEMES[:1], _STOCK_DATA_THEMES)
    bubble = payload["messages"][0]["contents"]["contents"][0]

    body_contents = bubble["body"]["contents"]
    # separator + 最大3行の株行があるはず
    stock_text_items = [
        c for c in body_contents
        if c.get("type") == "box" and c.get("layout") == "horizontal"
    ]
    assert len(stock_text_items) == 3


def test_bubble_stock_row_price_format_with_change(client):
    """価格と前日比が両方ある場合に「¥X,XXX (+Y.Z%)」形式になること。"""
    _, payload = _capture_flex_payload(client, _THEMES[:1], _STOCK_DATA_THEMES)
    bubble = payload["messages"][0]["contents"]["contents"][0]

    stock_rows = [
        c for c in bubble["body"]["contents"]
        if c.get("type") == "box" and c.get("layout") == "horizontal"
    ]
    first_price_text = stock_rows[0]["contents"][1]["text"]
    assert "¥" in first_price_text
    assert "%" in first_price_text


def test_bubble_stock_row_price_only_when_no_change(client):
    """change_pct がない場合に価格のみ表示されること。"""
    stock_data = [
        {
            "theme_name": "防衛・宇宙テック",
            "stocks": [{"code": "7011", "name": "三菱重工業", "current_price": 2850}],
        }
    ]
    _, payload = _capture_flex_payload(client, _THEMES[:1], stock_data)
    bubble = payload["messages"][0]["contents"]["contents"][0]

    stock_rows = [
        c for c in bubble["body"]["contents"]
        if c.get("type") == "box" and c.get("layout") == "horizontal"
    ]
    price_text = stock_rows[0]["contents"][1]["text"]
    assert "¥" in price_text
    assert "%" not in price_text


def test_sector_warning_label_shown_when_flag_true(client):
    """sector_overlap_warning=True のバブルに警告ラベルが含まれること。"""
    _, payload = _capture_flex_payload(client, _THEMES, _STOCK_DATA_THEMES)
    bubbles = payload["messages"][0]["contents"]["contents"]

    # _THEMES[1] に sector_overlap_warning=True が設定されている
    warning_bubble = bubbles[1]
    body_texts = [
        c["text"] for c in warning_bubble["body"]["contents"]
        if c.get("type") == "text"
    ]
    assert any("分散注意" in t for t in body_texts)


def test_sector_warning_label_absent_when_flag_false(client):
    """sector_overlap_warning=False のバブルに警告ラベルがないこと。"""
    _, payload = _capture_flex_payload(client, _THEMES, _STOCK_DATA_THEMES)
    bubbles = payload["messages"][0]["contents"]["contents"]

    # _THEMES[0] は sector_overlap_warning=False
    normal_bubble = bubbles[0]
    body_texts = [
        c["text"] for c in normal_bubble["body"]["contents"]
        if c.get("type") == "text"
    ]
    assert not any("分散注意" in t for t in body_texts)


def test_footer_button_uri_matches_report_url(client):
    """フッターボタンの uri が REPORT_URL と一致すること。"""
    url = "https://example.com/my-report"
    _, payload = _capture_flex_payload(client, _THEMES[:1], _STOCK_DATA_THEMES, report_url=url)
    bubble = payload["messages"][0]["contents"]["contents"][0]
    button = bubble["footer"]["contents"][0]

    assert button["type"] == "button"
    assert button["action"]["type"] == "uri"
    assert button["action"]["uri"] == url


def test_bubble_header_text_truncated_to_40_chars(client):
    """ヘッダーテキストが 40 文字以内に切り詰められること。"""
    long_name_theme = [{"name": "あ" * 50, "icon": "💹", "summary": "", "sector_overlap_warning": False}]
    _, payload = _capture_flex_payload(client, long_name_theme, [])
    header_text = payload["messages"][0]["contents"]["contents"][0]["header"]["contents"][0]["text"]
    assert len(header_text) <= 40


def test_summary_truncated_to_80_chars(client):
    """summary が 80 文字を超える場合に切り詰めて末尾に「…」が付くこと。"""
    long_summary = "テ" * 100
    theme = [{"name": "テスト", "icon": "💹", "summary": long_summary, "sector_overlap_warning": False}]
    _, payload = _capture_flex_payload(client, theme, [])
    bubble = payload["messages"][0]["contents"]["contents"][0]
    summary_texts = [
        c["text"] for c in bubble["body"]["contents"]
        if c.get("type") == "text"
    ]
    assert any(t.endswith("…") for t in summary_texts)
    assert all(len(t) <= 81 for t in summary_texts)  # 80文字 + "…"


# ─────────────────────────────────────────────────────────────────────────────
# フォールバック: Flex 失敗時にテキスト送信が呼ばれること
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_to_text_on_flex_failure(client):
    """Flex Message 送信失敗時にテキストメッセージへフォールバックすること。"""
    flex_fail = mock.Mock()
    flex_fail.status_code = 500
    flex_fail.text = "Internal Server Error"

    text_ok = mock.Mock()
    text_ok.status_code = 200

    with mock.patch("requests.post", side_effect=[flex_fail, text_ok]) as mock_post:
        result = client.send_flex_report_notification(
            to="C1",
            year_month="2026年5月",
            themes=_THEMES[:1],
            stock_data_themes=_STOCK_DATA_THEMES,
            report_url="https://example.com",
        )

    assert result is True
    assert mock_post.call_count == 2
    # 2回目の呼び出しはテキストメッセージ
    fallback_payload = mock_post.call_args_list[1][1]["json"]
    assert fallback_payload["messages"][0]["type"] == "text"


def test_no_fallback_when_flex_succeeds(client):
    """Flex 送信成功時はテキスト送信を呼ばないこと（API 呼び出し 1 回のみ）。"""
    ok = mock.Mock()
    ok.status_code = 200

    with mock.patch("requests.post", return_value=ok) as mock_post:
        client.send_flex_report_notification(
            to="C1",
            year_month="2026年5月",
            themes=_THEMES,
            stock_data_themes=_STOCK_DATA_THEMES,
            report_url="https://example.com",
        )

    assert mock_post.call_count == 1


def test_fallback_when_no_themes(client):
    """テーマが空の場合にテキストフォールバックが呼ばれること。"""
    ok = mock.Mock()
    ok.status_code = 200

    with mock.patch("requests.post", return_value=ok) as mock_post:
        result = client.send_flex_report_notification(
            to="C1",
            year_month="2026年5月",
            themes=[],
            stock_data_themes=[],
            report_url="https://example.com",
        )

    assert result is True
    fallback_payload = mock_post.call_args[1]["json"]
    assert fallback_payload["messages"][0]["type"] == "text"
