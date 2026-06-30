"""Tests for the legacy (disabled-by-default) HTML + plain-text email rendering."""

import pytest

from legacy_email.render import render_digest, render_empty_digest

ITEMS = [
    {
        "_index": 1,
        "title": "AI Voice Cloning Used in Fraud",
        "url": "https://example.com/1",
        "source": "TechCrunch",
        "published": "2024-06-01T10:00:00+00:00",
        "kind": "article",
        "text": "Article body...",
    },
    {
        "_index": 2,
        "title": "Deepfake Scam Video",
        "url": "https://youtube.com/watch?v=abc",
        "source": "YouTube Channel",
        "published": "2024-06-01T09:00:00+00:00",
        "kind": "video",
        "text": "Video description...",
    },
]

SUMMARY = {
    "overview": "Fraud is rising rapidly.",
    "items": [
        {"index": 1, "summary": "Criminals cloned a CEO voice."},
        {"index": 2, "summary": "A deepfake video fooled investors."},
    ],
}


def test_render_digest_returns_html_and_plain():
    html, plain = render_digest(ITEMS, SUMMARY, "Test Subject")
    assert "<html" in html
    assert "Test Subject" in html
    assert "Fraud is rising rapidly." in html


def test_render_digest_plain_contains_overview():
    _, plain = render_digest(ITEMS, SUMMARY, "Test Subject")
    assert "Fraud is rising rapidly." in plain


def test_render_digest_articles_section():
    # The legacy email renderer lists articles and videos under one "Articles"
    # heading — it never had a separate Clips section (unlike the new site,
    # which does). Preserved as-is since this path is disabled by default.
    html, _ = render_digest(ITEMS, SUMMARY, "Test Subject")
    assert "Articles" in html


def test_render_digest_links_present():
    html, _ = render_digest(ITEMS, SUMMARY, "Test Subject")
    assert "https://example.com/1" in html
    assert "https://youtube.com/watch?v=abc" in html


def test_render_digest_item_summaries():
    html, _ = render_digest(ITEMS, SUMMARY, "Test Subject")
    assert "Criminals cloned a CEO voice." in html
    assert "A deepfake video fooled investors." in html


def test_render_empty_digest():
    html, plain = render_empty_digest("Empty Subject")
    assert "no significant" in html.lower()
    assert "no significant" in plain.lower()


def test_render_escapes_html_in_title():
    items = [{**ITEMS[0], "title": "<script>alert(1)</script>"}]
    html, _ = render_digest(items, SUMMARY, "Subject")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
