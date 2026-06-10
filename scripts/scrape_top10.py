#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_top10.py
Netflix Tudum の公式週次ランキングを取得する。

調査結果: Tudum の Top10 サイトは「ダウンロード用 TSV」を公開しており、
SPA を解析するより遥かに堅牢。地域指定もこのTSVの country_iso2 列で可能。
  - グローバル: https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv
      列: week, category, weekly_rank, show_title, season_title,
          weekly_hours_viewed, runtime(時間), weekly_views, cumulative_weeks_in_top_10
      category は "Films (English)" / "Films (Non-English)" / "TV (...)"
  - 国別:     https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv
      列: country_name, country_iso2, week, category, weekly_rank, show_title,
          season_title, cumulative_weeks_in_top_10
      日本は country_iso2 == "JP"、category は "Films"

映画のみ・最新週のみを抽出して JSON を標準出力に出す。
(workflow では `python scripts/scrape_top10.py > data/top10_raw.json`)
"""
import sys, json, csv, io, time, datetime, urllib.request

# Windowsコンソール(cp932)対策: 出力をUTF-8に固定(Linux CIでは無害)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

GLOBAL_TSV = "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv"
COUNTRIES_TSV = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            sys.stderr.write(f"[warn] fetch失敗({i+1}/{tries}) {url}: {e}\n")
            time.sleep(3)
    raise last


def parse_tsv(text):
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def latest_week(rows, key="week"):
    weeks = sorted({r.get(key, "") for r in rows if r.get(key)}, reverse=True)
    return weeks[0] if weeks else None


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def runtime_to_minutes(v):
    # global の runtime は「時間」単位の小数(例 1.9167)。分に変換。
    try:
        return int(round(float(v) * 60))
    except (TypeError, ValueError):
        return None


def main():
    sys.stderr.write("[info] global TSV 取得中…\n")
    g_rows = parse_tsv(fetch(GLOBAL_TSV))
    time.sleep(3)
    sys.stderr.write("[info] countries TSV 取得中…(大きいので時間がかかります)\n")
    c_rows = parse_tsv(fetch(COUNTRIES_TSV))

    gw = latest_week(g_rows)
    cw = latest_week(c_rows)
    sys.stderr.write(f"[info] latest week  global={gw}  countries={cw}\n")

    # ── グローバル映画(English) Top10 ──
    g_movies = []
    for r in g_rows:
        if r.get("week") != gw or r.get("category") != "Films (English)":
            continue
        g_movies.append({
            "rank": to_int(r.get("weekly_rank")),
            "title_en": (r.get("show_title") or "").strip(),
            "views_this_week": to_int(r.get("weekly_views")),
            "hours_viewed": to_int(r.get("weekly_hours_viewed")),
            "runtime_minutes": runtime_to_minutes(r.get("runtime")),
            "weeks_in_top10": to_int(r.get("cumulative_weeks_in_top_10")),
            "poster_url": "",
            "netflix_watch_url": "",
            "category": "movies_english",
        })
    g_movies = sorted([m for m in g_movies if m["rank"]], key=lambda m: m["rank"])[:10]

    # ── 日本 映画 Top10 ──
    j_movies = []
    for r in c_rows:
        if r.get("country_iso2") != "JP" or r.get("week") != cw:
            continue
        if r.get("category") != "Films":
            continue
        j_movies.append({
            "rank": to_int(r.get("weekly_rank")),
            "title_en": (r.get("show_title") or "").strip(),
            "views_this_week": None,        # 国別TSVには視聴数が無い
            "hours_viewed": None,
            "runtime_minutes": None,
            "weeks_in_top10": to_int(r.get("cumulative_weeks_in_top_10")),
            "poster_url": "",
            "netflix_watch_url": "",
            "category": "movies",
        })
    j_movies = sorted([m for m in j_movies if m["rank"]], key=lambda m: m["rank"])[:10]

    if not g_movies:
        sys.stderr.write("[warn] グローバル映画が0件。TSVの構造が変わった可能性があります。\n")
    if not j_movies:
        sys.stderr.write("[warn] 日本映画が0件。TSVの構造が変わった可能性があります。\n")

    out = {
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_label": gw or cw or "",
        "week_label_global": gw or "",
        "week_label_japan": cw or "",
        "source_url": "https://www.netflix.com/tudum/top10",
        "global": {"movies_english": g_movies, "movies_non_english": []},
        "japan": {"movies_english": j_movies, "movies_non_english": []},
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))
    sys.stderr.write(f"[info] 完了 global={len(g_movies)}件 japan={len(j_movies)}件\n")


if __name__ == "__main__":
    main()
