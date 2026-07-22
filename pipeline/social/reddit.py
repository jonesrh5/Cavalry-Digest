"""
Reddit social provider for the Social Pulse feed.

Auth: OAuth script-type app (read-only). Required env vars:
    REDDIT_CLIENT_ID      — from https://www.reddit.com/prefs/apps
    REDDIT_CLIENT_SECRET  — from the same app page
    REDDIT_USER_AGENT     — optional, defaults to CavalryNewsDigest/1.0

Reddit free-tier rate limits (OAuth script app, 2026):
    100 requests / minute — PRAW respects this automatically via rate-limit headers.
    Typical usage per pipeline run: ~35 requests
      (5 pillars × ~4 subreddit listings + ~3 searches each)
    At 2-hour intervals this is comfortably within free-tier limits.

Engagement/velocity ranking formula — tunable via module-level constants:
    velocity = (upvotes + COMMENT_WEIGHT × comments) × 2^(-age_hours / HALF_LIFE)
    COMMENT_WEIGHT = 3   (comments signal active discussion; weight more than passive upvotes)
    HALF_LIFE = 6 hours  (a post's effective score halves every 6 hours)
"""

import logging
import math
import os
from datetime import datetime, timedelta, timezone

from pipeline.social.base import SocialProvider

logger = logging.getLogger(__name__)

# ── Tunable ranking constants ──────────────────────────────────────────────────
_COMMENT_WEIGHT = 3      # comments count N× vs upvotes in velocity score
_HALF_LIFE_HOURS = 6     # effective score halves every N hours
_MAX_AGE_HOURS = 48      # drop posts older than this window
_MIN_SCORE = -5          # drop posts with score below this (heavily downvoted)
_MIN_TITLE_LEN = 12      # drop obviously stub/spam titles shorter than this


def _velocity_score(upvotes: int, comments: int, age_hours: float) -> float:
    """
    Engagement × recency score. Ranks a fast-rising 6-hour-old post above a
    stale high-total post from yesterday.
    Formula: (upvotes + COMMENT_WEIGHT * comments) * 2^(-age / HALF_LIFE)
    """
    recency = math.exp(-age_hours * math.log(2) / _HALF_LIFE_HOURS)
    return (upvotes + _COMMENT_WEIGHT * comments) * recency


def _is_clean(post) -> bool:
    """Return False for NSFW, removed, deleted, or spam-signal posts."""
    if post.over_18:
        return False
    if getattr(post, "removed_by_category", None):
        return False
    if post.selftext in ("[removed]", "[deleted]"):
        return False
    if post.author is None:
        return False
    if post.score < _MIN_SCORE:
        return False
    if len(post.title.strip()) < _MIN_TITLE_LEN:
        return False
    return True


def _post_to_item(post, pillar_slug: str, now: datetime) -> dict:
    age_hours = (
        now - datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
    ).total_seconds() / 3600
    sub = post.subreddit.display_name
    return {
        "post_id": post.id,
        "provider": "reddit",
        "pillar": pillar_slug,
        "title": post.title,
        "url": f"https://www.reddit.com{post.permalink}",
        "source": f"r/{sub}",
        "source_href": f"https://www.reddit.com/r/{sub}",
        "subreddit": sub,
        "score": post.score,
        "num_comments": post.num_comments,
        "velocity_score": _velocity_score(post.score, post.num_comments, age_hours),
        "text": (post.selftext or "")[:1000],  # transient; never persisted
        "published_at": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
        "fetched_at": now.isoformat(),
        "candidate_pillars": {pillar_slug},  # required by score_items
    }


class RedditProvider(SocialProvider):

    def __init__(self):
        self._client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
        self._client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
        self._user_agent = os.getenv(
            "REDDIT_USER_AGENT",
            "script:CavalryNewsDigest:1.0 (automated news monitor)",
        )
        self._reddit = None

    def available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _client(self):
        if self._reddit is None:
            import praw
            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
        return self._reddit

    def fetch_for_pillar(self, pillar_slug: str, pillar_config: dict) -> list:
        """
        Fetch candidate posts from subreddit hot listings and search terms.
        Returns deduplicated candidates sorted by velocity score descending.
        One failing subreddit or search term does not abort the rest.
        """
        reddit = self._client()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=_MAX_AGE_HOURS)
        seen_ids: set = set()
        candidates: list = []

        sources = pillar_config.get("reddit_sources", {})
        subreddits = sources.get("subreddits", [])
        terms = sources.get("terms", [])

        # ── Hot listings from each subreddit ─────────────────────────────────
        for sub_name in subreddits:
            try:
                for post in reddit.subreddit(sub_name).hot(limit=25):
                    if post.id in seen_ids:
                        continue
                    if not _is_clean(post):
                        logger.debug("Reddit: dropped r/%s post %s (clean check)", sub_name, post.id)
                        continue
                    created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                    if created < cutoff:
                        continue
                    seen_ids.add(post.id)
                    candidates.append(_post_to_item(post, pillar_slug, now))
            except Exception as exc:
                logger.warning("Reddit: r/%s hot listing failed for pillar %s: %s", sub_name, pillar_slug, exc)

        # ── Search terms across all of Reddit (last 24h) ──────────────────────
        for term in terms:
            try:
                for post in reddit.subreddit("all").search(
                    term, sort="hot", time_filter="day", limit=15
                ):
                    if post.id in seen_ids:
                        continue
                    if not _is_clean(post):
                        continue
                    seen_ids.add(post.id)
                    candidates.append(_post_to_item(post, pillar_slug, now))
            except Exception as exc:
                logger.warning("Reddit: search '%s' failed for pillar %s: %s", term, pillar_slug, exc)

        candidates.sort(key=lambda x: x["velocity_score"], reverse=True)
        logger.info("Reddit: fetched %d candidates for pillar %s", len(candidates), pillar_slug)
        return candidates
