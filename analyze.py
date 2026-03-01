#!/usr/bin/env python3
"""
Sonar-Style Game Sentiment Analyzer
Fetches Steam reviews → Claude Haiku classification → self-contained HTML reports

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    pip install anthropic requests
    python3 analyze.py
"""

import os, sys, json, time, re, math, requests
from pathlib import Path
from datetime import datetime
import anthropic

# ── Config ───────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    sys.exit("ERROR: Set ANTHROPIC_API_KEY before running.")

client  = anthropic.Anthropic(api_key=API_KEY)
OUT_DIR = Path(__file__).parent / "reports"
OUT_DIR.mkdir(exist_ok=True)

# ── Game Catalogue ────────────────────────────────────────────────────────────

GAMES = [
    # Recent — high review volume
    dict(name="Black Myth: Wukong", appid=2358720, year=2024, era="recent", slug="black-myth-wukong"),
    dict(name="Palworld",           appid=1623730, year=2024, era="recent", slug="palworld"),
    dict(name="Helldivers 2",       appid=553850,  year=2024, era="recent", slug="helldivers-2"),
    dict(name="Balatro",            appid=2379780, year=2024, era="recent", slug="balatro"),
    dict(name="Hell Is Us",         appid=None,    year=2025, era="recent", slug="hell-is-us"),
    # Older — sparser coverage
    dict(name="Outer Wilds",        appid=753640,  year=2019, era="older",  slug="outer-wilds"),
    dict(name="Disco Elysium",      appid=632470,  year=2019, era="older",  slug="disco-elysium"),
    dict(name="Pentiment",          appid=1205520, year=2022, era="older",  slug="pentiment"),
    dict(name="Citizen Sleeper",    appid=1578650, year=2022, era="older",  slug="citizen-sleeper"),
    dict(name="Signalis",           appid=1262350, year=2022, era="older",  slug="signalis"),
]

# ── Analysis Categories ───────────────────────────────────────────────────────

CATS = [
    ("gameplay",          "Gameplay",                 "Core mechanics, combat, controls, game feel"),
    ("accessibility",     "Accessibility",            "Difficulty options, colorblind/subtitle/motor accessibility"),
    ("ux_ui",             "UX-UI Design",             "Menus, HUD clarity, onboarding, information architecture"),
    ("player_experience", "Player Experience",        "Pacing, emotional resonance, player agency, frustration & delight"),
    ("mechanics",         "Mechanics",                "Progression, crafting, economy, loop design"),
    ("world_systems",     "Game & World Systems",     "Open world design, AI behavior, physics, systemic depth"),
    ("technical",         "Technical Aspects",        "Performance, bugs, load times, visual fidelity"),
    ("aesthetics",        "Aesthetics & Presentation","Art direction, sound design, music, voice acting"),
]

# ── Steam Helpers ─────────────────────────────────────────────────────────────

def search_steam_appid(name: str):
    try:
        r = requests.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": name, "l": "english", "cc": "US"},
            timeout=10
        )
        items = r.json().get("items", [])
        return items[0]["id"] if items else None
    except Exception as e:
        print(f"  ⚠ Search failed: {e}")
        return None

def get_steam_game_info(appid: int) -> dict:
    try:
        r = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "l": "english"},
            timeout=10
        )
        data = r.json().get(str(appid), {})
        if data.get("success"):
            d = data["data"]
            return {
                "name":         d.get("name", ""),
                "developer":    ", ".join(d.get("developers", [])),
                "publisher":    ", ".join(d.get("publishers", [])),
                "release_date": d.get("release_date", {}).get("date", ""),
                "header_image": d.get("header_image", ""),
                "genres":       [g["description"] for g in d.get("genres", [])],
            }
    except Exception as e:
        print(f"  ⚠ App details failed: {e}")
    return {}

