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
  - `gcloud` CLI のインストールは完了（`brew install --cask google-cloud-sdk`）
  - 2026-08-18：`gcloud auth login`（`dec.4th.1978@gmail.com`）・GCPプロジェクト作成（`seo-llmo-tool`、
    請求先アカウント `miraiagogo.cloud - Topgate` をリンク）・`cloudbuild.googleapis.com` /
    `containerregistry.googleapis.com` / `run.googleapis.com` を有効化済み
  - 2026-08-18：作成時は組織なしで作られてしまったため、`gcloud beta projects move seo-llmo-tool
    --organization=800471672671` で `miraiagogo.cloud` 組織配下に移動済み（Cloud Console上でも
    `miraiagogo.cloud` の下に表示されることを確認）
    - 備考：`gcloud auth login` はこの環境（Safariが既定ブラウザ、非対話シェル）だと `--no-launch-browser` +
      `expect` での手動コード受け渡しが必要だった（通常はブラウザが自動で開き完結する）
    - 備考：プロジェクト作成・API有効化の直後は権限伝播に数分かかり、`gcloud builds submit` が
      一時的に `PERMISSION_DENIED` になった（約90秒待って再実行したら解消）
  - 2026-08-18：`gcloud builds submit --tag gcr.io/seo-llmo-tool/seo-llmo-tool --project=seo-llmo-tool` で
    Cloud Build 経由のビルド確認 **成功**（`SUCCESS`、所要3分6秒）。イメージ
    `gcr.io/seo-llmo-tool/seo-llmo-tool:latest` を Container Registry にプッシュ済み。
    Secret Manager へのAPIキー格納も完了（下記参照）。
    **次回はここから再開**：Cloud Run サービス作成
    （min=1/max=1/CPU2/メモリ2GB/タイムアウト300秒/同時実行数20、シークレットを`--set-secrets`で注入）
    → GCSバケット・Cloud Scheduler の順にセットアップ
- [x] Secret Manager にAPIキー類を格納し環境変数として注入（2026-08-18）
  - [x] `secretmanager.googleapis.com` を有効化、9件のシークレットの箱を作成済み（2026-08-18）：
    `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET` / `SERPAPI_KEY` /
    `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` / `ANTHROPIC_API_KEY` / `PSI_API_KEY` / `BACKUP_SECRET`
  - [x] 各シークレットへの実際の値の投入完了（2026-08-18、ユーザー作業）：
    `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET`（自動生成分を投入） / `SERPAPI_KEY` /
    `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` / `ANTHROPIC_API_KEY` / `PSI_API_KEY` /
    `BACKUP_SECRET`（自動生成分を投入） の9件、全てバージョン登録済みを確認
    - `pagespeedonline.googleapis.com` の有効化はこちらで実施済み
    - DataForSEOは当初「今回使わない」と一度検討したが、被リンく評価（権威性カテゴリ15点）は
      診断対象サイト側の許可なしで使える代替APIがなく、他社（Ahrefs/Moz等）も同様に別契約が
      必要なため、DataForSEOを継続利用する方針で確定（ユーザー判断、2026-08-18）
  - [x] Cloud Run サービス作成時にこれらのシークレットを環境変数として注入する設定（`--set-secrets`）済み
- [x] Cloud Run 設定：min-instances=1 / max-instances=1 / CPU2 / メモリ2GB / タイムアウト300秒 / 同時実行数20（2026-08-18）
  - `gcloud run deploy seo-llmo-tool --region=asia-northeast1 --allow-unauthenticated --set-secrets=...` でデプロイ成功
  - Service URL: `https://seo-llmo-tool-864337619528.asia-northeast1.run.app`
  - 初回デプロイは Secret Manager への `Permission denied` で失敗。Cloud Run のデフォルトサービスアカウント
    （`864337619528-compute@developer.gserviceaccount.com`）に `roles/secretmanager.secretAccessor` を
    プロジェクトレベルで付与してから再実行し成功した（デフォルトSAには自動付与されない）
  - [x] `BASE_URL` 環境変数を設定して再デプロイ済み（2026-08-18）
  - [x] Google OAuthクライアントの「承認済みのリダイレクトURI」に
    `https://seo-llmo-tool-864337619528.asia-northeast1.run.app/auth/callback` を追加済み（ユーザー作業）
  - [x] `ALLOWED_EMAILS` を設定済み（2026-08-19、社内ドメイン一括ではなく個別メール許可の方針で確定・ユーザー判断）。
    現時点で判明している1件のみ登録、残りメンバーは判明次第カンマ区切りで追記していく運用：
    `ipi.dai2.ishigouoka.20260801@gmail.com`
    - 追加時のコマンド（毎回フルリストを指定する。差分追加ではなく上書きなので既存分を含めること）：
      ```
      gcloud run services update seo-llmo-tool --region=asia-northeast1 --project=seo-llmo-tool \
        --update-env-vars="ALLOWED_EMAILS=addr1@example.com,addr2@example.com"
      ```
    - `ALLOWED_HD` は使わない方針（個別メール許可を選択）
  - [x] ブラウザで実際にログイン・診断1件を実走確認（2026-08-20）
    - `ipi.dai2.ishigouoka.20260801@gmail.com` でログイン時に一度Google側で
      `accounts.google.com/signin/oauth/consent` が「400. That's an error.（malformed request）」を返す事象が発生。
      Cloud Runのアクセスログには`/auth/login`・`/auth/callback`いずれも記録がなく、
      リクエストがアプリに到達する前にGoogle側の内部セッション状態（同一ブラウザで複数回ログイン試行した際の
      混線と推測）で弾かれていたと判断。アプリ・`redirect_uri`生成ロジック側の実装に問題は見当たらなかった
      （`app/auth.py`の`/auth/login`が生成する認可URLを直接確認し、`redirect_uri`・`client_id`とも正しい値だった）
    - シークレットウィンドウで開き直したところ解消。ログイン→診断起票まで問題なく完了
