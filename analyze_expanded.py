#!/usr/bin/env python3
"""
Sonar Prototype — EXPANDED PIPELINE (Phases 1–4)
Sources: Steam (500) + YouTube transcripts (8 vids) + Reddit (10 posts)
Extraction: Multi-insight AI (each source → N discrete insights)
Output: Self-contained HTML per game + index

Usage:
    python3 analyze_expanded.py
    python3 analyze_expanded.py --no-ai   # keyword fallback
    python3 analyze_expanded.py --game "Balatro"  # single game test
"""

import os, sys, json, time, re, math, textwrap, requests, urllib.parse
from pathlib import Path
from datetime import datetime

# Ensure yt-dlp is on PATH (local dev sessions add their own bin path)
_extra_bin = "/sessions/peaceful-gallant-albattani/.local/bin"
if os.path.isdir(_extra_bin):
    os.environ["PATH"] += f":{_extra_bin}"

# Global flag: set True if YouTube IpBlocks this VM — skip all further YT calls
YT_BLOCKED = False

# ── Deps ─────────────────────────────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    sys.exit("pip install anthropic")

try:
    import yt_dlp
except ImportError:
    sys.exit("pip install yt-dlp")

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    sys.exit("pip install youtube-transcript-api")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY   = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("Error: ANTHROPIC_API_KEY environment variable is not set.\n  Set it with: export ANTHROPIC_API_KEY=sk-ant-...")
USE_AI    = "--no-ai" not in sys.argv
SOLO_GAME = next((sys.argv[sys.argv.index("--game")+1] for i,a in enumerate(sys.argv) if a=="--game"), None) if "--game" in sys.argv else None
# --transcripts-dir: path to pre-fetched transcript cache from youtube-scrape skill
TRANSCRIPTS_DIR = Path(sys.argv[sys.argv.index("--transcripts-dir")+1]) if "--transcripts-dir" in sys.argv else None

client  = anthropic.Anthropic(api_key=API_KEY)
OUT_DIR = Path(__file__).parent / "reports_expanded"
OUT_DIR.mkdir(exist_ok=True)

REDDIT_HEADERS = {"User-Agent": "GameSentimentBot/1.0 (uxisfine.com)"}

# ── Game Catalogue ────────────────────────────────────────────────────────────
GAMES = [
    dict(name="Black Myth: Wukong", appid=2358720, year=2024, era="recent", slug="black-myth-wukong",   subreddit="BlackMythWukong"),
    dict(name="Palworld",           appid=1623730, year=2024, era="recent", slug="palworld",            subreddit="Palworld"),
    dict(name="Helldivers 2",       appid=553850,  year=2024, era="recent", slug="helldivers-2",        subreddit="Helldivers"),
    dict(name="Balatro",            appid=2379780, year=2024, era="recent", slug="balatro",             subreddit="Balatro"),
    dict(name="Hell Is Us",         appid=1620730, year=2025, era="recent", slug="hell-is-us",          subreddit="HellIsUs"),
    dict(name="Outer Wilds",        appid=753640,  year=2019, era="older",  slug="outer-wilds",         subreddit="outerwilds"),
    dict(name="Disco Elysium",      appid=632470,  year=2019, era="older",  slug="disco-elysium",       subreddit="DiscoElysium"),
    dict(name="Pentiment",          appid=1205520, year=2022, era="older",  slug="pentiment",           subreddit="pentiment"),
    dict(name="Citizen Sleeper",    appid=1578650, year=2022, era="older",  slug="citizen-sleeper",     subreddit="CitizenSleeper"),
    dict(name="Signalis",           appid=1262350, year=2022, era="older",  slug="signalis",            subreddit="signalis"),
]

# ── 8 Categories ─────────────────────────────────────────────────────────────
CATS = [
    ("gameplay",          "Gameplay",                  "Core mechanics, combat, controls, game feel"),
    ("accessibility",     "Accessibility",             "Difficulty options, colorblind/subtitle/motor accessibility"),
    ("ux_ui",             "UX-UI Design",              "Menus, HUD clarity, onboarding, information architecture"),
    ("player_experience", "Player Experience",         "Pacing, emotional resonance, player agency, frustration & delight"),
    ("mechanics",         "Mechanics",                 "Progression, crafting, economy, loop design"),
    ("world_systems",     "Game & World Systems",      "Open world design, AI behavior, physics, systemic depth"),
    ("technical",         "Technical Aspects",         "Performance, bugs, load times, visual fidelity"),
    ("aesthetics",        "Aesthetics & Presentation", "Art direction, sound design, music, voice acting"),
]
CAT_KEYS  = [c[0] for c in CATS]
CAT_NAMES = {c[0]: c[1] for c in CATS}

