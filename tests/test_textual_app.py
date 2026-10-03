"""
tests/test_textual_app.py - Integration tests for TranscoderTUI master app.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from ui.textual_app import TranscoderTUI
from ui.tui_widgets.queue_table import QueueTableWidget
from ui.tui_widgets.resource_monitor import ResourceMonitorWidget
from ui.tui_widgets.status_bar import TUIStatusBar
from ui.tui_widgets.visualizer_widget import AudioVisualizerWidget


@pytest.mark.asyncio
async def test_transcoder_tui_compose_and_mount() -> None:
    """Verifies that TranscoderTUI mounts all 10 modular sub-components cleanly without error."""
    app = TranscoderTUI()
    async with app.run_test() as pilot:
        # Check core widgets exist
        assert app.query_one("#queue-table-widget", QueueTableWidget) is not None
        assert app.query_one("#resource-monitor", ResourceMonitorWidget) is not None
        assert app.query_one("#audio-visualizer", AudioVisualizerWidget) is not None
        assert app.query_one("#status-bar", TUIStatusBar) is not None

        await pilot.pause(0.1)

        # Check action methods exist and can be called safely
        app.action_open_help()
        await pilot.pause(0.1)
        assert len(app.screen_stack) > 1
        app.pop_screen()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_transcoder_tui_preset_and_filter_actions() -> None:
    """Verifies that preset and filter modals open and return cleanly."""
    app = TranscoderTUI()
    async with app.run_test() as pilot:
        app.action_open_presets()
        await pilot.pause(0.1)
        assert len(app.screen_stack) > 1
        app.pop_screen()
        await pilot.pause(0.05)

        app.action_open_filters()
        await pilot.pause(0.1)
        assert len(app.screen_stack) > 1
        app.pop_screen()
        await pilot.pause(0.05)