- [x] Cloud Storage への SQLite 日次バックアップ（2026-08-17）
  - `POST /internal/backup`（`app/main.py`・`app/backup.py`）をCloud Schedulerから日次で呼ぶ方式で実装（ユーザー承認済み）
  - 認証は共有シークレットヘッダー `X-Backup-Secret`（環境変数 `BACKUP_SECRET`、ユーザー承認済み）。バケットは `BACKUP_GCS_BUCKET`
  - SQLiteは `sqlite3.Connection.backup()`（Online Backup API）でスナップショットしてからアップロード（書き込み中でも安全）
  - `requirements.txt` に `google-cloud-storage` を追加
  - ユニットテスト `tests/test_backup.py`・`tests/test_backup_endpoint.py` で検証済み
  - **GCP側の作業が別途必要**（`docs/deployment.md` 4章に手順を明記）：GCSバケット作成・Cloud Runサービスアカウントへの書き込み権限付与・`BACKUP_SECRET`をSecret Managerに格納・Cloud Schedulerジョブ作成
- [x] 実サイト診断1件目を実走確認（2026-08-19、Cloud Run本番環境）。ログイン→診断完了まで一連の流れをブラウザで確認済み
  - 確認過程で2件のバグを発見・修正：
    1. Cloud RunがTLS終端後にコンテナへ`X-Forwarded-Proto`付きの平文HTTPで転送するため、Uvicornが`--proxy-headers`なしだと
       スキームを`http`と誤認識し、Google OAuthの`redirect_uri`が`http://`になって`redirect_uri_mismatch`エラーが発生。
       `Dockerfile`のCMDに`--proxy-headers --forwarded-allow-ips='*'`を追加して解消
    2. `app/auth.py`のログイン確定用Cookie名`SESSION_COOKIE = "session"`がStarletteの`SessionMiddleware`の
       デフォルトCookie名（`"session"`）と衝突し、OAuthコールバック直後に`SessionMiddleware`がstate消費後の
       空セッションを「クリア」として同名Cookieを1970年期限切れで上書きし、ログインが成立しない不具合があった。
       `SESSION_COOKIE`を`"auth_session"`に変更して分離（`app/main.py`・テストの`_auth_cookie()`も追随済み）
  - 上記2件はいずれもCloud Buildでイメージ再ビルド→`gcloud run deploy`で再デプロイ済み
- [ ] 実サイトでの診断10〜20件を確認し、`scoring.py` の満点ライン（本文文字数等）を調整 — **実サイトURLでの追加の診断実行が必要（ユーザー）**
- [ ] SerpApi 残枠の運用確認体制の整備 — 残枠表示API自体は工程5で実装済み（`GET /api/serp-account`）。運用フロー（誰がいつ確認するか）の取り決めは**ユーザー判断**

### 2026-08-20：競合除外ドメインの初期リスト未投入を修正

診断結果画面の「競合3社との比較」に無関係なサイト（ポータル・比較サイト等）が出るという指摘。
`docs/requirements.md` 4.5 は「自社より上位表示回数が多い順」の抽出ロジックに加えて
「除外リストの初期値」（食べログ・ホットペッパー・エキテン・ぐるなび・Wikipedia・Amazon・楽天市場・
Yahoo!ショッピング・求人媒体各社・まとめ系メディア）を定義していたが、`app/db.py` にはテーブル定義のみで
実際の投入処理がなく、`excluded_domains` テーブルが常に空のまま運用されていた
（`docs/requirements.md` 8章のフェーズ2タスク「競合除外ドメインの初期リスト最終確認」も未着手のまま残っていた）。

- `app/db.py`：`init_db()` に `_seed_excluded_domains()` を追加。`excluded_domains` が0件の場合のみ、
  ドメインが一意に特定できるもの（tabelog.com / hotpepper.jp / ekiten.jp / gnavi.co.jp / wikipedia.org
  （ja含む）/ amazon.co.jp / rakuten.co.jp / shopping.yahoo.co.jp）を投入する。「求人媒体各社」
  「まとめ系メディア」は具体的なドメイン名の指定がないため対象外とした（管理画面から追加する運用）
  - `excluded_domains.created_by` が `users` への外部キー必須のため、シード専用ユーザー行
    （`system@internal`）を `users` テーブルに先に作成してから投入
- `tests/test_db.py`・`tests/test_report_and_admin.py`：除外ドメイン一覧が「空であること」を前提にしていた
  アサーションを、初期リストの存在を考慮した部分一致・個別ドメイン確認に変更。全92件パス
- **未実施**：本番Cloud Runへの再デプロイ（起動時の`init_db()`で自動投入されるため、次回デプロイ時に反映される）
- 「求人媒体各社」「まとめ系メディア」の具体的なドメインは未確定。必要な業種が分かり次第、
  管理画面（`/admin/excluded-domains`）から個別に追加する運用とする

### 2026-08-19：ユーザーフィードバックによる仕様変更2件

診断1件目の実走確認後、ユーザーから以下の修正指示があり実装済み。

1. **「営業トーク要約」→「改善アドバイス」に変更**：文面を営業担当が読み上げる想定から、
   サイト運営者・担当者への改善アドバイス（「〜を改善しましょう」等、次の行動が分かる文章）に変更。
   - `app/clients/llm.py`：`sales_summary()` → `improvement_advice()` にリネーム、プロンプト変更
   - `app/pipeline.py`：結果JSONのキーを `sales_summary` → `improvement_advice` に変更
   - `app/templates/index.html` / `report.html`・`app/static/js/audit.js` / `main.css`：表示文言・DOM ID・CSSクラスを追随
