#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Filmarks の配信中リストから、Netflix / Prime Video の映画（見放題のみ）を取得する。

設計方針:
- /list/vod/ の映画一覧ページのみを巡回（個別ページは叩かない）。
- 見放題ラベル（label-svod クラス）が付いた作品のみ採用する。
- Prime は採用数が PRIME_TARGET_COUNT に達したら打ち切る。
- 1日1サービス（4日サイクル: 偶数位相=Netflix / 奇数位相=Prime）。
- その日取得しないサービスは既存データを維持する。
- ユーザーレビュー本文は取得・保存しない（他人の著作物）。

GitHub Actions 上で毎日実行される想定。
"""

import json
import os
import re
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup

BASE = "https://filmarks.com"

SERVICES = {
    "netflix": "netflix",
    "prime":   "prime_video",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://filmarks.com/",
}

SLEEP_LIST = 1.5
TIMEOUT = 60
MAX_RETRY = 3
FIRST_PAGE_RETRY = 6
STOP_AFTER_404 = 3
HARD_PAGE_CAP = 900
PRIME_TARGET_COUNT = 2500
CYCLE_LEN = 4


def get(url, retry=MAX_RETRY):
    """GET。成功なら本文、404なら "404"、その他失敗なら None を返す。"""
    for attempt in range(1, retry + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return "404"
            print(f"  [warn] {url} -> HTTP {r.status_code} (try {attempt}/{retry})", file=sys.stderr)
        except requests.exceptions.Timeout:
            print(f"  [warn] {url} -> タイムアウト({TIMEOUT}秒) (try {attempt}/{retry})", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  [warn] {url} -> 接続エラー: {e} (try {attempt}/{retry})", file=sys.stderr)
        except Exception as e:
            print(f"  [warn] {url} -> 予期せぬエラー: {type(e).__name__}: {e} (try {attempt}/{retry})", file=sys.stderr)
        time.sleep(3 * attempt)
    return None


def parse_cassette(cassette, service_key):
    """js-cassette div 1つから作品データを抽出する。
    見放題でなければ None を返す。レビュー本文は取得しない。
    """
    # movie_id を data-clip 属性の JSON から取得
    data_clip = cassette.get("data-clip", "{}")
    try:
        clip_data = json.loads(data_clip)
        movie_id = str(clip_data.get("movie_id", ""))
    except (json.JSONDecodeError, AttributeError):
        movie_id = ""
    if not movie_id:
        return None

    # 見放題ラベル確認（label-svod クラスが存在するか）
    if not cassette.find("div", class_="label-svod"):
        return None  # レンタル/購入のみ → スキップ

    # タイトル
    title_el = cassette.find("h3", class_="p-content-cassette__title")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    # サムネイル
    thumb = ""
    jacket = cassette.find("div", class_="c2-poster-m")
    if jacket:
        img = jacket.find("img")
        if img:
            thumb = img.get("src", "")

    # 公開年（「上映日：YYYY年MM月DD日」から西暦を取得）
    year = None
    date_info = cassette.find("div", class_="up-screen_and_country")
    if date_info:
        for span in date_info.find_all("span"):
            m = re.search(r"(\d{4})年", span.get_text(strip=True))
            if m:
                year = int(m.group(1))
                break

    # ジャンル（ul.genres > li > a のリンクテキスト）
    genres = []
    genres_ul = cassette.find("ul", class_="genres")
    if genres_ul:
        genres = [a.get_text(strip=True) for a in genres_ul.find_all("a") if a.get_text(strip=True)]

    # あらすじ（p.p-content-cassette__synopsis-desc-text）
    # ユーザーレビューは取得しない（div.p-content-cassette__reviews は触らない）
    synopsis = ""
    syn_el = cassette.find("p", class_="p-content-cassette__synopsis-desc-text")
    if syn_el:
        synopsis = syn_el.get_text(strip=True)
        synopsis = re.sub(r"[…]+\s*$", "", synopsis).strip()

    # 視聴URL（a.p-content-cassette__vod-button の href）
    watch_url = ""
    vod_btn = cassette.find("a", class_="p-content-cassette__vod-button")
    if vod_btn:
        href = vod_btn.get("href", "")
        if service_key == "prime":
            href = re.sub(r"\?.*$", "", href)  # アフィリエイトタグ（?tag=filmarks_web-22 等）を削除
        watch_url = href

    url = f"{BASE}/movies/{movie_id}"
    if not watch_url:
        watch_url = url  # フォールバック: Filmarks 作品ページ

    return {
        "id":       movie_id,
        "title":    title,
        "url":      url,
        "thumb":    thumb,
        "year":     year,
        "platform": service_key,
        "genres":   genres,
        "synopsis": synopsis,
        "watch_url": watch_url,
    }


def parse_list_page(html, service_key):
    """一覧ページから見放題作品のリストを抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    cassettes = soup.find_all("div", class_="js-cassette")
    items = []
    for c in cassettes:
        item = parse_cassette(c, service_key)
        if item:
            items.append(item)
    return items


