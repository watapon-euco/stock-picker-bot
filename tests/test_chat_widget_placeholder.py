"""CHAT_PROXY_URL プレースホルダーの動作テスト"""
import os
from pathlib import Path
from unittest.mock import patch

TEMPLATE_PATH = Path("src/templates/report_template.html")


def _load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_chat_proxy_url_placeholder_in_template():
    content = _load_template()
    assert "{{CHAT_WIDGET_SECTION}}" in content, "{{CHAT_WIDGET_SECTION}} placeholder missing from template"


def test_chat_widget_html_present():
    # Widget HTML is now Python-generated via build_chat_widget_section(); the
    # template only contains the {{CHAT_WIDGET_SECTION}} placeholder.
    content = _load_template()
    assert "{{CHAT_WIDGET_SECTION}}" in content


def test_widget_hidden_when_url_empty():
    from src.step2_report import build_chat_widget_section
    assert build_chat_widget_section("") == ""


def test_step2_replaces_chat_proxy_url_env_set():
    """Verify that {{CHAT_PROXY_URL}} is substituted with the env var value."""
    expected_url = "https://example.vercel.app/api/ask"
    template = "{{CHAT_PROXY_URL}}"
    with patch.dict(os.environ, {"CHAT_PROXY_URL": expected_url}):
        result = template.replace("{{CHAT_PROXY_URL}}", os.environ.get("CHAT_PROXY_URL", ""))
    assert result == expected_url, f"Expected '{expected_url}', got '{result}'"


def test_step2_replaces_chat_proxy_url_env_unset():
    env_without_proxy = {k: v for k, v in os.environ.items() if k != "CHAT_PROXY_URL"}
    with patch.dict(os.environ, env_without_proxy, clear=True):
        proxy_url = os.environ.get("CHAT_PROXY_URL", "")
    assert proxy_url == ""


def test_no_api_key_in_template():
    content = _load_template()
    assert "sk-ant-" not in content, "API key must not appear in the template"
    assert "ANTHROPIC_API_KEY" not in content, "API key env var name must not appear in template"


def test_chat_widget_sends_messages_array():
    """ウィジェットは proxy/api/ask.js が期待する messages 配列形式で送信する。
    旧実装は単数 message を送っており proxy 側で 400 になり機能しなかった。
    """
    from src.step2_report import build_chat_widget_section

    html_out = build_chat_widget_section("https://example.vercel.app/api/ask")
    assert "messages:[{role:'user',content:q}]" in html_out
    # 旧形式の単数 message は送らない
    assert "message:q" not in html_out
