"""
tests/test_visualizer_widget.py - Comprehensive Unit & Integration Tests for AudioVisualizerWidget.

Tests:
1. Mode Switcher Header:
   - Clean tab bar: [SPECTRUM], [OSCILLOSCOPE], [COMPACT].
   - Active tab highlighted cleanly with slate cyan accent, inactive tabs dim.
   - Mouse click hitboxes aligned precisely with rendered tab labels.
   - Click events on tabs switch modes directly; clicks on body cycle modes.
2. Main visualizer surface:
   - Mode A: Stereo VU meter and 7-band spectrum equalizer scaled to fill available height & width without scrolling.
   - Mode B: Braille oscilloscope waveform scaled to available terminal columns.
   - Mode C: Compact level meter.
3. Ensure no vertical or horizontal scrollbars appear inside the visualizer widget.
"""

from __future__ import annotations

import io
from unittest.mock import PropertyMock, patch

import pytest
from rich.cells import cell_len
from rich.console import Console
from textual.app import App, ComposeResult
from textual.events import Click
from textual.geometry import Size

from ui.tui_widgets.visualizer_widget import (
    AudioVisualizerWidget,
    STYLE_TAB_ACTIVE,
    STYLE_TAB_INACTIVE,
    VisualizerMode,
)


class TestVisualizerWidgetUnit:
    """Unit tests for AudioVisualizerWidget rendering logic and layout bounds."""

    def test_mode_tabs_labels_and_styling(self) -> None:
        """Verifies clean tab bar [SPECTRUM], [OSCILLOSCOPE], [COMPACT] with slate cyan accent and dim inactive."""
        vis = AudioVisualizerWidget()

        # 1. Mode SPECTRUM
        vis.mode = VisualizerMode.SPECTRUM
        header = vis._render_header_row(80)
        plain = header.plain
        assert "[SPECTRUM]" in plain
        assert "[OSCILLOSCOPE]" in plain
        assert "[COMPACT]" in plain

        # Check styles of active vs inactive tabs
        span_spectrum = next(s for s in header.spans if plain[s.start:s.end] == "[SPECTRUM]")
        span_oscillo = next(s for s in header.spans if plain[s.start:s.end] == "[OSCILLOSCOPE]")
        span_compact = next(s for s in header.spans if plain[s.start:s.end] == "[COMPACT]")

        assert str(span_spectrum.style) == STYLE_TAB_ACTIVE
        assert str(span_oscillo.style) == STYLE_TAB_INACTIVE
        assert str(span_compact.style) == STYLE_TAB_INACTIVE

        # 2. Mode OSCILLOSCOPE
        vis.mode = VisualizerMode.OSCILLOSCOPE
        header = vis._render_header_row(80)
        plain = header.plain
        span_spectrum = next(s for s in header.spans if plain[s.start:s.end] == "[SPECTRUM]")
        span_oscillo = next(s for s in header.spans if plain[s.start:s.end] == "[OSCILLOSCOPE]")
        span_compact = next(s for s in header.spans if plain[s.start:s.end] == "[COMPACT]")

        assert str(span_oscillo.style) == STYLE_TAB_ACTIVE
        assert str(span_spectrum.style) == STYLE_TAB_INACTIVE
        assert str(span_compact.style) == STYLE_TAB_INACTIVE

        # 3. Mode COMPACT
        vis.mode = VisualizerMode.COMPACT
        header = vis._render_header_row(80)
        plain = header.plain
        span_spectrum = next(s for s in header.spans if plain[s.start:s.end] == "[SPECTRUM]")
        span_oscillo = next(s for s in header.spans if plain[s.start:s.end] == "[OSCILLOSCOPE]")
        span_compact = next(s for s in header.spans if plain[s.start:s.end] == "[COMPACT]")

        assert str(span_compact.style) == STYLE_TAB_ACTIVE
        assert str(span_spectrum.style) == STYLE_TAB_INACTIVE
        assert str(span_oscillo.style) == STYLE_TAB_INACTIVE

    def test_mouse_click_hitboxes_alignment_and_actions(self) -> None:
        """Verifies mouse click hitboxes match rendered tab labels and trigger mode transitions."""
        vis = AudioVisualizerWidget()
        header = vis._render_header_row(80)
        hitboxes = vis._tab_hitboxes

        assert len(hitboxes) == 3
        expected_modes = [VisualizerMode.SPECTRUM, VisualizerMode.OSCILLOSCOPE, VisualizerMode.COMPACT]
        expected_labels = ["[SPECTRUM]", "[OSCILLOSCOPE]", "[COMPACT]"]

        for (start_x, end_x, v_mode), exp_mode, exp_label in zip(hitboxes, expected_modes, expected_labels):
            assert v_mode == exp_mode
            assert header.plain[start_x:end_x + 1] == exp_label
            # Clicking anywhere within this hitbox on row 0 selects this mode
            for click_x in range(start_x, end_x + 1):
                vis.mode = VisualizerMode.COMPACT if v_mode != VisualizerMode.COMPACT else VisualizerMode.SPECTRUM
                event = Click(widget=vis, x=click_x, y=0, delta_x=0, delta_y=0, button=1, shift=False, meta=False, ctrl=False)
                vis.on_click(event)
                assert vis.mode == v_mode

        # Clicks on row 1 (body) should cycle mode instead of triggering row 0 hitbox
        vis.mode = VisualizerMode.SPECTRUM
        event_body = Click(widget=vis, x=5, y=1, delta_x=0, delta_y=0, button=1, shift=False, meta=False, ctrl=False)
        vis.on_click(event_body)
        assert vis.mode == VisualizerMode.OSCILLOSCOPE

    def test_mode_a_spectrum_scaling_dimensions(self) -> None:
        """Verifies Mode A Stereo VU and 7-band spectrum scale across dimensions without overflowing."""
        vis = AudioVisualizerWidget(mode=VisualizerMode.SPECTRUM)
        vis.set_active(True)
        vis.update_levels(-6.0, -9.0)

        widths = [26, 36, 45, 60, 80, 100, 120]
        heights = [3, 5, 8, 12, 16, 24]

        for w in widths:
            for h in heights:
                with patch.object(AudioVisualizerWidget, "size", new_callable=PropertyMock) as mock_size:
                    mock_size.return_value = Size(w, h)
                    rend = vis.render()
                    console = Console(width=w, file=io.StringIO(), no_color=True)
                    console.print(rend)
                    lines = console.file.getvalue().splitlines()

                    assert len(lines) <= h, f"Mode A exceeded height {h} at width {w}: got {len(lines)} lines"
                    for idx, line in enumerate(lines):
                        assert cell_len(line) <= w, f"Mode A exceeded width {w} at line {idx}: {cell_len(line)} > {w}"

    def test_mode_b_oscilloscope_scaling_dimensions(self) -> None:
        """Verifies Mode B Braille oscilloscope scales across terminal widths without overflowing."""
        vis = AudioVisualizerWidget(mode=VisualizerMode.OSCILLOSCOPE)
        vis.set_active(True)

        widths = [26, 36, 45, 60, 80, 100, 120]
        heights = [3, 5, 8, 12, 16, 24]

        for w in widths:
            for h in heights:
                with patch.object(AudioVisualizerWidget, "size", new_callable=PropertyMock) as mock_size:
                    mock_size.return_value = Size(w, h)
                    rend = vis.render()
                    console = Console(width=w, file=io.StringIO(), no_color=True)
                    console.print(rend)
                    lines = console.file.getvalue().splitlines()

                    assert len(lines) <= h, f"Mode B exceeded height {h} at width {w}: got {len(lines)} lines"
                    for idx, line in enumerate(lines):
                        assert cell_len(line) <= w, f"Mode B exceeded width {w} at line {idx}: {cell_len(line)} > {w}"

    def test_mode_c_compact_scaling_dimensions(self) -> None:
        """Verifies Mode C compact level meter renders cleanly without overflowing."""
        vis = AudioVisualizerWidget(mode=VisualizerMode.COMPACT)
        vis.set_active(True)

        widths = [26, 36, 45, 60, 80, 100, 120]
        heights = [3, 5, 8, 12, 16, 24]

        for w in widths:
            for h in heights:
                with patch.object(AudioVisualizerWidget, "size", new_callable=PropertyMock) as mock_size:
                    mock_size.return_value = Size(w, h)
                    rend = vis.render()
                    console = Console(width=w, file=io.StringIO(), no_color=True)
                    console.print(rend)
                    lines = console.file.getvalue().splitlines()

                    assert len(lines) <= h, f"Mode C exceeded height {h} at width {w}: got {len(lines)} lines"
                    for idx, line in enumerate(lines):
                        assert cell_len(line) <= w, f"Mode C exceeded width {w} at line {idx}: {cell_len(line)} > {w}"

    def test_minimal_fallback_extremely_small_terminal(self) -> None:
        """Verifies minimal fallback rendering when terminal size is very small."""
        vis = AudioVisualizerWidget()
        with patch.object(AudioVisualizerWidget, "size", new_callable=PropertyMock) as mock_size:
            mock_size.return_value = Size(18, 2)
            rend = vis.render()
            console = Console(width=18, file=io.StringIO(), no_color=True)
            console.print(rend)
            lines = console.file.getvalue().splitlines()
            assert len(lines) <= 2


