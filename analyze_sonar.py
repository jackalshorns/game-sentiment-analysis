#!/usr/bin/env python3
"""
Sonar Dual-Stream Pipeline
Press stream:  Gaming press articles (+ YouTube when re-enabled)
Player stream: Steam reviews + Reddit community boards
Reference:     Steam all-time review rating (total_positive / total_reviews)
Output:        Per-game HTML with Press Score, Player Score, Steam Reference + 26-category dual bars

Usage:
    python3 analyze_sonar.py
    python3 analyze_sonar.py --no-ai
    python3 analyze_sonar.py --game "Balatro"
"""

import os, sys, json, time, re, math, requests, urllib.parse
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

# Ensure yt-dlp is on PATH (local dev sessions add their own bin path)
_extra_bin = "/sessions/peaceful-gallant-albattani/.local/bin"
if os.path.isdir(_extra_bin):
    os.environ["PATH"] += f":{_extra_bin}"

# YouTube search deprecated — backlogged for re-enablement later
YT_BLOCKED = True

try:
    import anthropic
    import yt_dlp
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    sys.exit("pip install anthropic yt-dlp youtube-transcript-api")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY   = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("Error: ANTHROPIC_API_KEY environment variable is not set.\n  Set it with: export ANTHROPIC_API_KEY=sk-ant-...")
USE_AI    = "--no-ai" not in sys.argv
SOLO_GAME = next((sys.argv[sys.argv.index("--game")+1] for i,a in enumerate(sys.argv) if a=="--game"), None) if "--game" in sys.argv else None
TRANSCRIPTS_DIR = Path(sys.argv[sys.argv.index("--transcripts-dir")+1]) if "--transcripts-dir" in sys.argv else None

client  = anthropic.Anthropic(api_key=API_KEY)
OUT_DIR = Path(__file__).parent / "reports_sonar"
OUT_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 GameSentimentResearcher/1.0 (uxisfine.com)"}

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

# ── 26 Sonar-Equivalent Categories ───────────────────────────────────────────
CATS = [
    ("gameplay",          "Gameplay",                  "Core loop, game feel, fun factor"),
    ("combat",            "Combat & Action",           "Combat mechanics, weapon feel, enemy encounters"),
    ("controls",          "Controls & Input",          "Controller/KB responsiveness, key bindings, haptics"),
    ("ux_ui",             "UI Design",                 "Menus, HUD, information architecture"),
    ("accessibility",     "Accessibility",             "Disability support, subtitle options, colorblind modes"),
    ("onboarding",        "Onboarding & Tutorial",     "New player experience, in-game guidance, learning curve"),
    ("player_experience", "Player Experience",         "Immersion, emotional resonance, moment-to-moment feel"),
    ("narrative",         "Narrative & Writing",       "Story quality, dialogue, lore, character writing"),
    ("player_agency",     "Player Agency & Choice",    "Meaningful decisions, consequence systems, freedom"),
    ("pacing",            "Pacing & Structure",        "Game flow, chapter structure, downtime vs. action"),
    ("mechanics",         "Core Mechanics",            "Systems depth, mechanical novelty, loop design"),
    ("progression",       "Progression & Rewards",     "Leveling, unlocks, skill trees, sense of growth"),
    ("crafting_economy",  "Crafting & Economy",        "Crafting systems, in-game economy, resource loops"),
    ("replayability",     "Replayability",             "New game+, branching paths, randomization"),
    ("multiplayer",       "Multiplayer & Social",      "Co-op, PvP, social features, netcode"),
    ("world_design",      "World & Level Design",      "Map layout, exploration, environmental storytelling"),
    ("world_systems",     "World Systems & AI",        "NPC behavior, physics, emergent simulation"),
    ("content_depth",     "Content & Depth",           "Amount of content, side quests, hours of play"),
    ("technical",         "Technical Performance",     "FPS, crashes, bugs, load times"),
    ("visual_fidelity",   "Visual Fidelity",           "Graphics quality, resolution, effects"),
    ("art_direction",     "Art Direction",             "Art style, aesthetic cohesion, visual identity"),
    ("audio",             "Audio & Music",             "Soundtrack, sound design, voice acting"),
    ("monetization",      "Monetization",              "DLC, microtransactions, price value, live service"),
    ("platform",          "Platform & Port Quality",   "Console/PC differences, controller support, optimization"),
    ("updates_support",   "Updates & Developer Support","Patches, communication, post-launch content"),
    ("value",             "Value & Price",             "Price fairness, hours per dollar, overall value"),
]
CAT_KEYS  = [c[0] for c in CATS]
CAT_NAMES = {c[0]: c[1] for c in CATS}
CAT_DESC  = {c[0]: c[2] for c in CATS}

# ── AI Extraction ─────────────────────────────────────────────────────────────
CAT_LIST_STR = ", ".join(CAT_KEYS)

SYSTEM_PROMPT = f"""You are a senior game UX analyst replicating the methodology of Sonar by Sprung Studios.

Your job: extract every discrete player insight from a piece of game content (review, video transcript, forum post).

For each distinct insight, return a JSON object with:
- "cat": EXACTLY one of [{CAT_LIST_STR}]
- "sentiment": one of [positive, negative, mixed]
- "severity": one of [minor, notable, major] (how strongly players feel about this)
- "quote": verbatim excerpt, 1–2 sentences, under 220 chars

Rules:
- Extract ALL insights — if a source touches 8 categories, return 8 objects
- Prefer verbatim quotes over paraphrasing
- Never combine two insights into one object
- Minimum 1, maximum 20 insights per call
- Return ONLY a valid JSON array, no commentary"""

