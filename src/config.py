"""プロジェクト全体で共有する設定定数（インポート依存なし）"""

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
CLAUDE_MODEL = "claude-sonnet-4-6"

# yfinance 失敗時に Stooq をフォールバックとして試すか
ENABLE_STOOQ_FALLBACK = True
