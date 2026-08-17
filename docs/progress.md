# 実装進捗

CLAUDE.md の実装状況チェックリスト（工程1〜6）を作業単位まで分解したもの。
**作業を終えるたびに本ファイルのチェックボックスを更新し、CLAUDE.md 側のチェックリストも合わせて更新すること。**

参照する仕様書：`docs/requirements.md`（全体の正）/ `docs/architecture.md` / `docs/api-integrations.md` / `docs/scoring.md` / `docs/frontend.md` / `docs/deployment.md`

---

## 工程1 基盤（FastAPI・Google OAuth・DB・ジョブ管理・SSE）

- [x] ディレクトリ構成の作成（`app/clients` `app/templates` `app/static` `data`）
- [x] `requirements.txt` 作成（FastAPI / Playwright / httpx+BeautifulSoup / Authlib / anthropic SDK / pytest 等）
- [x] Python 3.13 で `.venv` 作成、依存インストール、`playwright install chromium`
- [x] `.env.example` 作成（`docs/deployment.md` の環境変数一覧に準拠）、`.gitignore` 作成
- [x] GitHubリポジトリ初期化・初回コミット・push（https://github.com/NobuharuIshigooka/seo-llmo-tool.git）
- [x] `app/config.py`：環境変数読み込み
- [x] `app/main.py`：FastAPI エントリ、`/healthz` のみ実装・起動確認済み
- [x] `app/db.py`：SQLite初期化・テーブル定義（`users` / `audits` / `cache` / `excluded_domains`）
- [x] `app/auth.py`：Google OAuth 2.0（Authorization Code フロー）
  - [x] `/auth/login` `/auth/callback` `/auth/logout`
  - [x] `hd` パラメータ＋サーバー側での email ドメイン再検証（`ALLOWED_HD` / `ALLOWED_EMAILS`）
  - [x] セッションクッキー（署名付き・HttpOnly・Secure・SameSite=Lax）
  - [x] `/healthz` 以外の全エンドポイントに認証必須を適用（`app/main.py` のミドルウェアで一括適用、`/auth/*` はログインフロー自体のため除外）
- [x] `app/jobs.py`：ジョブ管理
  - [x] プロセス内メモリ（dict）でジョブ状態を保持
  - [x] `job_id` を即返却し `asyncio.create_task()` でバックグラウンド実行する `JobManager.create_job()`
  - [x] SSE イベント配信の仕組み（工程ごとの progress イベント、`app/jobs.py` の `sse_stream()`。ユニットテスト `tests/test_jobs.py` で検証済み）
- [x] `GET /api/audits/{id}/events`（SSE）※ `app/main.py` に配線済み
  - [x] 15秒ごとのキープアライブコメント（`app/jobs.py` の `sse_stream()` に実装済み）
  - [x] レスポンスヘッダ `Cache-Control: no-cache` / `X-Accel-Buffering: no`
  - [x] 後から接続したクライアントへの過去イベント再送（`Job.subscribe()` で実装・テスト済み）
- [x] `POST /api/audits`：URL正規化（スキーム自動補完）・devicesバリデーション・`JobManager.create_job()`起票・`audits`テーブルへの起票時保存
- [x] `GET /api/audits/{id}`：結果JSON取得（`audits`テーブルから）
- [x] ジョブ完了/エラー時に `audits` テーブルへ結果を保存（`app/main.py` の `_run_and_persist()`）
  - ユニットテスト `tests/test_api_audits.py` で認証必須・URL正規化・devicesバリデーション・結果保存・SSEヘッダを検証済み
- [x] 1日の診断上限（`DAILY_AUDIT_LIMIT`、人あたり）のレート制限実装
  - [x] `app/db.py` の `count_audits_today()`：JST基準（0時〜翌0時）で当日の起票件数をカウント
  - [x] `POST /api/audits` で上限到達時に `429` を返す（`app/main.py`）
  - ユニットテスト `tests/test_db.py`（JST日境界の判定）・`tests/test_api_audits.py`（429レスポンス）で検証済み

## 工程2 診断エンジン（クロール・SerpApi・PSI・DataForSEO・Claude）