def ai_extract(text: str, source_label: str, stream: str = "press") -> list:
    if not text.strip() or len(text.split()) < 30:
        return []
    words = text.split()
    chunk = " ".join(words[:2000])
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role":"user","content":f"Analyze this content about a video game:\n\n{chunk}\n\nReturn JSON array only."}]
        )
        raw = msg.content[0].text.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            items = json.loads(m.group())
            valid = []
            for item in items:
                if isinstance(item,dict) and item.get("cat") in CAT_KEYS:
                    item["source"] = source_label
                    item["stream"] = stream
                    valid.append(item)
            return valid
    except Exception as e:
        print(f"    AI error: {e}")
    return []

def kw_extract(text: str, source_label: str, stream: str = "press") -> list:
    """Keyword fallback — maps sentences to 26 categories."""
    KW26 = {
        "gameplay":         ["gameplay","game feel","core loop","fun","mechanic","play"],
        "combat":           ["combat","fight","battle","attack","dodge","boss","weapon","enemy","kill"],
        "controls":         ["control","button","input","keybind","haptic","responsive","clunky","kb","mouse"],
        "ux_ui":            ["menu","ui ","hud","interface","inventory","icon","tooltip","pause","navigation","map"],
        "accessibility":    ["accessibility","colorblind","subtitle","caption","difficulty","assist","dyslexia","rebind"],
        "onboarding":       ["tutorial","onboard","learn","new player","beginning","start","explain","guide","intro"],
        "player_experience":["immersive","atmosphere","tension","feeling","engrossing","addictive","frustrat","satisfying","experience","engag"],
        "narrative":        ["story","narrative","dialogue","lore","writing","plot","character","cutscene","quest text"],
        "player_agency":    ["choice","decision","consequence","freedom","agency","branch","meaningful","path"],
        "pacing":           ["pacing","flow","chapter","structure","slow","fast","momentum","downtime"],
        "mechanics":        ["system","mechanic","depth","roguelike","roguelite","deck","card","combo","loop"],
        "progression":      ["level up","progression","upgrade","skill tree","unlock","perk","talent","build","growth"],
        "crafting_economy": ["craft","loot","economy","currency","resource","grind","recipe","material"],
        "replayability":    ["replay","ng+","new game plus","randomiz","branch","multiple run","hour","replayab"],
        "multiplayer":      ["multiplayer","co-op","coop","pvp","online","netcode","server","social","friend"],
        "world_design":     ["world design","level design","map","explore","area","zone","biome","open world","dungeon"],
        "world_systems":    ["npc"," ai ","physics","simulation","faction","ecosystem","emergent","behavior"],
        "content_depth":    ["content","side quest","hour","depth","completionist","optional","secrets","collectible"],
        "technical":        ["bug","crash","performance","fps","frame rate","lag","stutter","load time","glitch","optim"],
        "visual_fidelity":  ["graphic","resolution","fidelity","4k","hdr","texture","pop-in","visual quality","ray trac"],
        "art_direction":    ["art style","art direction","aesthetic","pixel art","visual design","animation","beautiful","gorgeous"],
        "audio":            ["music","soundtrack","sound design","voice acting","audio","score","ambient","sfx"],
        "monetization":     ["dlc","microtransaction","mtx","battle pass","season pass","price","paid","free to play","f2p","live service"],
        "platform":         ["console","pc","port","controller support","steam deck","switch","xbox","ps5","platform"],
        "updates_support":  ["update","patch","developer","support","post-launch","communication","roadmap","fix"],
        "value":            ["value","worth","price","cheap","expensive","bang for","money","hours per"],
    }
    sentences = re.split(r'(?<=[.!?])\s+', text)
    pos_w = ["great","good","excellent","amazing","love","best","perfect","fantastic","brilliant","enjoy","smooth","clean"]
    neg_w = ["bad","terrible","awful","hate","worst","broken","horrible","disappoint","frustrat","annoying","poor","clunky"]
    insights = []
    for sent in sentences:
        sl = sent.lower()
        for cat, kws in KW26.items():
            if any(kw in sl for kw in kws):
                pos = sum(1 for w in pos_w if w in sl)
                neg = sum(1 for w in neg_w if w in sl)
                sent_s = "positive" if pos>neg else ("negative" if neg>pos else "mixed")
                insights.append({"cat":cat,"sentiment":sent_s,"severity":"minor","quote":sent.strip()[:220],"source":source_label,"stream":stream})
                break
    return insights

def extract_long(text: str, source_label: str, stream: str = "press") -> list:
    """Chunk long text and extract from each chunk."""
    if not text.strip():
        return []
    words = text.split()
    all_ins = []
    chunk_sz = 1500
    chunks = [" ".join(words[i:i+chunk_sz]) for i in range(0, len(words), chunk_sz)]
    fn = ai_extract if USE_AI else kw_extract
    for i, chunk in enumerate(chunks[:5]):
        ins = fn(chunk, source_label, stream)
        all_ins.extend(ins)
        if USE_AI:
            time.sleep(0.3)
    return all_ins

def extract_short(text: str, source_label: str, stream: str = "press") -> list:
    fn = ai_extract if USE_AI else kw_extract
    return fn(text, source_label, stream)

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