def scrape_service(service_key, slug, prime_target=None):
    """指定サービスを巡回する。
    prime_target: Prime のみ使用。見放題採用数がこの値に達したら打ち切る（None=上限なし）。
    """
    label = f"{service_key} ({slug})"
    if prime_target:
        label += f" [見放題上限 {prime_target} 件]"
    print(f"\n=== {label} ===", file=sys.stderr)

    by_id = {}
    consecutive_404 = 0

    for page in range(1, HARD_PAGE_CAP + 1):
        url = f"{BASE}/list/vod/{slug}?page={page}"
        retry = FIRST_PAGE_RETRY if page == 1 else MAX_RETRY
        html = get(url, retry=retry)

        if html == "404":
            consecutive_404 += 1
            print(f"  page {page}: 404 ({consecutive_404}/{STOP_AFTER_404})", file=sys.stderr)
            if consecutive_404 >= STOP_AFTER_404:
                print(f"  → 404が{STOP_AFTER_404}回連続。巡回を打ち切ります。", file=sys.stderr)
                break
            time.sleep(SLEEP_LIST)
            continue

        if html is None:
            if page == 1:
                print("  [error] 一覧1ページ目を取得できませんでした", file=sys.stderr)
                return []
            print(f"  page {page}: 取得失敗（スキップ）", file=sys.stderr)
            time.sleep(SLEEP_LIST)
            continue

        consecutive_404 = 0
        page_items = parse_list_page(html, service_key)

        if not page_items and page > 1:
            print(f"  page {page}: 作品0件。末尾とみなして終了。", file=sys.stderr)
            break

        for it in page_items:
            by_id[it["id"]] = it

        print(f"  page {page}: +{len(page_items)}件 (累計 {len(by_id)}件)", file=sys.stderr)

        if prime_target and len(by_id) >= prime_target:
            print(f"  → 採用上限 {prime_target} 件に到達。打ち切ります。", file=sys.stderr)
            break

        time.sleep(SLEEP_LIST)

    items = list(by_id.values())
    print(f"  取得完了: {len(items)}件", file=sys.stderr)
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


def main():
    today = datetime.date.today()
    phase = today.toordinal() % CYCLE_LEN
    # 偶数位相 → Netflix 全件 / 奇数位相 → Prime（見放題 2,500 件上限）
    do_netflix = (phase % 2 == 0)
    target_service = "netflix" if do_netflix else "prime"

    # FORCE_SERVICE 環境変数が指定されていればphaseを無視して上書き（手動実行用）
    force = os.environ.get("FORCE_SERVICE", "").strip().lower()
    if force in ("netflix", "prime"):
        do_netflix = (force == "netflix")
        target_service = force
        print(
            f"本日 {today} / サイクル位相: {phase} → FORCE_SERVICE={force} で上書き",
            file=sys.stderr,
        )
    else:
        print(
            f"本日 {today} / サイクル位相: {phase} → {target_service} を更新",
            file=sys.stderr,
        )

    existing = load_existing()
    services = {
        "netflix": {it["id"]: it for it in existing["services"].get("netflix", [])},
        "prime":   {it["id"]: it for it in existing["services"].get("prime",   [])},
    }

    # 初回起動（どちらかが空）は空のほうを優先
    nf_empty = len(services["netflix"]) == 0
    pr_empty = len(services["prime"])   == 0
    if nf_empty and not pr_empty:
        do_netflix = True
        target_service = "netflix"
        print("  Netflix のデータが空のため、優先的に取得します。", file=sys.stderr)
    elif pr_empty and not nf_empty:
        do_netflix = False
        target_service = "prime"
        print("  Prime のデータが空のため、優先的に取得します。", file=sys.stderr)

    if do_netflix:
        items = scrape_service("netflix", SERVICES["netflix"])
        if items:
            services["netflix"] = {it["id"]: it for it in items}
            print(f"  Netflix を {len(items)} 件で更新しました。", file=sys.stderr)
        else:
            print("  [warn] Netflix 取得0件。既存データを維持します。", file=sys.stderr)
    else:
        items = scrape_service("prime", SERVICES["prime"], prime_target=PRIME_TARGET_COUNT)
        if items:
            services["prime"] = {it["id"]: it for it in items}
            print(f"  Prime を {len(items)} 件で更新しました。", file=sys.stderr)
        else:
            print("  [warn] Prime 取得0件。既存データを維持します。", file=sys.stderr)

    result = {
        "updated_at":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source":           "Filmarks",
        "cycle_phase_today": phase,
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