# ── Keyword Fallback ──────────────────────────────────────────────────────────
_KW = {
    "gameplay":          ["combat", "fight", "mechanic", "control", "gameplay", "battle",
                          "dodge", "attack", "play", "fun", "moves", "boss", "action", "game feel", "input", "responsive"],
    "accessibility":     ["accessibility", "colorblind", "subtitle", "closed caption", "difficulty",
                          "hard mode", "easy mode", "assist", "dyslexia", "font size", "rebind"],
    "ux_ui":             ["menu", " ui ", "hud", "interface", "inventory", "minimap", "map",
                          "tutorial", "onboarding", "tooltip", "icon", "button layout", "pause", "navigation"],
    "player_experience": ["immersive", "story", "narrative", "emotion", "pacing", "boring",
                          "engaging", "addictive", "tension", "atmosphere", "feeling", "experience",
                          "engrossing", "captivating", "frustrat", "satisfying", "rewarding"],
    "mechanics":         ["progression", "level up", "craft", "upgrade", "skill tree", "build",
                          "loot", "inventory", "equipment", "unlock", "grind", "economy", "currency",
                          "talent", "perk", "deck", "card", "roguelike", "roguelite"],
    "world_systems":     ["open world", "npc", " ai ", "physics", "explor", "enemy variety",
                          "world design", "level design", "sandbox", "simulation", "ecosystem",
                          "faction", "quest", "side quest", "biome", "zone", "map design"],
    "technical":         ["bug", "crash", "performance", "fps", "frame rate", "lag", "stutter",
                          "load time", "resolution", "graphic", "visual", "glitch", "optimization",
                          "ray tracing", "4k", "hdr", "texture", "pop-in"],
    "aesthetics":        ["art style", "art direction", "music", "soundtrack", "sound design",
                          "voice acting", "voice", "score", "ambient", "audio", "beautiful",
                          "pixel art", "visual design", "animation", "aesthetic"],
}

def keyword_insights(text: str, source_label: str) -> list:
    """Extract insight objects via keywords from any text block."""
    text_l = text.lower()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    insights = []
    for sent in sentences:
        sent_l = sent.lower()
        for cat, kws in _KW.items():
            if any(kw in sent_l for kw in kws):
                # Basic sentiment
                pos_words = ["great","good","excellent","amazing","love","best","perfect","fantastic","brilliant","enjoy"]
                neg_words = ["bad","terrible","awful","hate","worst","broken","horrible","disappoint","frustrat","annoying","poor"]
                pos = sum(1 for w in pos_words if w in sent_l)
                neg = sum(1 for w in neg_words if w in sent_l)
                sentiment = "positive" if pos > neg else ("negative" if neg > pos else "mixed")
                insights.append({
                    "cat": cat,
                    "sentiment": sentiment,
                    "quote": sent.strip()[:200],
                    "source": source_label,
                })
                break  # one category per sentence
    return insights

# ── Steam ─────────────────────────────────────────────────────────────────────
def get_steam_info(appid: int) -> dict:
    try:
        r = requests.get("https://store.steampowered.com/api/appdetails",
                         params={"appids": appid, "l": "english"}, timeout=10)
        d = r.json().get(str(appid), {})
        if d.get("success"):
            dd = d["data"]
            return {
                "name":         dd.get("name",""),
                "developer":    ", ".join(dd.get("developers",[])),
                "release_date": dd.get("release_date",{}).get("date",""),
                "header_image": dd.get("header_image",""),
                "genres":       [g["description"] for g in dd.get("genres",[])],
            }
    except: pass
    return {}