def search_yt(game_name: str, query_suffix: str = "review", n: int = 10) -> list:
    global YT_BLOCKED
    if YT_BLOCKED:
        return []
    try:
        ydl_opts = {"quiet":True,"extract_flat":True,"default_search":f"ytsearch{n*2}","noplaylist":True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{n*2}:{game_name} {query_suffix}", download=False)
            vids = []
            for e in result.get("entries",[]):
                dur = e.get("duration") or 0
                if 240 <= dur <= 4500:
                    vids.append({"id":e["id"],"title":e.get("title",""),"duration":dur,"views":e.get("view_count",0)})
                if len(vids) >= n:
                    break
            return vids
    except Exception as e:
        print(f"    YT search error: {e}")
        return []

def get_transcript(vid_id: str) -> str:
    global YT_BLOCKED
    if YT_BLOCKED:
        return ""
    try:
        api = YouTubeTranscriptApi()
        snips = api.fetch(vid_id, languages=["en","en-US","en-GB"])
        return " ".join(s.text for s in snips).replace("\n"," ")
    except Exception as e:
        err = str(e)
        if "IpBlocked" in err or ("ip" in err.lower() and "block" in err.lower()):
            print(f"    YouTube IpBlocked — disabling YouTube for this run")
            YT_BLOCKED = True
        return ""

# ── Gaming Press Articles ─────────────────────────────────────────────────────
PRESS_SITES_KW = ["eurogamer","rockpapershotgun","pcgamer","ign.com","kotaku","polygon","gamespot"]
PRESS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def extract_article_text(html: str) -> str:
    """Strip HTML, prefer <article> tag, fall back to <p> tags."""
    m = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    body = m.group(1) if m else " ".join(re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL))
    body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
    body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'&[a-zA-Z#0-9]+;', ' ', body)
    body = re.sub(r'\s+', ' ', body).strip()
    return body

def fetch_press_articles(game_name: str, n: int = 5) -> list:
    """Fetch gaming press review text via DuckDuckGo HTML search + direct URL fetch."""
    articles = []
    seen_urls = set()
    game_first = game_name.lower().split()[0]

    # Strategy 1: direct URL patterns for known outlets
    slug = re.sub(r'[^a-z0-9]+', '-', game_name.lower()).strip('-')
    direct_candidates = [
        f"https://www.eurogamer.net/{slug}-review",
        f"https://www.rockpapershotgun.com/{slug}-review",
        f"https://www.pcgamer.com/{slug}-review",
    ]
    for url in direct_candidates:
        if len(articles) >= n:
            break
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            r = requests.get(url, headers=PRESS_HEADERS, timeout=12, allow_redirects=True)
            if r.status_code == 200:
                text = extract_article_text(r.text)
                if len(text.split()) >= 200 and game_first in text.lower():
                    articles.append({"title": slug + " review", "text": text[:6000], "url": url})
                    print(f"    Direct OK: {url.split('/')[-1][:50]}")
        except Exception:
            pass

    # Strategy 2: DuckDuckGo HTML search → extract uddg= URLs → fetch articles
    if len(articles) < n:
        try:
            q = urllib.parse.quote(f"{game_name} review 2024 OR 2023 OR 2022 OR 2025")
            r = requests.get(f"https://html.duckduckgo.com/html/?q={q}",
                             headers=PRESS_HEADERS, timeout=12)
            if r.status_code == 200:
                raw_enc = re.findall(r'uddg=(https?[^&"]+)', r.text)
                ddg_urls = [urllib.parse.unquote(u) for u in raw_enc]
                press_urls = []
                seen_dedup = set()
                for u in ddg_urls:
                    if any(site in u for site in PRESS_SITES_KW):
                        base = u.split("?")[0]
                        if base not in seen_dedup and base not in seen_urls:
                            seen_dedup.add(base)
                            press_urls.append(base)

                for url in press_urls[:12]:
                    if len(articles) >= n:
                        break
                    seen_urls.add(url)
                    try:
                        r2 = requests.get(url, headers=PRESS_HEADERS, timeout=12, allow_redirects=True)
                        if r2.status_code == 200:
                            text = extract_article_text(r2.text)
                            if len(text.split()) >= 200 and game_first in text.lower():
                                title = url.split("/")[-1].replace("-"," ")[:60]
                                articles.append({"title": title, "text": text[:6000], "url": url})
                                print(f"    DDG OK: {title[:50]}")
                        time.sleep(0.6)
                    except Exception:
                        pass
        except Exception as e:
            print(f"    DDG search error: {e}")

    print(f"  ▶ Press articles: {len(articles)} fetched")
    return articles[:n]

# ── OpenCritic ────────────────────────────────────────────────────────────────
OC_HEADERS = {
    "User-Agent": "Mozilla/5.0 GameSentimentResearcher/1.0 (uxisfine.com)",
    "Accept": "application/json",
}

def fetch_opencritic(game_name: str, n: int = 10) -> dict:
    """Fetch critic reviews from OpenCritic (free public API, no key required).
    Returns: {reviews, percent_recommended, top_critic_score}
    """
    try:
        # Step 1: Search for game
        r = requests.get("https://api.opencritic.com/api/game/search",
                         params={"criteria": game_name},
                         headers=OC_HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"    OpenCritic search failed: HTTP {r.status_code}")
            return {}
        results = r.json()
        if not results:
            print(f"    OpenCritic: no match for '{game_name}'")
            return {}
        game = results[0]
        game_id       = game.get("id")
        pct_rec       = game.get("percentRecommended")
        top_score     = game.get("topCriticScore")

        # Step 2: Fetch individual reviews
        time.sleep(0.5)
        r2 = requests.get("https://api.opencritic.com/api/review",
                          params={"game": game_id, "take": n},
                          headers=OC_HEADERS, timeout=10)
        if r2.status_code != 200:
            print(f"    OpenCritic reviews failed: HTTP {r2.status_code}")
            return {"percent_recommended": pct_rec, "top_critic_score": top_score, "reviews": []}

        reviews_raw = r2.json()
        reviews = []
        for rev in reviews_raw:
            snippet = rev.get("snippet", "")
            outlet  = ""
            if isinstance(rev.get("Outlet"), dict):
                outlet = rev["Outlet"].get("name", "")
            if snippet and len(snippet.strip()) > 30:
                reviews.append({"text": snippet.strip(), "outlet": outlet})

        print(f"  ▶ OpenCritic: {len(reviews)} reviews · {pct_rec}% recommended · top score {top_score}")
        return {
            "reviews":             reviews,
            "percent_recommended": pct_rec,
            "top_critic_score":    top_score,
        }
    except Exception as e:
        print(f"    OpenCritic error: {e}")
        return {}

