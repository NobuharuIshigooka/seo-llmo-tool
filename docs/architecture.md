# アーキテクチャ

## 1. 全体構成

```
[ブラウザ] ──── Google OAuth ────> [FastAPI on Cloud Run]
     │                                    │
     ├──── POST /api/audits ─────────────>│ ジョブ登録 → job_id を即返却
     │                                    │
     ├──── GET  /api/audits/{id}/events ─>│ SSE で進捗配信
     │                                    │
     └──── GET  /api/audits/{id} ────────>│ 結果取得
                                          │
                                          ├─ Playwright（自社・競合クロール）
                                          ├─ SerpApi（順位・AI Overview）
                                          ├─ PageSpeed Insights（表示速度）
                                          ├─ Claude API（推定・解説・改善アドバイス）
                                          └─ SQLite（履歴・キャッシュ）
```

2026-08-19：DataForSEO（被リンク）連携を廃止した。

## 2. Webアプリとしての要件

本プロジェクトの成果物は**ブラウザで動作するWebアプリケーション**である。

| 項目 | 方針 |
|---|---|
| 提供形態 | Cloud Run 上の単一URL。営業担当はブックマークから開く |
| 対応ブラウザ | Chrome / Edge / Safari の最新版 |
| 画面サイズ | ノートPC（1280px〜）を主対象。タブレット表示も崩れないこと |
| インストール | 不要 |
| オフライン動作 | 非対応 |
| 同時利用 | 10名が同時に診断しても劣化しないこと |

デスクトップアプリ化、CLI化、ローカル配布は行わない。APIキーの集中管理・キャッシュ共有・診断履歴の一元化のため、サーバー集約型とする。

## 3. 非同期ジョブ設計

診断は60〜120秒かかるため、HTTPリクエストの中で完結させない。

```
1. POST /api/audits          → Job を生成し job_id を即返却
2. asyncio.create_task()     → バックグラウンドでパイプライン実行
3. GET  .../events (SSE)     → 工程ごとに進捗イベントを push
4. 完了時                    → SQLite に結果を保存し done イベント
```

### ジョブ状態の保持

プロセス内メモリ（dict）に保持する。このため **Cloud Run は min-instances=1 / max-instances=1** で運用する。

将来スケールが必要になった場合の変更点は次の2箇所のみ。それ以外に影響が出ない構造を保つこと。

- `jobs.py` → Redis + RQ に置き換え
- `db.py` → Cloud SQL (PostgreSQL) に置き換え

### SSE の注意点

- 15秒ごとにキープアライブコメント（`: keep-alive`）を送り、プロキシによる切断を防ぐ
- レスポンスヘッダに `Cache-Control: no-cache` と `X-Accel-Buffering: no` を付ける
- 後から接続したクライアントが取りこぼさないよう、過去のイベントを再送する

## 4. 診断パイプライン（6工程）

`pipeline.py` が統括する。工程名はUIの進捗レールと1対1で対応させる。

2026-08-19：被リンク調査工程（旧6番目）を廃止した。DataForSEO連携・権威性カテゴリを
診断から完全に除去し、7工程から6工程構成に変更した。

| # | stage | ラベル | 目安 | 失敗時 |
|---|---|---|---|---|
| 1 | `crawl` | サイトを読み込み中 | 6〜8秒 | **続行不能**（唯一の致命エラー） |
| 2 | `keywords` | 対策キーワードを推定中 | 5〜10秒 | 見出しからの簡易抽出にフォールバック |
| 3 | `serp` | 検索順位とAI Overviewを取得中 | 20〜30秒 | 順位項目を空にして続行 |
| 4 | `psi` | 表示速度を計測中 | 30〜45秒 | 表示速度の配点を0点にして続行 |
| 5 | `competitors` | 競合サイトを分析中 | 15〜25秒（2026-08-24：自動除外導入により延長） | 競合セクションを非表示にして続行 |
| 6 | `score` / `narrative` | スコア算出・要約生成 | 20〜30秒 | アドバイス文はテンプレート文にフォールバック |

工程3（serp）と工程4（psi）は並行実行する。工程5（competitors）は自社SERP結果からの
抽出に依存するため、実行順は `serp/psi 並行 → competitors` となる。
支配項は工程4（PageSpeed Insights の実測時間、短縮不可）。

**2026-08-24：工程5に自動除外フィルタを追加**（`docs/requirements.md` 4.5参照）。確定社数（3社）より
多め（既定10社）の候補を実クロールしてから自動除外を適用するため、候補プールの規模だけクロール数が
増える。実クロールは`asyncio.gather`で並行実行され`MAX_CONCURRENT_BROWSERS=2`のセマフォで律速される
ため、単純に3倍の時間になるわけではないが、旧仕様（3社のみクロール）より所要時間は伸びる。

### 部分成功の実装方針

- 各工程を try/except で包み、例外は `warnings: list[str]` に積む
- 結果JSONに `warnings` を必ず含め、画面に警告バナーとして表示する
- 例外を握りつぶして黙って空データを返さない。必ず理由を残す

## 5. データモデル

| テーブル | 用途 |
|---|---|
| `users` | email（PK）/ name / created_at |
| `audits` | id / url / domain / created_by / created_at / finished_at / status / devices / result(JSON) |
| `cache` | key / value(JSON) / expires_at |
| `excluded_domains` | domain / reason / created_by |

`cache` のキー規約：`serp:{device}:{gl}:{keyword}` / `crawl:{url}` / `psi:{strategy}:{url}`

## 6. エンドポイント

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/` | 診断入力画面 |
| GET | `/auth/login` `/auth/callback` `/auth/logout` | Google OAuth |
| POST | `/api/audits` | 診断開始（url, devices）→ job_id |
| GET | `/api/audits/{id}/events` | SSE 進捗配信 |
| GET | `/api/audits/{id}` | 結果JSON |
| GET | `/api/history` | 診断履歴一覧（JSON） |
| GET | `/history` | 診断履歴一覧画面 |
| GET | `/report/{id}` | 共有レポート（閲覧専用。`?pdf=1`はPDF生成時の内部自己参照用） |
| GET | `/report/{id}.pdf` | PDF ダウンロード |
| GET | `/admin/excluded-domains` | 競合除外ドメイン管理画面 |
| GET/POST | `/api/excluded-domains` | 除外ドメイン一覧取得・追加 |
| DELETE | `/api/excluded-domains/{domain}` | 除外ドメイン削除 |
| GET | `/api/serp-account` | SerpApi 残枠情報（管理画面表示用） |
| GET | `/healthz` | ヘルスチェック（認証不要） |
| POST | `/internal/backup` | SQLite→Cloud Storage 日次バックアップ（Cloud Schedulerから共有シークレット認証で呼び出し） |

`/healthz` 以外はすべて認証必須。

## 7. PDF出力

`/report/{id}` を Playwright で開き、`page.pdf()` でA4出力する。画面とPDFのレンダリングを1系統に保つため、Jinja2 と JS の両方でレポートを描画しない。
