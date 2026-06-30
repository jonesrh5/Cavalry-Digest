"""
Pillar-aware relevance scoring.

Each item carries `candidate_pillars` — the set of pillar slugs whose search
query surfaced it (set by pipeline/fetch.py). This module scores the item
against only those candidate pillars' relevance definitions (not all five
always, to keep API cost down), then assigns the item to whichever candidate
pillar scored it highest. Ties favor the more specific, non-"china" pillar,
since China is a cross-cutting filter rather than a topic of its own.

Items that score below every one of their candidate pillars' thresholds are
dropped entirely — same gate as before, just evaluated per-pillar.
"""

import json
import logging
import re
from pathlib import Path

import anthropic
import yaml

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_BLOCK_LIST_PATH = Path(__file__).parent.parent / "config" / "source_block_list.yaml"
_BATCH_SIZE = 30


def _load_block_list() -> list:
    if not _BLOCK_LIST_PATH.exists():
        return []
    with open(_BLOCK_LIST_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    return [s.lower() for s in (cfg.get("block") or [])]


def apply_source_block_list(items: list) -> list:
    """Drop items whose source matches the global block list. Applied before scoring."""
    block = _load_block_list()
    if not block:
        return items

    filtered = []
    for item in items:
        src = item["source"].lower()
        if any(b in src for b in block):
            logger.info("BLOCKED source '%s': %s", item["source"], item["title"][:60])
            continue
        filtered.append(item)

    if len(filtered) < len(items):
        logger.info("Source block list: %d → %d items", len(items), len(filtered))
    return filtered


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    return re.sub(r"\s*```$", "", text).strip()


def _build_prompt(batch: list, pillar_name: str, definition: str) -> str:
    lines = []
    for i, item in enumerate(batch, 1):
        lines.append(
            f"[{i}] \"{item['title']}\"\n"
            f"    Source: {item['source']}\n"
            f"    Excerpt: {(item.get('text') or '')[:300] or '(none)'}\n"
        )
    block = "\n".join(lines)
    return f"""You are a relevance filter for the "{pillar_name}" section of a news site.

RELEVANCE DEFINITION:
{definition}

Rate each article 0-10 using the definition above, then give a one-sentence reason.

Return ONLY a JSON array — no markdown, no extra keys:
[
  {{"index": 1, "score": 8, "reason": "Describes specific deepfake bank fraud incident."}},
  ...
]

ARTICLES TO SCORE:
{block}"""


def _score_against_pillar(client: anthropic.Anthropic, items: list, pillar: dict, default_threshold: int) -> dict:
    """Returns {item_url: {"score": int, "reason": str}} for this pillar's definition."""
    results = {}
    for i in range(0, len(items), _BATCH_SIZE):
        batch = items[i : i + _BATCH_SIZE]
        prompt = _build_prompt(batch, pillar["name"], pillar["definition"].strip())
        for attempt in (1, 2):
            try:
                msg = client.messages.create(
                    model=_MODEL,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = _strip_fences(msg.content[0].text)
                parsed = json.loads(raw)
                for entry in parsed:
                    local_idx = entry["index"] - 1
                    if 0 <= local_idx < len(batch):
                        results[batch[local_idx]["url"]] = {
                            "score": entry.get("score"),
                            "reason": entry.get("reason", ""),
                        }
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error(
                        "Scoring batch failed after retry for pillar %s: %s — keeping at threshold",
                        pillar["slug"], exc,
                    )
                    for it in batch:
                        results[it["url"]] = {"score": default_threshold, "reason": "scoring error"}
                else:
                    logger.warning("Scoring parse error for pillar %s, retrying: %s", pillar["slug"], exc)
    return results


def score_items(
    items: list,
    pillars_by_slug: dict,
    api_key: str,
    threshold: int = 5,
    high_significance_threshold: int = 8,
) -> list:
    """
    Scores each item against its candidate pillars' definitions, assigns it to
    the highest-scoring candidate, and drops it if that score is below the
    winning pillar's threshold. Mutates items with score, score_reason, pillar,
    high_significance. Returns only the items that passed.
    """
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping scoring, keeping all items at default pillar")
        for item in items:
            item["pillar"] = sorted(item["candidate_pillars"])[0]
            item["score"], item["score_reason"], item["high_significance"] = None, "", False
        return items

    client = anthropic.Anthropic(api_key=api_key)

    # Group items by each pillar they're a candidate for, score each group once.
    by_pillar: dict = {}
    for item in items:
        for slug in item["candidate_pillars"]:
            by_pillar.setdefault(slug, []).append(item)

    scores_by_url_pillar: dict = {}  # url -> {pillar_slug: {"score", "reason"}}
    for slug, pillar_items in by_pillar.items():
        try:
            pillar = pillars_by_slug[slug]
            pillar_threshold = pillar.get("threshold", threshold)
            results = _score_against_pillar(client, pillar_items, pillar, pillar_threshold)
            for url, result in results.items():
                scores_by_url_pillar.setdefault(url, {})[slug] = result
        except Exception as exc:
            logger.error("Pillar %s crashed during scoring — its candidate items fall back to other pillars: %s", slug, exc)

    passed, dropped = [], []
    for item in items:
        candidates = scores_by_url_pillar.get(item["url"], {})
        # Pick the highest-scoring candidate pillar; ties favor non-"china" pillars.
        best_slug, best = None, None
        for slug in sorted(item["candidate_pillars"], key=lambda s: (s == "china", s)):
            result = candidates.get(slug, {"score": threshold, "reason": ""})
            score = result.get("score")
            if not isinstance(score, (int, float)):
                continue
            if best is None or score > best["score"]:
                best_slug, best = slug, result

        if best is None:
            best_slug = sorted(item["candidate_pillars"])[0]
            best = {"score": threshold, "reason": "scoring unavailable"}

        pillar_cfg = pillars_by_slug[best_slug]
        pillar_threshold = pillar_cfg.get("threshold", threshold)
        pillar_high_threshold = pillar_cfg.get("high_significance_threshold", high_significance_threshold)

        item["pillar"] = best_slug
        item["score"] = best["score"]
        item["score_reason"] = best["reason"]
        item["high_significance"] = best["score"] >= pillar_high_threshold

        if best["score"] < pillar_threshold:
            dropped.append(item)
            logger.info(
                "DROPPED [pillar=%s score=%s] %s — %s | Source: %s",
                best_slug, best["score"], item["title"][:80], best["reason"], item["source"],
            )
        else:
            passed.append(item)

    logger.info(
        "Relevance scoring: %d passed, %d dropped, %d high-significance",
        len(passed), len(dropped), sum(1 for i in passed if i["high_significance"]),
    )
    return passed
