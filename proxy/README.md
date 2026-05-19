# Chat Proxy - Vercel Functions

GitHub Pages の投資レポートにチャットウィジェットを追加するための Vercel プロキシです。
ブラウザから直接 Anthropic API キーを使わずに Claude へ問い合わせできます。

## デプロイ手順

### 1. Vercel CLI のインストール

```bash
npm i -g vercel
```

### 2. このディレクトリに移動

```bash
cd proxy
```

### 3. Vercel に新規プロジェクトとして登録

```bash
vercel
```

対話式の質問に答えてください（デフォルトのままで OK）。

### 4. 環境変数を Vercel ダッシュボードで設定

Vercel プロジェクトの **Settings → Environment Variables** で以下を追加:

| 変数名 | 値 | 説明 |
|--------|-----|------|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic Console から取得 |
| `ALLOWED_ORIGIN` | `https://yourusername.github.io` | **必須** — GitHub Pages の URL（未設定だと 500 エラーで動かない） |

> **重要**: `ALLOWED_ORIGIN` を設定しないとサーバーが起動しても全リクエストが `500 Server misconfigured` で拒否されます。必ず設定してください。

### 5. 本番デプロイ

```bash
vercel --prod
```

### 6. 取得した URL を GitHub に設定

デプロイ完了後に表示される URL（例: `https://stock-picker-chat-proxy.vercel.app`）をメモします。

リポジトリの **Settings → Secrets and variables → Actions** で:

| Secret 名 | 値 |
|-----------|-----|
| `CHAT_PROXY_URL` | `https://stock-picker-chat-proxy.vercel.app/api/ask` |

次回の月次レポート生成時に、ウィジェットが自動的に HTML に埋め込まれます。

## ⚠️ レート制限について（重要）

現在の実装は **メモリ内カウンタ**なので、以下の制約があります:

- Vercel Functions は複数インスタンスで動くため、各インスタンスごとに別カウントになる
- コールドスタート（起動）時にカウンタがリセットされる
- 結果: **30 並列リクエストで簡単に制限を超えられる**

本番運用するなら、以下のいずれかを実装してください:

1. **Upstash Redis 連携**: Vercel Marketplace から1クリックで追加可能、無料枠あり
2. **Vercel KV**: より深く統合された KV ストア
3. **API トークン認証**: 自分専用にする場合、シンプルな bearer token で守る

環境変数 `UPSTASH_REDIS_REST_URL` と `UPSTASH_REDIS_REST_TOKEN` を設定すると、
コードがそれを検知してログに警告を出すフックが有効になります（実際の Redis 統合は別 PR で実装予定）。

参考: https://upstash.com/docs/redis/sdks/javascriptsdk/getting-started

## Vercel プラン要件

`vercel.json` の `maxDuration: 30` は **Vercel Pro 以上**で有効。
**Hobby（無料）プラン**では最大 10 秒に制限されるため、Claude API の応答が遅い場合タイムアウトの可能性あり。

Hobby プランで運用する場合は `vercel.json` の `maxDuration` を `10` に下げ、
タイムアウト時のフロント側エラーメッセージで再試行を案内してください。

## セキュリティ注意点

- `ANTHROPIC_API_KEY` は絶対にコミットしない（`.gitignore` で除外済み）
- `ALLOWED_ORIGIN` は必須。GitHub Pages の正確な URL に設定して他サイトからの不正利用を防ぐ
- レート制限は 1分10リクエスト / 1日100リクエスト（`api/ask.js` 内の閾値で調整可能）
- 本番運用には分散カウンタ（Upstash Redis 等）が必要（上記参照）

## ローカルテスト

```bash
npm install
vercel dev
```

`http://localhost:3000/api/ask` に POST リクエストを送信してテストできます:

```bash
curl -X POST http://localhost:3000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"テスト"}]}'
```

> ローカルテスト時は `.env.local` に `ALLOWED_ORIGIN=http://localhost:3000` を設定してください。