def fetch_steam_reviews(appid: int, max_r: int = 500) -> list:
    reviews, cursor = [], "*"
    while len(reviews) < max_r:
        try:
            r = requests.get(f"https://store.steampowered.com/appreviews/{appid}",
                             params={"json":1,"language":"english","filter":"recent",
                                     "num_per_page":100,"cursor":cursor},
                             headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
            data = r.json()
            batch = data.get("reviews", [])
            if not batch: break
            reviews += [b["review"] for b in batch if b.get("review","").strip()]
            cursor = data.get("cursor", "*")
            if cursor == "*" or not data.get("query_summary",{}).get("num_reviews"): break
            time.sleep(0.4)
        except Exception as e:
            print(f"    Steam error: {e}"); break
    return reviews[:max_r]

# ── YouTube ───────────────────────────────────────────────────────────────────
def load_cached_transcripts(slug: str) -> list:
    """Load pre-fetched transcripts from youtube-scrape cache, if available."""
    if not TRANSCRIPTS_DIR:
        return []
    cache_file = TRANSCRIPTS_DIR / f"{slug}_transcripts.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
        print(f"  ▶ YouTube: loaded {len(data)} videos from cache ({cache_file.name})")
        return data  # [{video_id, title, duration, transcript, ...}]
    return []

def search_youtube_videos(game_name: str, n: int = 8) -> list:
    """Return list of {id, title, duration} dicts filtered to review-length videos."""
    global YT_BLOCKED
    if YT_BLOCKED:
        print(f"  ▶ YouTube: SKIPPED (IP blocked by YouTube)")
        return []
    print(f"  ▶ YouTube search: {game_name}")
    try:
        ydl_opts = {"quiet": True, "extract_flat": True,
                    "default_search": f"ytsearch{n*2}", "noplaylist": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{n*2}:{game_name} review", download=False)
            videos = []
            for e in result.get("entries", []):
                dur = e.get("duration") or 0
                if 240 <= dur <= 3600:  # 4–60 min (proper reviews)
                    videos.append({"id": e["id"], "title": e.get("title",""), "duration": dur})
                if len(videos) >= n:
                    break
            print(f"    Found {len(videos)} review videos")
            return videos
    except Exception as e:
        print(f"    YouTube search error: {e}")
        return []

def get_transcript(video_id: str) -> str:
    """Fetch YouTube auto-captions, return cleaned text."""
    global YT_BLOCKED
    if YT_BLOCKED:
        return ""
    try:
        api = YouTubeTranscriptApi()
        snippets = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        return " ".join(s.text for s in snippets).replace("\n", " ")
    except Exception as e:
        err_str = str(e)
        if "IpBlocked" in err_str or "ip" in err_str.lower() and "block" in err_str.lower():
            print(f"    YouTube IpBlocked — disabling YouTube for this run")
            YT_BLOCKED = True
        else:
            print(f"    Transcript error {video_id}: {e}")
        return ""

# ── Reddit ────────────────────────────────────────────────────────────────────
def fetch_reddit_posts(subreddit: str, game_name: str, n: int = 10) -> list:
    """Fetch top posts from game subreddit + r/gaming mentioning the game."""
    posts = []
    # Game-specific subreddit
    urls = [
        f"https://www.reddit.com/r/{subreddit}/top.json?t=year&limit={n}",
        f"https://www.reddit.com/r/gaming/search.json?q={requests.utils.quote(game_name)}&sort=top&t=year&limit=5",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=REDDIT_HEADERS, timeout=10)
            if r.status_code == 200:
                children = r.json().get("data",{}).get("children",[])
                for c in children:
                    d = c["data"]
                    text = (d.get("selftext","") or "").strip()
                    title = d.get("title","")
                    if len(text) > 50 or len(title) > 30:
                        posts.append({
                            "title": title,
                            "text": f"{title}. {text}"[:3000],
                            "score": d.get("score",0),
                            "url": f"https://reddit.com{d.get('permalink','')}",
                        })
            time.sleep(0.5)
        except Exception as e:
            print(f"    Reddit error: {e}")
    # Sort by score, deduplicate
    seen = set()
    unique = []
    for p in sorted(posts, key=lambda x: x["score"], reverse=True):
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    print(f"  ▶ Reddit: {len(unique[:n])} posts from r/{subreddit}")
    return unique[:n]

# ── AI Multi-Insight Extraction ───────────────────────────────────────────────
SYSTEM_PROMPT = """You are a game UX/player experience analyst. Extract discrete player insights from text.

For each insight found, return a JSON object with:
- "cat": one of [gameplay, accessibility, ux_ui, player_experience, mechanics, world_systems, technical, aesthetics]
- "sentiment": one of [positive, negative, mixed]
- "quote": a verbatim or near-verbatim excerpt (1-2 sentences max, under 200 chars)

Return a JSON array of insight objects. Extract ALL distinct insights, not just one.
If a single paragraph touches 3 categories, return 3 separate objects.
Minimum 1, maximum 15 insights per text block."""

def ai_extract_insights(text: str, source_label: str) -> list:
    """Call Claude to extract multiple insights from a text chunk."""
    if len(text.strip()) < 50:
        return []
    # Truncate to ~2000 words to stay in one API call
    words = text.split()
    chunk = " ".join(words[:2000])

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role":"user", "content":
                f"Game text to analyze:\n\n{chunk}\n\nReturn only valid JSON array."}]
        )
        raw = msg.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            items = json.loads(match.group())
            result = []
            for item in items:
                if isinstance(item, dict) and "cat" in item and item["cat"] in CAT_KEYS:
                    item["source"] = source_label
                    result.append(item)
            return result
    except Exception as e:
        print(f"    AI error: {e}")
    return []

