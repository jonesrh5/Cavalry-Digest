#!/usr/bin/env python3
"""
Pipeline entry point: fetch -> source filters -> dedup -> score -> summarize -> store.

Replaces the old main.py email orchestrator. Output is now the database
(pipeline/storage.py), not an email. After a successful run this also
rebuilds the static site so it's always current with the latest DB state.
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))  # allow `from pipeline...` imports when run as a script

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"pipeline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
        ),
    ],
)
logger = logging.getLogger("pipeline.run")


def load_settings() -> dict:
    with open(BASE / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)


def _load_reddit_sources() -> dict:
    path = BASE / "config" / "reddit_sources.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def run_pipeline(cfg: dict) -> int:
    from pipeline.fetch import fetch_all, load_pillars
    from pipeline.score import apply_source_block_list, score_items
    from pipeline.source_filter import apply_allowlist
    from pipeline.storage import Store
    from pipeline.summarize import summarize_items

    pillars = load_pillars()
    pillars_by_slug = {p["slug"]: p for p in pillars}
    store = Store()

    timeout = cfg.get("request_timeout", 15)
    enforce_allowlist = cfg.get("enforce_source_allowlist", True)
    threshold = cfg.get("threshold", 5)
    high_threshold = cfg.get("high_significance_threshold", 8)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    # ── Fetch (pillar-aware; each pillar isolated internally) ────────────────
    raw_items = fetch_all(pillars, timeout=timeout)

    # ── Global source block list (press-release wires etc.) ──────────────────
    raw_items = apply_source_block_list(raw_items)

    # ── Domain allowlist ──────────────────────────────────────────────────────
    raw_items = apply_allowlist(raw_items, enforce=enforce_allowlist)

    # ── Deduplicate against stored items ──────────────────────────────────────
    new_items = [item for item in raw_items if store.is_new(item["url"], item["title"])]
    logger.info("Fetched: %d | Duplicates skipped: %d | New: %d",
                len(raw_items), len(raw_items) - len(new_items), len(new_items))

    if not new_items:
        store.set_meta("last_run_at", datetime.now(timezone.utc).isoformat())
        store.close()
        logger.info("No new items this run.")
        return 0

    # ── Score + assign to a single pillar ─────────────────────────────────────
    scored_items = score_items(new_items, pillars_by_slug, api_key, threshold, high_threshold)

    # ── Summarize survivors ───────────────────────────────────────────────────
    summaries: dict = {}
    try:
        summaries = summarize_items(scored_items, api_key)
    except Exception as exc:
        logger.warning("Summarization unavailable (%s) — falling back to truncated excerpts", exc)

    saved = 0
    for item in scored_items:
        item["summary"] = summaries.get(item["url"]) or (item.get("text") or "").strip()[:240] or item["title"]
        store.save_item(item)
        saved += 1

    store.set_meta("last_run_at", datetime.now(timezone.utc).isoformat())
    store.close()
    logger.info("Saved %d new items to %s", saved, "data/digest.db")

    # ── Social Pulse (Reddit) ─────────────────────────────────────────────────
    _run_social_pipeline(pillars, pillars_by_slug, cfg, api_key)

    # ── Optional legacy email (disabled by default) ──────────────────────────
    email_cfg = cfg.get("email", {})
    if email_cfg.get("enabled"):
        _send_legacy_email(scored_items, email_cfg)

    return saved


def _send_legacy_email(items: list, email_cfg: dict) -> None:
    from legacy_email.render import render_digest
    from legacy_email.sender import send_digest

    recipients_path = BASE / "config" / "recipients.txt"
    recipients = [l.strip() for l in recipients_path.read_text().splitlines() if l.strip() and not l.startswith("#")]

    for idx, item in enumerate(items, 1):
        item["_index"] = idx
    date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    subject = f"Cavalry News Digest — {date_str}: {len(items)} new {'story' if len(items) == 1 else 'stories'}"
    summary_data = {
        "overview": "",
        "items": [{"index": i["_index"], "summary": i.get("summary", "")} for i in items],
    }
    html, plain = render_digest(items, summary_data, subject)
    send_digest(subject, html, plain, recipients)


def _run_social_pipeline(pillars: list, pillars_by_slug: dict, cfg: dict, api_key: str) -> None:
    """
    Fetch Reddit posts for each pillar, run them through the same relevance-
    scoring gate as news articles, optionally summarize, then store.
    One failing pillar does not abort the rest. Missing credentials skip
    the whole step with a logged warning.
    """
    from pipeline.social.reddit import RedditProvider
    from pipeline.score import score_items
    from pipeline.summarize import summarize_items
    from pipeline.storage import Store

    provider = RedditProvider()
    if not provider.available():
        logger.info("Reddit credentials not set — skipping Social Pulse fetch.")
        return

    reddit_sources = _load_reddit_sources()
    threshold = cfg.get("threshold", 5)
    high_threshold = cfg.get("high_significance_threshold", 8)
    prescore_limit = cfg.get("social_prescore_limit", 20)
    per_pillar = cfg.get("social_items_per_pillar", 5)

    store = Store()
    try:
        for pillar in pillars:
            slug = pillar["slug"]
            try:
                pillar_reddit_cfg = {"reddit_sources": reddit_sources.get(slug, {})}
                candidates = provider.fetch_for_pillar(slug, pillar_reddit_cfg)

                new_candidates = [
                    c for c in candidates if store.is_new_social(c["post_id"], c["provider"])
                ]
                if not new_candidates:
                    logger.info("Social Pulse: no new posts for pillar %s", slug)
                    continue

                # Score only the top-velocity candidates to limit API cost.
                top_candidates = new_candidates[:prescore_limit]
                scored = score_items(top_candidates, pillars_by_slug, api_key, threshold, high_threshold)

                # Summarize the top survivors (capped at per_pillar).
                top_scored = scored[:per_pillar]
                summaries: dict = {}
                try:
                    summaries = summarize_items(top_scored, api_key)
                except Exception as exc:
                    logger.warning("Social summarization failed for %s: %s", slug, exc)

                saved = 0
                for item in top_scored:
                    item["summary"] = summaries.get(item["url"], "")
                    store.save_social_item(item)
                    saved += 1

                logger.info("Social Pulse: saved %d new items for pillar %s", saved, slug)

            except Exception as exc:
                logger.error("Social Pulse: pillar %s failed — skipping: %s", slug, exc)
    finally:
        store.close()


def rebuild_site() -> None:
    """Single command to regenerate the static site from current DB state."""
    result = subprocess.run([sys.executable, str(BASE / "site" / "generate.py")], capture_output=False)
    if result.returncode != 0:
        logger.error("site/generate.py exited with code %d", result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Cavalry News Digest pipeline")
    parser.add_argument("--reset-db", action="store_true", help="Clear the content database")
    parser.add_argument("--skip-site-build", action="store_true", help="Don't rebuild the static site after running")
    args = parser.parse_args()

    cfg = load_settings()

    if args.reset_db:
        from pipeline.storage import Store
        store = Store()
        store.reset()
        store.close()
        logger.info("Database reset.")
        return

    run_pipeline(cfg)

    if not args.skip_site_build:
        rebuild_site()


if __name__ == "__main__":
    main()
