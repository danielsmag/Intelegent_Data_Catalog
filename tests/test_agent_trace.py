"""Agent trace keeps long prompts readable at INFO and complete at DEBUG."""

import logging

import pytest

from intelegent_data_catalog.llm.trace import AgentTrace, _usage_detail


def test_long_ask_is_truncated_at_info_and_full_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """INFO shows a short ask. DEBUG keeps the full text."""
    caplog.set_level(logging.DEBUG, logger="intelegent_data_catalog.llm")
    ask: str = "x" * 600
    AgentTrace("table-catalog").start(ask)
    info_messages: list[str] = [
        record.message for record in caplog.records if record.levelno == logging.INFO
    ]
    debug_messages: list[str] = [
        record.message for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert len(info_messages) == 1
    assert info_messages[0].endswith("...")
    assert ask not in info_messages[0]
    assert len(debug_messages) == 1
    assert ask in debug_messages[0]


def test_usage_line_includes_elapsed_time() -> None:
    """Token counts keep the middle dot, and the run time follows them."""
    assert _usage_detail(789, 43, 2.2) == "789 in · 43 out · 2s"
    assert _usage_detail(1, 1, 0.2) == "1 in · 1 out · <1s"
    assert _usage_detail(1_200, 8, 65) == "1,200 in · 8 out · 1m 5s"
