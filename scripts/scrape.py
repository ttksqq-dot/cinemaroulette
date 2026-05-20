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
TIMEOUT = 30
MAX_RETRY = 2           # 一覧ページ取得のリトライ回数
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
        if m["block"] is not None:
            btext = m["block"].get_text(" ", strip=True)
            ym = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", btext)
            if ym:
                year = int(ym.group(1))
            for g in KNOWN_GENRES:
                if g in btext:
                    genres.append(g)
            genres = list(dict.fromkeys(genres))[:4]
        items.append({
            "id": mid,
            "title": title,
            "url": BASE + f"/mv{mid}/",
            "thumb": m["thumb"],
            "year": year,
            "platform": service_key,
            "genres": genres,
            "synopsis": "",   # 軽量版では取得しない（将来TMDb等で補完可能）
        })
    return items


def scrape_service(service_key, slug):
    print(f"\n=== {service_key} ({slug}) ===", file=sys.stderr)
    first = get(f"{BASE}/list/vod/{slug}/")
    if not first or first == "404":
        print("  [error] 一覧1ページ目を取得できませんでした", file=sys.stderr)
        return []

    hint = parse_total_pages(first)
    print(f"  総ページ数の参考値: {hint}", file=sys.stderr)

    by_id = {}
    # 1ページ目
    for it in parse_list_page(first, service_key):
        by_id[it["id"]] = it
    print(f"  page 1: 計 {len(by_id)}", file=sys.stderr)

    consecutive_404 = 0
    page = 2
    while page <= HARD_PAGE_CAP:
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
            # 一時的なエラー。打ち切らず次へ
            print(f"  page {page}: 取得失敗（スキップ）", file=sys.stderr)
            page += 1
            time.sleep(SLEEP_LIST)
            continue

        consecutive_404 = 0
        page_items = parse_list_page(html, service_key)
        if not page_items:
            # 作品が0件なら、もう末尾とみなす
            print(f"  page {page}: 作品0件。末尾とみなして終了。", file=sys.stderr)
            break
        for it in page_items:
            by_id[it["id"]] = it
        print(f"  page {page}: +{len(page_items)} (計 {len(by_id)})", file=sys.stderr)
        page += 1
        time.sleep(SLEEP_LIST)

    items = list(by_id.values())
    print(f"  取得作品数: {len(items)}", file=sys.stderr)
    return items


def main():
    result = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "MOVIE WALKER PRESS",
        "services": {},
    }

    for key, slug in SERVICES.items():
        result["services"][key] = scrape_service(key, slug)

    with open("data/movies.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in result["services"].values())
    print(f"\n✅ 保存完了: data/movies.json（合計 {total} 作品）", file=sys.stderr)
    for k, v in result["services"].items():
        print(f"   {k}: {len(v)} 作品", file=sys.stderr)


if __name__ == "__main__":
    main()
