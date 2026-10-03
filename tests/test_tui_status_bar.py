"""
tests/test_tui_status_bar.py - Comprehensive Unit Tests for TUIStatusBar.
"""

import asyncio
import threading
import time
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from ui.tui_widgets.status_bar import TUIStatusBar


class TestTUIStatusBarUnit:
    """Unit tests for TUIStatusBar state, formatters, and helper methods."""

    def test_default_initialization(self) -> None:
        bar = TUIStatusBar()
        assert bar.status == "READY"
        assert bar.preset == "MP3 320k | EBU R128"
        assert bar.gpu_status is False
        assert bar.watch_status is False
        assert bar.threads_status == "0/0"
        assert bar.show_clock is True

    def test_custom_initialization(self) -> None:
        bar = TUIStatusBar(
            status="CONVERTING [1/5]",
            preset="FLAC Lossless",
            gpu=True,
            watch=True,
            threads="4/16",
            show_clock=False,
            badge_style="subtle",
        )
        assert bar.status == "CONVERTING [1/5]"
        assert bar.preset == "FLAC Lossless"
        assert bar.gpu_status is True
        assert bar.watch_status is True
        assert bar.threads_status == "4/16"
        assert bar.show_clock is False
        assert bar.badge_style == "subtle"

    def test_set_status_helpers(self) -> None:
        bar = TUIStatusBar()
        bar.set_status("PAUSED")
        assert bar.status == "PAUSED"

        bar.set_status("CONVERTING", 3, 10)
        assert bar.status == "CONVERTING [3/10]"

        bar.set_status("ALL DONE")
        assert bar.status == "ALL DONE"

    def test_set_preset_helper(self) -> None:
        bar = TUIStatusBar()
        bar.set_preset("OPUS 128k VBR")
        assert bar.preset == "OPUS 128k VBR"

    def test_hardware_helpers(self) -> None:
        bar = TUIStatusBar()
        bar.set_gpu(True)
        assert bar.gpu_status is True
        bar.set_gpu("CUDA")
        assert bar.gpu_status == "CUDA"

        bar.set_watch(True)
        assert bar.watch_status is True

        bar.set_threads(4, 12)
        assert bar.threads_status == "4/12"

        bar.set_hardware_info(gpu=False, watch=False, threads="8/16")
        assert bar.gpu_status is False
        assert bar.watch_status is False
        assert bar.threads_status == "8/16"

        bar.set_hardware_info(threads=(2, 8))
        assert bar.threads_status == "2/8"

    def test_format_status_badge(self) -> None:
        bar = TUIStatusBar()
        # Test solid style
        badge_ready = bar._format_status_badge("READY")
        assert "READY" in badge_ready

        badge_conv = bar._format_status_badge("CONVERTING [3/10]")
        assert "CONVERTING [3/10]" in badge_conv

        badge_paused = bar._format_status_badge("PAUSED")
        assert "PAUSED" in badge_paused

        badge_done = bar._format_status_badge("ALL DONE")
        assert "ALL DONE" in badge_done

        # Test subtle style
        bar.badge_style = "subtle"
        badge_subtle = bar._format_status_badge("READY")
        assert "READY" in badge_subtle

        # Test bracket style
        bar.badge_style = "bracket"
        badge_bracket = bar._format_status_badge("READY")
        assert "[READY]" in badge_bracket

    def test_format_notification_markup(self) -> None:
        bar = TUIStatusBar()
        info_m = bar._format_notification_markup("Added 4 files", "info")
        assert "Added 4 files" in info_m
        assert "*" in info_m

        succ_m = bar._format_notification_markup("Saved report", "success")
        assert "Saved report" in succ_m
        assert "+" in succ_m

        warn_m = bar._format_notification_markup("Queue paused", "warning")
        assert "Queue paused" in warn_m
        assert "!" in warn_m

        err_m = bar._format_notification_markup("Transcode failed", "error")
        assert "Transcode failed" in err_m
        assert "x" in err_m

        dim_m = bar._format_notification_markup("Fading message", "info", faded=True)
        assert "[dim]Fading message[/]" == dim_m

    def test_render_markup_and_rich_text(self) -> None:
        bar = TUIStatusBar(
            status="READY",
            preset="MP3 320k | EBU R128",
            gpu=True,
            watch=False,
            threads="4/12",
        )
        markup = bar.render_markup()
        assert "READY" in markup
        assert "MP3 320k | EBU R128" in markup
        assert "GPU:" in markup
        assert "THREADS:" in markup

        rich_text = bar.render_rich_text()
        assert "READY" in rich_text.plain
        assert "MP3 320k | EBU R128" in rich_text.plain


class StatusBarTestApp(App):
    """Test harness application for TUIStatusBar."""

    def __init__(self, **bar_kwargs):
        super().__init__()
        self.bar_kwargs = bar_kwargs

    def compose(self) -> ComposeResult:
        yield TUIStatusBar(id="status-bar", **self.bar_kwargs)


@pytest.mark.asyncio
async def test_textual_app_lifecycle() -> None:
    """Test TUIStatusBar mounting, rendering, and dynamic updates inside a real Textual App."""
    app = StatusBarTestApp(
        status="READY",
        preset="MP3 320k | EBU R128",
        gpu=True,
        watch=False,
        threads="4/12",
    )
    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar", TUIStatusBar)
        assert bar.is_mounted is True

        left_widget = app.query_one("#status-bar-left", Static)
        center_widget = app.query_one("#status-bar-center", Static)
        right_widget = app.query_one("#status-bar-right", Static)

        # Verify left section initial content
        assert "READY" in str(left_widget.render())
        assert "MP3 320k | EBU R128" in str(left_widget.render())

        # Verify right section initial content
        assert "GPU:" in str(right_widget.render())
        assert "THREADS:" in str(right_widget.render())

        # Update status dynamically
        bar.set_status("CONVERTING", 3, 10)
        bar.set_preset("FLAC 24-bit")
        await pilot.pause()

        assert "CONVERTING [3/10]" in str(left_widget.render())
        assert "FLAC 24-bit" in str(left_widget.render())

        # Test notification delivery with short test duration
        bar.notify_status("Added 4 files from clipboard", level="info", duration=0.2)
        # Wait for fade
        await pilot.pause(0.12)
        rendered_fade = center_widget.render()
        assert "Added 4 files from clipboard" in str(rendered_fade)

        # Wait for dismiss
        await pilot.pause(0.15)
        assert str(center_widget.render()) == ""

        # Test clear_notification
        bar.notify_status("Test persistent", level="info", duration=0)
        await pilot.pause(0.02)
        assert "Test persistent" in str(center_widget.render())
        bar.clear_notification()
        await pilot.pause(0.02)
        assert str(center_widget.render()) == ""


@pytest.mark.asyncio
async def test_thread_safe_notify() -> None:
    """Test notifying from a background thread outside the main event loop."""
    app = StatusBarTestApp(status="READY")
    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar", TUIStatusBar)

        def worker():
            time.sleep(0.05)
            bar.notify_status("Background worker complete", level="success", duration=0.5)

        thread = threading.Thread(target=worker)
        thread.start()

        # Wait for thread notification to arrive
        await pilot.pause(0.15)
        thread.join()

        center_widget = app.query_one("#status-bar-center", Static)
        assert "Background worker complete" in str(center_widget.render())
