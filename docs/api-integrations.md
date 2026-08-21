# 外部API連携仕様

すべて自社契約のAPI。診断対象サイト側の契約・協力は一切不要。

| API | 用途 | 課金 |
|---|---|---|
| SerpApi | 検索順位・AI Overview | 月額サブスク |
| Claude API | キーワード推定・競合解説・改善アドバイス | 従量 |
| Google PageSpeed Insights | 表示速度・Core Web Vitals | 無料（要APIキー） |
| Google OAuth 2.0 | 認証 | 無料 |

2026-08-19：DataForSEO Backlinks（被リンク・ドメインランク）連携を廃止した（ユーザー判断）。

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

1診断あたり：キーワード10個（2026-08-21：8個から変更） × デバイス選択（PCのみ/スマホのみ/両方）＝ 10〜20リクエスト。

### 注意

- 月間枠を使い切ると早期更新（プラン料金の再支払い）となる。**残枠を管理画面に表示すること**
- プランごとに1時間あたりの上限がある（月間枠の20%）
- 同時実行数は4に制限する

### 将来の移行

DataForSEO SERP API（Liveモード）への切り替えを想定し、クライアントは抽象化しておく。`SerpProvider` プロトコルを定義し、`SerpApiProvider` / `DataForSeoProvider` を差し替え可能にする。環境変数 `SERP_PROVIDER` で選択。

---

## 2. Claude API

### 呼び出し

`POST https://api.anthropic.com/v1/messages`
ヘッダ：`x-api-key` / `anthropic-version: 2023-06-01`
モデル：`claude-sonnet-5`（環境変数 `ANTHROPIC_MODEL` で変更可）

### 用途は3つのみ

| 関数 | 役割 | temperature |
|---|---|---|
| `extract_keywords()` | 主要キーワードを10個推定（JSON配列で出力、2026-08-21：8個から変更） | 0.0 |
| `explain_competitors()` | 競合との数値差分を自然文化 | 0.3 |
| `improvement_advice()` | 顧客向けの改善アドバイス文（4〜5文・300字以内、2026-08-19：営業トーク要約から改称） | 0.3 |

### 絶対に守ること

- **スコアの数値を生成させない。** 採点は `scoring.py` のルールベースのみ
- プロンプトで「与えられた数値以外を書かない」「推測で数値を作らない」を明示する
- 出力はプレーンテキスト（Markdown記法を使わせない）。画面にそのまま表示するため
- `improvement_advice()` は「〜という状態です」の現状説明ではなく「〜を改善しましょう」のように次の行動が分かるアドバイス文にする
- JSON出力を期待する場合は、コードフェンスを除去してからパースする

### 失敗時

- `extract_keywords()` → ルールベースの候補生成にフォールバック（下記参照）
- `explain_competitors()` → 競合解説を非表示、警告に記録
- `improvement_advice()` → スコアと弱点項目から組み立てたテンプレート文にフォールバック

### `extract_keywords()` のフォールバック（`_fallback_keywords()`、2026-08-19実装）

Claude API呼び出しが失敗した場合、以下のルールベースでキーワード候補を生成する（`app/clients/llm.py`）。

1. **主要情報源**（title / meta description / 見出しh1〜h3 / meta keywords）を「・」「、」「/」
   「|」「()」「。」等の区切り文字・括弧で分割し、それぞれ個別の候補にする
2. **本文**を形態素解析（`fugashi` + `unidic-lite`、MeCab互換）で解析し、名詞のみを抽出
   （1文字名詞・「こと」「もの」等のストップワードは除外）して候補に加える
3. **補助情報源**（画像のalt属性、内部リンクのアンカーテキスト）も同様に区切り文字で分割し、
   候補に加える
4. 全候補の出現回数を集計し、主要情報源からの候補には優先度ボーナス（`_PRIMARY_SOURCE_BONUS`）を
   加算した上で、重複を除いてスコアが高い順に並べ、上位 `MAX_KEYWORDS` 件を返す
   （主要情報源からの候補は、本文中でどれだけ頻出する語があっても基本的に優先される設計）

Claude API本体（`extract_keywords()`のプロンプト）にも同じ情報源（meta keywords・alt属性・
内部リンクテキストを含む）を渡し、タイトルや見出しに出てこない語でも本文で繰り返し使われて
いれば候補に含めるよう指示している。ただし頻度カウント・優先度ソート自体はプロンプトでは
制御しておらず、Claudeの判断に委ねる（フォールバック側とは異なり厳密な再現性は保証しない）。

---

## 3. Google PageSpeed Insights

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

## 4. Google OAuth 2.0

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

## 5. キャッシュとコスト制御

| 対象 | TTL | キー |
|---|---|---|
| SERP結果 | 24時間 | `serp:{device}:{gl}:{keyword}` |
| クロール結果 | 6時間 | `crawl:{url}` |
| PageSpeed | 12時間 | `psi:{strategy}:{url}` |

- 失敗した結果はキャッシュしない
- 1日の診断上限：30件／人（環境変数 `DAILY_AUDIT_LIMIT`）
- SERP同時実行数：4
- クロール同時実行数：3

## 6. クロールのマナー

競合サイトを取得するため必ず守る。

- robots.txt を尊重する（取得できない場合は許可扱い）
- User-Agent を明示し、連絡先URLを含める
- 同一ドメインへの同時アクセスを制限する
- タイムアウト25秒
- Playwright が失敗した場合のみ httpx + BeautifulSoup にフォールバック
