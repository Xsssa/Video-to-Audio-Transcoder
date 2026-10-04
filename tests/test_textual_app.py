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


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40)])
async def test_transcoder_tui_layout_and_responsiveness(size: tuple[int, int]) -> None:
    """Validates 3-column layout proportions, container borders, button sizing, and responsiveness."""
    app = TranscoderTUI()
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.1)

        # 1. Sidebar checks: width 22, height 3 buttons, subtle borders, no clipping
        sidebar = app.query_one("#sidebar")
        assert sidebar.region.width == 22
        buttons = sidebar.query("Button")
        assert len(buttons) >= 10
        for btn in buttons:
            assert btn.styles.height.value == 3
            assert btn.styles.border.top[0] == "solid"
            # Ensure text label fits inside the available inner box width
            assert len(str(btn.label)) <= 16

        # 2. Center Column checks: #queue-wrapper (2fr) & #log-wrapper (1fr)
        queue_wrap = app.query_one("#queue-wrapper")
        log_wrap = app.query_one("#log-wrapper")
        assert queue_wrap.styles.height.fraction == 2
        assert log_wrap.styles.height.fraction == 1
        assert queue_wrap.styles.border.top[0] == "round"
        assert log_wrap.styles.border.top[0] == "round"

        # 3. Right Column checks: #telemetry-wrapper & #visualizer-wrapper proportional heights
        telem_wrap = app.query_one("#telemetry-wrapper")
        vis_wrap = app.query_one("#visualizer-wrapper")
        assert telem_wrap.styles.height.fraction == 1
        assert vis_wrap.styles.height.fraction == 1
        assert telem_wrap.styles.border.top[0] == "round"
        assert vis_wrap.styles.border.top[0] == "round"

        # 4. Responsiveness checks
        rm = app.query_one("#resource-monitor", ResourceMonitorWidget)
        if size[0] < 100:
            assert rm.compact is True
        else:
            assert rm.compact is False

