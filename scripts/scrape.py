#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVIE WALKER PRESS の配信中リストから、Netflix / Prime Video の作品を取得する（軽量版）。

設計方針:
- 一覧ページ（全ページ）だけを巡回して作品の基本情報（タイトル・年・サムネ・リンク・ジャンル）を取得する。
- 各作品の個別詳細ページにはアクセスしない（大量アクセスによるブロック/404を回避するため）。
- あらすじはサイト側の「作品情報を見る」リンク（MOVIE WALKER）で確認できるため、ここでは取得しない。
- 連続して404が続いたら、そのサービスの巡回を打ち切る（存在しないページへの空振りを防ぐ）。

この json に載っている作品だけがサイトに表示される。
リストから消えた作品は次回実行時に json から消えるため、自動的にサイトからも除外される。

GitHub Actions 上で毎日実行される想定。実行は数分で完了する。
"""

import json
import re
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup

BASE = "https://press.moviewalker.jp"

# 取得対象サービス（key はサイト側の識別子, slug は一覧URLのスラッグ）
SERVICES = {
    "netflix": "netflix",
    "prime":   "prime-video",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# 待機・リトライ設定
SLEEP_LIST = 1.5        # 一覧ページ間の待機（秒）。礼儀正しく巡回する
SLEEP_BETWEEN_SERVICES = 8  # サービス切り替え時の待機（秒）。連続アクセスによるブロック回避
TIMEOUT = 30
MAX_RETRY = 2           # 一覧ページ取得のリトライ回数
FIRST_PAGE_RETRY = 5    # 各サービス1ページ目は重要なので多めにリトライ
STOP_AFTER_404 = 3      # 404 ページがこの回数連続したら巡回を打ち切る
HARD_PAGE_CAP = 200     # 安全装置：最大ページ数の上限（暴走防止）


def get(url, retry=MAX_RETRY):
    """GET。成功なら本文、404 なら "404"、その他失敗なら None を返す。"""
    for attempt in range(1, retry + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return "404"
            print(f"  [warn] {url} -> HTTP {r.status_code} (try {attempt})", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  [warn] {url} -> {e} (try {attempt})", file=sys.stderr)
        time.sleep(2 * attempt)
    return None


def parse_total_pages(html):
    """一覧1ページ目から総ページ数を推定する（参考値）。"""
    soup = BeautifulSoup(html, "html.parser")
    pages = [1]
    for a in soup.find_all("a", href=re.compile(r"/list/vod/[^/]+/p(\d+)/")):
        m = re.search(r"/p(\d+)/", a.get("href", ""))
        if m:
            pages.append(int(m.group(1)))
    m = re.search(r"\b1\s*/\s*(\d+)\b", soup.get_text())
    if m:
        pages.append(int(m.group(1)))
    return max(pages)


# 一覧ページに現れる既知ジャンル名（作品ブロック内のテキストから拾う）
KNOWN_GENRES = [
    "アニメ", "ホラー", "アクション", "コメディ", "ドキュメンタリー", "恋愛", "時代劇",
    "SF", "ファンタジー", "戦争", "社会派", "アート", "西部劇", "伝記", "ミュージカル",
    "ヒューマンドラマ", "ファミリー", "文芸", "歴史", "青春", "スリラー", "舞台・音楽",
    "パニック", "冒険・アドベンチャー", "バイオレンス", "任侠・アウトロー", "キッズ",
    "特撮", "韓国", "サスペンス・ミステリー",
]


def parse_list_page(html, service_key):
    """一覧ページから作品の基本情報を抽出する。

    作品ごとに /mvXXXXX/ へのリンクが複数（画像リンク + 見出しリンク）あるため、
    作品IDを基準にまとめて1作品として扱う。
    """
    soup = BeautifulSoup(html, "html.parser")
    movies = {}

    for a in soup.find_all("a", href=re.compile(r"/mv\d+/?$")):
        href = a.get("href", "")
        mid_m = re.search(r"/mv(\d+)/?$", href)
        if not mid_m:
            continue
        mid = mid_m.group(1)
        m = movies.setdefault(mid, {"id": mid, "title": "", "thumb": "", "block": None})

        txt = a.get_text(strip=True)
        if txt:
            in_heading = a.find_parent(["h1", "h2", "h3", "h4"]) is not None
            if in_heading or not m["title"]:
                m["title"] = txt
                blk = a.find_parent(["li", "article", "div"])
                if blk is not None:
                    m["block"] = blk

        img = a.find("img")
        if img and img.get("src") and not m["thumb"]:
            src = img["src"]
            if src.startswith("/"):
                src = BASE + src
            if "notfound" not in src and "temporaryImage" not in src:
                m["thumb"] = src

    items = []
    for mid, m in movies.items():
        title = m["title"]
        if not title:
            continue
        year = None
        genres = []
        synopsis = ""
        if m["block"] is not None:
            btext = m["block"].get_text(" ", strip=True)
            ym = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", btext)
            if ym:
                year = int(ym.group(1))
            for g in KNOWN_GENRES:
                if g in btext:
                    genres.append(g)
            genres = list(dict.fromkeys(genres))[:4]
            # あらすじ抽出: 一覧ブロック内に紹介文が含まれる場合がある（特にPrime）
            # 「配信中」の後ろに続く説明文を拾う。末尾の「もっと見る」は除去。
            synopsis = extract_synopsis(m["block"], title)
        items.append({
            "id": mid,
            "title": title,
            "url": BASE + f"/mv{mid}/",
            "thumb": m["thumb"],
            "year": year,
            "platform": service_key,
            "genres": genres,
            "synopsis": synopsis,
        })
    return items


def extract_synopsis(block, title):
    """一覧の作品ブロックから紹介文（あらすじ）を抽出する。無ければ空文字。"""
    # ブロック内の各テキストノードを見て、説明文らしい長文を探す
    candidates = []
    for s in block.stripped_strings:
        t = s.strip()
        # 紹介文は通常そこそこ長い。メタ情報（年・分・ジャンル・配信中等）を除外
        if len(t) < 25:
            continue
        if "配信中" in t or "公開" in t or "もっと見る" in t and len(t) < 30:
            continue
        if title and t == title:
            continue
        candidates.append(t)
    if not candidates:
        return ""
    # 最も長いものを採用し、末尾の「···」「もっと見る」を除去
    syn = max(candidates, key=len)
    syn = re.sub(r"[･・]{2,}.*$", "", syn)   # 「···もっと見る」以降を切る
    syn = re.sub(r"もっと見る\s*$", "", syn)
    syn = re.sub(r"\s+", " ", syn).strip()
    return syn[:300]


def scrape_service(service_key, slug, start_page=1, max_pages=None):
    """指定サービスを巡回する。
    start_page: 開始ページ（1始まり）
    max_pages : このサービスで取得する最大ページ数（None=末尾まで）
    """
    label = f"{service_key} ({slug})"
    if start_page > 1 or max_pages:
        label += f" [p{start_page}〜 最大{max_pages}ページ]"
    print(f"\n=== {label} ===", file=sys.stderr)

    # 1ページ目（実際の開始ページ）は重要なので多めにリトライ
    first_url = f"{BASE}/list/vod/{slug}/" if start_page == 1 else f"{BASE}/list/vod/{slug}/p{start_page}/"
    first = get(first_url, retry=FIRST_PAGE_RETRY)
    if not first or first == "404":
        print("  [error] 開始ページを取得できませんでした", file=sys.stderr)
        return []

    if start_page == 1:
        hint = parse_total_pages(first)
        print(f"  総ページ数の参考値: {hint}", file=sys.stderr)

    by_id = {}
    for it in parse_list_page(first, service_key):
        by_id[it["id"]] = it
    print(f"  page {start_page}: 計 {len(by_id)}", file=sys.stderr)

    pages_done = 1
    consecutive_404 = 0
    page = start_page + 1
    while page <= HARD_PAGE_CAP:
        if max_pages and pages_done >= max_pages:
            print(f"  → 上限{max_pages}ページに到達。打ち切ります。", file=sys.stderr)
            break
        html = get(f"{BASE}/list/vod/{slug}/p{page}/")
        if html == "404":
            consecutive_404 += 1
            print(f"  page {page}: 404 ({consecutive_404}/{STOP_AFTER_404})", file=sys.stderr)
            if consecutive_404 >= STOP_AFTER_404:
                print(f"  → 404が{STOP_AFTER_404}回連続。巡回を打ち切ります。", file=sys.stderr)
                break
            page += 1
            time.sleep(SLEEP_LIST)
            continue

        if html is None:
            print(f"  page {page}: 取得失敗（スキップ）", file=sys.stderr)
            page += 1
            time.sleep(SLEEP_LIST)
            continue

        consecutive_404 = 0
        page_items = parse_list_page(html, service_key)
        if not page_items:
            print(f"  page {page}: 作品0件。末尾とみなして終了。", file=sys.stderr)
            break
        for it in page_items:
            by_id[it["id"]] = it
        pages_done += 1
        print(f"  page {page}: +{len(page_items)} (計 {len(by_id)})", file=sys.stderr)
        page += 1
        time.sleep(SLEEP_LIST)

    items = list(by_id.values())
    print(f"  取得作品数: {len(items)}", file=sys.stderr)
    return items


def load_existing():
    """既存の movies.json を読み込む。無ければ空構造を返す。"""
    try:
        with open("data/movies.json", encoding="utf-8") as f:
            data = json.load(f)
        if "services" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return {"services": {"netflix": [], "prime": []}}


# Prime を何分割するか（3日で一巡）
PRIME_SPLITS = 3
# Prime の1回あたりの取得ページ数の上限（安全側。3分割×この数で十分カバー）
PRIME_PAGES_PER_RUN = 240


def main():
    today = datetime.date.today()
    # 通算日を基準に、その日に取得する Prime の担当ブロック（0,1,2）を決める
    block = today.toordinal() % PRIME_SPLITS
    print(f"本日 {today} / Prime 担当ブロック: {block} (0=前半,1=中間,2=後半)", file=sys.stderr)

    # 既存データを土台にする（取得しなかった分は保持される）
    existing = load_existing()
    services = {
        "netflix": {it["id"]: it for it in existing["services"].get("netflix", [])},
        "prime":   {it["id"]: it for it in existing["services"].get("prime", [])},
    }

    # --- Netflix: 毎日全件更新（作品数が少ないので分割不要）---
    nf_items = scrape_service("netflix", SERVICES["netflix"])
    if nf_items:
        # 全件取得できたので、Netflixは丸ごと置き換え（配信終了作品も自動で消える）
        services["netflix"] = {it["id"]: it for it in nf_items}
    else:
        print("  [warn] Netflix取得0件。既存データを維持します。", file=sys.stderr)

    # サービス間の待機
    print(f"\n(次のサービスまで {SLEEP_BETWEEN_SERVICES}秒 待機)", file=sys.stderr)
    time.sleep(SLEEP_BETWEEN_SERVICES)

    # --- Prime: 3日分割。本日の担当ブロックのページ範囲だけ取得して上書き ---
    # ただし初回（既存Primeデータが空）は、まず前半（人気の新作が多い）から取得する
    if len(services["prime"]) == 0:
        print("  Primeの既存データが空のため、初回は前半(p1〜)から取得します。", file=sys.stderr)
        start_page = 1
    else:
        start_page = block * PRIME_PAGES_PER_RUN + 1
    pr_items = scrape_service("prime", SERVICES["prime"],
                              start_page=start_page, max_pages=PRIME_PAGES_PER_RUN)
    # 取得できた分を既存に統合（同じIDは新情報で更新、未取得の作品は残る）
    for it in pr_items:
        services["prime"][it["id"]] = it

    # 保存用に整形
    result = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "MOVIE WALKER PRESS",
        "prime_block_today": block,
        "services": {
            "netflix": list(services["netflix"].values()),
            "prime":   list(services["prime"].values()),
        },
    }

    with open("data/movies.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in result["services"].values())
    print(f"\n✅ 保存完了: data/movies.json（合計 {total} 作品）", file=sys.stderr)
    for k, v in result["services"].items():
        print(f"   {k}: {len(v)} 作品", file=sys.stderr)


if __name__ == "__main__":
    main()
