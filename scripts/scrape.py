#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVIE WALKER PRESS の配信中リストから、Netflix / Prime Video の作品を取得する。

- 一覧ページ（全ページ）を巡回して作品の基本情報を集める
- 各作品の詳細ページから「あらすじ」と「ジャンル」を取得する
- 結果を data/movies.json に保存する

この json に載っている作品だけがサイトに表示される。
リストから消えた作品は次回実行時に json から消えるため、自動的にサイトからも除外される。

GitHub Actions 上で毎日実行される想定。
"""

import json
import re
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup

BASE = "https://press.moviewalker.jp"

# 取得対象サービス（key はサイト側の識別子, path は一覧URLのスラッグ）
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

# サーバー負荷に配慮した待機時間（秒）
SLEEP_LIST = 1.2     # 一覧ページ間
SLEEP_DETAIL = 0.8   # 詳細ページ間
TIMEOUT = 30
MAX_RETRY = 3

# 詳細ページの取得は時間がかかるため、上限を設けられるようにする
# 0 を指定すると無制限（全作品の詳細を取得）
DETAIL_LIMIT_PER_SERVICE = int(__import__("os").environ.get("DETAIL_LIMIT", "0"))


def get(url):
    """リトライ付きGET。"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            print(f"  [warn] {url} -> HTTP {r.status_code} (try {attempt})", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  [warn] {url} -> {e} (try {attempt})", file=sys.stderr)
        time.sleep(2 * attempt)
    return None


def parse_total_pages(html):
    """一覧1ページ目から総ページ数を推定する。"""
    soup = BeautifulSoup(html, "html.parser")
    # ページネーションのリンク /pN/ を全部拾って最大値をとる
    pages = [1]
    for a in soup.find_all("a", href=re.compile(r"/list/vod/[^/]+/p(\d+)/")):
        m = re.search(r"/p(\d+)/", a.get("href", ""))
        if m:
            pages.append(int(m.group(1)))
    # 「1/92」のような表記も拾う
    m = re.search(r"\b1\s*/\s*(\d+)\b", soup.get_text())
    if m:
        pages.append(int(m.group(1)))
    return max(pages)


def parse_list_page(html, service_key):
    """一覧ページから作品の基本情報を抽出する。

    作品ごとに /mvXXXXX/ へのリンクが複数（画像リンク + 見出しリンク）あるため、
    作品IDを基準にまとめて1作品として扱う。
    タイトルは「テキストを持つリンク」から、サムネは img を持つリンクから拾う。
    """
    soup = BeautifulSoup(html, "html.parser")

    # 作品IDごとに情報を集約
    movies = {}  # id -> dict

    for a in soup.find_all("a", href=re.compile(r"/mv\d+/?$")):
        href = a.get("href", "")
        mid_m = re.search(r"/mv(\d+)/?$", href)
        if not mid_m:
            continue
        mid = mid_m.group(1)
        m = movies.setdefault(mid, {"id": mid, "title": "", "thumb": "", "block": None})

        # タイトル（テキストを持つリンクを優先。h2/h3配下なら最優先）
        txt = a.get_text(strip=True)
        if txt:
            in_heading = a.find_parent(["h1", "h2", "h3", "h4"]) is not None
            if in_heading or not m["title"]:
                m["title"] = txt
                # 公開日抽出用に、作品ブロックの親要素を覚えておく
                blk = a.find_parent(["li", "article", "div"])
                if blk is not None:
                    m["block"] = blk

        # サムネ（img を持つリンク）
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
        # 公開年
        year = None
        if m["block"] is not None:
            ym = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", m["block"].get_text(" ", strip=True))
            if ym:
                year = int(ym.group(1))
        items.append({
            "id": mid,
            "title": title,
            "url": BASE + f"/mv{mid}/",
            "thumb": m["thumb"],
            "year": year,
            "platform": service_key,
        })
    return items


