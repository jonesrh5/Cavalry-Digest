"""
SQLite-backed content store. Replaces the old dedup-only seen_items table —
this is now the canonical store the static site reads from.

Schema is deliberately plain ANSI SQL with parameterized (?) placeholders so
swapping to Postgres later only means changing get_connection() and the
autoincrement/datetime() syntax, not the call sites.

Copyright-critical: there is no body-text column. Article text is only ever
held in memory during fetch/score/summarize and is never written here, so the
site can never render scraped article bodies even by accident.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from thefuzz import fuzz

DB_PATH = Path(__file__).parent.parent / "data" / "digest.db"

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source",
}
FUZZY_THRESHOLD = 85  # titles above this ratio are considered duplicates


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(filtered, doseq=True)
    return urlunparse(parsed._replace(query=clean_query, fragment=""))


class Store:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                url                 TEXT NOT NULL UNIQUE,
                normalized_url      TEXT NOT NULL,
                title               TEXT NOT NULL,
                source              TEXT NOT NULL,
                source_href         TEXT,
                published_at        TEXT NOT NULL,
                kind                TEXT NOT NULL DEFAULT 'article',
                pillar              TEXT NOT NULL,
                score               INTEGER,
                score_reason        TEXT,
                high_significance   INTEGER NOT NULL DEFAULT 0,
                summary             TEXT,
                fetched_at          TEXT NOT NULL,
                created_at          TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_pillar_published "
            "ON items(pillar, published_at DESC)"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS social_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id         TEXT NOT NULL,
                provider        TEXT NOT NULL DEFAULT 'reddit',
                pillar          TEXT NOT NULL,
                title           TEXT NOT NULL,
                url             TEXT NOT NULL,
                subreddit       TEXT NOT NULL,
                score           INTEGER NOT NULL DEFAULT 0,
                num_comments    INTEGER NOT NULL DEFAULT 0,
                velocity_score  REAL NOT NULL DEFAULT 0.0,
                summary         TEXT,
                published_at    TEXT NOT NULL,
                fetched_at      TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(post_id, provider)
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_pillar_velocity "
            "ON social_items(pillar, velocity_score DESC)"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.commit()

    # ── Dedup ────────────────────────────────────────────────────────────────

    def _recent_titles(self, hours: int = 48) -> list:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT title FROM items WHERE fetched_at >= ?", (cutoff,)
        ).fetchall()
        return [r[0] for r in rows]

    def is_new(self, url: str, title: str) -> bool:
        norm = normalize_url(url)
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE normalized_url = ?", (norm,)
        ).fetchone()
        if row:
            return False
        for seen_title in self._recent_titles():
            if fuzz.token_sort_ratio(title.lower(), seen_title.lower()) >= FUZZY_THRESHOLD:
                return False
        return True

    # ── Writes ───────────────────────────────────────────────────────────────

    def save_item(self, item: dict) -> None:
        """
        Persist a scored + summarized item. Expects keys: url, title, source,
        source_href (optional), published, kind, pillar, score, score_reason,
        high_significance, summary. Never pass article body text — there is
        no column for it.
        """
        norm = normalize_url(item["url"])
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """
                INSERT INTO items
                    (url, normalized_url, title, source, source_href, published_at,
                     kind, pillar, score, score_reason, high_significance, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["url"],
                    norm,
                    item["title"],
                    item["source"],
                    item.get("source_href", ""),
                    item["published"],
                    item.get("kind", "article"),
                    item["pillar"],
                    item.get("score"),
                    item.get("score_reason", ""),
                    1 if item.get("high_significance") else 0,
                    item.get("summary", ""),
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # race between is_new and save_item — safe to ignore

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ── Reads (used by site/generate.py) ────────────────────────────────────

    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def get_recent(self, pillar: str = None, limit: int = 500) -> list:
        """Returns items ordered by published_at desc, optionally filtered by pillar."""
        cols = (
            "url, title, source, source_href, published_at, kind, pillar, "
            "score, score_reason, high_significance, summary, fetched_at"
        )
        if pillar:
            rows = self.conn.execute(
                f"SELECT {cols} FROM items WHERE pillar = ? "
                f"ORDER BY published_at DESC LIMIT ?",
                (pillar, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM items ORDER BY published_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        keys = cols.replace(" ", "").split(",")
        return [dict(zip(keys, row)) for row in rows]

    # ── Social items (separate table, separate provider interface) ───────────

    def is_new_social(self, post_id: str, provider: str = "reddit") -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM social_items WHERE post_id = ? AND provider = ?",
            (post_id, provider),
        ).fetchone()
        return row is None

    def save_social_item(self, item: dict) -> None:
        """
        Persist a scored + summarized social post. Post body text must NOT be
        passed — there is no column for it, matching the same copyright-safe
        constraint as the news items table.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """
                INSERT INTO social_items
                    (post_id, provider, pillar, title, url, subreddit, score,
                     num_comments, velocity_score, summary, published_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["post_id"],
                    item.get("provider", "reddit"),
                    item["pillar"],
                    item["title"],
                    item["url"],
                    item["subreddit"],
                    item.get("score", 0),
                    item.get("num_comments", 0),
                    item.get("velocity_score", 0.0),
                    item.get("summary", ""),
                    item["published_at"],
                    item.get("fetched_at", now),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # already stored from a prior run

    def get_recent_social(self, pillar: str = None, limit: int = 25) -> list:
        """Returns social items ordered by velocity score desc, optionally filtered by pillar."""
        cols = (
            "post_id, provider, pillar, title, url, subreddit, score, "
            "num_comments, velocity_score, summary, published_at, fetched_at"
        )
        if pillar:
            rows = self.conn.execute(
                f"SELECT {cols} FROM social_items WHERE pillar = ? "
                f"ORDER BY velocity_score DESC LIMIT ?",
                (pillar, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT {cols} FROM social_items ORDER BY velocity_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = cols.replace(" ", "").split(",")
        return [dict(zip(keys, row)) for row in rows]

    def reset(self) -> None:
        self.conn.execute("DELETE FROM items")
        self.conn.execute("DELETE FROM social_items")
        self.conn.execute("DELETE FROM meta")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
