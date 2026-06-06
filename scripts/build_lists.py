#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_lists.py
data/movies.json から netflix.html / prime.html（配信作品一覧ページ）を生成する。

- 作品データは全件 HTML に直接書き込む（SEO・AdSense 審査対策のため静的化）
- 年代・ジャンルでの絞り込みは JS で行うが、中身（DOM）は最初から全件存在する
- デザインは index.html のデザイントークンに合わせている

使い方:
    python3 scripts/build_lists.py
出力:
    ./netflix.html, ./prime.html （リポジトリのルートに出力）
"""
import json
import html
import os
import datetime
import zoneinfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "data", "movies.json")

SERVICE_META = {
    "netflix": {
        "label": "NETFLIX",
        "jp": "ネットフリックス",
        "color": "var(--netflix)",
        "badge_class": "badge-netflix",
        "out": "netflix.html",
        "title": "Netflixで今観られる映画一覧",
        "desc": "Netflix（ネットフリックス）で配信中の映画を一覧で掲載。年代・ジャンルで絞り込めます。今夜の一本選びに。",
    },
    "prime": {
        "label": "PRIME VIDEO",
        "jp": "プライムビデオ",
        "color": "var(--prime)",
        "badge_class": "badge-prime",
        "out": "prime.html",
        "title": "Prime Videoで今観られる映画一覧",
        "desc": "Amazon Prime Video（プライムビデオ）で配信中の映画を一覧で掲載。年代・ジャンルで絞り込めます。今夜の一本選びに。",
    },
}

def era_of(year):
    """年代ラベルを返す。year が無ければ None。"""
    if not year:
        return None
    try:
        y = int(year)
    except (ValueError, TypeError):
        return None
    if y < 1980:
        return "〜1970年代"
    decade = (y // 10) * 10
    return f"{decade}年代"

ERA_ORDER = ["2020年代", "2010年代", "2000年代", "1990年代", "1980年代", "〜1970年代"]

def card_html(m):
    """1作品分のカード HTML を返す。"""
    title = html.escape(m.get("title", "") or "")
    year = m.get("year")
    year_txt = html.escape(str(year)) if year else ""
    thumb = html.escape(m.get("thumb", "") or "")
    detail_url = html.escape(m.get("url", "") or "#")
    watch_url = html.escape(m.get("watch_url", "") or "")
    genres = m.get("genres", []) or []
    era = era_of(year) or ""

    # data 属性に年代・ジャンルを持たせ、JS の絞り込みに使う
    genres_attr = html.escape(",".join(genres))
    genre_tags = "".join(
        f'<span class="lc-genre">{html.escape(g)}</span>' for g in genres[:3]
    )

    if thumb:
        poster = f'<img loading="lazy" src="{thumb}" alt="{title} のポスター">'
    else:
        poster = f'<div class="lc-fallback">{title}</div>'

    watch_btn = ""
    if watch_url:
        watch_btn = f'<a class="lc-watch" href="{watch_url}" target="_blank" rel="noopener noreferrer">▶ 配信ページへ</a>'

    return f"""<article class="lc" data-year="{year_txt}" data-era="{html.escape(era)}" data-genres="{genres_attr}" data-title="{title}">
  <a class="lc-poster" href="{detail_url}" target="_blank" rel="noopener noreferrer">{poster}</a>
  <div class="lc-body">
    <h3 class="lc-title">{title}</h3>
    <div class="lc-meta">{year_txt}{('・' + era) if (year_txt and era) else era}</div>
    <div class="lc-genres">{genre_tags}</div>
    {watch_btn}
  </div>
