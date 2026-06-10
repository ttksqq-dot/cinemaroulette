#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_top10.py
data/top10_raw.json(scrape_top10.pyの出力) を日本語化して
data/top10.json と top10.html を生成する。

- movies.json と突き合わせて「日本でも配信中」「watch_url」「ポスター(thumb)」を取得
- Gemini API(gemini-2.5-flash) で 邦題/年/ジャンル/紹介文 を生成
  GEMINI_API_KEY が無い or 失敗時はフォールバック(英題流用・紹介文空)で続行
- sitemap.xml の top10.html 行も更新
"""
import os, re, sys, json, time, html, datetime, unicodedata, urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_PATH = os.path.join(ROOT, "data", "top10_raw.json")
MOVIES_PATH = os.path.join(ROOT, "data", "movies.json")
OUT_JSON = os.path.join(ROOT, "data", "top10.json")
OUT_HTML = os.path.join(ROOT, "top10.html")
SITEMAP = os.path.join(ROOT, "sitemap.xml")

# .env(ローカル)読み込み(任意)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


# ───────── 共通ユーティリティ ─────────
def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[\s　:：・,，.。!！?？\-‐―’'\"()（）\[\]【】~〜]", "", s)
    return s


def load_movies():
    with open(MOVIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    movies = []
    services = data.get("services", {})
    if isinstance(services, dict):
        for v in services.values():
            if isinstance(v, list):
                movies += v
    # 念のため: 直下に list があれば拾う
    for v in data.values() if isinstance(data, dict) else []:
        if isinstance(v, list) and v and isinstance(v[0], dict) and "title" in v[0]:
            movies += v
    lookup = {}
    for m in movies:
        key = norm(m.get("title"))
        if key and key not in lookup:
            lookup[key] = m
    return lookup


# ───────── Gemini ─────────
def gemini_model():
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        # google_search grounding を有効化(邦題を検索で正確に特定するため)
        try:
            m = genai.GenerativeModel("gemini-2.5-flash", tools=[{"google_search": {}}])
            sys.stderr.write("[info] Gemini grounding(google_search)有効\n")
            return m
        except Exception as e:
            sys.stderr.write(f"[warn] grounding初期化失敗→検索なしで継続: {e}\n")
            return genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        sys.stderr.write(f"[warn] Gemini初期化失敗: {e}\n")
        return None


def gemini_meta(model, title_en, year, runtime):
    """(obj or None, used_search:bool) を返す。"""
    if model is None:
        return None, False
    prompt = f"""あなたは映画情報の専門家です。Google検索を必ず使い、以下の映画の「日本での公式タイトル」を調べて特定してください。

映画タイトル(英語): {title_en}
公開年(参考): {year if year else "不明"}
上映時間: {runtime if runtime else "不明"}分

邦題の決め方(重要):
- アニメ・日本映画は元の日本語タイトル(検索で正式名を確認)
- 海外作品は日本配給・配信時の正式な邦題
- カタカナ転写は、検索しても日本での該当タイトルが本当に見つからない時だけの最終手段

具体例(この精度・粒度で特定すること):
- "Creed III" → "クリード 過去の逆襲"
- "Jujutsu Kaisen 0" → "劇場版 呪術廻戦 0"
- "Detective Conan: Black Iron Submarine" → "名探偵コナン 黒鉄の魚影"
- "Demon Slayer" → "鬼滅の刃"

以下のJSONのみを返してください(説明文やマークダウン不要):

{{
  "jp_title": "上記ルールで特定した日本での公式タイトル",
  "year": 公開年の整数(不明ならnull),
  "genres": ["ジャンル1","ジャンル2"],
  "summary": "2-3文の日本語紹介文。ネタバレなし。観たくなる導入。150〜200字程度"
}}

