"""Tests for news fetchers — all network calls mocked. YouTube fetching is descoped."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pipeline.fetch import fetch_all, fetch_google_news, fetch_newsapi

_RECENT = datetime.now(timezone.utc) - timedelta(hours=1)
_RECENT_ISO = _RECENT.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_feed_entry(title="AI Fraud Story", link="https://example.com/1", summary="A summary"):
    entry = MagicMock()
    entry.title = title
    entry.link = link
    entry.summary = summary
    entry.source = {"title": "Test News"}
    entry.published_parsed = _RECENT.timetuple()[:9]
    return entry


@patch("pipeline.fetch._extract_text", return_value="article body text")
@patch("pipeline.fetch.feedparser.parse")
def test_fetch_google_news_returns_items(mock_parse, mock_extract):
    mock_parse.return_value = MagicMock(entries=[_make_feed_entry()])
    items = fetch_google_news(["deepfake scam"])
    # Two passes (preferred outlets + general search) both hit the same mocked feed.
    assert len(items) == 2
    assert items[0]["kind"] == "article"
    assert items[0]["title"] == "AI Fraud Story"


@patch("pipeline.fetch.feedparser.parse", side_effect=Exception("timeout"))
def test_fetch_google_news_survives_failure(mock_parse):
    items = fetch_google_news(["deepfake scam"])
    assert items == []


@patch("pipeline.fetch._extract_text", return_value="")
@patch("pipeline.fetch._get")
def test_fetch_newsapi_returns_items(mock_get, mock_extract, monkeypatch):
    monkeypatch.setenv("NEWSAPI_KEY", "testkey")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "articles": [
            {
                "title": "Phishing via AI",
                "url": "https://newsapi.example/1",
                "source": {"name": "TechNews"},
                "publishedAt": _RECENT_ISO,
                "description": "AI phishing description",
                "content": None,
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp
    items = fetch_newsapi(["AI phishing"])
    assert len(items) == 1
    assert items[0]["source"] == "TechNews"


def test_fetch_newsapi_skips_without_key(monkeypatch):
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    items = fetch_newsapi(["AI phishing"])
    assert items == []


@patch("pipeline.fetch._get", side_effect=Exception("connection error"))
def test_fetch_newsapi_survives_failure(mock_get, monkeypatch):
    monkeypatch.setenv("NEWSAPI_KEY", "testkey")
    items = fetch_newsapi(["AI phishing"])
    assert items == []


# ── Pillar-aware orchestrator ─────────────────────────────────────────────────

@patch("pipeline.fetch.fetch_newsapi", return_value=[])
@patch("pipeline.fetch.fetch_google_news")
def test_fetch_all_merges_candidate_pillars_for_overlapping_item(mock_gnews, mock_newsapi):
    shared_item = {
        "title": "Chinese Group Used AI for Phishing", "url": "https://example.com/shared",
        "source": "Reuters", "source_href": "", "published": "2024-06-01T00:00:00+00:00",
        "text": "", "kind": "article",
    }
    mock_gnews.side_effect = [[dict(shared_item)], [dict(shared_item)]]
    pillars = [
        {"slug": "ai_fraud", "name": "AI Fraud", "topics": ["deepfake"], "definition": "x"},
        {"slug": "china", "name": "China", "topics": ["china ai fraud"], "definition": "x"},
    ]
    items = fetch_all(pillars)
    assert len(items) == 1
    assert items[0]["candidate_pillars"] == {"ai_fraud", "china"}


@patch("pipeline.fetch.fetch_newsapi", return_value=[])
@patch("pipeline.fetch.fetch_google_news", side_effect=Exception("malformed pillar"))
def test_fetch_all_survives_one_pillar_crashing(mock_gnews, mock_newsapi):
    pillars = [{"slug": "broken", "name": "Broken", "topics": ["x"], "definition": "x"}]
    items = fetch_all(pillars)
    assert items == []
