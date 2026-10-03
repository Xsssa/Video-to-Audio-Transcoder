"""
tests/test_filter_dialog.py - Comprehensive tests for CleanToggle and FilterDialogModal.
"""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Button

from ui.tui_screens.filter_dialog import CleanToggle, FilterDialogModal


class FilterDialogTestApp(App[None]):
    def compose(self) -> ComposeResult:
        yield CleanToggle(value=False, id="test-toggle")


@pytest.mark.asyncio
async def test_clean_toggle_interaction() -> None:
    """Verifies that CleanToggle renders cleanly and toggles with mouse and keyboard."""
    app = FilterDialogTestApp()
    async with app.run_test() as pilot:
        toggle = app.query_one("#test-toggle", CleanToggle)
        assert toggle.value is False
        assert "OFF" in toggle.render().plain

        # Test click toggle
        await pilot.click(CleanToggle)
        assert toggle.value is True
        assert "ON" in toggle.render().plain

        # Test space key toggle
        await pilot.press("space")
        assert toggle.value is False
        assert "OFF" in toggle.render().plain

        # Test enter key toggle
        await pilot.press("enter")
        assert toggle.value is True
        assert "ON" in toggle.render().plain


@pytest.mark.asyncio
async def test_filter_dialog_modal_lifecycle() -> None:
    """Verifies FilterDialogModal options extraction, clean toggles, and button actions."""
    result_holder = {}

    class ModalHarnessApp(App[None]):
        def on_mount(self) -> None:
            def on_dismiss(opts):
                result_holder["options"] = opts

            self.push_screen(
                FilterDialogModal(
                    initial_options={
                        "ebu_r128": True,
                        "lossless_copy_if_match": True,
                        "sample_rate": 48000,
                        "channels": 2,
                        "preserve_cover_art": False,
                    }
                ),
                on_dismiss,
            )

    app = ModalHarnessApp()
    async with app.run_test() as pilot:
        modal = app.screen
        assert isinstance(modal, FilterDialogModal)

        # Check options extraction
        opts = modal.get_options()
        assert opts["loudness_normalization"] is True
        assert opts["ebu_r128"] is True
        assert opts["lossless_stream_copy"] is True
        assert opts["sample_rate"] == 48000
        assert opts["channels"] == 2
        assert opts["preserve_cover_art"] is False

        # Toggle cover art
        cover_toggle = modal.query_one("#switch-cover-art", CleanToggle)
        await pilot.click("#switch-cover-art")
        assert cover_toggle.value is True

        # Click save button
        save_btn = modal.query_one("#btn-save", Button)
        save_btn.press()
        await pilot.pause(0.1)

        saved = result_holder.get("options")
        assert saved is not None
        assert saved["preserve_cover_art"] is True
        assert saved["ebu_r128"] is True