def fetch_steam_reviews(appid: int, max_reviews: int = 100) -> list:
    reviews, cursor = [], "*"
    while len(reviews) < max_reviews:
        try:
            r = requests.get(
                f"https://store.steampowered.com/appreviews/{appid}",
                params={
                    "json": 1, "filter": "recent", "language": "english",
                    "purchase_type": "all", "num_per_page": 100,
                    "cursor": cursor, "review_type": "all",
                },
                timeout=15
            )
            data = r.json()
            if data.get("success") != 1:
                break
            batch = data.get("reviews", [])
            if not batch:
                break
            for rv in batch:
                text = rv.get("review", "").strip()
                if text and len(text) > 30:
                    reviews.append({
                        "text":     text[:500],
                        "positive": rv.get("voted_up", False),
                        "hours":    rv.get("author", {}).get("playtime_forever", 0) / 60,
                    })
            new_cursor = data.get("cursor", "")
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
            if len(reviews) >= max_reviews:
                break
            time.sleep(0.4)
        except Exception as e:
            print(f"  ⚠ Review fetch error: {e}")
            break
    return reviews[:max_reviews]

# ── Claude Classification ─────────────────────────────────────────────────────

def _empty_result() -> dict:
    return {
        "overall_positive_pct": 0,
        "total_analyzed": 0,
        "categories": {
            k: {"positive_count": 0, "negative_count": 0,
                "positive_quote": "", "negative_quote": ""}
            for k, _, _ in CATS
        },
        "top_positive_insights": [],
        "top_negative_insights": [],
    }

def classify_reviews(game_name: str, reviews: list, use_ai: bool = True) -> dict:
    if not reviews:
        return _empty_result()
    if use_ai:
        return _classify_with_ai(game_name, reviews)
    else:
        return _classify_with_keywords(game_name, reviews)

