#!/usr/bin/env python3
"""
Single command to rebuild the static site from current DB state:

    python site/generate.py            # reads data/digest.db
    python site/generate.py --sample   # uses fabricated sample data, writes nothing to the DB

Reads pipeline/storage.py's content store and config/pillars/*.yaml, renders
Jinja2 templates in site/templates/, and writes static HTML + assets to
site/dist/. Each pillar is rendered independently (wrapped in try/except) so
one pillar's bad data can't break the rest of the site build.
"""

import argparse
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))  # allow `import pipeline` when run as a script

from pipeline.fetch import load_pillars  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("site.generate")

SITE_DIR = Path(__file__).parent
TEMPLATES_DIR = SITE_DIR / "templates"
STATIC_DIR = SITE_DIR / "static"
DIST_DIR = SITE_DIR / "dist"


def _load_settings() -> dict:
    with open(BASE / "config" / "settings.yaml") as f:
        return yaml.safe_load(f) or {}


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d, %Y %H:%M")
    except Exception:
        return iso or ""


def _prepare_item(item: dict) -> dict:
    return {
        **item,
        "high_significance": bool(item.get("high_significance")),
        "published_display": _fmt_date(item.get("published_at", "")),
    }


def _load_items_from_db(pillar_slug: str, limit: int):
    from pipeline.storage import Store
    store = Store()
    try:
        rows = store.get_recent(pillar=pillar_slug, limit=limit)
    finally:
        store.close()
    return [_prepare_item(r) for r in rows]


def _load_items_from_sample(pillar_slug: str):
    from sample_data import SAMPLE_ITEMS
    rows = [i for i in SAMPLE_ITEMS if i["pillar"] == pillar_slug]
    rows.sort(key=lambda r: r["published_at"], reverse=True)
    return [_prepare_item(r) for r in rows]


def build_site(use_sample: bool = False) -> None:
    settings = _load_settings()
    home_n = settings.get("home_items_per_pillar", 5)
    page_limit = settings.get("pillar_page_limit", 500)

    pillars = load_pillars()
    pillar_nav = [{"slug": p["slug"], "name": p["name"]} for p in pillars]

    if use_sample:
        last_updated = "SAMPLE DATA — " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    else:
        from pipeline.storage import Store
        store = Store()
        last_updated_raw = store.get_meta("last_run_at")
        store.close()
        last_updated = _fmt_date(last_updated_raw) if last_updated_raw else "never"

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)
    (DIST_DIR / "pillar").mkdir()
    shutil.copytree(STATIC_DIR, DIST_DIR / "static")

    # ── Home page ────────────────────────────────────────────────────────────
    home_pillars = []
    for pillar in pillars:
        try:
            items = (
                _load_items_from_sample(pillar["slug"])[:home_n]
                if use_sample else
                _load_items_from_db(pillar["slug"], home_n)
            )
            home_pillars.append({"slug": pillar["slug"], "name": pillar["name"], "entries": items})
        except Exception as exc:
            logger.error("Pillar %s failed while building home page — showing it empty: %s", pillar["slug"], exc)
            home_pillars.append({"slug": pillar["slug"], "name": pillar["name"], "entries": []})

    index_html = env.get_template("index.html").render(
        root="", pillars=home_pillars, last_updated=last_updated,
    )
    (DIST_DIR / "index.html").write_text(index_html)

    # ── Pillar pages ─────────────────────────────────────────────────────────
    for pillar in pillars:
        try:
            items = (
                _load_items_from_sample(pillar["slug"])
                if use_sample else
                _load_items_from_db(pillar["slug"], page_limit)
            )
            articles = [i for i in items if i.get("kind") != "video"]
            clips = [i for i in items if i.get("kind") == "video"]

            pillar_html = env.get_template("pillar.html").render(
                root="../",
                pillars=pillar_nav,
                pillar={"slug": pillar["slug"], "name": pillar["name"]},
                articles=articles,
                clips=clips,
                last_updated=last_updated,
            )
            (DIST_DIR / "pillar" / f"{pillar['slug']}.html").write_text(pillar_html)
        except Exception as exc:
            logger.error("Pillar %s failed while building its page — skipping: %s", pillar["slug"], exc)

    # ── About page ───────────────────────────────────────────────────────────
    about_html = env.get_template("about.html").render(
        root="", pillars=pillar_nav, last_updated=last_updated,
    )
    (DIST_DIR / "about.html").write_text(about_html)

    logger.info("Site built at %s (last_updated=%s)", DIST_DIR, last_updated)


def main():
    parser = argparse.ArgumentParser(description="Rebuild the static site from the current DB state")
    parser.add_argument("--sample", action="store_true", help="Use fabricated sample data instead of the real DB")
    args = parser.parse_args()
    build_site(use_sample=args.sample)


if __name__ == "__main__":
    main()
