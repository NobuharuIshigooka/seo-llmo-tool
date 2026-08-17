# デプロイ・環境設定

## 1. 環境変数

`.env.example` として同じ内容をリポジトリに置く。実際の値はコミットしない。

### 認証

| 変数 | 内容 |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth クライアントID |
| `GOOGLE_CLIENT_SECRET` | OAuth クライアントシークレット |
| `ALLOWED_HD` | 許可する社内Googleドメイン（例：`example.co.jp`） |
| `ALLOWED_EMAILS` | 追加で許可する個別メール（カンマ区切り・任意） |
| `SESSION_SECRET` | セッションクッキーの署名鍵 |

### 外部API

| 変数 | 内容 |
|---|---|
| `SERP_PROVIDER` | `serpapi`（既定） |
| `SERPAPI_KEY` | SerpApi のAPIキー |
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | DataForSEO の認証情報 |
| `ANTHROPIC_API_KEY` | Claude API キー |
| `ANTHROPIC_MODEL` | 既定 `claude-sonnet-5` |
| `PSI_API_KEY` | PageSpeed Insights APIキー |

### 動作設定

| 変数 | 既定値 | 内容 |
|---|---|---|
| `MAX_KEYWORDS` | 8 | 抽出キーワード数 |
| `MAX_COMPETITORS` | 3 | 競合の抽出社数 |
| `DAILY_AUDIT_LIMIT` | 30 | 1日の診断上限（人あたり） |
| `SERP_CONCURRENCY` | 4 | SERP同時実行数 |
| `SERP_CACHE_TTL` | 86400 | SERPキャッシュ（秒） |
| `CRAWL_CACHE_TTL` | 21600 | クロールキャッシュ |
| `PSI_CACHE_TTL` | 43200 | PageSpeedキャッシュ |
| `BACKLINK_CACHE_TTL` | 604800 | 被リンクキャッシュ |
| `FETCH_AI_OVERVIEW_DETAIL` | false | AI Overview本文の追加取得（追加課金） |
| `RESPECT_ROBOTS` | true | robots.txt の尊重 |
| `USER_AGENT` | — | クロール時のUA（連絡先URLを含めること） |
| `BASE_URL` | — | 公開URL（PDF生成時の自己参照に使用） |
| `DB_PATH` | `data/audit.db` | SQLite の配置先 |

## 2. ローカル開発

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # 値を埋める
uvicorn app.main:app --reload
```

OAuth のリダイレクトURIに `http://localhost:8000/auth/callback` を登録しておくこと。

**PDFダウンロード（`/report/{id}.pdf`）はローカル開発では動作しない場合がある。** セッションクッキーは
`secure=True` で発行されるため、Playwright が内部的に `/report/{id}?pdf=1` を自己参照する際、
`BASE_URL` が `http://localhost:8000` のような非HTTPSだとクッキーが送信されずログイン画面にリダイレクトされる。
本番（Cloud Run・HTTPS）では問題なく動作する。ローカルで確認する場合は `BASE_URL` を一時的にHTTPSの
リバースプロキシ経由にするか、本番環境で確認すること。

## 3. Cloud Run

### 設定値

| 項目 | 値 | 理由 |
|---|---|---|
| min-instances | **1** | ジョブ状態をプロセス内に保持するため |
| max-instances | **1** | 同上。複数インスタンスに分散すると進捗が追えない |
| CPU | 2 | Playwright のため |
| メモリ | 2GB | 同上 |
| リクエストタイムアウト | 300秒 | SSE接続の維持 |
| 同時実行数 | 20 | 10名の同時利用を想定 |

**min/max ともに1である点は必ず維持すること。** スケールが必要になった場合は、先に `jobs.py` を Redis + RQ に、`db.py` を Cloud SQL に置き換える。

### コンテナ

Playwright の公式イメージをベースにするか、`mcr.microsoft.com/playwright/python` を使う。Chromium の依存ライブラリを自前で揃えない。

`Dockerfile` は `mcr.microsoft.com/playwright/python:v1.48.0-jammy` をベースに作成済み（`requirements.txt` の
Playwright バージョンに合わせて選定。バージョンを上げる際はタグも合わせて更新すること）。

**ローカルMacがIntel・macOS 12.7.6（Monterey）の場合、`docker build` によるローカルビルド確認ができない。**
Docker Desktop の最新版は macOS Sonoma（14）以降が必須で導入不可。代替の Colima も依存の `qemu` が
ソースビルドを要求し、macOS 12 上での Python venv 作成に失敗する（Homebrew 側が「Tier 1構成外でサポート対象外」と
明言している既知の制約）。この場合はローカルDockerを諦め、`gcloud builds submit --tag gcr.io/{PROJECT_ID}/seo-llmo-tool`
で Cloud Build 上でのビルド確認に切り替える（`gcloud` CLI が必要。`brew install --cask google-cloud-sdk` で導入可能）。

### シークレット

APIキー類は Secret Manager に格納し、環境変数として注入する。イメージに焼き込まない。

## 4. データの永続化

Cloud Run のファイルシステムは揮発性のため、SQLite をそのまま置くと再デプロイで消える。

**対応方針**：日次で Cloud Storage にバックアップする。診断履歴が失われても業務は止まらないため、v1ではこれで足りる。履歴が業務上重要になった時点で Cloud SQL に移行する。

**実装**：Cloud Run は単体では定期実行できないため、`POST /internal/backup`（`app/main.py`・`app/backup.py`）を
Cloud Scheduler から日次で叩く方式とする。

- 認証は共有シークレットヘッダー（`X-Backup-Secret`）。値は環境変数 `BACKUP_SECRET` に設定し、
  Cloud Scheduler のジョブ設定側にも同じ値をヘッダーとして持たせる（セッションCookie認証とは別系統）
- バックアップ先バケットは環境変数 `BACKUP_GCS_BUCKET`
- SQLite は起動中のプロセスが書き込み中でも安全に取得できるよう、ファイルコピーではなく
  `sqlite3.Connection.backup()`（Online Backup API）でスナップショットを取ってからアップロードする
- 保存先パス：`gs://{BACKUP_GCS_BUCKET}/audit-db-backups/{YYYY-MM-DD}/audit.db`（JST基準の日付）

**Cloud Scheduler 側の設定（GCP作業）**：

1. Cloud Storage バケットを作成し、Cloud Run サービスアカウントに書き込み権限を付与
2. `BACKUP_SECRET` にランダムな文字列を生成して Secret Manager に格納、環境変数として注入
3. Cloud Scheduler ジョブを作成：頻度 `0 3 * * *`（JST日次）、ターゲットURL `{BASE_URL}/internal/backup`、
   HTTPメソッド POST、ヘッダー `X-Backup-Secret: {BACKUP_SECRETの値}`

## 5. 運用時の確認事項

| 項目 | 頻度 | 内容 |
|---|---|---|
| SerpApi 残枠 | 週次 | 枠切れは商談当日の停止に直結する。管理画面に残枠を表示すること |
| DataForSEO 残高 | 月次 | 残高不足で被リンクが取得できなくなる |
| スコア分布 | 運用開始後 | 診断10〜20件を確認し、閾値を調整（`docs/scoring.md` 参照） |
| 競合除外ドメイン | 随時 | 業種によって邪魔なドメインが変わる |