注意:
- jp_title は検索で裏取りした確実なものを最優先。安易なカタカナ直訳(例: Jujutsu Kaisen→ジュジュツカイセン)は禁止
- summaryは独自の言い回しで。あらすじの転載ではなく視聴を促す紹介文"""
    for attempt in range(3):
        try:
            resp = model.generate_content(prompt)
            # grounding metadata で google_search が実際に使われたか確認
            used_search = False
            try:
                cand = resp.candidates[0]
                gm = getattr(cand, "grounding_metadata", None)
                if gm and (getattr(gm, "web_search_queries", None)
                           or getattr(gm, "grounding_chunks", None)
                           or getattr(gm, "search_entry_point", None)):
                    used_search = True
            except Exception:
                pass
            text = (resp.text or "").strip()
            text = re.sub(r"^```(json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                text = m.group(0)
            obj = json.loads(text)
            time.sleep(3)
            return obj, used_search
        except Exception as e:
            sys.stderr.write(f"[warn] Gemini失敗({attempt+1}/3) {title_en}: {e}\n")
            time.sleep(3)
    return None, False


# ───────── SVGプレースホルダー ─────────
def svg_placeholder(title):
    """ポスターが取得できない時の、タイトル文字入りSVG(data URI)。"""
    t = (title or "").strip()
    # ざっくり10文字ごとに折り返し(最大3行)
    lines = [t[i:i + 10] for i in range(0, len(t), 10)][:3] or ["No Image"]
    tspans = "".join(
        f'<tspan x="150" dy="{30 if i else 0}">{html.escape(ln)}</tspan>'
        for i, ln in enumerate(lines)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#2a2a32"/><stop offset="1" stop-color="#0f0f14"/>'
        '</linearGradient></defs>'
        '<rect width="300" height="450" fill="url(#g)"/>'
        '<text x="150" y="165" text-anchor="middle" font-size="44">🎬</text>'
        f'<text x="150" y="235" text-anchor="middle" fill="#e8e6df" '
        f'font-family="sans-serif" font-size="18" font-weight="700">{tspans}</text>'
        '</svg>'
    )
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)


# ───────── 診断 ─────────
WARNINGS = []          # 邦題/メタの要確認リスト
STATS = {"success": 0, "failed": 0, "no_key": 0, "grounded": 0, "total": 0}


def _is_katakana_translit(jp, en):
    """jp が(ほぼ)カタカナのみ かつ en がラテン文字 → 単純なカタカナ転写の疑い。"""
    if not jp or not en or not re.search(r"[A-Za-z]", en):
        return False
    return bool(re.fullmatch(r"[゠-ヿー・＝=\s]+", jp))


# ───────── エントリ処理 ─────────
def enrich(entry, lookup, model, region="global"):
    title_en = entry.get("title_en", "")
    year = entry.get("year")
    runtime = entry.get("runtime_minutes")
    rank = entry.get("rank")

    g, used_search = gemini_meta(model, title_en, year, runtime)
    status = "success" if g is not None else ("no_key" if model is None else "failed")
    g = g or {}
    jp_title = (g.get("jp_title") or "").strip() or title_en
    gen_year = g.get("year")
    genres = g.get("genres") or []
    summary = (g.get("summary") or "").strip()

    # 診断ログ(a/b)
    STATS["total"] += 1
    STATS[status] = STATS.get(status, 0) + 1
    if used_search:
        STATS["grounded"] += 1
    sys.stderr.write(
        f'[info] Gemini call for "{title_en}" (rank {rank}, {region}): '
        f'status={status}, used_search={"true" if used_search else "false"}, jp_title="{jp_title}"\n'
    )
    # バリデーション(d/e)
    if status == "success":
        miss = []
        if not summary:
            miss.append("summary")
        if not gen_year:
            miss.append("year")
        if not genres:
            miss.append("genres")
        if miss:
            WARNINGS.append(f'{region} #{rank} "{title_en}": 欠落フィールド {miss}')
        if norm(jp_title) == norm(title_en):
            WARNINGS.append(f'{region} #{rank} "{title_en}": jp_titleが英題と一致(検索失敗の疑い)')
        elif _is_katakana_translit(jp_title, title_en):
            WARNINGS.append(f'{region} #{rank} "{title_en}": カタカナ転写の疑い -> "{jp_title}"')

    # movies.json 突き合わせ(邦題優先→英題)
    rec = lookup.get(norm(jp_title)) or lookup.get(norm(title_en))
    watch_url = ""
    poster_url = entry.get("poster_url") or ""
    filmarks_id = None
    if rec:
        jp_title = rec.get("title") or jp_title          # movies.json優先(正確)
        gen_year = rec.get("year") or gen_year
        if rec.get("genres"):
            genres = rec.get("genres")
        watch_url = rec.get("watch_url") or ""
        poster_url = rec.get("thumb") or poster_url
        filmarks_id = rec.get("id")

    # 問題1: 日本Top10は定義上 Japan Netflix で配信中 → 強制 true
    available = True if region == "japan" else bool(rec)

    # Netflix視聴URL: movies.json優先 → Tudumのwatch URL → (日本のみ)検索URLで保険
    final_watch = watch_url or entry.get("netflix_watch_url") or ""
    if region == "japan" and not final_watch:
        final_watch = "https://www.netflix.com/search?q=" + urllib.parse.quote(jp_title)

    # ポスター最終フォールバック
    if not poster_url:
        poster_url = svg_placeholder(jp_title)

    return {
        "rank": rank,
        "title_en": title_en,
        "jp_title": jp_title,
        "year": gen_year if gen_year else (year if year else None),
        "genres": genres[:3],
        "summary": summary,
        "runtime_minutes": runtime,
        "views_this_week": entry.get("views_this_week"),
        "weeks_in_top10": entry.get("weeks_in_top10"),
        "poster_url": poster_url,
        "netflix_watch_url": final_watch,
        "available_in_japan": available,
        "filmarks_id": filmarks_id,
    }


# ───────── HTML ─────────
def fmt_views(v):
    if not v:
        return ""
    man = round(v / 10000)
    return f"約{man:,}万回再生"


def fmt_runtime(mins):
    if not mins:
        return ""
    h, m = divmod(int(mins), 60)
    return (f"{h}時間{m}分" if h else f"{m}分")


def esc(s):
    return html.escape(str(s) if s is not None else "")


def card_html(it):
    rank = it.get("rank") or 0
    jp = esc(it.get("jp_title"))
    en = esc(it.get("title_en"))
    year = it.get("year")
    parts = []
    if year:
        parts.append(esc(year))
    rt = fmt_runtime(it.get("runtime_minutes"))
    if rt:
        parts.append(rt)
    if it.get("genres"):
        parts.append("、".join(esc(g) for g in it["genres"]))
    meta = " / ".join(parts)
    stat = fmt_views(it.get("views_this_week"))
    if not stat and it.get("weeks_in_top10"):
        stat = f"Top10入り{it['weeks_in_top10']}週目"
    summary = esc(it.get("summary"))

    poster = it.get("poster_url")
    poster_html = (
        f'<div class="t10-poster"><div class="t10-fallback">{jp}</div>'
        f'<img src="{esc(poster)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></div>'
        if poster else
        f'<div class="t10-poster"><div class="t10-fallback">{jp}</div></div>'
    )

    avail = it.get("available_in_japan")
    watch = it.get("netflix_watch_url")
    if avail and watch:
        watch_btn = f'<a class="t10-watch" href="{esc(watch)}" target="_blank" rel="noopener noreferrer">▶ Netflixで観る</a>'
    else:
        watch_btn = '<span class="t10-watch disabled">日本未配信</span>'
    bm_btn = ""
    if avail and it.get("filmarks_id"):
        bm_btn = (f'<button class="t10-bm" type="button" data-fid="{esc(it["filmarks_id"])}" '
                  f'aria-label="あとで見る" aria-pressed="false">☆</button>')
    avail_badge = ('<div class="t10-avail ok">✅ 日本でも配信中</div>' if avail
                   else '<div class="t10-avail ng">⚠️ 日本未配信</div>')

    return f"""<article class="t10-card">
  <div class="t10-rankbar"><span class="t10-rank">🏆 #{rank}</span><span class="t10-stat">{esc(stat)}</span></div>
  {poster_html}
  <div class="t10-body">
    <h3 class="t10-jp">{jp}</h3>
    <div class="t10-en">{en}</div>
    <div class="t10-meta">{meta}</div>
    <p class="t10-summary">{summary}</p>
    <div class="t10-actions">{watch_btn}{bm_btn}</div>
    {avail_badge}
  </div>