</article>"""

def build_page(service_key, movies, updated_at_txt):
    meta = SERVICE_META[service_key]

    # 並び順: 公開年の新しい順（年不明は末尾）
    movies_sorted = sorted(
        movies, key=lambda m: (m.get("year") or 0), reverse=True
    )

    # 絞り込み用: 出現するジャンルと年代を集計
    genre_set = {}
    era_set = set()
    for m in movies_sorted:
        for g in (m.get("genres") or []):
            genre_set[g] = genre_set.get(g, 0) + 1
        e = era_of(m.get("year"))
        if e:
            era_set.add(e)

    top_genres = sorted(genre_set.items(), key=lambda kv: kv[1], reverse=True)
    genre_chips = "".join(
        f'<button class="lc-chip" data-filter-genre="{html.escape(g)}">{html.escape(g)}<span class="lc-chip-n">{c}</span></button>'
        for g, c in top_genres
    )
    eras_present = [e for e in ERA_ORDER if e in era_set]
    era_chips = "".join(
        f'<button class="lc-chip" data-filter-era="{html.escape(e)}">{html.escape(e)}</button>'
        for e in eras_present
    )

    cards = "\n".join(card_html(m) for m in movies_sorted)
    count = len(movies_sorted)

    other_key = "prime" if service_key == "netflix" else "netflix"
    other_meta = SERVICE_META[other_key]

    page_title = f"{meta['title']} | シネマガチャ"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(page_title)}</title>
<meta name="description" content="{html.escape(meta['desc'])}">
<link rel="canonical" href="https://cinemagacha.com/{meta['out']}">
<meta property="og:title" content="{html.escape(meta['title'])}">
<meta property="og:description" content="{html.escape(meta['desc'])}">
<meta property="og:type" content="website">
<meta property="og:url" content="https://cinemagacha.com/{meta['out']}">
<!-- Google AdSense -->
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8972877686386494" crossorigin="anonymous"></script>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XCVTZSESV5"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-XCVTZSESV5');
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Noto+Sans+JP:wght@400;500;700;900&family=Shippori+Mincho:wght@600;700;800&display=swap" rel="stylesheet">
<style>
  :root{{
    --bg:#0a0a0c; --bg-2:#121216; --ink:#f5f3ee; --muted:#8a8a92;
    --line:rgba(255,255,255,.08); --netflix:#e50914; --prime:#00a8e1; --gold:#d4af37;
  }}
  *{{margin:0;padding:0;box-sizing:border-box}}
  html,body{{min-height:100%}}
  body{{font-family:"Noto Sans JP",sans-serif;background:var(--bg);color:var(--ink);
    min-height:100vh;overflow-x:hidden;position:relative}}
  body::before{{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background:
      radial-gradient(900px 600px at 15% -10%, rgba(229,9,20,.18), transparent 60%),
      radial-gradient(900px 600px at 110% 20%, rgba(0,168,225,.16), transparent 60%),
      radial-gradient(700px 700px at 50% 120%, rgba(212,175,55,.07), transparent 60%);}}
  .wrap{{position:relative;z-index:2;max-width:1180px;margin:0 auto;padding:40px 24px 90px}}

  /* ── ナビバー ── */
  .topnav{{position:relative;z-index:3;display:flex;align-items:center;justify-content:center;
    gap:10px;flex-wrap:wrap;padding:18px 24px 0}}
  .navbtn{{cursor:pointer;text-decoration:none;border:1px solid var(--line);
    background:rgba(10,10,12,.85);color:var(--muted);border-radius:999px;
    padding:8px 18px;font-size:13px;transition:.2s;white-space:nowrap}}
  .navbtn:hover{{color:var(--ink);border-color:rgba(255,255,255,.35)}}
  .navbtn.active{{color:var(--ink);border-color:rgba(255,255,255,.5);font-weight:700}}
  .navbtn.home{{background:var(--gold);border-color:var(--gold);color:#1a1505;font-weight:700}}

  header{{text-align:center;margin:30px 0 28px}}
  .kicker{{letter-spacing:.2em;font-size:12px;color:var(--muted);margin-bottom:8px}}
  h1{{font-family:"Shippori Mincho",serif;font-weight:800;font-size:clamp(26px,5.5vw,44px);
    line-height:1.2;letter-spacing:.02em}}
  h1 .accent{{color:{meta['color']}}}
  .sub{{margin-top:12px;color:var(--muted);font-size:13.5px;line-height:1.8}}
  .count{{margin-top:8px;color:var(--gold);font-size:14px;letter-spacing:.04em}}
  .updated{{margin-top:6px;color:#55555c;font-size:11.5px}}

  /* ── 絞り込み ── */
  .filters{{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.01));
    border:1px solid var(--line);border-radius:18px;padding:20px;margin-bottom:26px}}
  .filter-label{{font-size:11.5px;letter-spacing:.1em;color:var(--muted);margin:0 0 10px}}
  .filter-row{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}}
  .filter-row:last-child{{margin-bottom:0}}
  .lc-chip{{cursor:pointer;border:1px solid var(--line);background:var(--bg-2);color:var(--ink);
    padding:7px 14px;border-radius:999px;font-size:12.5px;transition:.2s;
    font-family:"Noto Sans JP",sans-serif;display:inline-flex;align-items:center;gap:6px}}
  .lc-chip:hover{{border-color:rgba(255,255,255,.4)}}
  .lc-chip.active{{background:var(--gold);color:#1a1505;border-color:var(--gold);font-weight:700}}
  .lc-chip-n{{font-size:10.5px;opacity:.6;font-feature-settings:"tnum"}}
  .clear-btn{{cursor:pointer;background:none;border:none;color:var(--muted);
    font-size:12px;text-decoration:underline;text-underline-offset:3px;padding:4px 8px}}
  .clear-btn:hover{{color:var(--ink)}}
  .result-count{{color:var(--muted);font-size:12.5px;margin-bottom:16px}}
  .result-count b{{color:var(--gold)}}

  /* ── 作品グリッド ── */
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:18px}}
  @media(max-width:560px){{.grid{{grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:12px}}}}
  .lc{{background:var(--bg-2);border:1px solid var(--line);border-radius:12px;overflow:hidden;
    display:flex;flex-direction:column;transition:.2s}}
  .lc:hover{{transform:translateY(-3px);border-color:rgba(255,255,255,.22)}}
  .lc.hidden{{display:none}}
  .lc-poster{{display:block;aspect-ratio:2/3;background:#1a1a20;overflow:hidden;text-decoration:none}}
  .lc-poster img{{width:100%;height:100%;object-fit:cover;display:block}}
  .lc-fallback{{width:100%;height:100%;display:grid;place-content:center;text-align:center;
    padding:14px;font-family:"Shippori Mincho",serif;font-size:14px;line-height:1.4;color:var(--muted)}}
  .lc-body{{padding:11px 12px 14px;display:flex;flex-direction:column;gap:6px;flex:1}}
  .lc-title{{font-size:13.5px;font-weight:700;line-height:1.4;color:var(--ink)}}
  .lc-meta{{font-size:11.5px;color:var(--muted);font-feature-settings:"tnum"}}
  .lc-genres{{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}}
  .lc-genre{{font-size:10.5px;border:1px solid var(--line);padding:2px 8px;border-radius:999px;color:var(--muted)}}
  .lc-watch{{margin-top:auto;text-decoration:none;font-family:"Bebas Neue",sans-serif;
    font-size:14px;letter-spacing:.06em;color:{meta['color']};padding-top:8px}}
  .lc-watch:hover{{filter:brightness(1.2)}}
  .no-match{{text-align:center;color:var(--muted);padding:60px 20px;display:none}}

  .footer-note{{text-align:center;color:#55555c;font-size:12px;margin-top:48px;line-height:1.8}}
  .footer-note a{{color:#7a7a82}}
</style>
</head>
<body>

<nav class="topnav">
  <a class="navbtn home" href="/">🎬 ガチャを回す</a>
  <a class="navbtn active" href="/{meta['out']}">{meta['label']} 作品一覧</a>
  <a class="navbtn" href="/{other_meta['out']}">{other_meta['label']} 作品一覧</a>
</nav>

<div class="wrap">
  <header>
    <div class="kicker">シネマガチャ</div>
    <h1><span class="accent">{meta['label']}</span> で今観られる映画一覧</h1>
    <p class="sub">{html.escape(meta['jp'])}で配信中の映画を一覧でまとめました。<br>年代・ジャンルで絞り込めます。観たい一本が決まらないときは、<a href="/" style="color:var(--gold)">ガチャ</a>もどうぞ。</p>
    <p class="count">全 <span id="total">{count}</span> 作品</p>
    <p class="updated">最終更新: {html.escape(updated_at_txt)}</p>
  </header>

  <div class="filters">
    <p class="filter-label">年代で絞り込む</p>
    <div class="filter-row" id="era-filters">{era_chips}</div>
    <p class="filter-label">ジャンルで絞り込む</p>
    <div class="filter-row" id="genre-filters">{genre_chips}</div>
    <div style="margin-top:8px"><button class="clear-btn" id="clear-btn" style="display:none">絞り込みを解除</button></div>
  </div>

  <p class="result-count" id="result-count" style="display:none"></p>

  <div class="grid" id="grid">
{cards}
  </div>
  <div class="no-match" id="no-match">🎬 条件に合う作品が見つかりませんでした。条件を変えてお試しください。</div>

  <p class="footer-note">
    配信データの出典は Filmarks です。配信状況は時期により変動します。<br>
    実際の視聴可否・課金区分は各サービスでご確認ください。<br>
    <a href="/">シネマガチャ トップへ戻る</a>
  </p>
</div>

<script>
(function(){{
  var grid = document.getElementById('grid');
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.lc'));
  var eraBtns = Array.prototype.slice.call(document.querySelectorAll('[data-filter-era]'));
  var genreBtns = Array.prototype.slice.call(document.querySelectorAll('[data-filter-genre]'));
  var clearBtn = document.getElementById('clear-btn');
  var noMatch = document.getElementById('no-match');
  var resultCount = document.getElementById('result-count');
  var total = cards.length;
  var activeEra = null;
  var activeGenre = null;

  function apply(){{
    var shown = 0;
    cards.forEach(function(c){{
      var okEra = !activeEra || c.getAttribute('data-era') === activeEra;
      var g = c.getAttribute('data-genres') || '';
      var okGenre = !activeGenre || (',' + g + ',').indexOf(',' + activeGenre + ',') !== -1;
      if (okEra && okGenre){{ c.classList.remove('hidden'); shown++; }}
      else {{ c.classList.add('hidden'); }}
    }});
    var filtering = activeEra || activeGenre;
    clearBtn.style.display = filtering ? '' : 'none';
    if (filtering){{
      resultCount.style.display = '';
      resultCount.innerHTML = '<b>' + shown + '</b> / ' + total + ' 作品を表示中';
    }} else {{
      resultCount.style.display = 'none';
    }}
    noMatch.style.display = shown === 0 ? '' : 'none';
  }}

  eraBtns.forEach(function(b){{
    b.addEventListener('click', function(){{
      var v = b.getAttribute('data-filter-era');
      if (activeEra === v){{ activeEra = null; b.classList.remove('active'); }}
      else {{
        eraBtns.forEach(function(x){{ x.classList.remove('active'); }});
        activeEra = v; b.classList.add('active');
      }}
      apply();
    }});
  }});
  genreBtns.forEach(function(b){{
    b.addEventListener('click', function(){{
      var v = b.getAttribute('data-filter-genre');
      if (activeGenre === v){{ activeGenre = null; b.classList.remove('active'); }}
      else {{
        genreBtns.forEach(function(x){{ x.classList.remove('active'); }});
        activeGenre = v; b.classList.add('active');
      }}
      apply();
    }});
  }});
  clearBtn.addEventListener('click', function(){{
    activeEra = null; activeGenre = null;
    eraBtns.concat(genreBtns).forEach(function(x){{ x.classList.remove('active'); }});
    apply();
  }});
}})();
</script>
</body>
</html>"""

def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # 更新日時を JST 表記に
    updated_raw = data.get("updated_at", "")
    updated_txt = updated_raw
    try:
        dt = datetime.datetime.fromisoformat(updated_raw)
        jst = dt.astimezone(zoneinfo.ZoneInfo("Asia/Tokyo"))
        updated_txt = jst.strftime("%Y年%m月%d日")
    except Exception:
        pass

    services = data.get("services", {})
    for key in ("netflix", "prime"):
        movies = services.get(key, [])
        page = build_page(key, movies, updated_txt)
        out_path = os.path.join(ROOT, SERVICE_META[key]["out"])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        print(f"  wrote {SERVICE_META[key]['out']}  ({len(movies)} movies)")

if __name__ == "__main__":
    main()