def extract_insights_long(text: str, source_label: str, use_ai: bool = True) -> list:
    """For long text (transcripts), chunk and extract insights from each chunk."""
    if not text.strip():
        return []
    if use_ai:
        words = text.split()
        chunks = []
        chunk_size = 1500
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i:i+chunk_size]))
        all_insights = []
        for i, chunk in enumerate(chunks[:4]):  # max 4 chunks per source
            print(f"    AI chunk {i+1}/{min(len(chunks),4)}...")
            insights = ai_extract_insights(chunk, source_label)
            all_insights.extend(insights)
            time.sleep(0.3)
        return all_insights
    else:
        return keyword_insights(text, source_label)

def extract_insights_short(text: str, source_label: str, use_ai: bool = True) -> list:
    """For short text (Steam reviews), extract insights."""
    if not text.strip():
        return []
    if use_ai:
        return ai_extract_insights(text, source_label)
    else:
        return keyword_insights(text, source_label)

# ── Aggregate ─────────────────────────────────────────────────────────────────
def aggregate(insights: list) -> dict:
    """Aggregate insight list into per-category positive/negative counts."""
    agg = {k: {"pos": 0, "neg": 0, "quotes": []} for k in CAT_KEYS}
    for ins in insights:
        cat = ins.get("cat")
        if cat not in agg:
            continue
        if ins.get("sentiment") == "positive":
            agg[cat]["pos"] += 1
        elif ins.get("sentiment") == "negative":
            agg[cat]["neg"] += 1
        else:
            agg[cat]["pos"] += 1  # mixed counts as positive signal
        q = ins.get("quote","").strip()
        if q and len(agg[cat]["quotes"]) < 3:
            agg[cat]["quotes"].append(q)
    return agg

# ── HTML Rendering ────────────────────────────────────────────────────────────
def score_color(pct):
    if pct >= 75: return "#00d4ff"
    if pct >= 50: return "#fbbf24"
    return "#f87171"

def gauge_svg(pct, color):
    total = math.pi * 80
    filled = (pct / 100) * total
    return f"""<svg width="200" height="110" viewBox="0 0 200 110">
  <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#1e293b" stroke-width="18" stroke-linecap="round"/>
  <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="{color}" stroke-width="18" stroke-linecap="round"
        stroke-dasharray="{filled:.1f} {total:.1f}" stroke-dashoffset="0"/>
  <text x="100" y="88" text-anchor="middle" font-size="34" font-weight="800" fill="#ffffff" font-family="system-ui,sans-serif">{pct}%</text>
  <text x="100" y="107" text-anchor="middle" font-size="11" fill="#64748b" font-family="system-ui,sans-serif">Positive</text>
</svg>"""

