# テーマ株レポート自動生成パイプライン

毎月1日に自動実行され、直近1ヶ月の株式市場ニュースから注目テーマを選定し、
関連銘柄をランキングしたHTMLレポートを GitHub Pages で公開します。
さらに週次速報・決算アラート・ウォッチリスト機能により、常時市場をモニタリングします。

## パイプライン概要

### 月次レポート（毎月1日 JST 10:00）

```
毎月1日 JST 10:00（GitHub Actions cron）
    ↓
[Step 1] Google News RSS → Gemini 3.1 Flash-Lite
         テーマ抽出 → テクニカル指標・セクター分析 → 銘柄リストアップ → 株価取得
    ↓
[Step 2] Claude Sonnet 4.6 Batch API
         5軸スコアリング・ランキング → リスク分析 → HTML レポート生成
         (Chart.js チャート, Flex Message カルーセル化)
    ↓
[Step 3] docs/ に HTML を保存 → git commit & push → GitHub Pages 公開
    ↓
[Step 4] LINE Messaging API でグループに通知
    ↓
[Step 6] 過去推奨銘柄のバックテストと成績ダッシュボード (performance.html) 生成
```

### 独立した並行ワークフロー

#### ウォッチリスト監視（毎平日 17:00）
```
[Step 5] data/watchlist.json に登録された銘柄の監視
         → 価格変化（指定幅）・出来高スパイク（2倍以上）を検知
         → LINE Flex Message で通知
```

#### 週次速報（毎週金曜 19:00）
```
[Step: step_weekly_flash]
    → 直近1週間の急騰銘柄 TOP5 の発掘
    → 注目ニュース 3 本の要約
    → docs/weekly/YYYY-WW.html 生成 + LINE Flex Message 配信
```

#### 決算アラート（毎平日朝 06:00）
```
[Step: step_earnings_check]
    → ウォッチリスト + 過去推奨銘柄の決算発表日を監視
    → 3日以内に決算がある場合は LINE Flex Message で通知
    → 重複通知を回避 (data/earnings_notified.json で管理)
```

#### パフォーマンス更新（毎月末）
```
[Step 6 (月次実行)]
    → theme_history.json から過去推奨銘柄を抽出
    → yfinance で価格データを再取得
    → バックテスト実行（推奨時と現在の価格比較）
    → docs/performance.html を更新
```

## 新機能の概要

### 分析の質の向上

**テクニカル指標** (Phase A2)
- 全銘柄に対し MA25, MA75, RSI, 出来高比率, 52週高安乖離率を計算・付加
- Chart.js による過去6ヶ月推移グラフを HTML に埋め込み（IntersectionObserver で遅延描画）

**セクター分散チェック** (Phase A4)
- 推奨銘柄が3テーマで同セクターに過度に偏中していないか自動警告
- ポートフォリオのセクター多様性を確保

**リスクシナリオ分析** (Phase A3)
- 各テーマに「失速条件」セクションを追加
- テーマの想定リスクと逆張りシナリオを明記

**サプライチェーン分析** (Phase D2)
- Top 3 銘柄について上流・下流の関連企業を列挙
- 関連度（direct/indirect）を明示

**銘柄比較表** (Phase D3)
- Top 5 銘柄を「成長性」「安定性」「割安性」の3軸で 5 段階評価
- 視覚的な比較で投資判断をサポート

**売買プラン** (Phase F1)
- 各テーマ上位5銘柄に「エントリー帯・損切りライン・目標株価・リスクリワード比・RSI」を付与
- MA25 等のサポートと52週高安から `src/utils/trade_levels.py` が決定論的に算出（LLM非依存で再現性あり）
- リスクリワード 1:2 以上を緑表示、RSI 70 以上で過熱（高値掴み）を警告
- 「何を買うか」だけでなく「いくらで買い・どこで損切り・どこを目標にするか」を提示

### ユーザー体験の向上

**チャット機能** (Phase D1)
- ブラウザ上で Claude に自由形式で質問可能（Vercel proxy 経由）
- オプション機能、未デプロイなら非表示