2. **被リンク（権威性・被リンクカテゴリ15点）を廃止**：配点表から完全削除し、残り3カテゴリ
   （テクニカル30・コンテンツ30・LLMO25＝合計85点）を100点に正規化する方式に変更（ユーザー判断で決定、
   DataForSEO連携自体も完全停止・課金停止）。
   - `docs/scoring.md`・`docs/requirements.md`・`docs/architecture.md`・`docs/api-integrations.md`・
     `docs/deployment.md`：配点表・パイプライン図・APIキー一覧・キャッシュキー一覧などから被リンク関連の記載を削除
   - `app/scoring.py`：`score_authority()`・`AUTHORITY_*`定数を削除、`CATEGORY_WEIGHTS`から`authority`を削除、
     `rubric_version`を`2026.08.2`に更新
   - `app/pipeline.py`：`_run_backlinks()`工程を削除（7工程→6工程）、競合比較の「参照ドメイン数」列も削除
   - `app/clients/backlinks.py`：削除（DataForSEO呼び出しコード自体を撤去）
   - `app/config.py`：`dataforseo_login`/`dataforseo_password`/`backlink_cache_ttl`を削除
   - テスト一式（`test_scoring.py`・`test_pipeline.py`・`test_llm.py`・`test_api_audits.py`・`test_report_and_admin.py`）を更新、
     `test_backlinks.py`は削除。全83件パス確認済み
   - 上記2件をビルド・デプロイ後、再度実走確認したところ「被リンクを調査中」のまま画面が進まない不具合が発生。
     原因はブラウザが再デプロイ前の古い`static/js/audit.js`をキャッシュしたまま使い続けていたため
     （旧JSの`STAGES`配列に残っていた`backlinks`ステージの完了をSSEから永遠に待ち続けていた）。
     `app/main.py`でコンテナ起動時刻を`asset_version`としてJinja2グローバルに登録し、
     `base.html`（CSS）・`index.html`（audit.js）の読み込みURLに`?v={{ asset_version }}`を付与して
     再デプロイのたびにキャッシュバスティングされるよう修正（`app/main.py`・`app/templates/base.html`・`index.html`）
   - **未実施**：Secret Manager上の`DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD`シークレットの削除是非
     （残しておいても実害はないが、不要になったため削除を検討してよい）

### 2026-08-19：キーワード抽出の改善（ユーザーフィードバック3件目）

タイトルが「包茎手術・亀頭増大術・長茎術・ED・性病検査」のように`・`区切りで複数語を
並べている場合、1個のキーワードとしてまとめず語句ごとに分割してほしいという指摘。

- `app/clients/llm.py`
  - `extract_keywords()`のプロンプトに、区切り文字・括弧で並んだ語句は個別キーワードとして
    抽出する旨と具体例を追加
  - `_fallback_keywords()`（LLM呼び出し失敗時のフォールバック）の分割パターンを
    `[|｜\-–—:：/／]`（区切り文字のみ）から`・`・読点・カンマ・各種括弧を含む
    `_KEYWORD_SPLIT_PATTERN`に拡張し、`re.split()[0]`（先頭要素のみ採用）から
    全分割片を候補に追加する方式に変更
- `tests/test_llm.py`：フォールバックの新しい分割挙動に合わせて既存2件を更新、
  `・`区切りタイトルの分割を検証するテストを追加。全84件パス

### 2026-08-19：メモリ上限超過によるコンテナクラッシュを修正

診断が「スコア算出・要約生成中」で止まる不具合が発生。Cloud Runログを確認したところ、
`Memory limit of 2048 MiB exceeded with 2167 MiB used` でコンテナが再起動していた
（進行中ジョブはプロセス内メモリ保持のため、再起動でジョブごと消え、SSE再接続が404になり
画面がそこで止まって見える）。

原因は`app/pipeline.py`の`_run_competitor_comparison`が競合3社を`asyncio.gather`で完全並行クロールし、
自社クロールと合わせて最大4つのPlaywright（Chromium）プロセスがほぼ同時に起動していたこと。
Chromiumは1プロセスあたりのメモリ消費が大きく、2GB上限を超えていた。

対応（ユーザー判断：メモリ増設ではなく並列数制限を選択）：
- `app/clients/crawler.py`：`MAX_CONCURRENT_BROWSERS = 2`のグローバルセマフォ（`_browser_semaphore`）を追加し、
  `_fetch_with_playwright()`をこれで囲んだ。ドメイン単位の既存セマフォ（`_domain_semaphores`、同一ドメイン3並列）は
  異なるドメイン間の同時実行を制限できないため、別途ドメイン横断の上限として新設
- 診断1回あたりの実質的なクロール所要時間は伸びる可能性があるが、コンテナクラッシュより優先

同時に気づいた別件（今回は未修正、再発時に想定される事象として記録）：
Cloud RunのSQLiteは再デプロイ・コンテナ再起動のたびに揮発する一方、ブラウザの認証Cookieは
有効期限内であれば残り続けるため、「Cookieは有効だがusersテーブルに該当ユーザーがいない」状態で
`FOREIGN KEY constraint failed`が発生することがある（`docs/deployment.md`に既知の制約として記載あり）。
今回はログアウト→再ログインで復旧。恒久対応（例：外部キー違反時に自動ログアウトさせるミドルウェア等）は
工程6の本番運用開始が近づいたタイミングで改めて検討する。

### 2026-08-19：キーワード抽出の対象をmeta description・H3にも拡大（ユーザーフィードバック4件目）

前回（3件目）はタイトルの`・`区切り分割のみ対応していたが、meta descriptionと見出し(H1〜H3)にも
同様の分割を適用してほしいという指摘。

- `app/clients/crawler.py`：`CrawlResult`に`h3_list`フィールドを追加、`_parse_html()`でH3タグも抽出。
  スコアリング（`scoring.py`）はH1・H2のみを対象とする`docs/scoring.md`の定義通りのため変更なし
  （H3追加はキーワード抽出専用の情報源で、配点ロジックには影響しない）
- `app/clients/llm.py`
  - `extract_keywords()`のプロンプトにH3を追加、対象範囲を明示
  - `_fallback_keywords()`の候補にmeta descriptionとh3_listを追加
  - 分割パターン`_KEYWORD_SPLIT_PATTERN`に句点`。`を追加（meta descriptionは文章として
    書かれることが多く、句点区切りの各文もキーワード候補にする必要があったため）
- `tests/test_llm.py`：`_fallback_keywords`がmeta description・H3も分割対象にすることを検証する
  テストを追加、既存テストの期待値を更新。全85件パス

### 2026-08-19：ログイン時の`MismatchingStateError`（再発）を修正

前回（2026-08-19早い時点）直したのはCookie名衝突によるログイン失敗だったが、今回は別要因で
同じ`authlib.integrations.base_client.errors.MismatchingStateError`が発生し「サーバーエラー」画面が出た。

ログを確認したところ、1回目のログイン（`/auth/login`→`/auth/callback`）は正常に成功し診断も起票できていたが、
約6分後にGoogle側が`prompt=none`のサイレント再認証で**同じstateを使って`/auth/callback`を再度呼んでいた**。
1回目の処理でauthlibがそのstateをセッションから消費済みのため、2回目の照合で不一致となり500エラーになっていた。
ブラウザ・Google IdP側の挙動であり、コード側の不具合ではないが、ユーザー体験としてエラー画面を出すべきではない。

