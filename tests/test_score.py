"""Tests for pillar-aware relevance scoring and pillar assignment."""

import json
from unittest.mock import MagicMock, patch

from pipeline.score import apply_source_block_list, score_items

PILLARS = {
    "ai_fraud": {"slug": "ai_fraud", "name": "AI Fraud", "definition": "fraud def"},
    "china": {"slug": "china", "name": "China", "definition": "china def"},
}


def make_item(url="https://example.com/1", candidate_pillars=None):
    return {
        "url": url, "title": "Story", "source": "Reuters", "text": "body",
        "candidate_pillars": candidate_pillars or {"ai_fraud"},
    }


def test_apply_source_block_list_drops_matching_source():
    items = [
        {"url": "1", "title": "a", "source": "BusinessWire.com"},
        {"url": "2", "title": "b", "source": "Reuters"},
    ]
    filtered = apply_source_block_list(items)
    assert len(filtered) == 1
    assert filtered[0]["source"] == "Reuters"


def test_score_items_no_api_key_assigns_default_pillar():
    items = [make_item(candidate_pillars={"ai_fraud", "china"})]
    result = score_items(items, PILLARS, api_key="")
    assert result[0]["pillar"] == "ai_fraud"  # sorted order picks ai_fraud before china
    assert result[0]["score"] is None


@patch("pipeline.score.anthropic.Anthropic")
def test_score_items_assigns_highest_scoring_candidate_pillar(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    def fake_create(model, max_tokens, messages):
        prompt = messages[0]["content"]
        msg = MagicMock()
        if "AI Fraud" in prompt:
            msg.content = [MagicMock(text=json.dumps([{"index": 1, "score": 6, "reason": "ok"}]))]
        else:
            msg.content = [MagicMock(text=json.dumps([{"index": 1, "score": 9, "reason": "strong china angle"}]))]
        return msg

    mock_client.messages.create.side_effect = fake_create

    items = [make_item(candidate_pillars={"ai_fraud", "china"})]
    result = score_items(items, PILLARS, api_key="test-key", threshold=5, high_significance_threshold=8)
    assert len(result) == 1
    assert result[0]["pillar"] == "china"
    assert result[0]["score"] == 9
    assert result[0]["high_significance"] is True


@patch("pipeline.score.anthropic.Anthropic")
def test_score_items_drops_item_below_threshold_for_all_candidates(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps([{"index": 1, "score": 2, "reason": "not relevant"}]))]
    mock_client.messages.create.return_value = mock_msg

    items = [make_item(candidate_pillars={"ai_fraud"})]
    result = score_items(items, PILLARS, api_key="test-key", threshold=5)
    assert result == []
