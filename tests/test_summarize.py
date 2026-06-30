"""Tests for the per-item summarizer (no batch overview — site only shows one-liners)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.summarize import _strip_fences, summarize_items

VALID_JSON = [{"index": 1, "summary": "Criminals used deepfakes to defraud a bank."}]


def test_strip_fences_removes_markdown():
    raw = "```json\n[{\"key\": \"val\"}]\n```"
    assert _strip_fences(raw) == '[{"key": "val"}]'


def test_strip_fences_no_fences():
    raw = '[{"key": "val"}]'
    assert _strip_fences(raw) == raw


def test_summarize_items_raises_without_key():
    with pytest.raises(EnvironmentError):
        summarize_items([{"title": "x", "source": "y", "url": "https://example.com/1"}], api_key="")


def test_summarize_items_empty_list_returns_empty_dict():
    assert summarize_items([], api_key="test-key") == {}


@patch("pipeline.summarize.anthropic.Anthropic")
def test_summarize_items_success(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(VALID_JSON))]
    mock_client.messages.create.return_value = mock_msg

    items = [{"title": "Deepfake Fraud", "source": "CNN", "url": "https://example.com/1", "text": "text"}]
    result = summarize_items(items, api_key="test-key")
    assert result["https://example.com/1"] == "Criminals used deepfakes to defraud a bank."


@patch("pipeline.summarize.anthropic.Anthropic")
def test_summarize_items_retries_on_bad_json(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    bad = MagicMock()
    bad.content = [MagicMock(text="not json at all")]
    good = MagicMock()
    good.content = [MagicMock(text=json.dumps(VALID_JSON))]
    mock_client.messages.create.side_effect = [bad, good]

    items = [{"title": "Story", "source": "X", "url": "https://example.com/1", "text": "t"}]
    result = summarize_items(items, api_key="test-key")
    assert "https://example.com/1" in result
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.summarize.anthropic.Anthropic")
def test_summarize_items_raises_after_two_failed_attempts(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    bad = MagicMock()
    bad.content = [MagicMock(text="not json at all")]
    mock_client.messages.create.side_effect = [bad, bad]

    items = [{"title": "Story", "source": "X", "url": "https://example.com/1", "text": "t"}]
    with pytest.raises(RuntimeError):
        summarize_items(items, api_key="test-key")