class VisualizerHarnessApp(App):
    """Test harness application for Textual visualizer integration."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #vis-container {
        width: 60;
        height: 14;
        border: none;
        padding: 0;
        margin: 0;
    }
    """

    def compose(self) -> ComposeResult:
        from textual.containers import Container
        with Container(id="vis-container"):
            yield AudioVisualizerWidget(id="test-vis")


@pytest.mark.asyncio
async def test_visualizer_widget_in_textual_app() -> None:
    """Verifies that visualizer widget mounts inside Textual with overflow hidden and responsive clicks."""
    app = VisualizerHarnessApp()
    async with app.run_test() as pilot:
        vis = app.query_one("#test-vis", AudioVisualizerWidget)
        assert vis is not None

        # Verify overflow hidden to ensure no scrollbars appear
        assert vis.styles.overflow_x == "hidden"
        assert vis.styles.overflow_y == "hidden"

        # Check default mode
        assert vis.mode == VisualizerMode.SPECTRUM

        # Click on OSCILLOSCOPE tab via pilot (offset x~18 on row 0)
        # Tab 1: [SPECTRUM] is ~1..10, Tab 2: [OSCILLOSCOPE] is ~12..25
        await pilot.click(AudioVisualizerWidget, offset=(18, 0))
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.OSCILLOSCOPE

        # Click on COMPACT tab via pilot (offset x~30 on row 0)
        await pilot.click(AudioVisualizerWidget, offset=(30, 0))
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.COMPACT

        # Click on SPECTRUM tab via pilot (offset x~5 on row 0)
        await pilot.click(AudioVisualizerWidget, offset=(5, 0))
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.SPECTRUM

        # Verify keyboard shortcuts
        await pilot.press("2")
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.OSCILLOSCOPE

        await pilot.press("3")
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.COMPACT

        await pilot.press("1")
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.SPECTRUM

        await pilot.press("v")
        await pilot.pause(0.05)
        assert vis.mode == VisualizerMode.OSCILLOSCOPE