- `app/auth.py`：`callback()`で`MismatchingStateError`を捕捉し、その時点で有効なログインセッションCookie
  （`SESSION_COOKIE`）が既にあればエラーを見せずトップページへリダイレクト。ログイン済みでない場合のみ
  401エラーとして扱う
- `tests/test_auth.py`（新規）：ログイン済み時はリダイレクト、未ログイン時は401になることを検証。
  全87件パス

### 2026-08-19：`FOREIGN KEY constraint failed`を恒久対応

同じ`sqlite3.IntegrityError: FOREIGN KEY constraint failed`（`audits.created_by`→`users.email`）が
再デプロイのたびに再発するため、これまでの「ログアウト→再ログインで復旧」という運用回避をやめ、
根本対応した。

原因：`app/auth.py`の`require_auth()`は署名付きセッションCookieの検証のみ行い、DBに該当ユーザーが
実在するかは見ていなかった。Cloud RunのSQLiteは再デプロイ・コンテナ再起動のたびに揮発する一方、
ブラウザの認証Cookieは有効期限（14日）内であれば残り続けるため、「Cookieは有効だがusersテーブルに
該当ユーザー行がない」状態が频発し、audits等への書き込みで外部キー制約に落ちていた。

- `app/auth.py`：`require_auth()`の中で毎回`_upsert_user(email, name)`を呼び、セッションCookieが
  有効な時点でDBの該当ユーザー行の存在を保証するように変更。ログインコールバック時と同じ
  `INSERT ... ON CONFLICT DO UPDATE`なので、通常時（ユーザーが既に存在する）はオーバーヘッドも小さい
- `tests/test_auth.py`：`test_require_auth_recreates_missing_user_row`を追加。usersテーブルが空の状態で
  有効なセッションCookieのみを使って`POST /api/audits`が成功し、ユーザー行が自動的に作り直される
  ことを検証。全88件パス
- この対応により、今後のCloud Run再デプロイ後もユーザーは再ログイン不要で使い続けられる
  （ただし進行中の診断ジョブ自体はプロセス内メモリ保持のため、コンテナ再起動を挟むと消える点は
  引き続き制約として残る。工程6のジョブ管理をRedis+RQに置き換えるまでの暫定挙動）

### 2026-08-19：キーワード抽出をルールベースで大幅拡張（ユーザーフィードバック5件目）

これまで（3・4件目）はタイトル・meta description・見出しの区切り文字分割のみだったが、
「本文の頻出名詞」「meta keywords・alt属性・内部リンク文字列を補助的に使う」
「重複排除して出現回数順に並べる」という要望。ルールベースの候補生成として`_fallback_keywords()`
（LLM失敗時のフォールバックだが、要件自体が数え上げ処理でLLM判断が不要なため、ここに実装する
方針をユーザーと確認して決定）に実装した。

- `requirements.txt`：`fugashi==1.4.0`（MeCab互換の形態素解析、純Python実装）・
  `unidic-lite==1.0.8`（辞書データ）を追加
- `app/clients/crawler.py`：`CrawlResult`に`meta_keywords`・`alt_texts`・`internal_link_texts`を追加、
  `_parse_html()`で`<meta name="keywords">`・`<img alt>`・内部リンクのアンカーテキストを抽出
- `app/clients/llm.py`
  - `_fallback_keywords()`を全面刷新。主要情報源（title/meta description/h1〜h3/meta keywords、
    区切り文字分割）・本文頻出名詞（形態素解析）・補助情報源（alt属性・内部リンク文字列、
    区切り文字分割）の3種類の候補を`Counter`で集計し、主要情報源には優先度ボーナス
    （`_PRIMARY_SOURCE_BONUS = 1000`、本文でどれだけ頻出する語があっても基本的に逆転されない値）
    を加算した上で、出現回数の多い順・同スコア時は初出順にソートして上位N件を返す
  - `extract_keywords()`のプロンプトにもmeta keywords・alt属性・内部リンク文字列を追加情報として
    渡すよう変更（頻度ソート等のロジック自体はClaudeに強制していない）
- `tests/test_crawler.py`：meta keywords・alt属性・内部リンクテキストの抽出を検証するテストを追加
- `tests/test_llm.py`：本文頻出名詞の抽出、主要情報源が本文頻度に埋もれないこと、alt属性・
  内部リンク文字列の利用、重複排除を検証するテストを追加。全92件パス
- ローカル動作確認：`fugashi.Tagger()`がPython 3.13のvenvでインストール・実行できることを確認済み。
  Cloud Run側（`mcr.microsoft.com/playwright/python:v1.48.0-jammy`ベース）でのビルド確認はこの後実施

### 2026-08-21：競合候補の抽出範囲を検索結果1〜5位に限定（ユーザー指摘）

「競合3社との比較」の抽出プールが検索結果20位以内全体だったため、順位の低い無関係なサイトが
競合候補に混ざりうるという指摘。`docs/requirements.md` 4.5の選定ルールを「検索結果1〜5位に入り、
かつ自社より上位に表示された回数が多い順」に改訂し、`app/pipeline.py`の`_pick_competitors()`に
`COMPETITOR_CANDIDATE_MAX_RANK = 5`の足切りを追加（6位以降のドメインは候補にしない）。
「自社より上位」という既存の軸自体は維持。`tests/test_pipeline.py`に境界値テストを追加、全93件パス。
2026-08-21にCloud Buildでビルド（image digest `sha256:cfccf620...`）・`gcloud run deploy`で本番反映済み
（revision `seo-llmo-tool-00016-94m`、100%トラフィック）。ユーザーが実際の画面で確認予定。

### 2026-08-21：表示速度スコアの根拠文言から「PageSpeed」表記を削除（ユーザー指摘）

商談の場で外部ツール名（PageSpeed Insights）を見せたくないという要望。採点ロジック・PSI連携自体は
変更せず、`app/scoring.py`の`_speed_score()`が返すevidence文言のみ「PageSpeedスコア」→「表示速度スコア」、
失敗時の文言も「PageSpeed Insights 取得失敗のため0点」→「表示速度の計測に失敗したため0点」に変更。
`docs/scoring.md`表示要件に「外部ツール名を出さない」方針を追記。

