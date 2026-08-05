# 実装進捗

CLAUDE.md の実装状況チェックリスト（工程1〜6）を作業単位まで分解したもの。
**作業を終えるたびに本ファイルのチェックボックスを更新し、CLAUDE.md 側のチェックリストも合わせて更新すること。**

参照する仕様書：`docs/requirements.md`（全体の正）/ `docs/architecture.md` / `docs/api-integrations.md` / `docs/scoring.md` / `docs/frontend.md` / `docs/deployment.md`

---

## 工程1 基盤（FastAPI・Google OAuth・DB・ジョブ管理・SSE）

- [x] ディレクトリ構成の作成（`app/clients` `app/templates` `app/static` `data`）
- [x] `requirements.txt` 作成（FastAPI / Playwright / httpx+BeautifulSoup / Authlib / anthropic SDK / pytest 等）
- [x] Python 3.11 で `.venv` 作成、依存インストール、`playwright install chromium`
- [x] `.env.example` 作成（`docs/deployment.md` の環境変数一覧に準拠）、`.gitignore` 作成
- [x] `app/config.py`：環境変数読み込み
- [x] `app/main.py`：FastAPI エントリ、`/healthz` のみ実装・起動確認済み
- [ ] `app/db.py`：SQLite初期化・テーブル定義（`users` / `audits` / `cache` / `excluded_domains`）
- [ ] `app/auth.py`：Google OAuth 2.0（Authorization Code フロー）
  - [ ] `/auth/login` `/auth/callback` `/auth/logout`
  - [ ] `hd` パラメータ＋サーバー側での email ドメイン再検証（`ALLOWED_HD` / `ALLOWED_EMAILS`）
  - [ ] セッションクッキー（署名付き・HttpOnly・Secure・SameSite=Lax）
  - [ ] `/healthz` 以外の全エンドポイントに認証必須を適用
- [ ] `app/jobs.py`：ジョブ管理
  - [ ] プロセス内メモリ（dict）でジョブ状態を保持
  - [ ] `POST /api/audits` で job_id を即返却し `asyncio.create_task()` でバックグラウンド実行
  - [ ] SSE イベント配信の仕組み（工程ごとの progress イベント）
- [ ] `GET /api/audits/{id}/events`（SSE）
  - [ ] 15秒ごとのキープアライブコメント
  - [ ] レスポンスヘッダ `Cache-Control: no-cache` / `X-Accel-Buffering: no`
  - [ ] 後から接続したクライアントへの過去イベント再送
- [ ] 1日の診断上限（`DAILY_AUDIT_LIMIT`、人あたり）のレート制限実装

## 工程2 診断エンジン（クロール・SerpApi・PSI・DataForSEO・Claude）

- [ ] `app/clients/crawler.py`
  - [ ] Playwright (Chromium) による自社・競合クロール
  - [ ] 失敗時のみ httpx + BeautifulSoup にフォールバック
  - [ ] robots.txt 尊重（取得不可時は許可扱い）／User-Agent 明示（連絡先URL含む）
  - [ ] 同一ドメイン同時アクセス制限（3並列）／タイムアウト25秒
  - [ ] キャッシュ `crawl:{url}`（TTL: `CRAWL_CACHE_TTL`）
- [ ] `app/clients/llm.py`（Claude API）
  - [ ] `extract_keywords()`：title/meta description/見出し/本文から8個推定、JSON配列出力、temperature=0.0
  - [ ] `explain_competitors()`：競合との数値差分の自然文化、temperature=0.3
  - [ ] `sales_summary()`：営業トーク要約（4〜5文・300字以内）、temperature=0.3
  - [ ] プロンプトで「与えられた数値以外を書かない」を明示、スコアを生成させない
  - [ ] JSON出力はコードフェンス除去してからパース
  - [ ] 失敗時フォールバック（簡易抽出／非表示＋警告／テンプレート文）
- [ ] `app/clients/serp.py`（SerpApi）
  - [ ] `SerpProvider` プロトコル定義（将来 DataForSEO SERP へ切替可能な抽象化）
  - [ ] `SerpApiProvider` 実装（`engine=google`, `hl=ja`, `gl=jp`, `location=Japan`, `device`, `num=20`）
  - [ ] AI Overview 判定（`ai_overview` キーの有無、`references[].link` からドメイン正規化して引用判定）
  - [ ] キャッシュ `serp:{device}:{gl}:{keyword}`（TTL: `SERP_CACHE_TTL`）、同時実行数4
  - [ ] 失敗時は順位項目を空にして続行
  - [ ] SerpApi 残枠を取得・保持する仕組み（管理画面表示用、工程5）
- [ ] `app/clients/psi.py`（PageSpeed Insights）
  - [ ] `strategy=mobile` / `desktop` 両方取得、`category=PERFORMANCE`
  - [ ] `categories.performance.score` / LCP / CLS / TBT / `loadingExperience` 取得
  - [ ] キャッシュ `psi:{strategy}:{url}`（TTL: `PSI_CACHE_TTL`）、タイムアウト90秒
  - [ ] 失敗時は表示速度の配点を0点にして続行