# 一覧から拾えるジャンル候補（詳細ページのジャンル表記の正規化に使用）
KNOWN_GENRES = [
    "アニメ", "ホラー", "アクション", "コメディ", "ドキュメンタリー", "恋愛", "時代劇",
    "SF", "ファンタジー", "戦争", "社会派", "アート", "西部劇", "伝記", "ミュージカル",
    "ヒューマンドラマ", "ファミリー", "文芸", "歴史", "青春", "スリラー", "舞台・音楽",
    "パニック", "冒険・アドベンチャー", "バイオレンス", "任侠・アウトロー", "キッズ",
    "特撮", "R18", "韓国", "サスペンス・ミステリー", "ミステリー", "ドラマ", "音楽",
    "ロマンス", "クライム",
]


def parse_detail_page(html):
    """詳細ページからあらすじとジャンルを抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    synopsis = ""
    genres = []

    # メタディスクリプションを第一候補にする（あらすじが入っていることが多い）
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        synopsis = md["content"].strip()

    # ジャンル: ページ内テキストから既知ジャンル名を拾う
    text = soup.get_text(" ", strip=True)
    for g in KNOWN_GENRES:
        if g in text:
            genres.append(g)
    # 重複を除き、長い名前を優先（"サスペンス・ミステリー"がある時は"ミステリー"を落とす等は簡略化）
    genres = list(dict.fromkeys(genres))[:4]

    # あらすじが取れない場合は空のまま
    if synopsis:
        # 余計な定型文をある程度カット
        synopsis = re.sub(r"\s+", " ", synopsis).strip()
        synopsis = synopsis[:300]

    return synopsis, genres


def scrape_service(service_key, slug):
    print(f"\n=== {service_key} ({slug}) ===", file=sys.stderr)
    first = get(f"{BASE}/list/vod/{slug}/")
    if not first:
        print(f"  [error] 一覧1ページ目を取得できませんでした", file=sys.stderr)
        return []

    total_pages = parse_total_pages(first)
    print(f"  総ページ数: {total_pages}", file=sys.stderr)

    all_items = []
    # 1ページ目はすでに取得済み
    all_items.extend(parse_list_page(first, service_key))

    for p in range(2, total_pages + 1):
        url = f"{BASE}/list/vod/{slug}/p{p}/"
        html = get(url)
        if html:
            page_items = parse_list_page(html, service_key)
            all_items.extend(page_items)
            print(f"  page {p}/{total_pages}: +{len(page_items)} (計 {len(all_items)})", file=sys.stderr)
        time.sleep(SLEEP_LIST)

    # 重複排除（ID基準）
    uniq = {}
    for it in all_items:
        uniq[it["id"]] = it
    items = list(uniq.values())
    print(f"  基本情報 取得作品数: {len(items)}", file=sys.stderr)

    # 詳細ページからあらすじ・ジャンルを取得
    target = items if DETAIL_LIMIT_PER_SERVICE == 0 else items[:DETAIL_LIMIT_PER_SERVICE]
    print(f"  詳細取得対象: {len(target)} 作品", file=sys.stderr)
    for i, it in enumerate(target, 1):
        html = get(it["url"])
        if html:
            syn, genres = parse_detail_page(html)
            it["synopsis"] = syn
            it["genres"] = genres
        else:
            it["synopsis"] = ""
            it["genres"] = []
        if i % 25 == 0:
            print(f"    detail {i}/{len(target)}", file=sys.stderr)
        time.sleep(SLEEP_DETAIL)

    # 詳細を取得しなかった作品にも空フィールドを用意
    for it in items:
        it.setdefault("synopsis", "")
        it.setdefault("genres", [])

    return items


def main():
    result = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "MOVIE WALKER PRESS",
        "services": {},
    }

    for key, slug in SERVICES.items():
        items = scrape_service(key, slug)
        result["services"][key] = items

    out_path = "data/movies.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in result["services"].values())
    print(f"\n✅ 保存完了: {out_path}（合計 {total} 作品）", file=sys.stderr)
    for k, v in result["services"].items():
        print(f"   {k}: {len(v)} 作品", file=sys.stderr)


if __name__ == "__main__":
    main()