### 2026-08-21：カテゴリ別スコアに抽象的な一言アドバイスを追加（ユーザー要望）

「カテゴリー別スコア」の各行の下に、そのカテゴリの改善余地を抽象的なニュアンスで一言添える機能を追加。
「〜すればもっと閲覧数が増える」「〜すれば改善の余地はあります」のような、具体的な施策名・数値には
一切触れない文言（実測値ベースの根拠は既存の折りたたみ内訳に任せる）。

- `app/scoring.py`：`CategoryScore`に`advice`フィールドを追加。カテゴリの達成率（score/weight）を
  総合スコアと同じ4段階バンド閾値で判定する`category_band()`を新設
- `app/clients/llm.py`：`category_advice(category_label, band)`を追加。Claude APIで生成し、
  カテゴリ名×バンド単位でキャッシュ（`llm:category_advice:{label}:{band}`、TTL30日。サイト非依存の
  文言のためキャッシュヒット率が高い）。失敗時は固定文にフォールバックし、フォールバック結果は
  キャッシュしない（`docs/api-integrations.md`5章「失敗した結果はキャッシュしない」に準拠）
- `app/pipeline.py`：`_run_score_and_narrative()`で計測済みカテゴリごとに`category_advice()`を並行実行。
  未計測カテゴリはアドバイス自体が不自然なため対象外
- `app/templates/report.html`・`app/static/js/audit.js`：カテゴリ行の直下・折りたたみ内訳の外側に
  `.category-advice`として常時表示
- `docs/requirements.md` 4.4.4・`docs/frontend.md` 5.5・`docs/api-integrations.md` 5章を追随して更新
- テスト：`tests/test_llm.py`（成功時キャッシュ・キャッシュヒット・失敗時フォールバック＆非キャッシュ）、
  `tests/test_scoring.py`（`category_band`の閾値・未計測時）、`tests/test_pipeline.py`（既存3件の統合テストに
  `category_advice`モックを追加）。全98件パス

### 2026-08-21：総合スコアをドーナツ型円グラフに変更（ユーザー要望）

「総合スコアのグラフを円グラフで、各カテゴリが分かるように色分けしたい」という要望。
`docs/frontend.md` 5.4節は旧仕様で「円形ゲージ（ドーナツ型）を使わない。横方向の目盛り付きバーとする」
（斜めから見たとき円は歪むが横バーは歪まないため）と明確に禁止していたが、ユーザー判断で撤回し改訂。
併せてCLAUDE.md「スコアが低いカテゴリのバーを赤くしない」原則との違い（今回の色分けは良し悪しではなく
カテゴリの区別のためであり、バンド色とは別系統）をユーザーに確認済み。

- `app/static/css/main.css`：カテゴリ区別用の3色を追加（`--category-technical-seo`＝青、
  `--category-content-seo`＝紫、`--category-llmo`＝橙。評価バンド色（緑・黄・オレンジ・赤）と
  混同しないよう別系統の配色にした）。旧`.score-track`/`.score-ticks`を`.score-donut*`に置き換え
- `app/report_view.py`：`build_score_donut_segments()`を新設。各カテゴリの獲得点数（%ではなく
  素点。3カテゴリ合計＋未達成分＝360度＝100点）を`conic-gradient()`用の累積%区間に変換し、
  完成済みのgradient文字列も返す。`build_report_context()`の戻り値に追加
- `app/templates/report.html`：total-scoreカードを円グラフ＋中央の穴（総合スコア数値）＋
  右側の凡例（色・カテゴリ名・獲得点数）に置き換え
- `app/templates/index.html`・`app/static/js/audit.js`：アプリ内リアルタイム結果表示も同様に変更。
  `renderTotalScore()`が`report_view.py`と同じロジックをJS側で再実装（画面・共有URL・PDFの3箇所で
  同一の計算結果になることをそれぞれ確認）
- `docs/frontend.md`：冒頭ステータス注記・3章配色・5.4節を改訂（円形ゲージ禁止の原則を撤回）
- テスト：`tests/test_report_and_admin.py`に`build_score_donut_segments()`のユニットテスト3件
  （合計が総合スコアと一致・未計測とゼロ点の除外・全未計測時）を追加。全101件パス
- Playwrightで`report.html`のレンダリング結果を確認済み（円グラフ・凡例・中央の総合スコア数値が
  期待通り表示されることを確認）。`index.html`側はDOM要素の存在のみ確認（`audit.js`はIIFEのため
  ブラウザ上で直接関数を呼べず、レンダリング結果の目視確認は`report.html`と共通のCSS・DOM構造で代替）

上記3件（表示速度スコアの表記変更・カテゴリ別アドバイス・総合スコアのドーナツ円グラフ化）はコミット
`e215355`にまとめ、2026-08-21にCloud Buildでビルド（image digest `sha256:893c391c...`）・
`gcloud run deploy`で本番反映済み（revision `seo-llmo-tool-00017-67q`、100%トラフィック）。

### 2026-08-21：カテゴリ別アドバイスを抽象的な一言から具体的な文章に方針転換（ユーザー指摘）

上記のカテゴリ別アドバイス初回実装は「具体的な施策名・数値には一切触れない抽象的な一言」だったが、
「改善アドバイス（右上のカラム）と同等のアドバイスをカテゴリ別に出したい」という指摘。実測値を根拠に
した具体的な文章に方針転換した。

- `app/clients/llm.py`：`category_advice()`のシグネチャに`items`（カテゴリ内の各評価項目。label・
  evidence・score・max_score）を追加。プロンプトを`improvement_advice()`と同様の構成
  （実測値を列挙→「〜を改善しましょう」調の1〜2文・100字以内）に全面変更。サイト個別の実測値に
  依存するためキャッシュは廃止（`improvement_advice()`と同じ扱い）。失敗時のフォールバックは
  達成率が最も低い項目名を根拠にした固定文に変更（`_fallback_category_advice(band, weak_items)`）