def render_game_html(game, info, all_insights, agg, source_summary) -> str:
    name      = info.get("name", game["name"])
    dev       = info.get("developer", "Unknown")
    released  = info.get("release_date", str(game["year"]))
    img       = info.get("header_image","")
    genres    = ", ".join(info.get("genres",[]))
    n_insights = len(all_insights)
    n_sources  = source_summary.get("total_sources", 0)

    total_pos = sum(a["pos"] for a in agg.values())
    total_neg = sum(a["neg"] for a in agg.values())
    total = total_pos + total_neg
    pct = round(total_pos / total * 100) if total else 0
    color = score_color(pct)

    def cat_section(key, label):
        a = agg[key]
        pos, neg = a["pos"], a["neg"]
        t = pos + neg
        if t == 0:
            return f'<div style="padding:14px 0;border-bottom:1px solid #1e293b"><div style="display:flex;justify-content:space-between"><span style="font-weight:600;color:#cbd5e1">{label}</span><span style="font-size:11px;color:#334155">No mentions</span></div></div>'
        pp = round(pos/t*100)
        quotes_html = ""
        for q in a["quotes"][:2]:
            quotes_html += f'<div style="font-size:11px;color:#64748b;border-left:2px solid #334155;padding-left:8px;margin:6px 0;font-style:italic">&ldquo;{q[:180]}&rdquo;</div>'
        return f"""<div style="padding:14px 0;border-bottom:1px solid #1e293b">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <span style="font-weight:600;color:#cbd5e1">{label}</span>
    <span style="font-size:11px;color:#334155">{t} insights</span>
  </div>
  <div style="display:flex;align-items:center;gap:8px">
    <div style="flex:1;height:8px;background:#1e293b;border-radius:4px;overflow:hidden">
      <div style="width:{pp}%;height:100%;background:{color};border-radius:4px"></div>
    </div>
    <span style="font-size:11px;color:#64748b;width:36px;text-align:right">{pp}%</span>
  </div>
  <div style="font-size:10px;color:#475569;margin-top:4px">{pos} positive · {neg} negative</div>
  {quotes_html}
</div>"""

    cats_html = "".join(cat_section(k, CAT_NAMES[k]) for k in CAT_KEYS)

    src_lines = ""
    for s in source_summary.get("breakdown", []):
        src_lines += f'<div style="display:flex;justify-content:space-between;padding:3px 0"><span style="color:#94a3b8;font-size:12px">{s["type"]}</span><span style="color:#64748b;font-size:12px">{s["count"]} items · {s["insights"]} insights</span></div>'

    method = "AI (Claude Haiku)" if USE_AI else "KEYWORD fallback"

    # Build image HTML separately to avoid backslashes in f-string expressions
    img_html = ("<img src='" + img + "' style='width:100%;border-radius:12px;margin-bottom:24px;max-height:200px;object-fit:cover' onerror=\"this.style.display='none'\">" if img else "")


    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{name} — Expanded Sonar Analysis</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0f1a;color:#e2e8f0;line-height:1.6}}
  .container{{max-width:760px;margin:0 auto;padding:32px 20px 60px}}
  .back{{display:inline-block;color:#475569;font-size:12px;text-decoration:none;margin-bottom:20px}}
  .back:hover{{color:#94a3b8}}
</style>
</head>
<body>
<div class="container">
  <a href="index.html" class="back">← Back to all games</a>
  {img_html}
  <h1 style="font-size:1.6rem;font-weight:800;color:#f1f5f9;margin-bottom:4px">{name}</h1>
  <div style="color:#64748b;font-size:13px;margin-bottom:20px">{dev} &mdash; {released} &mdash; {genres}</div>

  <div style="display:grid;grid-template-columns:180px 1fr;gap:20px;margin-bottom:28px;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px;align-items:center">
    {gauge_svg(pct, color)}
    <div>
      <div style="font-size:1.1rem;font-weight:700;color:#f1f5f9">{'Overwhelmingly Positive' if pct>=90 else 'Very Positive' if pct>=80 else 'Mostly Positive' if pct>=70 else 'Mixed' if pct>=50 else 'Negative'}</div>
      <div style="color:#64748b;font-size:13px;margin:6px 0"><strong style="color:#818cf8">{n_insights}</strong> insights from <strong style="color:#818cf8">{n_sources}</strong> sources</div>
      <div style="font-size:11px;color:#475569">{total_pos} positive · {total_neg} negative signals</div>
    </div>
  </div>

  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px;margin-bottom:20px">
    <div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#475569;margin-bottom:12px">Source Breakdown</div>
    {src_lines}
  </div>

  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px">
    <div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#475569;margin-bottom:4px">Category Analysis</div>
    {cats_html}
  </div>

  <div style="text-align:center;color:#334155;font-size:11px;margin-top:24px">
    Generated {datetime.now().strftime('%B %d, %Y')} · Sources: Steam + YouTube + Reddit · Classification: {method} · Expanded Pipeline
  </div>
</div>
</body></html>"""

def render_index(results: list) -> str:
    cards = ""
    for r in sorted(results, key=lambda x: x["pct"], reverse=True):
        color = score_color(r["pct"])
        era_tag = f'<span style="font-size:10px;padding:2px 7px;border-radius:10px;background:#1e293b;color:#64748b">{r["era"]}</span>'
        cards += f"""<a href="{r['slug']}.html" style="display:block;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:18px;text-decoration:none;color:inherit">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
    <div><div style="font-weight:700;color:#f1f5f9;font-size:.95rem">{r['name']}</div><div style="font-size:11px;color:#475569;margin-top:2px">{r['dev']}</div></div>
    {era_tag}
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.5rem;font-weight:800;color:{color}">{r['pct']}%</div>
    <div style="flex:1;height:6px;background:#1e293b;border-radius:3px"><div style="width:{r['pct']}%;height:100%;background:{color};border-radius:3px"></div></div>
  </div>
  <div style="font-size:11px;color:#475569;margin-top:8px"><strong style="color:#818cf8">{r['n_insights']}</strong> insights · <strong style="color:#818cf8">{r['n_sources']}</strong> sources (Steam + YT + Reddit)</div>
</a>"""

    avg = round(sum(r["pct"] for r in results) / len(results)) if results else 0
    total_insights = sum(r["n_insights"] for r in results)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Expanded Sonar Prototype — All Games</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0f1a;color:#e2e8f0}}
  .hero{{background:linear-gradient(135deg,#0f172a,#1e1b4b);padding:40px 24px 32px;text-align:center;border-bottom:1px solid #1e293b}}
  .grid{{max-width:900px;margin:32px auto;padding:0 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
  .stat{{display:inline-block;background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:12px 20px;margin:6px}}
</style>
</head>
<body>
<div class="hero">
  <div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:8px">Expanded Pipeline · Phases 1–4</div>
  <h1 style="font-size:1.8rem;font-weight:800;color:#f1f5f9;margin-bottom:12px">Sonar Prototype — 10 Games</h1>
  <div>
    <span class="stat"><span style="font-size:1.4rem;font-weight:800;color:#00d4ff">{avg}%</span><br><span style="font-size:10px;color:#475569">Avg Positive</span></span>
    <span class="stat"><span style="font-size:1.4rem;font-weight:800;color:#818cf8">{total_insights:,}</span><br><span style="font-size:10px;color:#475569">Total Insights</span></span>
    <span class="stat"><span style="font-size:1.4rem;font-weight:800;color:#4ade80">3</span><br><span style="font-size:10px;color:#475569">Source Types</span></span>
  </div>
</div>
<div class="grid">{cards}</div>
<div style="text-align:center;color:#334155;font-size:11px;padding:20px">
  Generated {datetime.now().strftime('%B %d, %Y')} · Steam ×500 + YouTube + Reddit · {'AI (Claude Haiku)' if USE_AI else 'KEYWORD'} classification
</div>
</body></html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def process_game(game: dict) -> dict:
    name = game["name"]
    print(f"\n{'='*55}")
    print(f"  {name.upper()}")
    print(f"{'='*55}")

    # Steam
    print(f"  ► Steam reviews (up to 500)...")
    info    = get_steam_info(game["appid"])
    reviews = fetch_steam_reviews(game["appid"], max_r=500)
    print(f"    Got {len(reviews)} reviews")

    all_insights   = []
    source_summary = {"total_sources": 0, "breakdown": []}

    # -- Steam insights
    steam_insights = []
    if USE_AI:
        # Batch steam reviews in groups of 10 for efficiency
        batch_size = 10
        for i in range(0, min(len(reviews), 200), batch_size):
            batch_text = "\n---\n".join(reviews[i:i+batch_size])
            ins = ai_extract_insights(batch_text, "Steam Review")
            steam_insights.extend(ins)
            time.sleep(0.2)
        print(f"    Steam → {len(steam_insights)} insights (AI)")
    else:
        for rev in reviews:
            steam_insights.extend(keyword_insights(rev, "Steam Review"))
        print(f"    Steam → {len(steam_insights)} insights (keyword)")
    all_insights.extend(steam_insights)
    source_summary["breakdown"].append({"type":"Steam Reviews","count":len(reviews),"insights":len(steam_insights)})
    source_summary["total_sources"] += len(reviews)

    # -- YouTube (uses pre-fetched cache from youtube-scrape skill if available)
    print(f"  ► YouTube transcripts...")
    cached = load_cached_transcripts(game["slug"])
    if cached:
        # Cache hit: use pre-fetched transcripts, no network calls needed
        yt_insights = []
        for v in cached:
            transcript = v.get("transcript", "")
            if len(transcript.split()) < 100:
                continue
            label = f"YouTube: {v['title'][:50]}"
            print(f"    {v['title'][:50]} ({len(transcript.split())} words)")
            ins = extract_insights_long(transcript, label, use_ai=USE_AI)
            yt_insights.extend(ins)
            time.sleep(0.3)
        videos = cached
    else:
        # Live fetch fallback
        videos = search_youtube_videos(name, n=8)
        yt_insights = []
        for v in videos:
            transcript = get_transcript(v["id"])
            if len(transcript.split()) < 100:
                continue
            label = f"YouTube: {v['title'][:50]}"
            print(f"    {v['title'][:50]} ({len(transcript.split())} words)")
            ins = extract_insights_long(transcript, label, use_ai=USE_AI)
            yt_insights.extend(ins)
            time.sleep(0.5)
    print(f"    YouTube → {len(yt_insights)} insights from {len(videos)} videos")
    all_insights.extend(yt_insights)
    source_summary["breakdown"].append({"type":"YouTube Videos","count":len(videos),"insights":len(yt_insights)})
    source_summary["total_sources"] += len(videos)

    # -- Reddit
    print(f"  ► Reddit posts...")
    reddit_posts = fetch_reddit_posts(game["subreddit"], name, n=10)
    reddit_insights = []
    for post in reddit_posts:
        ins = extract_insights_short(post["text"], f"Reddit: r/{game['subreddit']}", use_ai=USE_AI)
        reddit_insights.extend(ins)
        time.sleep(0.3)
    print(f"    Reddit → {len(reddit_insights)} insights from {len(reddit_posts)} posts")
    all_insights.extend(reddit_insights)
    source_summary["breakdown"].append({"type":"Reddit","count":len(reddit_posts),"insights":len(reddit_insights)})
    source_summary["total_sources"] += len(reddit_posts)

    # Aggregate
    agg = aggregate(all_insights)
    total_pos = sum(a["pos"] for a in agg.values())
    total_neg = sum(a["neg"] for a in agg.values())
    total = total_pos + total_neg
    pct   = round(total_pos / total * 100) if total else 0

    # Render
    html = render_game_html(game, info, all_insights, agg, source_summary)
    (OUT_DIR / f"{game['slug']}.html").write_text(html, encoding="utf-8")
    print(f"  ✓ {name}: {pct}% · {len(all_insights)} insights · {source_summary['total_sources']} sources")

    return {
        "name": name,
        "slug": game["slug"],
        "era":  game["era"],
        "dev":  info.get("developer",""),
        "pct":  pct,
        "n_insights": len(all_insights),
        "n_sources":  source_summary["total_sources"],
    }

def main():
    print(f"\n🚀 Sonar Expanded Pipeline (Phases 1–4)")
    print(f"   Mode: {'AI (Claude Haiku)' if USE_AI else 'KEYWORD fallback'}")
    print(f"   Sources: Steam×500 + YouTube (8 vids) + Reddit (10 posts)")
    print(f"   Output: {OUT_DIR}")

    games = GAMES
    if SOLO_GAME:
        games = [g for g in GAMES if SOLO_GAME.lower() in g["name"].lower()]
        if not games:
            print(f"Game '{SOLO_GAME}' not found")
            sys.exit(1)

    results = []
    for game in games:
        try:
            result = process_game(game)
            results.append(result)
        except Exception as e:
            print(f"  ✗ {game['name']}: {e}")
            import traceback; traceback.print_exc()

    # Index
    (OUT_DIR / "index.html").write_text(render_index(results), encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"  DONE — {len(results)}/10 games processed")
    for r in sorted(results, key=lambda x: x["pct"], reverse=True):
        print(f"  {r['pct']:3}%  {r['n_insights']:4} insights  {r['name']}")
    print(f"  Reports: {OUT_DIR}/index.html")

if __name__ == "__main__":
    main()
