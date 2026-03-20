# テーマ株レポート自動生成パイプライン

毎月1日に自動実行され、直近1ヶ月の株式市場ニュースから注目テーマを選定し、
関連銘柄をランキングしたHTMLレポートを GitHub Pages で公開します。

## パイプライン概要

```
毎月1日 JST 10:00（GitHub Actions cron）
    ↓
[Step 1] Google News RSS → Gemini 3.1 Flash-Lite
         テーマ抽出 → 関連銘柄リストアップ → yfinance で株価取得
    ↓
[Step 2] Claude Sonnet 4.6 Batch API
         5軸スコアリング・ランキング → HTML レポート生成
    ↓
[Step 3] docs/ に HTML を保存 → git commit & push → GitHub Pages 公開
    ↓
[Step 4] LINE Messaging API でグループに通知
```

## セットアップ

### 1. リポジトリの準備

```bash
git clone https://github.com/YOUR_USERNAME/stock-report.git
cd stock-report
```

### 2. GitHub Secrets の設定

リポジトリの **Settings → Secrets and variables → Actions** で以下を登録:

| Secret 名 | 説明 | 取得先 |
|-----------|------|--------|
| `GEMINI_API_KEY` | Gemini API キー | [Google AI Studio](https://aistudio.google.com/) |
| `ANTHROPIC_API_KEY` | Claude API キー | [Anthropic Console](https://console.anthropic.com/) |
| `LINE_CHANNEL_TOKEN` | LINE チャネルアクセストークン | [LINE Developers](https://developers.line.biz/) |
| `LINE_GROUP_ID` | 通知先のLINEグループID | LINE Developers コンソール |
| `REPORT_URL` | GitHub Pages の URL | 例: `https://username.github.io/stock-report` |

### 3. GitHub Pages の有効化

リポジトリの **Settings → Pages** で:
- Source: `Deploy from a branch`
- Branch: `main` / `docs` フォルダ

### 4. ローカル開発・テスト

```bash
# 依存関係のインストール
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .env を編集して各APIキーを設定

# 全ステップ実行
python -m src.main

# 特定ステップのみ実行
python -m src.main --step 1
python -m src.main --step 1 2
python -m src.step1_research  # 直接実行も可
```

### 5. 手動実行（GitHub Actions）

リポジトリの **Actions → Monthly Stock Report → Run workflow** から手動実行できます。

## ファイル構成

```
stock-report/
├── .github/workflows/monthly-report.yml  # GitHub Actions（月次cron）
├── src/
│   ├── main.py                # パイプライン全体のオーケストレーション
│   ├── step1_research.py      # ニュース収集・テーマ抽出・銘柄調査・株価取得
│   ├── step2_report.py        # Claude Batch API で分析＋HTML生成
│   ├── step3_deploy.py        # Git commit & push（GitHub Pages デプロイ）
│   ├── step4_notify.py        # LINE 通知送信
│   ├── templates/
│   │   └── report_template.html  # HTMLテンプレート
│   └── utils/
│       ├── gemini_client.py   # Gemini API ラッパー
│       ├── claude_batch.py    # Claude Batch API ラッパー
│       ├── yfinance_fetcher.py  # 株価データ取得
│       ├── rss_fetcher.py     # Google News RSS 取得
│       └── line_client.py     # LINE Messaging API
├── docs/                      # GitHub Pages 公開ディレクトリ
│   ├── index.html             # 最新号（自動更新）
│   └── archive/               # 過去号アーカイブ
│       └── index.html
├── data/
│   └── theme_history.json     # 過去テーマの履歴（重複回避用）
├── requirements.txt
├── .env.example
└── README.md
```

## 月額コスト目安

| 項目 | 月額 |
|------|------|
| Gemini 3.1 Flash-Lite | ~5円（無料枠内の可能性あり）|
| Claude Sonnet 4.6 Batch | ~10円 |
| yfinance / GitHub / LINE | 無料 |
| **合計** | **~15円** |

## 免責事項

本レポートはAIが自動生成した情報提供を目的としたものであり、
投資助言・勧誘を目的とするものではありません。
投資の最終判断はご自身の責任においてお行いください。
