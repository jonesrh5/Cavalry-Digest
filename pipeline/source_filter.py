"""
Source allowlist filter.

Resolution order for finding an article's true domain:
  1. source_href from the RSS <source> tag (publisher homepage — no HTTP needed)
  2. Follow the article URL redirect with a HEAD request (for edge cases)
  3. Fall back to the article URL itself

Caches resolved domains by URL so Google News redirect chains are only
followed once per unique article per run.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_ALLOWLIST_PATH = Path(__file__).parent.parent / "config" / "allowed_sources.txt"
_RESOLVE_TIMEOUT = 8
_GNEWS_PATTERN = re.compile(r"news\.google\.com", re.IGNORECASE)

_resolve_session = requests.Session()
_resolve_session.headers["User-Agent"] = "NewsDigestBot/1.0"
_resolve_session.max_redirects = 10


def _load_allowlist() -> frozenset:
    lines = _ALLOWLIST_PATH.read_text().splitlines()
    return frozenset(
        line.strip().lower()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    )


def _registrable_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        host = host.split(":")[0]
        return host.lstrip(".")
    except Exception:
        return ""


def _matches_allowlist(hostname: str, allowlist: frozenset) -> bool:
    if not hostname:
        return False
    clean = hostname.removeprefix("www.")
    for entry in allowlist:
        entry_clean = entry.removeprefix("www.")
        if clean == entry_clean or clean.endswith("." + entry_clean):
            return True
    return False


@lru_cache(maxsize=512)
def _resolve_redirect(url: str) -> str:
    try:
        resp = _resolve_session.head(url, allow_redirects=True, timeout=_RESOLVE_TIMEOUT)
        return resp.url
    except Exception:
        try:
            resp = _resolve_session.get(url, allow_redirects=True, timeout=_RESOLVE_TIMEOUT, stream=True)
            resp.close()
            return resp.url
        except Exception:
            return url


def _true_domain(item: dict) -> str:
    source_href = item.get("source_href", "")
    if source_href:
        domain = _registrable_domain(source_href)
        if domain and domain != "news.google.com":
            return domain

    url = item.get("url", "")

    if not _GNEWS_PATTERN.search(url):
        return _registrable_domain(url)

    resolved = _resolve_redirect(url)
    return _registrable_domain(resolved)


def apply_allowlist(items: list, enforce: bool = True) -> list:
    """Filter items to only those whose publisher domain is on the allowlist."""
    if not _ALLOWLIST_PATH.exists():
        logger.warning("allowed_sources.txt not found — skipping allowlist filter")
        return items

    allowlist = _load_allowlist()
    passed, dropped = [], []

    for item in items:
        domain = _true_domain(item)
        item["_resolved_domain"] = domain

        if _matches_allowlist(domain, allowlist):
            passed.append(item)
        else:
            dropped.append(item)
            logger.info(
                "ALLOWLIST DROP [domain=%s] %s | Source: %s",
                domain or "unknown", item["title"][:80], item["source"],
            )

    action = "filtered" if enforce else "would drop (allowlist not enforced)"
    logger.info(
        "Source allowlist: %d passed, %d %s",
        len(passed) if enforce else len(items), len(dropped), action,
    )

    return passed if enforce else items