def _classify_with_ai(game_name: str, reviews: list) -> dict:
    lines = []
    for i, r in enumerate(reviews[:80]):
        icon = "👍" if r["positive"] else "👎"
        text = r["text"].replace("\n", " ")[:300]
        lines.append(f"[{i+1}] {icon} {text}")

    cats_desc = "\n".join(f"- {k}: {desc}" for k, _, desc in CATS)
    cat_keys  = [k for k, _, _ in CATS]

    schema = "{\n" + ",\n".join(
        f'    "{k}": {{"positive_count": 0, "negative_count": 0, "positive_quote": "", "negative_quote": ""}}'
        for k in cat_keys
    ) + "\n  }"

    prompt = f"""Analyze these {len(lines)} Steam player reviews for "{game_name}" and produce a structured sentiment report.

REVIEWS:
{chr(10).join(lines)}

CATEGORIES (only classify a review under a category if it explicitly discusses that topic):
{cats_desc}

Return ONLY valid JSON, no markdown fences, exactly this structure:
{{
  "overall_positive_pct": <integer 0-100, % of 👍 reviews>,
  "total_analyzed": {len(lines)},
  "categories": {schema},
  "top_positive_insights": ["<concise insight>", "<concise insight>", "<concise insight>"],
  "top_negative_insights": ["<concise insight>", "<concise insight>", "<concise insight>"]
}}

Rules:
- positive_quote / negative_quote: real fragments from above reviews, max 110 chars, empty string if none
- If a category isn't mentioned at all, both counts = 0 and both quotes = ""
- overall_positive_pct = round( count(👍) / total * 100 )"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = msg.content[0].text.strip()
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw).strip()

    try:
        result = json.loads(raw)
        result["method"] = "ai"
        return result
    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON parse error: {e}\n  Raw: {raw[:300]}")
        return _empty_result()

# Keyword maps for each category — used in fallback mode
_KEYWORD_MAP = {
    "gameplay":          ["combat", "fight", "mechanic", "control", "gameplay", "battle",
                          "dodge", "attack", "play", "fun", "moves", "boss", "action", "game feels"],
    "accessibility":     ["accessibility", "colorblind", "subtitle", "closed caption", "difficulty",
                          "deaf", "blind", "easy mode", "hard mode", "setting", "options menu",
                          "color blind", "font size"],
    "ux_ui":             ["menu", " ui ", "hud", "interface", "inventory", "minimap", "map",
                          "tutorial", "button", "navigate", "onboard", "quest log", "waypoint",
                          "user interface", "screen", "icon"],
    "player_experience": ["immersive", "story", "narrative", "emotion", "pacing", "boring",
                          "engaging", "frustrat", "satisf", "experience", "atmosphere", "feel",
                          "tension", "dread", "delight", "hours in", "addictive", "compelling"],
    "mechanics":         ["progression", "level up", "craft", "upgrade", "skill tree", "build",
                          "loot", "grind", "economy", "system", "unlock", "perks", "stats",
                          "ability", "talents", "resource"],
    "world_systems":     ["open world", "npc", " ai ", "physics", "explor", "enemy variety",
                          "environment", "world design", "map design", "ecosystem", "faction",
                          "sandbox", "emergent", "simulation"],
    "technical":         ["bug", "crash", "performance", "fps", "frame rate", "lag", "stutter",
                          "load time", "optim", "glitch", "error", "freeze", "stuttering",
                          "framerate", "technical", "patch", "update", "fix"],
    "aesthetics":        ["art style", "art direction", "music", "soundtrack", "sound design",
                          "visual", "graphic", "beautiful", "animation", "voice act", "atmosphere",
                          "aesthetic", "stunning", "gorgeous", "art"],
}

def _classify_with_keywords(game_name: str, reviews: list) -> dict:
    """Keyword-based fallback — no AI required."""
    pos_count   = sum(1 for r in reviews if r["positive"])
    overall_pct = round(pos_count / len(reviews) * 100) if reviews else 0

    cat_results = {}
    for key, _, _ in CATS:
        kws        = _KEYWORD_MAP.get(key, [])
        pos_hits, neg_hits = [], []
        for r in reviews:
            tl = r["text"].lower()
            if any(kw in tl for kw in kws):
                (pos_hits if r["positive"] else neg_hits).append(r["text"])

        def best_quote(hits):
            # Prefer medium-length quotes (not too short, not walls of text)
            sorted_hits = sorted(hits, key=lambda t: abs(len(t) - 120))
            return sorted_hits[0][:110] if sorted_hits else ""

        cat_results[key] = {
            "positive_count": len(pos_hits),
            "negative_count": len(neg_hits),
            "positive_quote": best_quote(pos_hits),
            "negative_quote": best_quote(neg_hits),
        }

    # Surface top insights: pick the most keyword-dense sentences from each group
    pos_reviews = [r["text"] for r in reviews if r["positive"]]
    neg_reviews = [r["text"] for r in reviews if not r["positive"]]

    def top_sentences(texts, n=3):
        sentences = []
        for t in texts[:30]:
            for s in re.split(r'[.!?\n]', t):
                s = s.strip()
                if 40 < len(s) < 200:
                    sentences.append(s)
        return sentences[:n]

    return {
        "overall_positive_pct": overall_pct,
        "total_analyzed":       len(reviews),
        "categories":           cat_results,
        "top_positive_insights": top_sentences(pos_reviews),
        "top_negative_insights": top_sentences(neg_reviews),
        "method":               "keyword",
    }

# ── HTML Helpers ──────────────────────────────────────────────────────────────

def score_color(pct: int) -> str:
    if pct >= 70: return "#00d4ff"
    if pct >= 40: return "#fbbf24"
    return "#f87171"

def score_label(pct: int) -> str:
    if pct >= 85: return "Overwhelmingly Positive"
    if pct >= 70: return "Very Positive"
    if pct >= 55: return "Mostly Positive"
    if pct >= 40: return "Mixed"
    if pct >= 25: return "Mostly Negative"
    return "Overwhelmingly Negative"

def gauge_svg(pct: int, color: str) -> str:
    total  = math.pi * 80
    filled = (pct / 100) * total
    return f"""<svg viewBox="0 0 200 120" style="width:180px;height:108px">
  <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#1e293b" stroke-width="18" stroke-linecap="round"/>
  <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="{color}" stroke-width="18" stroke-linecap="round"
        stroke-dasharray="{filled:.1f} {total:.1f}"/>
  <text x="100" y="88" text-anchor="middle" font-size="34" font-weight="800" fill="#ffffff" font-family="system-ui,sans-serif">{pct}%</text>
  <text x="100" y="107" text-anchor="middle" font-size="11" fill="#64748b" font-family="system-ui,sans-serif">Positive</text>
