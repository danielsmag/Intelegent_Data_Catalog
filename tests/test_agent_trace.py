"""Agent trace keeps long prompts readable at INFO and complete at DEBUG."""

import logging

import pytest

from intelegent_data_catalog.llm.trace import AgentTrace


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
