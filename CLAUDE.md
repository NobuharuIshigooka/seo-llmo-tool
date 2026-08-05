# CLAUDE.md

## プロジェクト概要

営業担当が商談中にプロスペクトのサイトURLを入力すると、約90秒で SEO・LLMO の診断結果を表示する **Webアプリケーション**。社内10名が利用する。プロスペクト側の許可・データ連携は不要で、公開情報のみで診断が完結する。

**成果物はブラウザで動作するWebアプリ。** デスクトップアプリ（.exe）やCLIツールではない。営業担当がノートPCのブラウザで開き、商談相手に画面を見せながら使う。Cloud Run 上に1つのURLとして公開し、社内Googleアカウントでログインする。

## 最重要ルール

1. **スコアの数値は必ずルールベースで算出する。** Claude API に点数を判断させない。同一入力からは必ず同一スコアが出ること。配点は `docs/scoring.md` と `app/scoring.py` にのみ定義する。
2. **部分成功を徹底する。** どの工程が失敗しても診断全体を止めない。取得できた項目だけで結果を組み立て、失敗は画面に警告として明示する。商談中に画面が真っ白になるのが最悪の事態。
3. **診断は非同期ジョブとして実行する。** 90秒かかるため同期レスポンスにはしない。進捗は SSE で配信する。
4. **APIキーをフロントエンドに露出させない。** すべてサーバー側で保持する。
5. **外部APIは必ずキャッシュを経由する。** 従量課金のため、キャッシュを迂回する実装をしない。

## ドキュメント

作業前に該当するドキュメントを読むこと。

| ファイル | 内容 | 参照するタイミング |
|---|---|---|
| `docs/requirements.md` | 要件定義書（全体の正） | 仕様に迷ったとき |
| `docs/architecture.md` | 構成・技術スタック・ディレクトリ・ジョブ設計 | 設計判断が必要なとき |
| `docs/api-integrations.md` | 外部API4種の仕様・キャッシュ・エラー処理 | API連携の実装時 |
| `docs/scoring.md` | 配点表（スコアの唯一の定義） | 採点ロジックの実装・調整時 |
| `docs/frontend.md` | 画面仕様・配色・書体・レイアウト | UI実装時 |
| `docs/deployment.md` | 環境変数・デプロイ手順 | 環境構築・デプロイ時 |
| `docs/progress.md` | 実装進捗の詳細タスク一覧（工程1〜6の作業単位チェックリスト） | 作業開始前・作業完了後の進捗更新時 |

配色（紺＋金）・書体・サイズは `docs/frontend.md` に確定値がある。**そこにない色や文字サイズを勝手に足さないこと。** 特に、スコアが低いカテゴリのバーを赤くしない（商談の場で攻撃的に見えるため、全カテゴリ同色）。

## 技術スタック

- バックエンド: Python 3.11 / FastAPI
- 非同期処理: asyncio（プロセス内ジョブ管理）＋ Server-Sent Events
- クロール: Playwright (Chromium)、フォールバックに httpx + BeautifulSoup
- フロントエンド: サーバーサイドテンプレート + 素のJavaScript（ビルド工程なし）
- DB: SQLite
- 認証: Google OAuth 2.0（社内ドメイン限定）
- 実行環境: Google Cloud Run（min=1 / max=1）

Reactやビルドツールを導入しない。1画面完結の社内ツールであり、保守を単純に保つことを優先する。

## ディレクトリ構成

```
app/
  main.py            FastAPI エントリ・ルーティング
  config.py          環境変数の読み込み
  auth.py            Google OAuth・セッション
  db.py              SQLite（履歴・キャッシュ・除外ドメイン）
  jobs.py            ジョブ管理・SSE イベント
  pipeline.py        診断パイプライン（7工程の統括）
  scoring.py         配点表の実装 ★数値の定義はここだけ
  clients/
    serp.py          SerpApi
    backlinks.py     DataForSEO Backlinks
    crawler.py       Playwright / httpx
    psi.py           PageSpeed Insights
    llm.py           Claude API
  templates/         Jinja2 テンプレート
  static/            CSS / JS
docs/                各種仕様書
data/                SQLite ファイル（Git管理外）
```

## 開発コマンド

```bash
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload    # http://localhost:8000
pytest                            # テスト
```

## 実装状況

工程ごとの作業単位の詳細チェックリストは `docs/progress.md` にある。作業完了後は必ずそちらを更新し、工程全体が完了した時点で以下も更新すること。

- [ ] 工程1 基盤（FastAPI・Google OAuth・DB・ジョブ管理・SSE）※着手中：ディレクトリ構成・requirements・.venv・`/healthz` まで完了
- [ ] 工程2 診断エンジン（クロール・SerpApi・PSI・DataForSEO・Claude）
- [ ] 工程3 スコアリング
- [ ] 工程4 結果画面 ※`docs/frontend.md` 確定後
- [ ] 工程5 共有URL・PDF出力・履歴・除外ドメイン管理
- [ ] 工程6 Cloud Run デプロイ・実サイト検証・閾値調整

## やらないこと

- v2 の機能（複数ページ診断、バッチ診断、ChatGPT/Gemini の言及監視、前回比較）に先回りして着手しない
- 配点や閾値を `scoring.py` 以外の場所にハードコードしない
- ローカル配布用のパッケージング（.exe 等）を作らない
- 既存のドキュメントと異なる仕様を勝手に決めない。矛盾を見つけたら実装前に指摘すること