- [x] `app/clients/crawler.py`
  - [x] Playwright (Chromium) による自社・競合クロール
  - [x] 失敗時のみ httpx + BeautifulSoup にフォールバック
  - [x] robots.txt 尊重（取得不可時は許可扱い）／User-Agent 明示（連絡先URL含む）
  - [x] 同一ドメイン同時アクセス制限（3並列）／タイムアウト25秒
  - [x] キャッシュ `crawl:{url}`（TTL: `CRAWL_CACHE_TTL`、共通ヘルパー `app/cache.py` 経由）
  - [x] `docs/scoring.md` の採点に必要な項目を一括抽出（title / meta description / h1・h2 / 本文 / 冒頭段落 / viewport / JSON-LD種別 / noindex / canonical / HTTPS / リスト・表数）。ユニットテスト `tests/test_crawler.py` で検証済み
- [x] `app/clients/llm.py`（Claude API）
  - [x] `extract_keywords()`：title/meta description/見出し/本文から8個推定、JSON配列出力、temperature=0.0
  - [x] `explain_competitors()`：競合との数値差分の自然文化、temperature=0.3
  - [x] `sales_summary()`：営業トーク要約（4〜5文・300字以内）、temperature=0.3
  - [x] プロンプトで「与えられた数値以外を書かない」を明示、スコアを生成させない
  - [x] JSON出力はコードフェンス除去してからパース
  - [x] 失敗時フォールバック（簡易抽出／非表示＋警告／テンプレート文）。ユニットテスト `tests/test_llm.py` で検証済み
- [x] `app/clients/serp.py`（SerpApi）
  - [x] `SerpProvider` プロトコル定義（将来 DataForSEO SERP へ切替可能な抽象化）
  - [x] `SerpApiProvider` 実装（`engine=google`, `hl=ja`, `gl=jp`, `location=Japan`, `device`, `num=20`）
  - [x] AI Overview 判定（`ai_overview` キーの有無、`references[].link` からドメイン正規化して引用判定）
  - [x] キャッシュ `serp:{device}:{gl}:{keyword}`（TTL: `SERP_CACHE_TTL`）、同時実行数4
  - [x] 失敗時は順位項目を空にして続行。ユニットテスト `tests/test_serp.py` で検証済み
  - [ ] SerpApi 残枠を取得・保持する仕組み（管理画面表示用、工程5で実施）
- [x] `app/clients/psi.py`（PageSpeed Insights）
  - [x] `strategy=mobile` / `desktop` 両方取得（並行実行）、`category=PERFORMANCE`
  - [x] `categories.performance.score` / LCP / CLS / TBT / `loadingExperience` 取得
  - [x] キャッシュ `psi:{strategy}:{url}`（TTL: `PSI_CACHE_TTL`）、タイムアウト90秒
  - [x] 失敗時は表示速度の配点を0点にして続行。ユニットテスト `tests/test_psi.py` で検証済み
- [x] `app/clients/backlinks.py`（DataForSEO Backlinks）
  - [x] `POST /v3/backlinks/summary/live`（Basic認証、リクエストボディは配列）
  - [x] 自社1件＋競合3社＝4リクエスト（並行実行）、`tasks[0].result[0]` からパース
  - [x] 取得指標：`referring_domains`（主指標）／`rank`／`backlinks`（参考）／dofollow比率（参考）
  - [x] キャッシュ `backlinks:{domain}`（TTL: `BACKLINK_CACHE_TTL`）
  - [x] 失敗・未契約時は権威性カテゴリを「未計測」として続行。ユニットテスト `tests/test_backlinks.py` で検証済み
