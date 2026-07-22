"""
Abstract social provider interface.

Each provider (Reddit, X/Twitter, etc.) implements SocialProvider.
Drop in a new platform by creating a subclass — the orchestrator in run.py
calls the same interface regardless of provider.
"""

from abc import ABC, abstractmethod


class SocialProvider(ABC):

    @abstractmethod
    def available(self) -> bool:
        """Return False if credentials are missing; caller skips gracefully."""
        ...

    @abstractmethod
    def fetch_for_pillar(self, pillar_slug: str, pillar_config: dict) -> list:
        """
        Fetch candidate posts for one pillar.

        Returns a list of dicts with at minimum:
            post_id         str   — platform-unique ID (used for dedup)
            provider        str   — e.g. "reddit"
            pillar          str   — pillar_slug
            title           str   — post title
            url             str   — link to the discussion thread
            source          str   — display name for the community (e.g. "r/scams")
            source_href     str   — link to the community
            subreddit       str   — community identifier (or platform equivalent)
            score           int   — upvote/reaction count
            num_comments    int
            velocity_score  float — pre-computed engagement × recency score
            text            str   — post body, transient only — never persisted
            published_at    str   — ISO-8601 UTC
            fetched_at      str   — ISO-8601 UTC
            candidate_pillars set — {pillar_slug} (required by score_items)
        """
        ...
