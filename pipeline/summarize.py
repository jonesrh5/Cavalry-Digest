"""
Per-item, one-sentence summaries via Claude Sonnet.

The website only ever displays a single declarative sentence per item — no
batch overview paragraph (that was an email-only construct, dropped here).
The prompt explicitly forbids copying source phrasing verbatim: the summary
must be genuinely our own wording, since this output is now published
publicly and stands in for content we never store or display ourselves.
"""

import json
import logging
import re

import anthropic

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_SYSTEM_PROMPT = """\
You write brief article summaries for a public news site covering AI fraud, \
pharma, Meta, AI data centers, and China's involvement in those areas. \
Write 1–2 sentences in a clear, direct journalistic style — like a wire editor \
would write a lede. Include concrete details (names, numbers, dollar amounts, \
outcomes) when the source text provides them. Convey why the story matters \
without hype or opinion. Write in your own words: never copy or lightly paraphrase \
phrases from the source text, since this summary is the only text from this item \
the site will ever display."""


def _build_prompt(items: list) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(
            f"[{i}] Title: {item['title']}\n"
            f"    Source: {item['source']}\n"
            f"    Text: {(item.get('text') or '')[:800] or '(no text available)'}\n"
        )
    items_block = "\n".join(lines)

    return f"""Write a 1–2 sentence summary per item in a clear, direct journalistic style.
Include concrete details — names, numbers, dollar amounts, rulings, outcomes — when available.
Explain what happened and why it matters. Do not copy phrases verbatim from the source text.
No hype, no opinion, no filler phrases like "in a significant development."

Return ONLY a JSON array — no markdown, no extra keys:
[
  {{"index": 1, "summary": "<1-2 sentences>"}},
  ...
]

ITEMS TO SUMMARIZE ({len(items)} items):
{items_block}"""


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    return re.sub(r"\s*```$", "", text).strip()


def summarize_items(items: list, api_key: str) -> dict:
    """
    Returns {item_url: summary_str} for every item. Raises on unrecoverable
    failure so the caller can fall back (e.g. to a truncated text excerpt).
    """
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")
    if not items:
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    results: dict = {}
    batch_size = 20

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        prompt = _build_prompt(batch)
        for attempt in (1, 2):
            try:
                message = client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = _strip_fences(message.content[0].text)
                parsed = json.loads(raw)
                for entry in parsed:
                    local_idx = entry["index"] - 1
                    if 0 <= local_idx < len(batch):
                        results[batch[local_idx]["url"]] = entry.get("summary", "")
                break
            except (json.JSONDecodeError, ValueError, KeyError, IndexError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"Failed to parse summarizer response after retry: {exc}") from exc
                logger.warning("Summarizer parse error on attempt %d, retrying: %s", attempt, exc)
            except anthropic.APIError as exc:
                raise RuntimeError(f"Anthropic API error: {exc}") from exc

    return results