</svg>"""

def cat_bar(pos: int, neg: int) -> str:
    total = pos + neg
    if total == 0:
        return '<div style="flex:1;height:10px;background:#1e293b;border-radius:5px"></div>'
    pp = pos / total * 100
    np = neg / total * 100
    return f"""<div style="flex:1;height:10px;border-radius:5px;overflow:hidden;display:flex;background:#1e293b">
  <div style="width:{pp:.1f}%;background:#00d4ff"></div>
  <div style="width:{np:.1f}%;background:#f43f5e"></div>
</div>"""

# ── Individual Report HTML ────────────────────────────────────────────────────

def render_game_html(game: dict, info: dict, reviews: list, analysis: dict) -> str:
    pct      = analysis.get("overall_positive_pct", 0)
    total    = analysis.get("total_analyzed", len(reviews))
    color    = score_color(pct)
    label    = score_label(pct)
    era_tag  = "Recent Release" if game["era"] == "recent" else "Older Title"
    era_col  = "#00d4ff" if game["era"] == "recent" else "#a78bfa"

    developer = info.get("developer", "—")
    publisher = info.get("publisher", "—")
    rel_date  = info.get("release_date", str(game["year"]))
    genres    = info.get("genres", [])

    genre_tags = "".join(
        f'<span style="background:#0d1b2a;border:1px solid #2d3a52;border-radius:20px;padding:3px 10px;font-size:11px;color:#7dd3fc">{g}</span>'
        for g in genres[:5]
    )

    # Category rows + quotes
    cat_rows = ""
    quotes_html = ""
    for key, lbl, _ in CATS:
        cat   = analysis.get("categories", {}).get(key, {})
        pos   = cat.get("positive_count", 0)
        neg   = cat.get("negative_count", 0)
        total_cat = pos + neg
        bar   = cat_bar(pos, neg)
        cat_rows += f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
          <span style="width:160px;font-size:12px;color:#94a3b8;text-align:right;flex-shrink:0">{lbl}</span>
          {bar}
          <span style="width:70px;font-size:11px;color:#334155;flex-shrink:0">{total_cat} mentions</span>
        </div>"""

        pq = cat.get("positive_quote", "")
        nq = cat.get("negative_quote", "")
        if pq or nq:
            quotes_html += f'<div style="margin-bottom:14px"><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#00d4ff;margin-bottom:6px">{lbl}</div>'
            if pq:
                quotes_html += f'<div style="background:#0d2218;border-left:3px solid #00d4ff;padding:7px 10px;border-radius:0 5px 5px 0;font-size:12px;color:#94a3b8;margin-bottom:5px;font-style:italic">"{pq}"</div>'
            if nq:
                quotes_html += f'<div style="background:#1f0d12;border-left:3px solid #f43f5e;padding:7px 10px;border-radius:0 5px 5px 0;font-size:12px;color:#94a3b8;font-style:italic">"{nq}"</div>'
            quotes_html += '</div>'

    pos_li = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid #1e293b;font-size:12px;color:#94a3b8;list-style:none;padding-left:1rem;position:relative"><span style="position:absolute;left:0;color:#34d399">+</span>{i}</li>'
        for i in analysis.get("top_positive_insights", [])
    )
    neg_li = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid #1e293b;font-size:12px;color:#94a3b8;list-style:none;padding-left:1rem;position:relative"><span style="position:absolute;left:0;color:#f87171">−</span>{i}</li>'
        for i in analysis.get("top_negative_insights", [])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{game['name']} – Sonar Analysis</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;line-height:1.6}}
    .page{{max-width:920px;margin:0 auto;padding:2rem 1.5rem 4rem}}
    a{{color:#7dd3fc;text-decoration:none}} a:hover{{text-decoration:underline}}
    .card{{background:#1a2233;border:1px solid #2d3a52;border-radius:12px;padding:1.5rem;margin-bottom:1.25rem}}
    .sec-title{{font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;margin-bottom:1.1rem}}
    @media(max-width:600px){{.two-col{{grid-template-columns:1fr!important}}}}
  </style>
</head>
<body>
<div style="background:linear-gradient(135deg,#0d1b2a,#1a2744);border-bottom:2px solid #00d4ff22;padding:.9rem 1.5rem">
  <div style="max-width:920px;margin:0 auto;font-size:11px;color:#475569"><a href="index.html">← All Games</a></div>
</div>

<div class="page">

  <!-- Header card -->
  <div class="card" style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:flex-start">
    <div style="flex:1;min-width:200px">
      <span style="background:#0d1b2a;border:1px solid {era_col}44;color:{era_col};font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;text-transform:uppercase;letter-spacing:.06em">{era_tag}</span>
      <h1 style="font-size:1.9rem;font-weight:900;color:#fff;margin:.5rem 0">{game['name']}</h1>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px;color:#64748b;margin-bottom:.9rem">
        <span style="color:#7dd3fc;font-weight:600">Developer</span><span>{developer}</span>
        <span style="color:#7dd3fc;font-weight:600">Publisher</span><span>{publisher}</span>
        <span style="color:#7dd3fc;font-weight:600">Released</span><span>{rel_date}</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:5px">{genre_tags}</div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:center;gap:.3rem;flex-shrink:0">
      {gauge_svg(pct, color)}
      <div style="font-size:12px;font-weight:700;color:{color}">{label}</div>
      <div style="font-size:10px;color:#334155">{total} reviews · Steam</div>
    </div>
  </div>

  <!-- Category breakdown -->
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.1rem">
      <div class="sec-title" style="color:#00d4ff;margin-bottom:0">Category Breakdown</div>
      <div style="display:flex;gap:10px;font-size:10px;color:#64748b">
        <span><span style="display:inline-block;width:8px;height:8px;background:#00d4ff;border-radius:2px;margin-right:3px;vertical-align:middle"></span>Positive</span>
        <span><span style="display:inline-block;width:8px;height:8px;background:#f43f5e;border-radius:2px;margin-right:3px;vertical-align:middle"></span>Negative</span>
        <span><span style="display:inline-block;width:8px;height:8px;background:#1e293b;border-radius:2px;margin-right:3px;vertical-align:middle"></span>Not mentioned</span>
      </div>
    </div>
    {cat_rows}
  </div>

  <!-- Quotes + Insights -->
  <div class="two-col" style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem">
    <div class="card">
      <div class="sec-title" style="color:#00d4ff">Player Voices</div>
      {quotes_html if quotes_html else '<p style="font-size:12px;color:#334155">No categorized quotes extracted.</p>'}
    </div>
    <div class="card">
      <div class="sec-title" style="color:#34d399">What Players Love</div>
      <ul style="margin-bottom:1.5rem">{pos_li or '<li style="font-size:12px;color:#334155;list-style:none">None extracted</li>'}</ul>
      <div class="sec-title" style="color:#f87171">Top Criticisms</div>
      <ul>{neg_li or '<li style="font-size:12px;color:#334155;list-style:none">None extracted</li>'}</ul>
    </div>
  </div>

  <div style="margin-top:1.5rem;font-size:10px;color:#1e293b;text-align:center">
    Generated {datetime.now().strftime('%B %d, %Y')} · Source: Steam Reviews · Classification: {analysis.get("method","keyword").upper()} · Prototype for competitive examination
  </div>

</div>
</body>
</html>"""

# ── Index HTML ────────────────────────────────────────────────────────────────

def render_index_html(results: list) -> str:
    recent = [r for r in results if r["game"]["era"] == "recent"]
    older  = [r for r in results if r["game"]["era"] == "older"]

    def game_card(r):
        game  = r["game"]
        pct   = r["analysis"].get("overall_positive_pct", 0)
        total = r["analysis"].get("total_analyzed", 0)
        color = score_color(pct)
        label = score_label(pct)
        return f"""<a href="{game['slug']}.html" style="text-decoration:none">
  <div style="background:#1a2233;border:1px solid #2d3a52;border-radius:10px;padding:1.1rem;cursor:pointer"
       onmouseover="this.style.borderColor='#00d4ff55'" onmouseout="this.style.borderColor='#2d3a52'">
    <div style="display:flex;justify-content:space-between;gap:.5rem;align-items:flex-start">
      <div style="flex:1;min-width:0">
        <div style="font-size:.65rem;color:#334155;font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.25rem">{game['year']}</div>
        <div style="font-size:.95rem;font-weight:700;color:#f1f5f9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{game['name']}</div>
        <div style="font-size:.7rem;color:#334155;margin-top:.25rem">{total} reviews</div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:1.5rem;font-weight:900;color:{color};line-height:1">{pct}%</div>
        <div style="font-size:.6rem;color:{color};font-weight:600;text-transform:uppercase;letter-spacing:.04em">{label}</div>
      </div>
    </div>
    <div style="margin-top:.8rem;height:5px;background:#0d1b2a;border-radius:3px;overflow:hidden">
      <div style="height:100%;width:{pct}%;background:{color};border-radius:3px"></div>
    </div>
  </div>
</a>"""

    recent_cards = "".join(game_card(r) for r in recent)
    older_cards  = "".join(game_card(r) for r in older)
    count = len(results)
    avg   = round(sum(r["analysis"].get("overall_positive_pct", 0) for r in results) / max(count, 1))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sonar Prototype – Game Index</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;line-height:1.6}}
    .page{{max-width:920px;margin:0 auto;padding:2.5rem 1.5rem 4rem}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1rem}}
    .section-head{{font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;border-left:3px solid;padding-left:.65rem;margin-bottom:1rem}}
    @media(max-width:520px){{.stat-row{{flex-direction:column!important}}}}
  </style>
</head>
<body>

<div style="background:linear-gradient(135deg,#0d1b2a 0%,#1a2744 100%);border-bottom:2px solid #00d4ff22;padding:2rem 1.5rem 1.75rem">
  <div style="max-width:920px;margin:0 auto">
    <div style="font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:#00d4ff;margin-bottom:.4rem">Sonar Prototype · Competitive Examination</div>
    <h1 style="font-size:2rem;font-weight:900;color:#fff;margin-bottom:.4rem">Game Sentiment Dashboard</h1>
    <div style="font-size:.8rem;color:#475569">AI-powered player review analysis · Steam · Claude Haiku · {datetime.now().strftime('%B %d, %Y')}</div>
  </div>
</div>

<div class="page">

  <!-- Summary stats -->
  <div class="stat-row" style="display:flex;gap:1rem;margin-bottom:2.5rem;flex-wrap:wrap">
    <div style="background:#1a2233;border:1px solid #2d3a52;border-radius:10px;padding:1rem 1.5rem;flex:1;min-width:140px;text-align:center">
      <div style="font-size:1.8rem;font-weight:900;color:#00d4ff">{count}</div>
      <div style="font-size:.72rem;color:#475569;text-transform:uppercase;letter-spacing:.06em">Games Analyzed</div>
    </div>
    <div style="background:#1a2233;border:1px solid #2d3a52;border-radius:10px;padding:1rem 1.5rem;flex:1;min-width:140px;text-align:center">
      <div style="font-size:1.8rem;font-weight:900;color:{score_color(avg)}">{avg}%</div>
      <div style="font-size:.72rem;color:#475569;text-transform:uppercase;letter-spacing:.06em">Avg Positive Score</div>
    </div>
    <div style="background:#1a2233;border:1px solid #2d3a52;border-radius:10px;padding:1rem 1.5rem;flex:1;min-width:140px;text-align:center">
      <div style="font-size:1.8rem;font-weight:900;color:#a78bfa">Steam</div>
      <div style="font-size:.72rem;color:#475569;text-transform:uppercase;letter-spacing:.06em">Data Source</div>
    </div>
    <div style="background:#1a2233;border:1px solid #2d3a52;border-radius:10px;padding:1rem 1.5rem;flex:1;min-width:140px;text-align:center">
      <div style="font-size:1.8rem;font-weight:900;color:#34d399">8</div>
      <div style="font-size:.72rem;color:#475569;text-transform:uppercase;letter-spacing:.06em">UX Categories</div>
    </div>
  </div>

  <!-- Recent -->
  <div style="margin-bottom:2.5rem">
    <div class="section-head" style="color:#00d4ff;border-color:#00d4ff">Recent Releases — High Review Volume</div>
    <div class="grid">{recent_cards}</div>
  </div>

  <!-- Older -->
  <div>
    <div class="section-head" style="color:#a78bfa;border-color:#a78bfa">Older Titles — Sparser Coverage</div>
    <div class="grid">{older_cards}</div>
  </div>

  <div style="margin-top:2rem;font-size:10px;color:#1e293b;text-align:center">
    Prototype built for competitive analysis of SprungSonar · Not for distribution
  </div>
</div>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    use_ai = "--no-ai" not in sys.argv
    mode   = "Claude Haiku (AI)" if use_ai else "Keyword matching (no-AI fallback)"

    results = []
    print(f"\n{'='*65}")
    print(f"  SONAR PROTOTYPE — {len(GAMES)} games")
    print(f"  Classification: {mode}")
    print(f"{'='*65}")

    for game in GAMES:
        print(f"\n▶ {game['name']}")
        print(f"  {'─'*50}")

        # Resolve App ID if missing
        if game["appid"] is None:
            print(f"  Looking up Steam App ID...")
            appid = search_steam_appid(game["name"])
            if appid:
                game["appid"] = appid
                print(f"  ✓ Found App ID: {appid}")
            else:
                print(f"  ✗ Could not find App ID — skipping.")
                continue

        # Game metadata
        print(f"  Fetching metadata (App ID {game['appid']})...")
        info = get_steam_game_info(game["appid"])
        if info.get("name"):
            print(f"  ✓ {info['name']} by {info.get('developer','?')}")
        else:
            print(f"  ⚠ Metadata incomplete, using defaults")
            info = {}

        # Steam reviews
        print(f"  Fetching Steam reviews...")
        reviews = fetch_steam_reviews(game["appid"], max_reviews=100)
        pos_count = sum(1 for r in reviews if r["positive"])
        print(f"  ✓ {len(reviews)} reviews ({pos_count} positive, {len(reviews)-pos_count} negative)")

        if not reviews:
            print(f"  ✗ No reviews — skipping.")
            continue

        # Classification
        label = "Claude Haiku" if use_ai else "keyword matching"
        print(f"  Classifying ({label})...")
        analysis = classify_reviews(game["name"], reviews, use_ai=use_ai)
        score    = analysis.get("overall_positive_pct", 0)
        print(f"  ✓ Score: {score}% positive")

        # Write report
        html     = render_game_html(game, info, reviews, analysis)
        out_path = OUT_DIR / f"{game['slug']}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"  ✓ Report: {out_path.name}")

        results.append({"game": game, "info": info, "analysis": analysis})
        time.sleep(0.5)   # polite delay between games

    # Index
    print(f"\n{'─'*65}")
    print(f"  Writing index...")
    (OUT_DIR / "index.html").write_text(render_index_html(results), encoding="utf-8")

    print(f"\n{'='*65}")
    print(f"  ✅ Done — {len(results)}/{len(GAMES)} games processed")
    print(f"  📂 Open: {OUT_DIR / 'index.html'}")
    print(f"{'='*65}\n")

if __name__ == "__main__":
    main()