- [x] `app/pipeline.py`：7工程の統括
  - [x] 工程1 crawl（致命エラー扱い、唯一続行不能）
  - [x] 工程2 keywords（失敗時は見出し簡易抽出にフォールバック）
  - [x] 工程3 serp／工程4 psi を並行実行
  - [x] 工程5 competitors（自社より上位表示回数が多い順に3社抽出、除外ドメイン適用）
  - [x] 工程6 backlinks（自社＋工程5で確定した競合ドメイン）
    - **仕様上の矛盾を解消**：`docs/architecture.md` は「工程3・4・6を並行実行」としていたが、
      backlinks の対象（自社＋競合3社）は competitors（工程5）の出力に依存するため、
      実装は `serp/psi 並行 → competitors → backlinks` の順に変更した（ユーザー承認済み）。
      `docs/architecture.md` 側の記述は今後更新すること。
  - [x] 工程7 score／narrative
  - [x] 各工程を try/except で包み `warnings: list[str]` に積む。黙って空データを返さない
  - [x] 結果JSONに `warnings` を必ず含める。ユニットテスト `tests/test_pipeline.py` で検証済み
  - [x] `POST /api/audits`・SSEエンドポイントへの配線（`app/main.py`、工程1側に反映済み）
  - [x] 結果JSONに `serp_results`（キーワード別順位・AI Overview）・`competitor_backlinks`（競合の被リンク指標）を追加（工程4着手時に発覚した抜け漏れを解消）
  - [x] `docs/requirements.md` 4.5 が定める競合比較の詳細指標（文字数／H2数／Q&A見出し数／FAQスキーマ／
    構造化データ種類数／リスト・表数／内部リンク数／E-E-A-T要素数）を実装（2026-08-17）
    - `app/clients/crawler.py`：`CrawlResult.internal_link_count`（同一ドメイン内リンク数）を追加
    - `app/scoring.py`：E-E-A-T検出（`detect_eeat`）・Q&A見出し判定（`is_qa_heading`）・FAQスキーマ判定
      （`has_faq_schema`）を配点ロジックから公開関数に切り出し、比較指標算出と共有（配点の定義自体はscoring.pyに閉じたまま）
    - `app/pipeline.py`：`_run_score_and_narrative` 内で `score`（sales_summary生成）と並行して
      競合3社を実クロール（`_run_competitor_comparison`）。クロール失敗サイトは比較対象から個別除外し
      warningsに記録（部分成功）。`llm.explain_competitors()` を比較データ（日本語キーに変換）で呼び出し
    - 結果JSONに `competitor_comparison`（ドメイン別指標）・`competitor_comparison_labels`・
      `competitor_explanation` を追加
    - ユニットテスト `tests/test_pipeline.py`・`tests/test_crawler.py` で検証済み

## 工程3 スコアリング

- [x] `app/scoring.py`：`docs/scoring.md` の配点をそのまま実装（配点・閾値はこのファイルのみに定義）
  - [x] テクニカルSEO（30点）：表示速度モバイル40/PC10・モバイル対応15・構造化データ20・クロール可能性15
  - [x] コンテンツSEO（30点）：本文情報量30・キーワード網羅性25・タイトル見出し構造25・E-E-A-T20
  - [x] LLMO対応度（25点）：FAQ構造化25・Q&A見出し20・冒頭定義文20・リスト表15・AI Overview引用20
  - [x] 権威性・被リンク（15点）：参照ドメイン数8・ドメインランク7
  - [x] 未計測カテゴリの除外＋残り配点での100点正規化ロジック
  - [x] 評価バンド（good/fair/weak/poor）算出
  - [x] 改善インパクト算出（失点÷100×カテゴリ重み、上位3件抽出、被リンク関連は除外）
  - [x] `rubric_version` を結果JSONに含める
  - [x] 同一入力→同一スコアを保証するユニットテスト（`pytest`、`tests/test_scoring.py`）
  - 備考：E-E-A-T検出（運営者情報／著者・監修情報／連絡先／実績表記／資格・許認可）は
    本文・タイトル中の正規表現マッチングによる簡易判定。仕様書に検出方法の定義がないため、
    実サイト検証（工程6）で誤検出・過少検出がないか確認し、必要ならパターンを調整すること。

## 工程4 結果画面（`docs/frontend.md` 確定後に着手）

- [x] `docs/frontend.md` の確定値（配色・書体・サイズ）を確認し、独自の色や文字サイズを追加しない
  - 2026-08-07：デザイン参考画像を受け、配色を紺＋金からモノクロ基調に改訂（`docs/frontend.md` 3章）。
    評価バンド色（good/fair/weak/poor）のみ例外として維持。構造原則（縦1本スクロール・タブ分割禁止・
    円形ゲージ禁止）は変更なし
  - 入力画面の見出しは「あなたのサイトをSEO・LLMO診断」、URL入力欄のプレースホルダーは
    「ここにURLを入力して下さい。」（ユーザー指示によりコピーを確定）