- `app/pipeline.py`：`_run_score_and_narrative()`で各カテゴリの`items`をdictに変換して渡すよう変更
- `docs/api-integrations.md`：カテゴリ別アドバイスのキャッシュ表エントリを削除（キャッシュ廃止のため）
- `docs/requirements.md` 4.4.4・`docs/frontend.md` 5.5：抽象的な一言→具体的な文章という方針転換を追記
- 表示側（`.category-advice`のCSS・DOM構造、`report.html`・`audit.js`のテンプレート部分）は変更なし。
  データの流し込み先（`result.score.categories[].advice`）が同じため
- テスト：`tests/test_llm.py`のキャッシュ関連テスト3件を新シグネチャに合わせて2件に統合。全100件パス
- Playwrightで`report.html`のレンダリング結果を確認済み（実測値を根拠にした具体的な文章が
  各カテゴリの下に表示されることを確認）
- コミット`e1b50a4`にまとめ、2026-08-21にCloud Buildでビルド（image digest `sha256:0d1dbfad...`）・
  `gcloud run deploy`で本番反映済み（revision `seo-llmo-tool-00018-qmt`、100%トラフィック）

### 2026-08-21：UI修正3件（ヘッダータイトル・キーワード数・カテゴリ別アドバイス位置）

- ヘッダータイトルを全画面共通で「SEO/LLMO診断ツール」→「SEO/AI検索（LLMO）診断ツール」に変更
  （`base.html`の`<title>`・各テンプレートの`app-title`、計5ファイル）
- `MAX_KEYWORDS`を8→10に変更（`app/config.py`のデフォルト値・`.env`・`.env.example`、
  および`docs/deployment.md`・`docs/api-integrations.md`・`docs/requirements.md`・`docs/frontend.md`の
  記載値を追随して更新）。SerpApi消費リクエスト数が1診断あたり約25%増（8〜16→10〜20）になる点は
  事前にユーザーへ確認済み（残枠に問題なしとの回答）
- カテゴリ別アドバイスの表示位置をユーザー指摘により修正。従来は「カテゴリ行（下線あり）→
  アドバイス文→折りたたみ内訳」の順で、区切り線がアドバイス文の上（カテゴリ行の直下）にあったため、
  アドバイス文が次のカテゴリの区画に近く見えていた。行・アドバイス文・内訳を`.category-group`で
  ひとまとめにし、区切り線を`.category-group`の下端に移動（`app/static/js/audit.js`・
  `app/templates/report.html`・`app/static/css/main.css`）。「行→アドバイス文→区切り線→内訳」の順になる
- Playwrightで`report.html`のレンダリング結果を確認済み。テスト全100件パス（既存テストにアプリ名・
  MAX_KEYWORDS値のハードコードなし、影響なしを確認済み）

### 2026-08-21：カテゴリ別スコアの表示名「LLMO対応度」→「AI検索対応度」（ユーザー要望）

画面表示のみの変更（`app/scoring.py`の`CategoryScore("llmo", "AI検索対応度", ...)`）。内部の
キー名（`llmo`）・CSS変数名（`--category-llmo`）・docs/の仕様書見出しは維持し、ユーザーに見える
文字列のみ変更した。テストデータ内のラベル文字列（`tests/test_scoring.py`・
`tests/test_report_and_admin.py`）も実態に合わせて更新。全100件パス。

上記4件（ヘッダータイトル・MAX_KEYWORDS・カテゴリ別アドバイス位置・LLMO表示名）はコミット
`227c9bc`にまとめ、2026-08-21にCloud Buildでビルド（image digest `sha256:b2f7c968...`）・
`gcloud run deploy`で本番反映済み（revision `seo-llmo-tool-00019-bk6`、100%トラフィック）。

### 2026-08-21：SerpApiからの移行先調査（Bright Data→却下、DataForSEO→保留中）

SerpApiの残枠・料金の問題から、他のSERPプロバイダへの切り替えをユーザーと検討した。

- **Bright Data**：却下。実際にAPIキーを発行しレスポンスを取得したところ、本ツールが依存する
  Google AI Overview（表示有無・引用URL判定）は構造化JSONでは返らず、「生のHTML」
  （`format: "raw"`）をこちらでパースする必要があると判明した。SerpApiは`ai_overview`を
  JSON1フィールドとして構造化済みで返すため、乗り換えるとパース処理の実装コスト・Googleの
  マークアップ変更への耐障害性の面で明確に劣る。詳細は開発者向けメモリ
  （`project-serp-provider-brightdata-rejected`）にも記録済み
- **DataForSEO SERP API**：有力候補。公式ドキュメント調査の結果、`ai_overview_element.references`
  （domain・url・title・text）という構造化JSONでAI Overviewが取得できることを確認した
  （`docs/api-integrations.md` 1章「将来の移行」に元々明記されていた移行先で、Backlinks連携の
  廃止＝2026-08-19とは別物）。料金は無料クレジット$1（登録時自動付与、Live queryで約500回分）、
  以降はLive $0.002/リクエスト・Standard $0.0006/リクエスト
  - **現在の状態**：ユーザーが https://app.dataforseo.com/register でアカウント登録済み、
    `.env`に`DATAFORSEO_LOGIN`・`DATAFORSEO_PASSWORD`（API Access画面の専用認証情報、
    サイトログインパスワードとは別物）を設定済み
  - 実際にテスト呼び出し（`serp/google/organic/live/advanced`エンドポイント）を1回試したところ、
    最初はパスワード誤りで401、修正後は**アカウント未検証（403、`Please verify your account`）**で
    ブロックされている。**次回はまずダッシュボードでメール確認等のアカウント検証を完了させることから
    再開する**
  - `SERP_PROVIDER=serpapi`のまま変更しておらず、本番・ローカルとも動作に影響なし
  - 検証完了後の流れ：(1) テストリクエストを再実行しレスポンスJSON構造を確認 →
    (2) `app/clients/serp.py`に`DataForSeoProvider`クラスを実装（`SerpProvider`プロトコルに準拠） →
    (3) `.env.example`・`docs/api-integrations.md`・`docs/deployment.md`に`DATAFORSEO_LOGIN`/
    `DATAFORSEO_PASSWORD`の仕様を追記 → (4) `SERP_PROVIDER`の切り替えテスト
  - 検討時に`.env`の`ANTHROPIC_API_KEY`へDataForSEOの認証情報が誤って混入する事故が1度発生し、
    ユーザーが訂正済み。次回作業時は`.env`の値を一通り目視確認してから着手すること