# ── Steam Reviews (player stream) ─────────────────────────────────────────────
def fetch_steam_reviews(appid: int, max_r: int = 100) -> tuple:
    """Fetch recent Steam reviews + all-time summary stats.
    Returns: (reviews_list, query_summary_dict)
    query_summary has: total_positive, total_reviews, review_score_desc
    """
    reviews, cursor = [], "*"
    steam_summary = {}
    while len(reviews) < max_r:
        try:
            r = requests.get(f"https://store.steampowered.com/appreviews/{appid}",
                             params={"json":1,"language":"english","filter":"recent",
                                     "num_per_page":100,"cursor":cursor},
                             headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
            data = r.json()
            if not steam_summary:          # capture all-time stats from first page
                steam_summary = data.get("query_summary", {})
            batch = data.get("reviews", [])
            if not batch:
                break
            reviews += [b["review"] for b in batch if b.get("review","").strip()]
            cursor = data.get("cursor", "*")
            if cursor == "*" or not data.get("query_summary",{}).get("num_reviews"):
                break
            time.sleep(0.4)
        except Exception as e:
            print(f"    Steam error: {e}")
            break
    return reviews[:max_r], steam_summary

# ── Reddit (player stream) ────────────────────────────────────────────────────
def fetch_reddit(subreddit: str, game_name: str, n: int = 12) -> list:
    """Fetch posts from game subreddit + r/games + r/gaming (broader player signal).
    Tries api.reddit.com (official API, different rate-limit pool) before www.reddit.com.
    """
    posts = []
    game_q = quote(game_name)
    # api.reddit.com has a separate rate-limit pool and often works from cloud IPs
    urls = [
        f"https://api.reddit.com/r/{subreddit}/top.json?t=year&limit={n}",
        f"https://api.reddit.com/r/{subreddit}/search.json?q=review+feedback+thoughts&sort=top&t=year&limit=8",
        f"https://api.reddit.com/r/games/search.json?q={game_q}&sort=top&t=year&limit=5",
        f"https://api.reddit.com/r/gaming/search.json?q={game_q}&sort=top&t=year&limit=5",
    ]
    seen = set()
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent":"GameSentimentBot/1.0 (research)"}, timeout=10)
            if r.status_code != 200:
                print(f"    Reddit HTTP {r.status_code}: ...{url[-55:]}")
                time.sleep(0.6)
                continue
            children = r.json().get("data",{}).get("children",[])
            for c in children:
                d = c["data"]
                title = d.get("title","")
                text  = (d.get("selftext","") or "").strip()
                if title not in seen and (len(text) > 50 or len(title) > 40):
                    seen.add(title)
                    posts.append({
                        "title": title,
                        "text":  f"{title}. {text}"[:4000],
                        "score": d.get("score",0),
                    })
            time.sleep(0.6)
        except Exception as e:
            print(f"    Reddit error: {e}")
    posts.sort(key=lambda x: x["score"], reverse=True)
    print(f"  ▶ Reddit: {min(len(posts),n)} posts (r/{subreddit} + r/games + r/gaming)")
    return posts[:n]

# ── Aggregate ─────────────────────────────────────────────────────────────────
def aggregate(insights: list) -> dict:
    agg = {k: {"pos":0,"neg":0,"mixed":0,"quotes":[],"major":0} for k in CAT_KEYS}
    for ins in insights:
        cat = ins.get("cat")
        if cat not in agg:
            continue
        s = ins.get("sentiment","mixed")
        if s == "positive":   agg[cat]["pos"]   += 1
        elif s == "negative": agg[cat]["neg"]   += 1
        else:                 agg[cat]["mixed"] += 1
        if ins.get("severity") == "major":
            agg[cat]["major"] += 1
        q = ins.get("quote","").strip()
        if q and len(agg[cat]["quotes"]) < 3:
            agg[cat]["quotes"].append({
                "text":      q[:220],
                "sentiment": s,
                "stream":    ins.get("stream","press"),
            })
    return agg

def pct_from_agg(agg: dict) -> int:
    """Compute overall positive % from an aggregate dict."""
    tot_p = sum(a["pos"]+a["mixed"] for a in agg.values())
    tot_n = sum(a["neg"] for a in agg.values())
    tot   = tot_p + tot_n
    return round(tot_p/tot*100) if tot else 0

def aggregate_by_stream(insights: list) -> dict:
    """Partition insights by stream, aggregate each partition separately."""
    press_ins  = [i for i in insights if i.get("stream") == "press"]
    player_ins = [i for i in insights if i.get("stream") == "player"]
    return {
        "press":        aggregate(press_ins),
        "player":       aggregate(player_ins),
        "combined":     aggregate(insights),
        "press_count":  len(press_ins),
        "player_count": len(player_ins),
    }

# ── Steam Metadata ────────────────────────────────────────────────────────────
def get_steam_info(appid: int) -> dict:
    try:
        r = requests.get("https://store.steampowered.com/api/appdetails",
                         params={"appids":appid,"l":"english"}, timeout=10)
        d = r.json().get(str(appid),{})
        if d.get("success"):
            dd = d["data"]
            return {
                "name":         dd.get("name",""),
                "developer":    ", ".join(dd.get("developers",[])),
                "release_date": dd.get("release_date",{}).get("date",""),
                "header_image": dd.get("header_image",""),
                "genres":       [g["description"] for g in dd.get("genres",[])],
            }
    except:
        pass
    return {}

# ── HTML ──────────────────────────────────────────────────────────────────────
def color(pct):
    if pct >= 75: return "#00d4ff"
    if pct >= 50: return "#fbbf24"
    return "#f87171"

def score_panel(emoji, label, pct_val, clr, subtitle="", note=""):
    """Render one score panel (press, player, or reference)."""
    if isinstance(pct_val, int):
        display = f"{pct_val}%"
        bar = f'<div style="height:4px;background:#1e293b;border-radius:2px;margin-top:8px"><div style="width:{pct_val}%;height:100%;background:{clr};border-radius:2px"></div></div>'
    else:
        display = pct_val or "—"
        bar = ""
    sub_html  = f'<div style="font-size:10px;color:#64748b;margin-top:4px">{subtitle}</div>' if subtitle else ""
    note_html = f'<div style="font-size:10px;color:#475569;margin-top:4px">{note}</div>'      if note    else ""
    return (
        f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px;text-align:center">'
        f'<div style="font-size:18px;margin-bottom:4px">{emoji}</div>'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#475569;margin-bottom:6px">{label}</div>'
        f'<div style="font-size:2rem;font-weight:800;color:{clr};line-height:1">{display}</div>'
        f'{sub_html}{bar}{note_html}</div>'
    )

def render_game(game, info, all_ins, streams, src_summary, scores):
    name   = info.get("name", game["name"])
    dev    = info.get("developer","Unknown")
    rel    = info.get("release_date", str(game["year"]))
    n_ins  = len(all_ins)
    n_src  = src_summary["total_sources"]

    press_pct    = scores.get("press_pct", 0)
    player_pct   = scores.get("player_pct", 0)
    combined_pct = scores.get("combined_pct", 0)
    steam_ref    = scores.get("steam_ref_pct")
    steam_desc   = scores.get("steam_ref_desc", "")
    press_count  = streams.get("press_count", 0)
    player_count = streams.get("player_count", 0)

    press_c    = color(press_pct)
    player_c   = "#4ade80"  # green for player
    ref_c      = "#a78bfa"  # purple for reference
    combined_c = color(combined_pct)

    label = ("Overwhelmingly Positive" if combined_pct>=90 else
             "Very Positive"           if combined_pct>=80 else
             "Mostly Positive"         if combined_pct>=70 else
             "Mixed"                   if combined_pct>=50 else "Negative")

    ref_display = steam_ref if isinstance(steam_ref, int) else None

    panels_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">'
        + score_panel("🗞️", "Press Score",    press_pct,   press_c,  f"{press_count} insights",  "press articles")
        + score_panel("👥", "Player Score",   player_pct,  player_c, f"{player_count} insights", "Steam + Reddit")
        + score_panel("📊", "Steam All-Time", ref_display, ref_c,    "all-time review rating",   steam_desc)
        + '</div>'
    )

    summary_html = (
        f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px;margin-bottom:16px;text-align:center">'
        f'<div style="font-size:1rem;font-weight:700;color:#f1f5f9">{label}</div>'
        f'<div style="color:#64748b;font-size:13px;margin:6px 0">'
        f'<strong style="color:#818cf8;font-size:1.3rem">{n_ins}</strong> total insights from '
        f'<strong style="color:#818cf8">{n_src}</strong> sources</div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-top:10px">'
        f'<span style="font-size:11px;color:#64748b;white-space:nowrap">{combined_pct}% combined</span>'
        f'<div style="flex:1;height:8px;background:#1e293b;border-radius:4px;overflow:hidden">'
        f'<div style="width:{combined_pct}%;height:100%;background:{combined_c};border-radius:4px"></div>'
        f'</div></div></div>'
    )

    # Per-category blocks with dual bars
    agg_press    = streams["press"]
    agg_player   = streams["player"]
    agg_combined = streams["combined"]

    active_cats = [(k,v) for k,v in agg_combined.items() if v["pos"]+v["neg"]+v["mixed"]>0]
    active_cats.sort(key=lambda x: x[1]["pos"]+x[1]["neg"]+x[1]["mixed"], reverse=True)

    cats_html = ""
    for k, a_comb in active_cats:
        t = a_comb["pos"]+a_comb["neg"]+a_comb["mixed"]

        # Press numbers
        a_p  = agg_press[k]
        pt   = a_p["pos"]+a_p["neg"]+a_p["mixed"]
        p_pct = round((a_p["pos"]+a_p["mixed"])/pt*100) if pt else None

        # Player numbers
        a_pl  = agg_player[k]
        plt   = a_pl["pos"]+a_pl["neg"]+a_pl["mixed"]
        pl_pct = round((a_pl["pos"]+a_pl["mixed"])/plt*100) if plt else None

        # Divergence badge (gap > 15 pts)
        div_badge = ""
        if p_pct is not None and pl_pct is not None and abs(p_pct - pl_pct) > 15:
            diff = p_pct - pl_pct
            if diff > 0:
                div_badge = f'<span style="font-size:9px;color:#60a5fa;background:#0a1628;border:1px solid #1e3a5f;padding:1px 5px;border-radius:8px;margin-left:6px">Press +{diff}pts</span>'
            else:
                div_badge = f'<span style="font-size:9px;color:#4ade80;background:#071e1a;border:1px solid #14532d;padding:1px 5px;border-radius:8px;margin-left:6px">Players +{-diff}pts</span>'

        major_badge = (f'<span style="font-size:9px;color:#f87171;background:#1f0a0a;border:1px solid #7f1d1d;'
                       f'padding:1px 5px;border-radius:8px;margin-left:6px">{a_comb["major"]} major</span>'
                       if a_comb["major"] > 0 else "")

        if pt > 0:
            press_bar = (
                f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">'
                f'<span style="font-size:9px;color:#60a5fa;width:44px;text-align:right">🗞️ {p_pct}%</span>'
                f'<div style="flex:1;height:6px;background:#1e293b;border-radius:3px;overflow:hidden">'
                f'<div style="width:{p_pct}%;height:100%;background:#3b82f6;border-radius:3px"></div></div>'
                f'<span style="font-size:9px;color:#475569;width:36px">{pt} ins.</span></div>'
            )
        else:
            press_bar = '<div style="font-size:9px;color:#334155;margin-bottom:3px;padding-left:4px">🗞️ No press data</div>'

        if plt > 0:
            player_bar = (
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-size:9px;color:#4ade80;width:44px;text-align:right">👥 {pl_pct}%</span>'
                f'<div style="flex:1;height:6px;background:#1e293b;border-radius:3px;overflow:hidden">'
                f'<div style="width:{pl_pct}%;height:100%;background:#22c55e;border-radius:3px"></div></div>'
                f'<span style="font-size:9px;color:#475569;width:36px">{plt} ins.</span></div>'
            )
        else:
            player_bar = '<div style="font-size:9px;color:#334155;padding-left:4px">👥 No player data</div>'

        qhtml = ""
        for q in a_comb.get("quotes", [])[:2]:
            qc = "#4ade80" if q["sentiment"]=="positive" else "#f87171" if q["sentiment"]=="negative" else "#fbbf24"
            stag = "🗞️" if q.get("stream") == "press" else "👥"
            qhtml += (f'<div style="font-size:11px;color:#94a3b8;border-left:2px solid {qc};'
                      f'padding-left:8px;margin:5px 0;font-style:italic">{stag} &ldquo;{q["text"]}&rdquo;</div>')

        cats_html += (
            f'<div style="padding:14px 0;border-bottom:1px solid #1e293b">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<span style="font-weight:700;color:#cbd5e1;font-size:.88rem">{CAT_NAMES[k]}{div_badge}{major_badge}</span>'
            f'<span style="font-size:11px;color:#334155">{t} total</span></div>'
            f'{press_bar}{player_bar}{qhtml}</div>'
        )

    # Source breakdown
    src_html = ""
    for s in src_summary["breakdown"]:
        stream_val = s.get("stream", "")
        if stream_val == "press":
            badge = '<span style="font-size:9px;color:#60a5fa;background:#0a1628;border:1px solid #1e3a5f;padding:1px 5px;border-radius:8px;margin-left:4px">press</span>'
        elif stream_val == "player":
            badge = '<span style="font-size:9px;color:#4ade80;background:#071e1a;border:1px solid #14532d;padding:1px 5px;border-radius:8px;margin-left:4px">player</span>'
        else:
            badge = ""
        src_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0">'
            f'<span style="color:#94a3b8;font-size:12px">{s["type"]}{badge}</span>'
            f'<span style="font-size:12px;color:#64748b">{s["count"]} docs · {s["insights"]} insights</span></div>'
        )

    cats_covered = len(active_cats)
    method = "AI (Claude Haiku)" if USE_AI else "KEYWORD fallback"

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>{name} — Dual-Stream Sonar</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0f1a;color:#e2e8f0;line-height:1.6}}.c{{max-width:800px;margin:0 auto;padding:32px 20px 60px}}</style>
</head>
<body><div class="c">
  <a href="index.html" style="color:#475569;font-size:12px;text-decoration:none">← All games</a>
  <h1 style="font-size:1.6rem;font-weight:800;color:#f1f5f9;margin:16px 0 4px">{name}</h1>
  <div style="color:#64748b;font-size:13px;margin-bottom:20px">{dev} · {rel}</div>

  {panels_html}
  {summary_html}

  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:16px;margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#475569;margin-bottom:10px">Sources Used</div>
    {src_html}
  </div>

  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px">
    <div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#475569;margin-bottom:4px">
      26-Category Analysis <span style="font-weight:400;text-transform:none;letter-spacing:0">(showing {cats_covered} with data)</span>
    </div>
    <div style="font-size:10px;color:#475569;margin-bottom:12px">🗞️ Blue = Press · 👥 Green = Player · Badge = divergence &gt;15 pts</div>
    {cats_html}
  </div>

  <div style="text-align:center;color:#334155;font-size:11px;margin-top:20px">
    Generated {datetime.now().strftime('%B %d, %Y')} · Press: articles · Player: Steam + Reddit · {method}
  </div>
