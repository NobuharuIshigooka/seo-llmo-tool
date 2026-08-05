# 外部API連携仕様

すべて自社契約のAPI。診断対象サイト側の契約・協力は一切不要。

| API | 用途 | 課金 |
|---|---|---|
| SerpApi | 検索順位・AI Overview | 月額サブスク |
| DataForSEO Backlinks | 被リンク・ドメインランク | 従量（前払い残高） |
| Claude API | キーワード推定・競合解説・営業トーク要約 | 従量 |
| Google PageSpeed Insights | 表示速度・Core Web Vitals | 無料（要APIキー） |
| Google OAuth 2.0 | 認証 | 無料 |

---

## 1. SerpApi

### 呼び出し

`GET https://serpapi.com/search.json`

| パラメータ | 値 |
|---|---|
| `engine` | `google` |
| `q` | キーワード |
| `hl` / `gl` / `location` | `ja` / `jp` / `Japan` |
| `device` | `desktop` または `mobile` |
| `num` | `20`（上位20位まで） |
| `api_key` | 環境変数 |

### AI Overview

レスポンスの `ai_overview` キーの有無で表示判定する。引用元は `references[].link` から取得し、ドメインに正規化して自社ドメインとの一致で「引用あり」と判定する。

`page_token` のみが返り `references` が含まれないケースがある。本文取得には `engine=google_ai_overview` への追加リクエスト（＝追加課金）が必要なため、**既定ではオフ**。環境変数 `FETCH_AI_OVERVIEW_DETAIL` で切り替える。

### リクエスト数

1診断あたり：キーワード8個 × デバイス選択（PCのみ/スマホのみ/両方）＝ 8〜16リクエスト。

### 注意

- 月間枠を使い切ると早期更新（プラン料金の再支払い）となる。**残枠を管理画面に表示すること**
- プランごとに1時間あたりの上限がある（月間枠の20%）
- 同時実行数は4に制限する

### 将来の移行

DataForSEO SERP API（Liveモード）への切り替えを想定し、クライアントは抽象化しておく。`SerpProvider` プロトコルを定義し、`SerpApiProvider` / `DataForSeoProvider` を差し替え可能にする。環境変数 `SERP_PROVIDER` で選択。

---

## 2. DataForSEO Backlinks

### 呼び出し

`POST https://api.dataforseo.com/v3/backlinks/summary/live`
Basic認証（`DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`）。

リクエストボディは**配列**で渡す形式。レスポンスは `tasks[0].result[0]` にネストする。

### 対象と取得指標

自社1件＋競合3社＝**4リクエスト／診断**。

| 指標 | 用途 |
|---|---|
| `referring_domains` | **主指標**。スコア算出に使用 |
| `rank` | ドメインランク。スコア算出に使用 |
| `backlinks` | 参考表示のみ |
| dofollow比率 | 参考表示のみ |

被リンク総数（`backlinks`）を主指標にしない。同一サイトからの大量リンクで実力より大きく見えるため。

### 未契約・失敗時

権威性カテゴリを「未計測」とし、**総合スコアから除外して残り85点満点を100点に正規化**する。推定値で埋めない。

---

## 3. Claude API

### 呼び出し

`POST https://api.anthropic.com/v1/messages`
ヘッダ：`x-api-key` / `anthropic-version: 2023-06-01`
モデル：`claude-sonnet-5`（環境変数 `ANTHROPIC_MODEL` で変更可）

### 用途は3つのみ

| 関数 | 役割 | temperature |
|---|---|---|
| `extract_keywords()` | 主要キーワードを8個推定（JSON配列で出力） | 0.0 |
| `explain_competitors()` | 競合との数値差分を自然文化 | 0.3 |
| `sales_summary()` | 営業トーク用の要約（4〜5文・300字以内） | 0.3 |

### 絶対に守ること

- **スコアの数値を生成させない。** 採点は `scoring.py` のルールベースのみ
- プロンプトで「与えられた数値以外を書かない」「推測で数値を作らない」を明示する
- 出力はプレーンテキスト（Markdown記法を使わせない）。画面にそのまま表示するため
- `sales_summary()` では、短期改善できない被リンク施策を「最初に着手すべきこと」に挙げさせない
- JSON出力を期待する場合は、コードフェンスを除去してからパースする

### 失敗時

- `extract_keywords()` → title・見出しからの簡易抽出にフォールバック
- `explain_competitors()` → 競合解説を非表示、警告に記録
- `sales_summary()` → スコアと弱点項目から組み立てたテンプレート文にフォールバック

---

## 4. Google PageSpeed Insights

`GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed`

| パラメータ | 値 |
|---|---|
| `url` | 診断対象URL |
| `strategy` | `mobile` / `desktop`（両方取得） |
| `category` | `PERFORMANCE` |
| `key` | 環境変数（無くても動くが制限が厳しい） |

取得値：`categories.performance.score`、LCP、CLS、TBT、`loadingExperience` のフィールドデータ。

**1回30〜45秒かかり、これが診断時間の支配項。** 短縮不可のため、UI側の進捗表示で体感を吸収する。タイムアウトは90秒。

---

## 5. Google OAuth 2.0

| 項目 | 仕様 |
|---|---|
| フロー | Authorization Code |
| スコープ | `openid` `email` `profile` |
| ドメイン制限 | `hd` パラメータ＋サーバー側で email ドメインを再検証 |
| 追加の許可リスト | 環境変数で個別メールアドレスを許可可能 |
| セッション | 署名付きクッキー（HttpOnly / Secure / SameSite=Lax） |
| 記録 | 診断ごとに実行者の email を保存 |

`hd` パラメータはクライアント側のヒントに過ぎないため、**サーバー側でのドメイン再検証を必ず行う**こと。

---

## 6. キャッシュとコスト制御

| 対象 | TTL | キー |
|---|---|---|
| SERP結果 | 24時間 | `serp:{device}:{gl}:{keyword}` |
| クロール結果 | 6時間 | `crawl:{url}` |
| PageSpeed | 12時間 | `psi:{strategy}:{url}` |
| 被リンク | 7日 | `backlinks:{domain}` |

- 失敗した結果はキャッシュしない
- 1日の診断上限：30件／人（環境変数 `DAILY_AUDIT_LIMIT`）
- SERP同時実行数：4
- クロール同時実行数：3

## 7. クロールのマナー

競合サイトを取得するため必ず守る。

- robots.txt を尊重する（取得できない場合は許可扱い）
- User-Agent を明示し、連絡先URLを含める
- 同一ドメインへの同時アクセスを制限する
- タイムアウト25秒
- Playwright が失敗した場合のみ httpx + BeautifulSoup にフォールバック
