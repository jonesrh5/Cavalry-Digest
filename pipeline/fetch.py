"""
Fetchers for Google News RSS and NewsAPI, run once per pillar's topic list.

fetch_all() is pillar-aware: it tags each item with the set of pillar slugs
whose search queries surfaced it (usually one; China-overlap items may carry
two). That candidate set is consumed by pipeline/score.py to decide which
single pillar an item is ultimately filed under.

YouTube fetching is intentionally not implemented in this pass (descoped).
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
import trafilatura
import yaml

logging.getLogger("trafilatura").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "NewsDigestBot/1.0 (automated research digest)"

_PILLARS_DIR = Path(__file__).parent.parent / "config" / "pillars"

PREFERRED_SOURCES = [
    "reuters.com", "nytimes.com", "washingtonpost.com", "bbc.com", "bbc.co.uk",
    "apnews.com", "theguardian.com", "ft.com", "wsj.com", "bloomberg.com",
    "nbcnews.com", "abcnews.go.com", "cbsnews.com",
]

PREFERRED_SOURCE_NAMES = {
    "Reuters", "The New York Times", "NYT", "The Washington Post",
    "BBC News", "BBC", "AP News", "Associated Press",
    "The Guardian", "Financial Times", "The Wall Street Journal",
    "Bloomberg", "NBC News", "ABC News", "CBS News",
}

LOOKBACK_HOURS = 48  # hard filter — drop anything older than this


def load_pillars() -> list:
    """Reads config/pillars/*.yaml. Returns list of {slug, name, topics, definition}."""
    pillars = []
    for path in sorted(_PILLARS_DIR.glob("*.yaml")):
        with open(path) as f:
            cfg = yaml.safe_load(f)
        pillars.append(cfg)
    return pillars


def _strip_html(text: str) -> str:
    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, d):
            self.parts.append(d)
    p = _P()
    p.feed(text)
    return " ".join(p.parts).strip()


def _get(url: str, timeout: int = 15, **kwargs) -> requests.Response:
    time.sleep(0.25)
    return _SESSION.get(url, timeout=timeout, **kwargs)


def _extract_text(url: str, timeout: int = 15) -> str:
    """Pull article body via trafilatura for scoring/summarizing only — never persisted."""
    if "news.google.com" in url:
        return ""
    try:
        resp = _get(url, timeout=timeout)
        return trafilatura.extract(resp.text) or ""
    except Exception:
        return ""


def _parse_date(entry) -> str:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _is_recent(iso_date: str, hours: int = LOOKBACK_HOURS) -> bool:
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        pub = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return pub >= cutoff
    except Exception:
        return True


def _source_tier(source: str) -> int:
    return 0 if source in PREFERRED_SOURCE_NAMES else 1


def _build_gnews_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def _parse_feed(url: str, kind: str = "article", limit: int = 10) -> list:
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            link = getattr(entry, "link", "")
            snippet = getattr(entry, "summary", "")
            title = getattr(entry, "title", "").strip()
            if not link or not title:
                continue
            pub = _parse_date(entry)
            if not _is_recent(pub):
                continue
            text = _extract_text(link) or _strip_html(snippet)
            src_obj = getattr(entry, "source", {})
            source = src_obj.get("title", "Google News")
            source_href = src_obj.get("href", "")
            items.append({
                "title": title,
                "url": link,
                "source": source,
                "source_href": source_href,
                "published": pub,
                "text": text[:2000],   # in-memory only — never written to the DB
                "kind": kind,
            })
    except Exception as exc:
        logger.warning("Feed parse error for '%s': %s", url, exc)
    return items


# ── Google News RSS ──────────────────────────────────────────────────────────

def fetch_google_news(topics: list, timeout: int = 15) -> list:
    items = []
    outlet_query = " OR ".join(f"site:{d}" for d in PREFERRED_SOURCES)
    for topic in topics:
        query = f"{topic} ({outlet_query})"
        items.extend(_parse_feed(_build_gnews_url(query), limit=8))
    for topic in topics:
        items.extend(_parse_feed(_build_gnews_url(topic), limit=6))
    logger.info("Google News: %d items before dedup", len(items))
    return items


# ── NewsAPI ──────────────────────────────────────────────────────────────────

def fetch_newsapi(topics: list, timeout: int = 15) -> list:
    api_key = os.getenv("NEWSAPI_KEY", "").strip()
    if not api_key:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    preferred_domains = ",".join(d.replace("www.", "") for d in PREFERRED_SOURCES)
    items = []
    for topic in topics:
        try:
            resp = _get(
                "https://newsapi.org/v2/everything",
                timeout=timeout,
                params={
                    "q": topic,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "from": cutoff,
                    "domains": preferred_domains,
                    "pageSize": 10,
                    "apiKey": api_key,
                },
            )
            resp.raise_for_status()
            for art in resp.json().get("articles", []):
                url = art.get("url", "")
                title = (art.get("title") or "").strip()
                pub = art.get("publishedAt", datetime.now(timezone.utc).isoformat())
                if not url or not title or url == "[Removed]" or not _is_recent(pub):
                    continue
                text = _extract_text(url, timeout=timeout) or art.get("description") or art.get("content") or ""
                items.append({
                    "title": title,
                    "url": url,
                    "source": art.get("source", {}).get("name", "NewsAPI"),
                    "source_href": "",
                    "published": pub,
                    "text": text[:2000],
                    "kind": "article",
                })
        except Exception as exc:
            logger.warning("NewsAPI fetch failed for '%s': %s", topic, exc)
    return items


# ── Orchestrator (pillar-aware) ──────────────────────────────────────────────

def fetch_pillar(pillar: dict, timeout: int = 15) -> list:
    """Fetch all sources for a single pillar's topic list."""
    items = []
    for fetcher in (fetch_google_news, fetch_newsapi):
        try:
            items.extend(fetcher(pillar["topics"], timeout=timeout))
        except Exception as exc:
            logger.error("Fetcher %s crashed for pillar %s: %s", fetcher.__name__, pillar["slug"], exc)
    return items


def fetch_all(pillars: list, timeout: int = 15) -> list:
    """
    Runs every pillar's fetch, merges results by URL, and tags each item with
    candidate_pillars — the set of pillar slugs whose search surfaced it.
    """
    by_url: dict = {}
    for pillar in pillars:
        try:
            slug = pillar["slug"]
            for item in fetch_pillar(pillar, timeout=timeout):
                existing = by_url.get(item["url"])
                if existing:
                    existing["candidate_pillars"].add(slug)
                else:
                    item["candidate_pillars"] = {slug}
                    by_url[item["url"]] = item
        except Exception as exc:
            logger.error("Pillar %s crashed during fetch — skipping it this run: %s", pillar.get("slug", "?"), exc)

    deduped = list(by_url.values())
    deduped.sort(key=lambda x: (_source_tier(x["source"]), x.get("published", "")), reverse=True)
    deduped.sort(key=lambda x: _source_tier(x["source"]))

    logger.info("Total fetched across %d pillars: %d unique items", len(pillars), len(deduped))
    return deduped
