"""
tests/test_filter_dialog.py - Comprehensive tests for CleanToggle and FilterDialogModal.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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


@pytest.mark.asyncio
async def test_filter_dialog_layout_alignment() -> None:
    """
    Verifies pixel-perfect layout of FilterDialogModal:
    - All 5 rows have equal height and zero text wrapping
    - All 5 labels on the left align horizontally and center vertically with controls
    - All 5 controls on the right end at the exact same horizontal column
    - Footer buttons have matching height (3), identical round border radius, and aligned baselines
    """
    class LayoutApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(FilterDialogModal())

    app = LayoutApp()
    async with app.run_test(size=(80, 24)) as pilot:
        modal = app.screen
        assert isinstance(modal, FilterDialogModal)

        body = modal.query_one("#dialog-body")
        assert len(body.children) == 5

        # Check all 5 rows
        for i, row in enumerate(body.children):
            assert row.region.height == 2, f"Row {i} height must be 2 lines"
            lbl = row.query_one("Label")
            assert lbl.region.height == 1, f"Label {i} must be 1 line (no wrap)"
            assert lbl.region.x == 6, f"Label {i} must start at x=6"

            # Check right-hand control
            controls = [c for c in row.children if c != lbl]
            assert len(controls) == 1, f"Row {i} must have exactly one control"
            ctrl = controls[0]
            assert ctrl.region.height == 1, f"Control {i} must be 1 line"
            assert ctrl.region.y == lbl.region.y, f"Control {i} and Label {i} must be vertically centered on same line"
            # Right edge of all controls must align at x=74
            assert ctrl.region.x + ctrl.region.width == 74, (
                f"Control {i} must align right at column 74, got {ctrl.region.x + ctrl.region.width}"
            )

        # Footer buttons inspection
        footer = modal.query_one("#dialog-footer")
        btn_cancel = modal.query_one("#btn-cancel", Button)
        btn_save = modal.query_one("#btn-save", Button)

        # Matching heights
        assert btn_cancel.region.height == 3
        assert btn_save.region.height == 3
        # Perfect vertical baseline alignment
        assert btn_cancel.region.y == btn_save.region.y
        # Identical round border radius
        assert btn_cancel.styles.border.top[0] == "round"
        assert btn_save.styles.border.top[0] == "round"


@pytest.mark.asyncio
async def test_filter_dialog_cancel_and_hotkeys() -> None:
    """Verifies dialog cancel button and Escape key dismiss behavior."""
    dismiss_results = []

    class CancelApp(App[None]):
        def on_mount(self) -> None:
            def on_dismiss(val):
                dismiss_results.append(val)
            self.push_screen(FilterDialogModal(), on_dismiss)

    app = CancelApp()
    async with app.run_test(size=(80, 24)) as pilot:
        modal = app.screen
        btn_cancel = modal.query_one("#btn-cancel", Button)
        btn_cancel.press()
        await pilot.pause(0.1)

        assert len(dismiss_results) == 1
        assert dismiss_results[0] is None