### 2026-08-24：LLMO対応度に「情報構造・発見巡回」系7項目を追加（ユーザー要望）

ユーザーからmainタグ・H1構造・見出し数・本文ブロック・タイトル文字数・description文字数・
構造化データ・canonical・robots.txt・sitemap.xml・llms.txt・noindexの12項目を追加したいという
要望があった。既存カテゴリ（テクニカルSEO／コンテンツSEO）と実測値が重複する5項目
（H1構造・タイトル文字数・description文字数・canonical・noindex）は追加せず、新規性のある
7項目のみをLLMO対応度に追加する方針をユーザーと合意（重複配点によるカテゴリ間の相関を避けるため）。
配点は「LLMO対応度の配点自体を増やす」「新規7項目に軽めの配点を新設し既存5項目は維持」を選択。

- `docs/scoring.md`・`docs/requirements.md`：総合配点をテクニカル30・コンテンツ30・LLMO35
  （合計95→100点換算）に変更。LLMO対応度は内部素点合計140点（既存5項目100点＋新規7項目40点）を
  35点に正規化する方式に変更した
- `app/clients/crawler.py`：`CrawlResult`に`main_count`・`heading_count`（h1〜h6合計）・
  `content_block_count`（article+section合計）・`has_json_ld_parse_error`・`robots_txt_status`
  （"allowed"/"disallowed_all"/"missing"）・`has_sitemap`・`has_llms_txt`を追加
  - `_robots_allows()`を`_fetch_robots_txt()`にリネームし、アクセス可否とスコア用の設置状態を
    1回のリクエストで両方返すよう変更（robots.txtの二重取得を避けるため）
  - `_fetch_text_exists()`を新設し、`fetch()`が`/sitemap.xml`・`/llms.txt`を並行取得する
  - JSON-LDの`json.JSONDecodeError`を握りつぶしていた既存コードに、パース失敗を記録する
    `has_json_ld_parse_error`フラグを追加（元のcontinueは維持しつつ、失敗した事実だけ残す）
- `app/scoring.py`：`score_llmo()`に新規7項目を追加。`CATEGORY_WEIGHTS["llmo"]`を25→35に、
  `RUBRIC_VERSION`を`2026.08.3`に更新
  - **実装中に発見したバグを合わせて修正**：`compute_improvements()`が「失点 ÷ 100 × カテゴリ重み」を
    ハードコードしており、各カテゴリの素点合計が100点であることを前提にしていた。LLMO素点が140点に
    なったことでこの前提が崩れるため、「失点 ÷ カテゴリ内の素点合計 × カテゴリ重み」に修正した
    （テクニカルSEO・コンテンツSEOは素点100のままのため計算結果に影響なし）
- `app/templates/report.html`・`app/static/js/audit.js`：カテゴリ内訳は`items`配列を汎用的に
  ループ表示する既存実装のため、テンプレート・JS側の変更は不要（新規7項目も自動的に表示される）
- テスト：`tests/test_crawler.py`に新規フィールドの検証（正常系・JSON-LD構文エラー系）を追加、
  `_robots_allows`参照テストを`_fetch_robots_txt`に追随。`tests/test_scoring.py`に新規7項目の
  満点・部分点・0点パターン、`compute_improvements`の正規化修正を検証するテストを追加。全106件パス
- **未実施**：実サイト検証（工程6の閾値調整タスクと合わせて、新規7項目の配点も実データで見直す）

### 2026-08-24（同日）：重複していた構造化データ判定を統合（ユーザー指摘）

上記の新規7項目デプロイ後、目視確認の依頼を受けてデプロイ作業中にユーザーから
「テクニカルSEOの構造化データ、LLMOの構造化データ（LLM可読性）はFAQ/QA構造化データの判定と
重複しているので削除してほしい。空いた点数は重要度の高い項目に割り振ってほしい」という指摘があった。

- **削除**：テクニカルSEO「構造化データ」（JSON-LD有無のみの判定、旧20点）、LLMO「構造化データ
  （LLM可読性）」（JSON-LDのパース成否判定、旧素点8点）。いずれもLLMOの「FAQ / QA構造化データ」
  （FAQPage/QAPage/HowTo型の有無）と実測値が重複していたため
- **再配分**（ユーザー判断）：
  - テクニカルSEOで空いた20点は残り4項目（表示速度モバイル/PC・モバイル対応・クロール可能性）に
    均等配分（各+5）。表示速度モバイル40→45・PC10→15・モバイル対応15→20・クロール可能性15→20
    （内訳noindex/canonical/HTTPSは旧8:4:3比率を維持したままスケール）
  - LLMOで空いた素点8点はFAQ/QA構造化データに上乗せ（25→33）。LLMO内部の素点合計は140点で変わらず
    （既存5項目100→108、新規項目40→32）
- `app/scoring.py`：`TECHNICAL_STRUCTURED_DATA_POINTS`・`LLMO_STRUCTURED_DATA_VALID_POINTS`・
  `LLMO_STRUCTURED_DATA_ERROR_POINTS`を削除、`score_technical_seo()`・`score_llmo()`から
  該当の採点ブロックを削除。`RUBRIC_VERSION`を`2026.08.4`に更新
- `app/clients/crawler.py`：`CrawlResult.has_json_ld_parse_error`を削除（LLM可読性判定の廃止で
  参照箇所がなくなったため）。JSON-LDパース失敗時の`continue`処理自体は維持（構文エラーのある
  スクリプトを無視して残りをパースし続ける挙動に変更なし）
- テスト：`tests/test_scoring.py`・`tests/test_crawler.py`から構造化データ関連の削除済み項目への
  参照を除去し、統合後のFAQ/QA構造化データがJSON-LD型検出を引き続きカバーすることを検証する
  テストを追加。全107件パス
- Cloud Buildでビルド（image digest `sha256:f3b7b637...`）・`gcloud run deploy`で本番反映済み
  （revision `seo-llmo-tool-00021-86z`、100%トラフィック、2026-08-24）

### 2026-08-24：DataForSEO SERP APIプロバイダを実装（アカウント検証完了・ユーザー作業）