- [x] `app/templates/base.html`・`app/static/css/main.css`：ベースレイアウト・CSS変数（`docs/frontend.md` 3〜4章準拠）
- [x] `app/main.py`：`GET /`（Jinja2レンダリング）・`/static` マウントを追加（`PUBLIC_PREFIXES` に `/static/` を追加）
- [x] 診断入力画面（URL入力＋デバイス選択、スキーム自動補完、その場バリデーション）— `app/templates/index.html` `#view-input`
- [x] 進捗レール画面（7工程・SSE購読・完了工程に実測値を表示）— `#view-progress`、`app/static/js/audit.js`
- [x] 診断結果画面 — `#view-result`
  - [x] 営業トーク要約（最上部）
  - [x] 総合スコア＋カテゴリ別スコア（全カテゴリ同色、赤色による強調はしない）
  - [x] キーワード別順位表（PC/スマホ・AI Overview表示）
  - [x] 競合比較セクション（詳細指標：文字数／H2数／Q&A見出し数／FAQスキーマ／構造化データ種類数／
    リスト・表数／内部リンク数／E-E-A-T要素数／参照ドメイン数。自社を最上段固定、指標は列方向、
    Claude生成の解説文を表の下に配置。`docs/frontend.md` 5.7準拠）
  - [x] 改善インパクト上位3件
  - [x] 「トップページを診断した結果です」の明示
  - [x] 警告バナー（`warnings` の表示）
- [x] キーワードの手動編集（追加・削除）→再診断
  - `POST /api/audits` に任意項目 `keywords` を追加、指定時は `pipeline.run_audit()` がLLM推定をスキップ
  - `app/pipeline.py` の結果JSONに `serp_results`・`competitor_backlinks` を追加（結果画面の実装中に判明した工程2の抜け漏れ。キーワード別順位表・競合比較の描画に必須だった）
- 動作確認：Chrome拡張機能によるスクリーンショット自動取得がセッション内で継続的にタイムアウトしたため
  （`example.com` 等の単純ページでも再現、実装起因ではないと判断）、`app/scoring.py` の実ロジックで
  生成したダミー結果を結果画面に強制描画するデバッグルートを一時的に用意し、ユーザーがブラウザで直接目視確認した。
  確認後、デバッグ用コード（`audit.js` の `window.__DEBUG_RESULT__` フック等）は削除済み
- 工程4は本タスクをもって完了（競合比較の詳細指標実装は上記工程2の項目を参照）

## 工程5 共有URL・PDF出力・履歴・除外ドメイン管理

2026-08-17 実装。未決事項#4（顧客共有時に社内向け情報を隠すか）はユーザー判断により
「改善インパクトも含め全項目をそのまま共有表示」で確定。共有レポートと社内結果画面の
表示内容に差は設けていない。

- [x] `GET /report/{id}`：閲覧専用の共有レポート画面（認証必須）
  - `app/templates/report.html` を新設し、Jinja2でサーバーサイド描画（`app/report_view.py` が
    結果JSON→テンプレート変数の変換を担う）。`docs/architecture.md` 7章「画面とPDFのレンダリングを
    1系統に保つ」方針に従い、既存の結果画面（`index.html` + `audit.js`のクライアント描画）とは
    別実装とした（ユーザー承認済み）。将来的に描画ロジックの二重管理コストが問題になれば統合を検討
  - 共有URLコピーボタンをヘッダー直下と末尾の2箇所に配置（`docs/frontend.md` 7章準拠）
- [x] `GET /report/{id}.pdf`：Playwright `page.pdf()` によるA4出力
  - サーバー内部で `BASE_URL` を自己参照し、`/report/{id}?pdf=1`（`is_pdf=True`でアクション類を非表示）を
    Playwrightで開いてPDF化。認証セッションクッキーをPlaywrightのブラウザコンテキストに注入して認証を通す
  - 既知の制約：セッションクッキーが`secure=True`のため、`BASE_URL`が非HTTPS（ローカル開発）だと
    PDF生成が失敗する。`docs/deployment.md` に記載済み。本番（Cloud Run・HTTPS）では問題なし
- [x] `GET /history`（画面）・`GET /api/history`（JSON）：診断履歴一覧（実行者・日時・スコア・状態、新しい順）
- [x] `GET /admin/excluded-domains`（画面）・`GET/POST /api/excluded-domains`・
  `DELETE /api/excluded-domains/{domain}`：競合除外ドメインの追加・削除
- [x] `GET /api/serp-account`：SerpApi 残枠表示（`app/clients/serp.py` の `get_account_info()`、
  `account.json` エンドポイントを1時間キャッシュ。除外ドメイン管理画面の上部に表示）