</div></body></html>"""

def render_index(results: list) -> str:
    cards = ""
    for r in sorted(results, key=lambda x: x["combined_pct"], reverse=True):
        pp     = r["press_pct"]
        plp    = r["player_pct"]
        ref    = r.get("steam_ref_pct")
        ref_str = f'{ref}%' if isinstance(ref, int) else '—'
        cards += (
            f'<a href="{r["slug"]}.html" style="display:block;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:18px;text-decoration:none;color:inherit">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px">'
            f'<div><div style="font-weight:700;color:#f1f5f9">{r["name"]}</div>'
            f'<div style="font-size:11px;color:#475569">{r["dev"]} · {r["era"]}</div></div>'
            f'<span style="font-size:10px;background:#0a1628;color:#60a5fa;border:1px solid #1e3a5f;padding:2px 8px;border-radius:10px;align-self:flex-start">Dual-stream</span></div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px">'
            f'<div style="text-align:center"><div style="font-size:1.2rem;font-weight:800;color:{color(pp)}">{pp}%</div><div style="font-size:9px;color:#475569">🗞️ Press</div></div>'
            f'<div style="text-align:center"><div style="font-size:1.2rem;font-weight:800;color:#4ade80">{plp}%</div><div style="font-size:9px;color:#475569">👥 Players</div></div>'
            f'<div style="text-align:center"><div style="font-size:1.2rem;font-weight:800;color:#a78bfa">{ref_str}</div><div style="font-size:9px;color:#475569">📊 Steam</div></div>'
            f'</div>'
            f'<div style="font-size:11px;color:#475569"><strong style="color:#818cf8">{r["n_insights"]}</strong> insights from <strong style="color:#818cf8">{r["n_sources"]}</strong> sources · {r["cats_covered"]} categories</div>'
            f'</a>'
        )

    avg = round(sum(r["combined_pct"] for r in results) / len(results)) if results else 0
    tot = sum(r["n_insights"] for r in results)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Sonar Dual-Stream — 10 Games</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:system-ui,sans-serif;background:#0a0f1a;color:#e2e8f0}}
.hero{{background:linear-gradient(135deg,#0a0f1a,#0d0b1a);padding:40px 24px 32px;text-align:center;border-bottom:1px solid #1e293b}}
.grid{{max-width:900px;margin:32px auto;padding:0 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
.s{{display:inline-block;background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:12px 20px;margin:4px}}</style></head>
<body>
<div class="hero">
  <div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#4f46e5;margin-bottom:8px">Dual-Stream Methodology</div>
  <h1 style="font-size:1.8rem;font-weight:800;color:#f1f5f9;margin-bottom:4px">Sonar — Press &amp; Player Analysis</h1>
  <p style="color:#64748b;font-size:.85rem;margin-bottom:16px">🗞️ Press (Articles) · 👥 Players (Steam + Reddit) · 📊 Steam All-Time · 26 Categories</p>
  <div>
    <span class="s"><span style="font-size:1.4rem;font-weight:800;color:#00d4ff">{avg}%</span><br><span style="font-size:10px;color:#475569">Avg Combined</span></span>
    <span class="s"><span style="font-size:1.4rem;font-weight:800;color:#818cf8">{tot:,}</span><br><span style="font-size:10px;color:#475569">Total Insights</span></span>
    <span class="s"><span style="font-size:1.4rem;font-weight:800;color:#4ade80">26</span><br><span style="font-size:10px;color:#475569">Categories</span></span>
  </div>
</div>
<div class="grid">{cards}</div>
<div style="text-align:center;color:#334155;font-size:11px;padding:20px">
  Generated {datetime.now().strftime('%B %d, %Y')} · Dual-stream: Press (Articles) · Player (Steam + Reddit) · {'AI' if USE_AI else 'KEYWORD'}
</div>
</body></html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def process_game(game: dict) -> dict:
    name = game["name"]
    print(f"\n{'='*55}")
    print(f"  {name.upper()} — DUAL-STREAM SONAR")
    print(f"{'='*55}")

    info        = get_steam_info(game["appid"])
    all_insights = []
    src_summary  = {"total_sources":0,"breakdown":[]}
    oc_data      = {}

    # ── PRESS STREAM ──────────────────────────────────────────────────────────

    # YouTube (press) — cached only; live fetch disabled
    cached   = load_cached_transcripts(game["slug"])
    all_vids = cached  # may be empty list
    yt_insights = []
    if cached:
        print(f"  ► YouTube: using transcript cache ({len(cached)} videos)...")
        for v in cached:
            transcript = v.get("transcript", "")
            if len(transcript.split()) < 100:
                print(f"    Skip (short): {v['title'][:40]}")
                continue
            label = f"YouTube: {v['title'][:55]}"
            print(f"    {v['title'][:50]} ({len(transcript.split())} words)")
            ins = extract_long(transcript, label, stream="press")
            yt_insights.extend(ins)
            time.sleep(0.3)
    else:
        print(f"  ► YouTube: SKIPPED (YT_BLOCKED=True; no cache found)")
        # Live fetch path (runs if YT_BLOCKED is later set to False)
        vids_review = search_yt(name, "review", n=7)
        vids_deep   = search_yt(name, "analysis deep dive critique", n=3)
        all_vids    = list({v["id"]:v for v in vids_review+vids_deep}.values())[:10]
        for v in all_vids:
            transcript = get_transcript(v["id"])
            if len(transcript.split()) < 100:
                continue
            label = f"YouTube: {v['title'][:55]}"
            ins = extract_long(transcript, label, stream="press")
            yt_insights.extend(ins)
            time.sleep(0.5)

    print(f"    YouTube → {len(yt_insights)} insights from {len(all_vids)} videos")
    all_insights.extend(yt_insights)
    src_summary["breakdown"].append({"type":"YouTube Videos","count":len(all_vids),"insights":len(yt_insights),"stream":"press"})
    src_summary["total_sources"] += len(all_vids)

    # Gaming press articles
    print(f"  ► Gaming press articles...")
    articles       = fetch_press_articles(name, n=5)
    press_insights = []
    for art in articles:
        label = f"Press: {art['title'][:50]}"
        ins = extract_long(art["text"], label, stream="press")
        press_insights.extend(ins)
        time.sleep(0.3)
    print(f"    Press → {len(press_insights)} insights from {len(articles)} articles")
    all_insights.extend(press_insights)
    src_summary["breakdown"].append({"type":"Gaming Press Articles","count":len(articles),"insights":len(press_insights),"stream":"press"})
    src_summary["total_sources"] += len(articles)

    # OpenCritic critic reviews
    print(f"  ► OpenCritic critic reviews...")
    oc_data     = fetch_opencritic(name, n=10)
    oc_insights = []
    for rev in oc_data.get("reviews", []):
        if rev.get("text") and len(rev["text"].strip()) > 30:
            outlet = rev.get("outlet") or "OpenCritic"
            ins = extract_short(rev["text"], f"OpenCritic: {outlet}", stream="press")
            oc_insights.extend(ins)
    n_oc = len(oc_data.get("reviews", []))
    print(f"    OpenCritic → {len(oc_insights)} insights from {n_oc} reviews")
    all_insights.extend(oc_insights)
    src_summary["breakdown"].append({"type":"OpenCritic Reviews","count":n_oc,"insights":len(oc_insights),"stream":"press"})
    src_summary["total_sources"] += n_oc

    # ── PLAYER STREAM ─────────────────────────────────────────────────────────

    # Steam reviews + all-time summary (used as reference score)
    print(f"  ► Steam reviews (up to 100)...")
    steam_revs, steam_summary = fetch_steam_reviews(game["appid"], max_r=100)
    steam_total_pos  = steam_summary.get("total_positive", 0)
    steam_total_rev  = steam_summary.get("total_reviews", 0)
    steam_ref_pct    = round(steam_total_pos / steam_total_rev * 100) if steam_total_rev else None
    steam_ref_desc   = steam_summary.get("review_score_desc", "")
    if steam_ref_pct is not None:
        print(f"    Steam all-time: {steam_ref_pct}% positive ({steam_total_rev:,} reviews) — {steam_ref_desc}")
    steam_insights = []
    if USE_AI:
        batch_size = 10
        for i in range(0, min(len(steam_revs), 100), batch_size):
            batch_text = "\n---\n".join(steam_revs[i:i+batch_size])
            ins = ai_extract(batch_text, "Steam Review", stream="player")
            steam_insights.extend(ins)
            time.sleep(0.2)
    else:
        for rev in steam_revs:
            steam_insights.extend(kw_extract(rev, "Steam Review", stream="player"))
    print(f"    Steam → {len(steam_insights)} insights from {len(steam_revs)} reviews")
    all_insights.extend(steam_insights)
    src_summary["breakdown"].append({"type":"Steam Reviews","count":len(steam_revs),"insights":len(steam_insights),"stream":"player"})
    src_summary["total_sources"] += len(steam_revs)

    # Reddit community boards
    print(f"  ► Reddit community boards...")
    reddit_posts    = fetch_reddit(game["subreddit"], name, n=12)
    reddit_insights = []
    for post in reddit_posts:
        ins = extract_short(post["text"], f"Reddit: r/{game['subreddit']}", stream="player")
        reddit_insights.extend(ins)
        time.sleep(0.3)
    print(f"    Reddit → {len(reddit_insights)} insights from {len(reddit_posts)} posts")
    all_insights.extend(reddit_insights)
    src_summary["breakdown"].append({"type":"Reddit Community","count":len(reddit_posts),"insights":len(reddit_insights),"stream":"player"})
    src_summary["total_sources"] += len(reddit_posts)

    # ── Aggregate + render ────────────────────────────────────────────────────
    streams      = aggregate_by_stream(all_insights)
    press_pct    = pct_from_agg(streams["press"])
    player_pct   = pct_from_agg(streams["player"])
    combined_pct = pct_from_agg(streams["combined"])

    scores = {
        "press_pct":      press_pct,
        "player_pct":     player_pct,
        "combined_pct":   combined_pct,
        "steam_ref_pct":  steam_ref_pct,
        "steam_ref_desc": steam_ref_desc,
    }

    cats_covered = sum(1 for k,v in streams["combined"].items() if v["pos"]+v["neg"]+v["mixed"]>0)

    html = render_game(game, info, all_insights, streams, src_summary, scores)
    (OUT_DIR / f"{game['slug']}.html").write_text(html, encoding="utf-8")
    print(f"  ✓ {name}: press={press_pct}% player={player_pct}% combined={combined_pct}% "
          f"· {len(all_insights)} insights · {src_summary['total_sources']} sources · {cats_covered}/26 cats")

    return {
        "name":          name,
        "slug":          game["slug"],
        "era":           game["era"],
        "dev":           info.get("developer",""),
        "press_pct":     press_pct,
        "player_pct":    player_pct,
        "combined_pct":  combined_pct,
        "steam_ref_pct": steam_ref_pct,
        "n_insights":    len(all_insights),
        "n_sources":     src_summary["total_sources"],
        "cats_covered":  cats_covered,
    }

def main():
    print(f"\n🔬 Sonar Dual-Stream Pipeline")
    print(f"   Mode:          {'AI (Claude Haiku)' if USE_AI else 'KEYWORD fallback'}")
    print(f"   Press stream:  YouTube (cached) + Gaming Press Articles")
    print(f"   Player stream: Steam Reviews (×100) + Reddit")
    print(f"   Reference:     Steam all-time review rating")
    print(f"   Categories:    26 (matching Sonar framework)")

    games = GAMES
    if SOLO_GAME:
        games = [g for g in GAMES if SOLO_GAME.lower() in g["name"].lower()]
        if not games:
            sys.exit(f"Game '{SOLO_GAME}' not found")

    results = []
    for game in games:
        try:
            result = process_game(game)
            results.append(result)
        except Exception as e:
            print(f"  ✗ {game['name']}: {e}")
            import traceback; traceback.print_exc()

    (OUT_DIR / "index.html").write_text(render_index(results), encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"  DONE — {len(results)}/10 games")
    for r in sorted(results, key=lambda x: x["combined_pct"], reverse=True):
        ref = f"{r['steam_ref_pct']:3}%" if isinstance(r.get("steam_ref_pct"), int) else "  — "
        print(f"  press={r['press_pct']:3}% player={r['player_pct']:3}% combined={r['combined_pct']:3}% steam={ref}"
              f"  {r['n_insights']:4} ins  {r['cats_covered']}/26 cats  {r['name']}")
    print(f"  Reports: {OUT_DIR}/index.html")

if __name__ == "__main__":
    main()