上記のLLMO配点変更と同日、保留していたDataForSEOアカウント検証（`Please verify your account`で
テスト呼び出しがブロックされていた件）がユーザー側の作業で完了した。`docs/progress.md`
「2026-08-21：SerpApiからの移行先調査」に記録した残タスク（テストリクエスト再実行→
`DataForSeoProvider`実装→ドキュメント追記→切替テスト）を実施した。

- テストリクエスト（`serp/google/organic/live/advanced`）を再実行し成功を確認（`status_code: 20000`）。
  レスポンスは`tasks[0].result[0].items[]`を`type`（organic/ai_overview等）で判別する構造で、
  AI Overviewの引用元は`items[type=ai_overview].items[].references[].domain/url`
  （SerpApiの`ai_overview.references[].link`よりネストが1階層深い）で取得できることを確認した。
  desktop（`os=windows`）・mobile（`os=android`）両方でテスト成功
- `app/config.py`：`dataforseo_login`・`dataforseo_password`を`Settings`に追加（2026-08-19の
  Backlinks廃止時に一度削除された設定項目だが、用途が異なるSERP用として再度必要になったため復活）
- `app/clients/serp.py`：`DataForSeoProvider`を実装（`SerpProvider`プロトコル準拠）。
  Basic認証（login:passwordをBase64化）でPOSTし、`items`をtype判別してSerpApiと同じ
  `SerpResult`形式に変換する。ステータスは`status_code`（全体）と`tasks[0].status_code`
  （タスク単位）の両方を検証し、いずれも`20000`以外は`SerpError`として扱う（部分成功方針）。
  `get_provider()`に`SERP_PROVIDER=dataforseo`の分岐を追加
- `.env.example`・`docs/api-integrations.md`・`docs/deployment.md`：DataForSEO SERP用の
  環境変数・エンドポイント仕様・SerpApiとのレスポンス構造対応表を追記
- テスト：`tests/test_serp.py`に`DataForSeoProvider`の順位判定・AI Overview引用判定・
  タスクエラー・HTTP失敗時のテストを追加。全112件パス
- **`SERP_PROVIDER`は本番・ローカルとも`serpapi`のまま変更していない。** 実際の切り替えは
  別途ユーザー判断で実施する（Secret Manager・Cloud Run環境変数には`DATAFORSEO_LOGIN`/
  `DATAFORSEO_PASSWORD`は2026-08-18時点で既に配線済みのため、切り替え時の追加のGCP作業は不要）
- **未実施**：`SERP_PROVIDER=dataforseo`への実際の切り替え、切り替え後の実診断での動作確認、
  DataForSEO側の料金消費状況の確認（無料クレジット$1の残量等）

### 2026-08-24（同日）：SerpApiの使用制限によりSERP_PROVIDERをDataForSEOに切り替え

ユーザーがSerpApiダッシュボードで残枠を確認したところ使用制限にかかっており、SerpApi経由での
検索順位・AI Overview取得ができない状態と判明した（部分成功設計により診断自体は継続するが、
検索順位表・LLMOのAI Overview評価が実質機能していない状態だった）。実装済みの`DataForSeoProvider`
に切り替えることをユーザーと合意し、即座に切替を実施した。

- ローカル`.env`の`SERP_PROVIDER`を`serpapi`→`dataforseo`に変更
- `.venv/bin/python`で`serp.get_provider()`が`DataForSeoProvider`を返し、実際に
  `SEO対策 費用`で検索してAI Overview・上位表示ドメインが取得できることをローカルで確認
- 本番：`gcloud run services update seo-llmo-tool --region=asia-northeast1 --project=seo-llmo-tool
  --update-env-vars="SERP_PROVIDER=dataforseo"` でCloud Runの環境変数を追加・即時反映
  （revision `seo-llmo-tool-00022-4jk`、100%トラフィック）。Secret Managerの
  `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD`は既に配線済みのため追加のGCP作業は不要だった
- デプロイ後、`/auth/login`が正常にGoogle認証へリダイレクトすることを確認（アプリ起動自体に問題なし）
- **未実施**：本番環境での実診断1件を通した動作確認（ユーザーによるブラウザでの目視確認が必要）、
  DataForSEO側の料金消費状況の確認（無料クレジット$1の残量、Live $0.002/リクエストでの
  実消費ペース）、SerpApiの使用制限が解除された場合に`serpapi`へ戻すかどうかの判断

### 2026-08-24（同日）：本番未再デプロイのまま`SERP_PROVIDER=dataforseo`にした不具合を修正

上記の切替直後、ユーザーがブラウザで実診断したところ「未対応の SERP_PROVIDER です: dataforseo」
という警告が出て検索順位・競合比較が取得できなかった。

原因：`gcloud run services update --update-env-vars` は環境変数のみを更新するコマンドで、
コンテナイメージ自体は前回デプロイ時点（`DataForSeoProvider`実装前のコミット）のまま変わらない。
そのため環境変数だけ`dataforseo`に切り替わったが、動いているコード側の`get_provider()`は
まだ`serpapi`しか分岐を知らないバージョンで、未対応エラーになっていた。`DataForSeoProvider`の
実装はコミット・push済みだったが、Cloud Buildでの再ビルド・`gcloud run deploy`をこの時点では
まだ実行していなかった（環境変数更新とイメージ更新は別操作という認識が抜けていた）。

- `gcloud builds submit --tag gcr.io/seo-llmo-tool/seo-llmo-tool --project=seo-llmo-tool` で
  `DataForSeoProvider`を含む最新コードを再ビルド（image digest `sha256:edbad7a2...`）
- `gcloud run deploy` で本番反映（revision `seo-llmo-tool-00023-t28`、100%トラフィック）。
  `SERP_PROVIDER=dataforseo`の環境変数は新リビジョンにも引き継がれていることを確認済み
- `/auth/login`が正常にリダイレクトすることを確認（アプリ起動自体に問題なし）
- **未実施**：ユーザーによる再度のブラウザでの実診断確認（今度こそ検索順位・競合比較が
  正しく表示されるか）

---

## 更新ルール

- 各タスク完了時に本ファイルの該当チェックボックスを `[x]` に更新する
- 工程全体が完了した時点で `CLAUDE.md` の実装状況チェックリストも合わせて更新する
- 仕様と実装の間で矛盾に気づいた場合は、実装前に指摘する（CLAUDE.md「やらないこと」参照）