- [x] `app/db.py` に `list_audits` / `get_audit` / `list_excluded_domains` / `add_excluded_domain` /
  `remove_excluded_domain` を追加
- ユニットテスト：`tests/test_report_and_admin.py`（共有レポート・履歴・除外ドメイン管理のエンドポイント）・
  `tests/test_db.py`（新規DB関数）・`tests/test_serp.py`（残枠取得）で検証済み
- Jinja2テンプレート内で `cat.items` `row.values` のようにPython辞書の組み込みメソッド名と
  キー名が衝突する書き方をすると、Jinja2の属性解決が `dict.items`/`dict.values`（builtin_function_or_method）を
  返してしまい描画時に例外になる。`cat["items"]` のようにブラケット記法で回避した（実装中に発覚した罠）

## 工程6 Cloud Run デプロイ・実サイト検証・閾値調整

- [x] Dockerfile（`mcr.microsoft.com/playwright/python:v1.48.0-jammy` ベース。requirements.txtのPlaywrightバージョンに合わせて選定）・`.dockerignore` を作成（2026-08-17）
  - **ローカルDockerビルドは断念、Cloud Build方式に変更**（2026-08-17）：ユーザー環境がIntel Mac・macOS 12.7.6（Monterey）のため、
    Docker Desktop最新版（Sonoma以降必須）が導入不可。代替のColimaも依存の`qemu`がソースビルドを要求し、
    macOS 12上でPython 3.14のvenv作成に失敗（Homebrew側が「Tier 1構成外でサポート対象外」と明言する既知の制約）。
    ローカルDockerインストールは諦め、`gcloud builds submit` によるクラウドビルド確認に方針転換（ユーザー承認）
  - `gcloud` CLI のインストールは完了（`brew install --cask google-cloud-sdk`）。**次回はここから再開**：
    `gcloud auth login` → GCPプロジェクトの確認/作成 → `gcloud builds submit --tag gcr.io/{PROJECT_ID}/seo-llmo-tool`
    でのビルド確認 → 通れば Secret Manager・GCSバケット・Cloud Run・Cloud Scheduler の順にセットアップ
- [ ] Secret Manager にAPIキー類を格納し環境変数として注入 — **GCPプロジェクトでの作業（ユーザー）**
- [ ] Cloud Run 設定：min-instances=1 / max-instances=1 / CPU2 / メモリ2GB / タイムアウト300秒 / 同時実行数20 — **GCPプロジェクトでの作業（ユーザー）**
- [x] Cloud Storage への SQLite 日次バックアップ（2026-08-17）
  - `POST /internal/backup`（`app/main.py`・`app/backup.py`）をCloud Schedulerから日次で呼ぶ方式で実装（ユーザー承認済み）
  - 認証は共有シークレットヘッダー `X-Backup-Secret`（環境変数 `BACKUP_SECRET`、ユーザー承認済み）。バケットは `BACKUP_GCS_BUCKET`
  - SQLiteは `sqlite3.Connection.backup()`（Online Backup API）でスナップショットしてからアップロード（書き込み中でも安全）
  - `requirements.txt` に `google-cloud-storage` を追加
  - ユニットテスト `tests/test_backup.py`・`tests/test_backup_endpoint.py` で検証済み
  - **GCP側の作業が別途必要**（`docs/deployment.md` 4章に手順を明記）：GCSバケット作成・Cloud Runサービスアカウントへの書き込み権限付与・`BACKUP_SECRET`をSecret Managerに格納・Cloud Schedulerジョブ作成
- [ ] 実サイトでの診断10〜20件を確認し、`scoring.py` の満点ライン（本文文字数・参照ドメイン数等）を調整 — **実サイトURLでの診断実行が必要（ユーザー）**
- [ ] SerpApi 残枠・DataForSEO 残高の運用確認体制の整備 — 残枠表示API自体は工程5で実装済み（`GET /api/serp-account`）。運用フロー（誰がいつ確認するか）の取り決めは**ユーザー判断**

---

## 更新ルール

- 各タスク完了時に本ファイルの該当チェックボックスを `[x]` に更新する
- 工程全体が完了した時点で `CLAUDE.md` の実装状況チェックリストも合わせて更新する
- 仕様と実装の間で矛盾に気づいた場合は、実装前に指摘する（CLAUDE.md「やらないこと」参照）