**LINE メッセージのリッチ化** (Phase E3)
- テキストベースから Flex Message カルーセル形式に変更
- 画像・リンク付きの見やすい通知

**チャート可視化** (Phase B1)
- 過去6ヶ月の価格推移を対話的に表示
- IntersectionObserver で画面内に入った時点でのみ描画（パフォーマンス最適化）

### 新規追跡機能

**ウォッチリスト** (Phase B3)
- `data/watchlist.json` に銘柄を手動登録
- GitHub Actions が毎平日 17:00 に価格変化・出来高を監視
- アラート条件に合致したら LINE 通知（Step 5）
- `last_prices` フィールドを GitHub Actions で自動更新

**バックテストダッシュボード** (Phase A1)
- `docs/performance.html` で過去推奨銘柄のパフォーマンスを可視化
- 推奨時から現在までの収益率（%）を一覧表示
- 月次で自動更新
- **対指数α（超過リターン）** (Phase F2): 推奨銘柄のリターンを同期間のベンチマーク
  （日本株=日経平均、米国株=S&P500）と比較し、市場に勝てているか（α>0）を可視化。
  「指数勝率」で個別銘柄が指数を上回った割合も表示

**週次速報** (Phase C1)
- 毎週金曜 19:00 に実行
- 直近1週間で +5% 以上かつ出来高が2倍以上の銘柄を抽出
- TOP 5 をランキング + 注目ニュース3本を要約
- `docs/weekly/YYYY-WW.html` に保存 + LINE で配信

**決算アラート** (Phase C2)
- ウォッチリストと過去推奨銘柄の決算日を一元監視
- 3日以内の決算を LINE で通知
- `data/earnings_notified.json` で重複を回避

### 信頼性向上

**Stooq フォールバック** (Phase E1)
- yfinance で株価取得に失敗したら Stooq から自動再取得
- データ欠落を最小化

**コスト・実行ログ** (Phase E2)
- `data/cost_log.json` に全 API 呼び出し記録（API, トークン数, コスト）
- `python -m src.utils.cost_report` で月別集計を表示
- 予算管理と使用状況の可視化

### 米国株対応** (Phase C3)
- 日本株（4桁、例: 9984）に加えて米国株（AAPL, GOOGL, MSFT 等）にも対応
- 銘柄ごとに `market: "JP" | "US"` フィールドで区別
- 通貨記号と価格フォーマットを自動切り替え（¥ 対 $）

## セットアップ

### 1. リポジトリの準備

```bash
git clone https://github.com/YOUR_USERNAME/stock-report.git
cd stock-report
```

### 2. GitHub Secrets の設定

リポジトリの **Settings → Secrets and variables → Actions** で以下を登録:

| Secret 名 | 説明 | 取得先 | 必須 |
|-----------|------|--------|------|
| `GEMINI_API_KEY` | Gemini API キー | [Google AI Studio](https://aistudio.google.com/) | ✓ |
| `ANTHROPIC_API_KEY` | Claude API キー | [Anthropic Console](https://console.anthropic.com/) | ✓ |
| `LINE_CHANNEL_TOKEN` | LINE チャネルアクセストークン | [LINE Developers](https://developers.line.biz/) | ✓ |
| `LINE_GROUP_ID` | 通知先のLINEグループID | LINE Developers コンソール | ✓ |
| `REPORT_URL` | GitHub Pages の URL | 例: `https://username.github.io/stock-report` | ✓ |
| `CHAT_PROXY_URL` | チャット Vercel proxy URL | デプロイ後に取得 | オプション |
| `WEEKLY_REPORT_BASE_URL` | 週次速報ベースURL | デフォルト: `REPORT_URL/weekly` | オプション |

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

# 特定ステップのみ実行（月次レポート）
python -m src.main --step 1
python -m src.main --step 1 2
python -m src.step1_research  # 直接実行も可

# 新機能の個別実行
python -m src.main --step 5                    # ウォッチリストチェック
python -m src.main --step 6                    # バックテスト・ダッシュボード生成
python -m src.step_weekly_flash                # 週次速報
python -m src.step_earnings_check              # 決算アラート
python -m src.utils.cost_report                # コスト集計表示
```

### 5. 手動実行（GitHub Actions）

リポジトリの **Actions** タブから各ワークフローを手動実行:
- **Monthly Stock Report**: 月次レポート（毎月1日の自動実行）
- **Weekly Flash Report**: 週次速報（毎週金曜の自動実行）
- **Earnings Check**: 決算アラート（毎平日朝の自動実行）
- **Watchlist Check**: ウォッチリスト監視（毎平日17時の自動実行）
- **Performance Update**: パフォーマンスダッシュボード更新（月次）

## ファイル構成

```
stock-report/
├── .github/workflows/
│   ├── monthly-report.yml              # 月次レポート (毎月1日)
│   ├── weekly-flash.yml                # 週次速報 (毎週金曜)
│   ├── earnings-check.yml              # 決算アラート (毎平日朝)
│   ├── watchlist-check.yml             # ウォッチリスト監視 (毎平日17時)
│   └── performance-update.yml          # パフォーマンス更新 (月次)
├── proxy/                              # Vercel proxy (チャット機能用、別途デプロイ)
│   └── README.md
├── src/
│   ├── main.py                         # パイプライン全体のオーケストレーション
│   ├── config.py                       # 設定（モデル選択等）
│   ├── step1_research.py               # テーマ抽出・銘柄調査・株価取得
│   ├── step2_report.py                 # 分析・スコアリング・HTML生成
│   ├── step3_deploy.py                 # Git commit & push
│   ├── step4_notify.py                 # LINE 通知送信
│   ├── step5_watchlist.py              # ウォッチリスト監視 [新規]
│   ├── step6_backtest.py               # バックテスト・ダッシュボード [新規]
│   ├── step_weekly_flash.py            # 週次速報 [新規]
│   ├── step_earnings_check.py          # 決算アラート [新規]
│   ├── templates/
│   │   ├── report_template.html        # 月次レポートテンプレート
│   │   ├── weekly_flash_template.html  # 週次速報テンプレート [新規]
│   │   └── performance_template.html   # パフォーマンスダッシュボード [新規]
│   └── utils/
│       ├── gemini_client.py            # Gemini API ラッパー
│       ├── claude_batch.py             # Claude Batch API ラッパー
│       ├── yfinance_fetcher.py         # 株価データ取得
│       ├── stooq_fetcher.py            # Stooq フォールバック [新規]
│       ├── rss_fetcher.py              # Google News RSS 取得
│       ├── line_client.py              # LINE Messaging API
│       ├── helpers.py                  # ユーティリティ関数
│       ├── ticker_utils.py             # 銘柄・通貨ユーティリティ [新規]
│       ├── cost_logger.py              # API コスト記録 [新規]
│       ├── cost_report.py              # コスト集計表示 [新規]
│       ├── watchlist_checker.py        # ウォッチリスト監視ロジック [新規]
│       ├── earnings_fetcher.py         # 決算日データ取得 [新規]
│       └── backtest.py                 # バックテストロジック [新規]
├── data/
│   ├── themes.json                     # 今月のテーマ（中間ファイル）
│   ├── candidates.json                 # 銘柄候補（中間ファイル）
│   ├── stock_data.json                 # 株価データ（中間ファイル）
│   ├── theme_history.json              # テーマ履歴と推奨銘柄（永続化）
│   ├── news_articles.json              # ニュース記事キャッシュ
│   ├── watchlist.json                  # ウォッチリスト [新規]
│   ├── earnings_notified.json          # 決算通知済み情報 [新規]
│   └── cost_log.json                   # API コスト・実行ログ [新規]
├── docs/                               # GitHub Pages 公開ディレクトリ
│   ├── index.html                      # 最新号レポート
│   ├── performance.html                # バックテストダッシュボード [新規]
│   ├── weekly/                         # 週次速報アーカイブ [新規]
│   │   └── YYYY-WW.html
│   └── archive/                        # 月次レポートアーカイブ
│       ├── index.html
│       └── YYYY-MM.html
├── tests/                              # テストスイート（拡充）
│   ├── test_security.py
│   ├── test_template.py
│   ├── test_backtest.py                # バックテスト tests [新規]
│   ├── test_watchlist.py               # ウォッチリスト tests [新規]
│   ├── test_stooq_fetcher.py           # Stooq fetcher tests [新規]
│   ├── test_cost_logger.py             # コスト記録 tests [新規]
│   └── ...
├── requirements.txt
├── .env.example
└── README.md
```

## 月額コスト目安

| 項目 | 月額 |
|------|------|
| Gemini 3.1 Flash-Lite | ~5円 |
| Claude Sonnet 4.6 Batch（月次レポート） | ~10円 |
| Claude Sonnet 4.6（週次速報・チャット等） | ~3～15円（チャット利用量次第） |
| yfinance / GitHub / LINE / Stooq | 無料 |
| Vercel Functions（チャット proxy） | 無料枠内 |
| **合計** | **~20～30円** |

実際のコストは `python -m src.utils.cost_report` で月別集計を確認できます。

## チャット機能の有効化（オプション）

レポート閲覧者がブラウザ上で Claude に質問できるチャットウィジェットを追加できます。
GitHub Pages は静的ホスティングなので、Vercel Functions をプロキシとして挟む構成です。

### 設定手順

1. **Vercel プロキシをデプロイ**: [`proxy/README.md`](proxy/README.md) の手順に従ってください
2. **GitHub Secret を追加**: `CHAT_PROXY_URL` に Vercel の API エンドポイント URL を設定
3. **次回の月次レポート生成時**に自動的にウィジェットが HTML に埋め込まれます

`CHAT_PROXY_URL` が未設定の場合、ウィジェットは非表示になり既存のレポート表示に影響しません。

## ウォッチリストの設定

銘柄を監視したい場合、`data/watchlist.json` に以下の形式で登録:

```json
{
  "stocks": [
    {
      "code": "9984",
      "market": "JP",
      "name": "SoftBank Group",
      "price_threshold_pct": 5.0,
      "volume_ratio_threshold": 2.0,
      "last_prices": {
        "close": 1500.0,
        "volume": 50000000
      }
    }
  ]
}
```

- `price_threshold_pct`: 価格変化の通知閾値（%）
- `volume_ratio_threshold`: 出来高が基準の何倍以上で通知するか

GitHub Actions がコミット時に `last_prices` を自動更新するため、手動編集は不要です。

## 注意事項

### チャット機能について
- `CHAT_PROXY_URL` が未設定な場合、ウィジェットは自動的に非表示になり既存機能に支障がありません
- Vercel proxy のデプロイは任意です

### 米国株について
- Gemini のプロンプトは JP / US 両市場対応ですが、銘柄選定品質は Gemini 次第
- yfinance で取得できる米国株シンボル（AAPL, GOOGL, MSFT など）はサポート
- Stooq フォールバックは米国株にも対応
- 週次速報の急騰スキャン・決算アラートも `market` フィールドに基づき US 銘柄を正しく解決
  （以前は常に `.T` を付与しており US 銘柄が取得失敗していた問題を修正）

### ウォッチリストの自動更新
- GitHub Actions が毎平日 17:00 に `data/watchlist.json` の `last_prices` を更新しコミット
- リポジトリをローカルで操作する場合は `git pull` で最新状態に同期してください

### コスト管理
- `python -m src.utils.cost_report` で月別 API コストを確認可能
- 想定コストを超える場合は Secret の `GEMINI_API_KEY` や `ANTHROPIC_API_KEY` を一時的に無効化してください

## 免責事項

本レポートはAIが自動生成した情報提供を目的としたものであり、
投資助言・勧誘を目的とするものではありません。
投資の最終判断はご自身の責任においてお行いください。