- [ ] `app/clients/backlinks.py`（DataForSEO Backlinks）
  - [ ] `POST /v3/backlinks/summary/live`（Basic認証、リクエストボディは配列）
  - [ ] 自社1件＋競合3社＝4リクエスト、`tasks[0].result[0]` からパース
  - [ ] 取得指標：`referring_domains`（主指標）／`rank`／`backlinks`（参考）／dofollow比率（参考）
  - [ ] キャッシュ `backlinks:{domain}`（TTL: `BACKLINK_CACHE_TTL`）
  - [ ] 失敗・未契約時は権威性カテゴリを「未計測」として続行
- [ ] `app/pipeline.py`：7工程の統括
  - [ ] 工程1 crawl（致命エラー扱い、唯一続行不能）
  - [ ] 工程2 keywords（失敗時は見出し簡易抽出にフォールバック）
  - [ ] 工程3 serp／工程4 psi／工程6 backlinks を並行実行
  - [ ] 工程5 competitors（自社より上位表示回数が多い順に3社抽出、除外ドメイン適用）
  - [ ] 工程7 score／narrative
  - [ ] 各工程を try/except で包み `warnings: list[str]` に積む。黙って空データを返さない
  - [ ] 結果JSONに `warnings` を必ず含める

## 工程3 スコアリング

- [ ] `app/scoring.py`：`docs/scoring.md` の配点をそのまま実装（配点・閾値はこのファイルのみに定義）
  - [ ] テクニカルSEO（30点）：表示速度モバイル40/PC10・モバイル対応15・構造化データ20・クロール可能性15
  - [ ] コンテンツSEO（30点）：本文情報量30・キーワード網羅性25・タイトル見出し構造25・E-E-A-T20
  - [ ] LLMO対応度（25点）：FAQ構造化25・Q&A見出し20・冒頭定義文20・リスト表15・AI Overview引用20
  - [ ] 権威性・被リンク（15点）：参照ドメイン数8・ドメインランク7
  - [ ] 未計測カテゴリの除外＋残り配点での100点正規化ロジック
  - [ ] 評価バンド（good/fair/weak/poor）算出
  - [ ] 改善インパクト算出（失点÷100×カテゴリ重み、上位3件抽出、被リンク関連は除外）
  - [ ] `rubric_version` を結果JSONに含める
  - [ ] 同一入力→同一スコアを保証するユニットテスト（`pytest`）

## 工程4 結果画面（`docs/frontend.md` 確定後に着手）

- [ ] `docs/frontend.md` の確定値（配色・書体・サイズ）を確認し、独自の色や文字サイズを追加しない
- [ ] 診断入力画面（URL入力＋デバイス選択、スキーム自動補完、その場バリデーション）
- [ ] 進捗レール画面（7工程・SSE購読・完了工程に実測値を表示）
- [ ] 診断結果画面
  - [ ] 営業トーク要約（最上部）
  - [ ] 総合スコア＋カテゴリ別スコア（全カテゴリ同色、赤色による強調はしない）
  - [ ] キーワード別順位表（PC/スマホ・AI Overview表示）
  - [ ] 競合比較セクション
  - [ ] 改善インパクト上位3件
  - [ ] 「トップページを診断した結果です」の明示
  - [ ] 警告バナー（`warnings` の表示）
- [ ] キーワードの手動編集（追加・削除）→再診断

## 工程5 共有URL・PDF出力・履歴・除外ドメイン管理

- [ ] `GET /report/{id}`：閲覧専用の共有レポート画面（認証必須）
- [ ] `GET /report/{id}.pdf`：Playwright `page.pdf()` によるA4出力（画面と同一のJinja2描画を再利用）
- [ ] `GET /history`：診断履歴一覧（ドメイン単位、実行者・日時・スコア）
- [ ] `GET /admin/excluded-domains`：競合除外ドメインの追加・削除画面
- [ ] SerpApi 残枠の管理画面表示

## 工程6 Cloud Run デプロイ・実サイト検証・閾値調整

- [ ] Dockerfile（`mcr.microsoft.com/playwright/python` ベース）
- [ ] Secret Manager にAPIキー類を格納し環境変数として注入
- [ ] Cloud Run 設定：min-instances=1 / max-instances=1 / CPU2 / メモリ2GB / タイムアウト300秒 / 同時実行数20
- [ ] Cloud Storage への SQLite 日次バックアップ
- [ ] 実サイトでの診断10〜20件を確認し、`scoring.py` の満点ライン（本文文字数・参照ドメイン数等）を調整
- [ ] SerpApi 残枠・DataForSEO 残高の運用確認体制の整備

---

## 更新ルール

- 各タスク完了時に本ファイルの該当チェックボックスを `[x]` に更新する
- 工程全体が完了した時点で `CLAUDE.md` の実装状況チェックリストも合わせて更新する
- 仕様と実装の間で矛盾に気づいた場合は、実装前に指摘する（CLAUDE.md「やらないこと」参照）
