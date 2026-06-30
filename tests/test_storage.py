"""Tests for the content store (dedup + permanent storage)."""

import tempfile
from pathlib import Path

from pipeline.storage import Store, normalize_url


def make_store() -> Store:
    tmp = tempfile.mktemp(suffix=".db")
    return Store(db_path=Path(tmp))


def make_item(url="https://example.com/a", title="Deep Fake Scam", pillar="ai_fraud", **overrides):
    item = {
        "url": url, "title": title, "source": "TestSource", "source_href": "",
        "published": "2024-06-01T10:00:00+00:00", "kind": "article", "pillar": pillar,
        "score": 8, "score_reason": "test", "high_significance": True, "summary": "A summary.",
    }
    item.update(overrides)
    return item


def test_normalize_url_strips_tracking():
    url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
    normed = normalize_url(url)
    assert "utm_source" not in normed
    assert "id=123" in normed


def test_normalize_url_strips_fragment():
    url = "https://example.com/article#section2"
    assert normalize_url(url) == "https://example.com/article"


def test_new_item_is_accepted():
    store = make_store()
    assert store.is_new("https://example.com/article1", "Deep Fake Scam Hits Bank")
    store.close()


def test_same_url_is_duplicate_after_save():
    store = make_store()
    store.save_item(make_item(url="https://example.com/a", title="Deep Fake Scam"))
    assert not store.is_new("https://example.com/a", "Deep Fake Scam")
    store.close()


def test_url_with_tracking_deduped():
    store = make_store()
    store.save_item(make_item(url="https://example.com/a?id=1", title="Fraud Story"))
    assert not store.is_new("https://example.com/a?id=1&utm_campaign=email", "Fraud Story")
    store.close()


def test_fuzzy_title_dedup():
    store = make_store()
    store.save_item(make_item(
        url="https://example.com/orig", title="AI Voice Cloning Used in Bank Fraud Scheme",
    ))
    assert not store.is_new("https://other.com/copy", "AI Voice Cloning Used in Bank Fraud Schemes")
    store.close()


def test_clearly_different_title_accepted():
    store = make_store()
    store.save_item(make_item(url="https://example.com/a", title="AI Voice Cloning Scam"))
    assert store.is_new("https://example.com/b", "Deepfake Images Used in Romance Fraud")
    store.close()


def test_reset_clears_db():
    store = make_store()
    store.save_item(make_item(url="https://example.com/z", title="Some Story"))
    store.reset()
    assert store.is_new("https://example.com/z", "Some Story")
    store.close()


def test_get_recent_filters_by_pillar():
    store = make_store()
    store.save_item(make_item(url="https://example.com/fraud1", title="Fraud Story", pillar="ai_fraud"))
    store.save_item(make_item(url="https://example.com/pharma1", title="Pharma Story", pillar="pharma"))
    fraud_items = store.get_recent(pillar="ai_fraud")
    assert len(fraud_items) == 1
    assert fraud_items[0]["pillar"] == "ai_fraud"
    store.close()


def test_get_recent_no_body_text_column():
    """Copyright guard: saved rows never carry article body text."""
    store = make_store()
    store.save_item(make_item(url="https://example.com/x", title="X", text="full scraped article body"))
    row = store.get_recent()[0]
    assert "text" not in row
    store.close()


def test_set_and_get_meta():
    store = make_store()
    store.set_meta("last_run_at", "2024-06-01T00:00:00+00:00")
    assert store.get_meta("last_run_at") == "2024-06-01T00:00:00+00:00"
    store.close()
