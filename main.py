import json
import os
import time
from typing import Any, Dict, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
BOT_CONFIG_JSON = os.getenv("BOT_CONFIG", "[]").strip()
TOP_TIME = os.getenv("TOP_TIME", "day").strip()
STATE_FILE = "state.json"
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "30"))
EMBEDS_PER_MESSAGE = 10
EMBED_COLOR_RED = 0xFF0000
USER_AGENT = "github-actions-universal-bot/1.0"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
# Safety Filter
BLOCKLIST_TERMS = {"loli", "lolicon", "shota", "shotacon", "underage", "minor", "kid", "child", "middle school", "elementary"}

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: Dict[str, Any]) -> None:
    for source_key in state:
        state[source_key] = state[source_key][-4000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_rss(url: str) -> List[Any]:
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
        return getattr(feed, "entries", []) or []
    except Exception:
        return []

def entry_uid(entry: Any) -> str:
    return getattr(entry, "id", None) or getattr(entry, "link", None) or getattr(entry, "title", "unknown")

def title_blocked(entry: Any) -> bool:
    title = (getattr(entry, "title", "") or "").strip().lower()
    if any(term in title for term in BLOCKLIST_TERMS):
        return True
    tags = []
    if hasattr(entry, "tags"):
        tags = [t.get("term", "").lower() for t in entry.tags]
    elif hasattr(entry, "media_keywords"):
        tags = [t.lower() for t in entry.media_keywords.split(",")]
    for tag in tags:
        if any(term in tag for term in BLOCKLIST_TERMS):
            return True
    return False

def normalize_url(u: str) -> str:
    return u.replace("&amp;", "&")

def extract_urls_from_html(html: str) -> List[str]:
    out: List[str] = []
    if not html: return out
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if src: out.append(src)
    for a in soup.find_all("a"):
        href = a.get("href")
        if href: out.append(href)
    return out

def guess_ext(url: str) -> str:
    u = url.split("?", 1)[0].split("#", 1)[0]
    return os.path.splitext(u)[1].lower()

def pick_media_url(entry: Any) -> Optional[str]:
    candidates: List[str] = []
    media = getattr(entry, "media_content", None)
    if media and isinstance(media, list):
        for m in media:
            if m.get("url"): candidates.append(m.get("url"))
    content_list = getattr(entry, "content", [])
    if content_list:
        for c in content_list:
            candidates += extract_urls_from_html(c.get("value", ""))
    summary = getattr(entry, "summary", None)
    if summary:
        candidates += extract_urls_from_html(summary)
    link = getattr(entry, "link", "")
    if link: candidates.append(link)

    seen = set()
    for u in candidates:
        u2 = normalize_url(u)
        if u2 in seen: continue
        seen.add(u2)
        if guess_ext(u2) in IMAGE_EXTS:
            return u2
    return None

def discord_post_embeds(webhook_url: str, embeds: List[Dict[str, Any]]) -> None:
    payload = {"content": "", "embeds": embeds, "allowed_mentions": {"parse": []}}
    while True:
        try:
            r = requests.post(webhook_url, json=payload, timeout=30)
            if r.status_code == 429:
                try: wait = float(r.json().get("retry_after", 1.0))
                except: wait = 1.0
                time.sleep(wait)
                continue
            r.raise_for_status()
            return
        except Exception as e:
            print(f"Error posting: {e}")
            return

def make_embed(url: str) -> Dict[str, Any]:
    return {"color": EMBED_COLOR_RED, "image": {"url": url}}

def process_feed(config: Dict[str, str], state: Dict[str, Any]):
    webhook = config.get("webhook")
    
    if config.get("subreddit"):
        sub = config["subreddit"]
        source_id = f"reddit_{sub}"
        feeds = [
            f"https://old.reddit.com/r/{sub}/new/.rss",
            f"https://old.reddit.com/r/{sub}/hot/.rss",
            f"https://old.reddit.com/r/{sub}/top/.rss?t={TOP_TIME}"
        ]
        print(f"--- Checking Reddit: r/{sub} ---")
    elif config.get("rss_url"):
        url = config["rss_url"]
        source_id = f"rss_{hash(url)}"
        feeds = [url]
        print(f"--- Checking RSS... ---")
    else:
        return

    if not webhook: return

    if source_id not in state:
        state[source_id] = []

    merged_media = {}

    for url in feeds:
        entries = fetch_rss(url)
        if "new" in url or "rss" in url:
            entries = list(entries)[::-1]

        for e in entries:
            uid = entry_uid(e)
            if uid in state[source_id]: continue
            if title_blocked(e): continue
                
            media = pick_media_url(e)
            if media:
                merged_media[uid] = media
                state[source_id].append(uid)

    items = list(merged_media.items())[:MAX_PER_RUN]
    embeds = []
    sent = 0

    for _, url in items:
        embeds.append(make_embed(url))
        if len(embeds) >= EMBEDS_PER_MESSAGE:
            discord_post_embeds(webhook, embeds)
            sent += len(embeds)
            embeds = []
            time.sleep(1)

    if embeds:
        discord_post_embeds(webhook, embeds)
        sent += len(embeds)
        
    print(f"Sent {sent} images.")

def main():
    try:
        config_list = json.loads(BOT_CONFIG_JSON)
    except:
        print("Error reading BOT_CONFIG")
        return

    state = load_state()
    
    for config in config_list:
        try:
            process_feed(config, state)
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")

    save_state(state)

if __name__ == "__main__":
    main()