</article>"""


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%%TITLE%%</title>
<meta name="description" content="%%DESC%%">
<link rel="canonical" href="https://cinemagacha.com/top10.html">
<meta property="og:title" content="%%TITLE%%">
<meta property="og:description" content="%%DESC%%">
<meta property="og:type" content="website">
<meta property="og:url" content="https://cinemagacha.com/top10.html">
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8972877686386494" crossorigin="anonymous"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XCVTZSESV5"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-XCVTZSESV5');
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Noto+Sans+JP:wght@400;500;700;900&family=Shippori+Mincho:wght@600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#fafaf7; --bg-2:#ffffff; --ink:#1a1a20; --muted:#6b6b73;
    --line:rgba(0,0,0,.08); --netflix:#e50914; --prime:#00a8e1; --gold:#b8860b;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{min-height:100%}
  body{font-family:"Noto Sans JP",sans-serif;background:var(--bg);color:var(--ink);
    min-height:100vh;overflow-x:hidden;position:relative}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background:
      radial-gradient(900px 600px at 15% -10%, rgba(229,9,20,.06), transparent 60%),
      radial-gradient(900px 600px at 110% 20%, rgba(0,168,225,.06), transparent 60%),
      radial-gradient(700px 700px at 50% 120%, rgba(184,134,11,.05), transparent 60%);}
  .wrap{position:relative;z-index:2;max-width:1180px;margin:0 auto;padding:40px 24px 90px}
  .topnav{position:relative;z-index:3;display:flex;align-items:center;justify-content:center;
    gap:10px;flex-wrap:wrap;padding:18px 24px 0}
  .navbtn{cursor:pointer;text-decoration:none;border:1px solid var(--line);
    background:rgba(0,0,0,.04);color:var(--ink);border-radius:999px;
    padding:8px 18px;font-size:14px;font-weight:500;transition:.2s;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.04)}
  .navbtn:hover{color:var(--ink);background:rgba(0,0,0,.08);border-color:rgba(0,0,0,.15)}
  .navbtn.home{background:linear-gradient(#e3bd55,#b8860b);border-color:#b8860b;color:#1a1505;font-weight:700;box-shadow:0 2px 6px rgba(184,134,11,.25)}
  .navbtn.home:hover{filter:brightness(1.05);box-shadow:0 4px 12px rgba(184,134,11,.35)}
  .navbtn.top10{border-color:#b8860b;color:#8a6608;background:rgba(184,134,11,.10)}
  .navbtn.top10.active,.navbtn.top10:hover{background:rgba(184,134,11,.18);border-color:#b8860b}
  header{text-align:center;margin:30px 0 22px}
  .kicker{letter-spacing:.2em;font-size:12px;color:var(--muted);margin-bottom:8px}
  h1{font-family:"Shippori Mincho",serif;font-weight:800;font-size:clamp(26px,5.5vw,44px);line-height:1.2}
  .sub{margin-top:12px;color:var(--muted);font-size:13.5px;line-height:1.8}
  .updated{margin-top:6px;color:#55555c;font-size:11.5px}
  .top10-tabs{display:flex;justify-content:center;gap:10px;margin:6px 0 28px}
  .top10-tabs a{text-decoration:none;border:1px solid var(--line);background:var(--bg-2);
    color:var(--ink);border-radius:999px;padding:8px 20px;font-size:14px;font-weight:600;
    box-shadow:0 1px 3px rgba(0,0,0,.04);transition:.2s}
  .top10-tabs a:hover{border-color:var(--gold);color:var(--gold)}
  .top10-sec{margin-bottom:44px;scroll-margin-top:20px}
  .top10-sec h2{font-family:"Shippori Mincho",serif;font-size:22px;margin-bottom:16px;
    padding-bottom:10px;border-bottom:1px solid var(--line)}
  .top10-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
  @media(max-width:800px){.top10-grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:520px){.top10-grid{grid-template-columns:1fr}}
  .t10-card{background:var(--bg-2);border:1px solid var(--line);border-radius:14px;overflow:hidden;
    box-shadow:0 2px 8px rgba(0,0,0,.04);display:flex;flex-direction:column;transition:.2s}
  .t10-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,.08)}
  .t10-rankbar{display:flex;align-items:center;justify-content:space-between;gap:8px;
    padding:9px 12px;background:linear-gradient(90deg,rgba(184,134,11,.14),rgba(184,134,11,.04))}
  .t10-rank{font-family:"Bebas Neue",sans-serif;font-size:20px;letter-spacing:.05em;color:#8a6608}
  .t10-stat{font-size:11px;color:var(--muted);font-feature-settings:"tnum"}
  .t10-poster{position:relative;aspect-ratio:2/3;background:#f0f0eb;overflow:hidden}
  .t10-poster img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
  .t10-fallback{position:absolute;inset:0;display:grid;place-content:center;text-align:center;
    padding:14px;font-family:"Shippori Mincho",serif;font-size:15px;line-height:1.4;color:var(--muted)}
  .t10-body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:5px;flex:1}
  .t10-jp{font-size:16px;font-weight:700;line-height:1.35;color:var(--ink)}
  .t10-en{font-size:11.5px;color:var(--muted)}
  .t10-meta{font-size:11.5px;color:var(--muted);font-feature-settings:"tnum"}
  .t10-summary{font-size:12.5px;line-height:1.7;color:#33333b;margin:4px 0 8px}
  .t10-actions{display:flex;align-items:center;gap:8px;margin-top:auto}
  .t10-watch{flex:1;text-align:center;text-decoration:none;background:var(--netflix);color:#fff;
    border-radius:999px;padding:9px 12px;font-size:13px;font-weight:700;transition:.2s}
  .t10-watch:hover{filter:brightness(1.08)}
  .t10-watch.disabled{background:rgba(0,0,0,.06);color:var(--muted);font-weight:500;cursor:default}
  .t10-bm{width:38px;height:38px;flex:none;border-radius:50%;border:1px solid var(--line);
    background:rgba(0,0,0,.04);cursor:pointer;font-size:18px;line-height:1;color:var(--muted);transition:.2s}
  .t10-bm:hover{background:#fff;color:var(--ink)}
  .t10-bm.bookmarked{color:var(--gold);border-color:var(--gold)}
  .t10-avail{font-size:11.5px;margin-top:8px}
  .t10-avail.ok{color:#1f7a4d}
  .t10-avail.ng{color:var(--muted)}
  .attribution{text-align:center;color:var(--muted);font-size:12px;margin:10px 0 24px;line-height:1.8}
  .attribution a{color:#4a4a52}
  .footer-note{text-align:center;color:var(--muted);font-size:12px;margin-top:24px;line-height:1.8}
  .footer-note a{color:#4a4a52}
  #t10-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);
    background:#1a1a20;color:#fff;padding:11px 18px;border-radius:999px;font-size:13px;
    opacity:0;pointer-events:none;transition:.25s;z-index:999;box-shadow:0 6px 20px rgba(0,0,0,.25)}
  #t10-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>

<nav class="topnav">
  <a class="navbtn home" href="/">TOP</a>
  <a class="navbtn" href="/netflix.html">NETFLIX 作品一覧</a>
  <a class="navbtn" href="/prime.html">PRIME VIDEO 作品一覧</a>
  <a class="navbtn top10 active" href="/top10.html">🏆 今週のTop10</a>
</nav>

<div class="wrap">
  <header>
    <div class="kicker">シネマガチャ</div>
    <h1>今週のNetflix人気映画Top10</h1>
    <p class="sub">%%WEEK%% のランキング(出典: Netflix Tudum)<br>グローバルと日本、それぞれの人気映画をまとめました。</p>
    <p class="updated">最終更新: %%UPDATED%%</p>
  </header>

  <nav class="top10-tabs">
    <a href="#global">🌍 グローバル</a>
    <a href="#japan">🇯🇵 日本</a>
  </nav>

  <section class="top10-sec" id="global">
    <h2>🌍 グローバル 映画 Top10</h2>
    <div class="top10-grid">
%%GLOBAL_CARDS%%
    </div>
  </section>

  <section class="top10-sec" id="japan">
    <h2>🇯🇵 日本 映画 Top10</h2>
    <div class="top10-grid">
%%JAPAN_CARDS%%
    </div>
  </section>

  <div class="attribution">
    <p>データ出典: <a href="%%SOURCE_URL%%" target="_blank" rel="noopener noreferrer">Netflix Tudum</a>(毎週更新)</p>
    <p>邦題・紹介文の一部はAIにより生成しています。配信状況は変動するため各サービスでご確認ください。</p>
  </div>

  <p class="footer-note">
    <a href="/">トップ</a>　<a href="/about.html">このサイトについて</a>　<a href="/privacy.html">プライバシーポリシー</a>　<a href="/contact.html">お問い合わせ</a>
  </p>
</div>

<script type="module">
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js';
import { getAuth, onAuthStateChanged } from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js';
import { getFirestore, doc, getDoc, setDoc, arrayUnion, arrayRemove }
  from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js';
const app = initializeApp({
  apiKey: "AIzaSyDlct7SsQ6B5h6W0KOzSP_aPghv1sATBk8",
  authDomain: "cinemagacha-9ad75.firebaseapp.com",
  projectId: "cinemagacha-9ad75",
  storageBucket: "cinemagacha-9ad75.firebasestorage.app",
  messagingSenderId: "483844268792",
  appId: "1:483844268792:web:0b2a9c345c9aaa45f3510d",
});
const auth = getAuth(app);
const db = getFirestore(app);
let currentUser = null;
let bookmarks = new Set();
function paintStars(){
  document.querySelectorAll('.t10-bm').forEach(b => {
    const on = !!(currentUser && bookmarks.has(b.dataset.fid));
    b.classList.toggle('bookmarked', on);
    b.textContent = on ? '★' : '☆';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
onAuthStateChanged(auth, async (u) => {
  currentUser = u; bookmarks = new Set();
  if (u) {
    try { const s = await getDoc(doc(db,'users',u.uid)); if (s.exists()) bookmarks = new Set(s.data().bookmarks || []); }
    catch(e){ console.error('load bookmarks', e); }
  }
  paintStars();
});
function toast(msg){
  let t = document.getElementById('t10-toast');
  if (!t){ t = document.createElement('div'); t.id = 't10-toast'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2800);
}
document.addEventListener('click', async (e) => {
  const b = e.target.closest('.t10-bm');
  if (!b) return;
  e.preventDefault();
  const id = b.dataset.fid;
  if (!id) return;
  if (!currentUser){ toast('ログインするとあとで見るに保存できます(トップページからログイン)'); return; }
  const ref = doc(db, 'users', currentUser.uid);
  try {
    if (bookmarks.has(id)){
      bookmarks.delete(id);
      await setDoc(ref, { bookmarks: arrayRemove(id), updatedAt: new Date().toISOString() }, { merge: true });
    } else {
      bookmarks.add(id);
      await setDoc(ref, { bookmarks: arrayUnion(id), email: currentUser.email || '',
                          updatedAt: new Date().toISOString() }, { merge: true });
    }
    const on = bookmarks.has(id);
    b.classList.toggle('bookmarked', on);
    b.textContent = on ? '★' : '☆';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  } catch(err){ console.error('bookmark error', err); }
});
</script>
</body>
</html>"""


