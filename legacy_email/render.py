"""
Build the HTML + plain-text digest email. LEGACY — disabled by default.

Kept so an optional digest (linking back to the site) can be re-enabled later
via config/settings.yaml `email.enabled: true` without a rewrite. Not called
by pipeline/run.py unless that flag is set.
"""

from datetime import datetime, timezone

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{subject}</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 24px 16px;
          color: #1a1a1a; background: #ffffff; line-height: 1.6; }}
  h1   {{ font-size: 1.4rem; border-bottom: 2px solid #d62728; padding-bottom: 8px; }}
  h2   {{ font-size: 1.1rem; margin-top: 32px; color: #444; text-transform: uppercase;
          letter-spacing: .05em; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
  .overview {{ background: #f8f8f8; border-left: 4px solid #d62728; padding: 12px 16px;
               margin: 20px 0; font-size: 1rem; }}
  .item       {{ margin: 20px 0; }}
  .item a     {{ color: #d62728; font-weight: bold; text-decoration: none; font-size: 1rem; }}
  .item a:hover {{ text-decoration: underline; }}
  .desc       {{ margin: 4px 0; font-size: 0.95rem; }}
  .meta       {{ font-size: 0.78rem; color: #777; }}
  .badge      {{ display: inline-block; font-size: 0.68rem; font-weight: bold;
                 background: #d62728; color: #fff; padding: 1px 6px; border-radius: 3px;
                 margin-right: 6px; vertical-align: middle; letter-spacing: .04em; }}
  .score      {{ font-size: 0.72rem; color: #999; margin-left: 6px; }}
  .no-news    {{ color: #555; font-style: italic; }}
  footer    {{ margin-top: 40px; font-size: 0.75rem; color: #aaa; border-top: 1px solid #eee;
               padding-top: 12px; }}
</style>
</head>
<body>
<h1>{subject}</h1>
<div class="overview">{overview}</div>
{articles_html}
<footer>Automated digest generated {generated_at} UTC &mdash; unsubscribe by removing your address from recipients.txt</footer>
</body>
</html>
"""

_ITEM_HTML = """\
<div class="item">
  {badge}<a href="{url}">{title}</a>{score_tag}
  <p class="desc">{summary}</p>
  <span class="meta">{source} &mdash; {date}</span>
</div>"""


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        return iso


def _render_items(items: list, summaries: dict) -> str:
    parts = []
    for item in items:
        idx = item["_index"]
        summary = summaries.get(idx, "")
        badge = '<span class="badge">HIGH SIGNIFICANCE</span>' if item.get("high_significance") else ""
        score = item.get("score")
        score_tag = f' <span class="score">[{score}/10]</span>' if score is not None else ""
        parts.append(_ITEM_HTML.format(
            url=item["url"],
            title=item["title"].replace("<", "&lt;").replace(">", "&gt;"),
            summary=summary.replace("<", "&lt;").replace(">", "&gt;"),
            source=item["source"].replace("<", "&lt;").replace(">", "&gt;"),
            date=_fmt_date(item["published"]),
            badge=badge,
            score_tag=score_tag,
        ))
    return "\n".join(parts)


def render_digest(items: list, summary_data: dict, subject: str):
    """
    Returns (html_body, plain_text_body).
    items must have an "_index" key (1-based) matching summary_data["items"].
    """
    summaries = {s["index"]: s["summary"] for s in summary_data.get("items", [])}
    overview = summary_data.get("overview", "")

    articles_html = ""
    if items:
        articles_html = "<h2>Articles</h2>\n" + _render_items(items, summaries)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    html = _HTML_TEMPLATE.format(
        subject=subject, overview=overview, articles_html=articles_html, generated_at=generated_at,
    )

    lines = [subject, "=" * len(subject), "", "OVERVIEW", "-" * 8, overview, ""]
    if items:
        lines += ["ARTICLES", "-" * 8]
        for item in items:
            flag = " [HIGH SIGNIFICANCE]" if item.get("high_significance") else ""
            score = f" [score {item['score']}/10]" if item.get("score") is not None else ""
            lines += [
                f"{item['title']}{flag}{score}",
                item["url"],
                summaries.get(item["_index"], ""),
                f"{item['source']} — {_fmt_date(item['published'])}",
                "",
            ]
    plain = "\n".join(lines)
    return html, plain


def render_empty_digest(subject: str):
    """Digest for runs that find no new items."""
    msg = "No significant new developments were found in this monitoring period."
    html = _HTML_TEMPLATE.format(
        subject=subject,
        overview=f'<span class="no-news">{msg}</span>',
        articles_html="",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    plain = f"{subject}\n{'=' * len(subject)}\n\n{msg}\n"
    return html, plain
