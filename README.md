# 🎬 CINEMA ROULETTE

迷ったとき、今夜見る映画をランダムに1本選んでくれるサイトです。
**Netflix / Prime Video** で配信中の作品から、ジャンル指定または完全ランダムで1本を表示します。

配信リストは [MOVIE WALKER PRESS](https://press.moviewalker.jp/list/vod/) の「配信中の作品」ページを
**GitHub Actions で毎日自動取得**して更新します。リストから外れた作品は自動的に表示対象から除外されます。

---

## 仕組み

```
GitHub Actions（毎日朝6時 JST）
   └─ scripts/scrape.py が MOVIE WALKER を巡回
        └─ data/movies.json を更新して自動コミット
              └─ index.html が movies.json を読んで表示
```

- **サーバー不要**。GitHub Pages で無料公開できます。
- リストに載っている作品だけが対象になるので、配信終了した作品は翌日には出てこなくなります。

---

## セットアップ手順（はじめての方向け）

### 1. このフォルダを GitHub にアップロード

新しいリポジトリ（例: `cinema-roulette`）を作り、このフォルダの中身を全部アップロードします。
（フォルダ構成を崩さないように、`scripts/` `data/` `.github/` ごとアップしてください）

### 2. Actions の書き込み権限を確認

リポジトリの **Settings → Actions → General → Workflow permissions** で
**「Read and write permissions」** を選んで保存します。
（自動コミットに必要です）

### 3. 初回だけ手動で実行してデータを作る

リポジトリの **Actions** タブ → 「配信リストを毎日更新」→ **Run workflow** を押します。
数分〜十数分で `data/movies.json` が実データに更新されます。
（作品数が多いため、初回は時間がかかります）

### 4. GitHub Pages で公開する

**Settings → Pages → Build and deployment** で
Source を **Deploy from a branch**、Branch を **main / (root)** にして保存。
数分後、表示される URL（`https://ユーザー名.github.io/cinema-roulette/`）でサイトが見られます。

以降は **毎朝自動で** 配信リストが更新されます。手動操作は不要です。

---

## 取得量の調整（任意）

全作品の「あらすじ」まで取ると時間がかかります（Netflix だけで1800作品以上）。
件数を絞りたい場合は `.github/workflows/update-movies.yml` の `env: DETAIL_LIMIT` を有効化してください。

```yaml
      - name: 配信リストを取得
        run: python scripts/scrape.py
        env:
          DETAIL_LIMIT: '300'   # 各サービス先頭300作品まであらすじを取得
```

`DETAIL_LIMIT` を設定しても、**作品リスト自体（タイトル・年・サムネ）は全件取得**されます。
あらすじの取得数だけが制限されます。

---

## ローカルで試すには

`index.html` をブラウザでダブルクリックしても、ブラウザの制約で `movies.json` を読めず
デモ用作品が表示されます。実データで確認したい場合は簡易サーバーを立ててください。

```bash
cd cinema-roulette
python3 -m http.server 8000
# ブラウザで http://localhost:8000/ を開く
```

---

## ファイル構成

```
cinema-roulette/
├── index.html                       # サイト本体
├── data/
│   └── movies.json                  # 配信リスト（自動更新される）
├── scripts/
│   └── scrape.py                    # MOVIE WALKER 取得スクリプト
├── .github/
│   └── workflows/
│       └── update-movies.yml        # 毎日自動実行の設定
└── README.md
```

---

## 🏆 今週のTop10 機能のステータス（現在: 非公開）

Netflix Tudum の週次ランキングを日本語化して紹介する Top10 機能は、**現在ユーザーには非公開**です。

- **フロント**: 全ページのヘッダーから「🏆 今週のTop10」ナビリンクを削除済み。`top10.html` に直接アクセスした場合は「ページが見つかりません（ご利用いただけません）」表示（`noindex, nofollow`）。`sitemap.xml` からも除外済み。
- **バックエンドは稼働継続**: `.github/workflows/update-top10.yml`（毎週水曜）は **停止していません**。`scripts/scrape_top10.py` と `scripts/build_top10.py` が毎週実行され、`data/top10_cache.json`（邦題・メタの永続キャッシュ）と `data/top10.json` は裏で更新され続けます。復活時にキャッシュ済みデータをすぐ使えるようにするためです。
- 非公開／公開の切り替えは `scripts/build_top10.py` 冒頭の `MAINTENANCE_MODE` で制御します（`True`=非公開 / `False`=本番カード一覧）。

### 復活手順（再公開する場合）

1. `scripts/build_top10.py` の `MAINTENANCE_MODE = True` を **`False`** に変更。
2. 各ページヘッダーにナビリンクを再追加：`scripts/build_lists.py`（netflix/prime 用テンプレート）と `index.html` / `about.html` / `privacy.html` / `contact.html` の `<nav class="topnav">` 内に
   `<a class="navbtn top10" href="/top10.html">🏆 今週のTop10</a>` を追加（必要なら `.navbtn.top10` のCSSも復元）。
3. `python scripts/build_lists.py` と `python scripts/build_top10.py` を実行して再生成（`top10.html` がカード一覧に戻り、`sitemap.xml` にも自動再登録される）。
4. コミット & push。

---

## 注意・免責

- 配信データの出典は MOVIE WALKER PRESS です。配信状況は時期により変動します。
- 実際の視聴可否・課金区分（見放題／レンタル等）は各サービスでご確認ください。
- スクレイピングはサーバー負荷に配慮し、リクエスト間に待機時間を入れています。
  取得間隔を極端に短くするなどの改変は避けてください。
- MOVIE WALKER の利用規約・robots の方針が変わった場合は、取得を停止してください。