def update_sitemap(updated_iso):
    try:
        with open(SITEMAP, encoding="utf-8") as f:
            xml = f.read()
        line = (f'  <url><loc>https://cinemagacha.com/top10.html</loc>'
                f'<lastmod>{updated_iso}</lastmod><changefreq>weekly</changefreq>'
                f'<priority>0.8</priority></url>\n')
        if "top10.html" in xml:
            xml = re.sub(r'\s*<url><loc>https://cinemagacha\.com/top10\.html</loc>.*?</url>\n',
                         "\n" + line, xml, flags=re.S)
        else:
            xml = xml.replace("</urlset>", line + "</urlset>")
        with open(SITEMAP, "w", encoding="utf-8") as f:
            f.write(xml)
        sys.stderr.write("[info] sitemap.xml 更新\n")
    except Exception as e:
        sys.stderr.write(f"[warn] sitemap更新失敗: {e}\n")


def main():
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    lookup = load_movies()
    model = gemini_model()
    if model is None:
        sys.stderr.write("[info] GEMINI_API_KEY 未設定/初期化失敗 → フォールバック(英題流用・紹介文空)で生成\n")

    g_src = raw.get("global", {}).get("movies_english", [])
    j_src = raw.get("japan", {}).get("movies_english", [])
    sys.stderr.write(f"[info] enrich global={len(g_src)} japan={len(j_src)}\n")
    global_movies = [enrich(e, lookup, model, "global") for e in g_src]
    japan_movies = [enrich(e, lookup, model, "japan") for e in j_src]

    # ── 診断サマリ ──
    sys.stderr.write(
        f"[info] === Gemini診断: total={STATS['total']} success={STATS['success']} "
        f"failed={STATS['failed']} no_key={STATS['no_key']} "
        f"grounded(used_search)={STATS['grounded']}/{STATS['total']} ===\n"
    )
    g_avail = sum(x["available_in_japan"] for x in global_movies)
    j_avail = sum(x["available_in_japan"] for x in japan_movies)
    sys.stderr.write(f"[info] available_in_japan: global={g_avail}/{len(global_movies)} "
                     f"japan={j_avail}/{len(japan_movies)} (日本は全件true想定)\n")
    if WARNINGS:
        sys.stderr.write(f"[warn] 邦題/メタ 要確認 {len(WARNINGS)}件:\n")
        for w in WARNINGS:
            sys.stderr.write(f"   - {w}\n")
    else:
        sys.stderr.write("[info] 邦題/メタ 警告なし\n")

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    updated_at = now.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    week_label = raw.get("week_label", "")

    top10 = {
        "updated_at": updated_at,
        "week_label": week_label,
        "source": "Netflix Tudum",
        "source_url": raw.get("source_url", "https://www.netflix.com/tudum/top10"),
        "global_movies": global_movies,
        "japan_movies": japan_movies,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(top10, f, ensure_ascii=False, indent=2)
    sys.stderr.write(f"[info] wrote {OUT_JSON}\n")

    g_cards = "\n".join(card_html(it) for it in global_movies) or '<p style="color:var(--muted)">データを取得できませんでした。</p>'
    j_cards = "\n".join(card_html(it) for it in japan_movies) or '<p style="color:var(--muted)">データを取得できませんでした。</p>'

    week_disp = esc(week_label) or "最新週"
    updated_disp = now.strftime("%Y年%m月%d日 %H:%M")
    title = "今週のNetflix人気映画Top10(グローバル/日本) | シネマガチャ"
    desc = f"Netflix Tudum発表の週次人気映画ランキング。{week_disp}のグローバルTop10と日本Top10を日本語で紹介。"

    htmlout = (TEMPLATE
               .replace("%%TITLE%%", esc(title))
               .replace("%%DESC%%", esc(desc))
               .replace("%%WEEK%%", week_disp)
               .replace("%%UPDATED%%", esc(updated_disp))
               .replace("%%SOURCE_URL%%", esc(top10["source_url"]))
               .replace("%%GLOBAL_CARDS%%", g_cards)
               .replace("%%JAPAN_CARDS%%", j_cards))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(htmlout)
    sys.stderr.write(f"[info] wrote {OUT_HTML}\n")

    update_sitemap(now.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    main()
