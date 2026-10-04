"""
Tests for ui.tui_screens.help_screen (HelpModalScreen & HelpView).
"""

from __future__ import annotations

from pathlib import Path
import sys

# Add workspace root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from textual.app import App

from ui.tui_screens.help_screen import (
    HelpModalScreen,
    HelpView,
    SHORTCUT_CATEGORIES,
    MOUSE_ACTIONS_GUIDE,
    PRO_TIPS_GUIDE,
    format_key_badges,
    build_shortcut_table,
    build_mouse_guide_table,
    build_tips_table,
)


class HelpTestApp(App[None]):
    """Minimal test application runner."""
    pass


def test_shortcut_categories_structure():
    """Verify all required shortcut categories and keys are present."""
    assert "NAVIGATION" in SHORTCUT_CATEGORIES
    assert "ACTIONS" in SHORTCUT_CATEGORIES
    assert "MODALS & OVERLAYS" in SHORTCUT_CATEGORIES
    assert "QUEUE CONTROL" in SHORTCUT_CATEGORIES

    # Navigation items
    nav_keys = [k for k, _ in SHORTCUT_CATEGORIES["NAVIGATION"]]
    assert any("j" in k and "k" in k and "Arrows" in k for k in nav_keys)
    assert any("Enter" in k for k in nav_keys)

    # Actions items
    action_keys = [k for k, _ in SHORTCUT_CATEGORIES["ACTIONS"]]
    assert any("a" in k for k in action_keys)
    assert any("b" in k for k in action_keys)
    assert "v" in action_keys
    assert "s" in action_keys

    # Modals items
    modal_keys = [k for k, _ in SHORTCUT_CATEGORIES["MODALS & OVERLAYS"]]
    assert "p" in modal_keys
    assert "f" in modal_keys
    assert "h" in modal_keys
    assert "?" in modal_keys

    # Queue Control items
    queue_keys = [k for k, _ in SHORTCUT_CATEGORIES["QUEUE CONTROL"]]
    assert any("d" in k and "Delete" in k for k in queue_keys)
    assert "c" in queue_keys
    assert "q" in queue_keys


def test_mouse_actions_guide_structure():
    """Verify all required mouse actions are covered in the guide."""
    actions = [a.lower() for a, _ in MOUSE_ACTIONS_GUIDE]
    assert any("sidebar" in a for a in actions)
    assert any("table rows" in a for a in actions)
    assert any("borders" in a for a in actions)


def test_pro_tips_guide_structure():
    """Verify hardware acceleration, watch folder daemon, and lossless copy tips."""
    features = [f.lower() for f, _ in PRO_TIPS_GUIDE]
    assert any("hardware acceleration" in f for f in features)
    assert any("watch folder" in f for f in features)
    assert any("lossless stream copy" in f for f in features)

    descriptions = " ".join([d.lower() for _, d in PRO_TIPS_GUIDE])
    assert "nvenc" in descriptions or "cuda" in descriptions
    assert "quicksync" in descriptions or "qsv" in descriptions
    assert "amf" in descriptions
    assert "-c:a copy" in descriptions


def test_render_helpers():
    """Verify table builder and badge formatters."""
    badges = format_key_badges("j / k / Arrows")
    assert "j" in badges
    assert "k" in badges
    assert "Arrows" in badges

    table_shortcuts = build_shortcut_table(SHORTCUT_CATEGORIES["NAVIGATION"])
    assert table_shortcuts.row_count == len(SHORTCUT_CATEGORIES["NAVIGATION"])

    table_mouse = build_mouse_guide_table(MOUSE_ACTIONS_GUIDE)
    assert table_mouse.row_count == len(MOUSE_ACTIONS_GUIDE)

    table_tips = build_tips_table(PRO_TIPS_GUIDE)
    assert table_tips.row_count == len(PRO_TIPS_GUIDE)


@pytest.mark.asyncio
async def test_help_modal_mount_and_dom():
    """Verify HelpModalScreen DOM hierarchy and widget containment."""
    app = HelpTestApp()
    async with app.run_test() as pilot:
        screen = HelpModalScreen()
        await app.push_screen(screen)
        assert app.screen is screen

        # Verify dialog container and header
        assert screen.query_one("#help-dialog-container") is not None
        assert screen.query_one("#help-header") is not None
        assert screen.query_one("#help-dialog-title") is not None
        assert screen.query_one("#btn-close") is not None

        # Verify HelpView and cards
        help_view = screen.query_one("#help-view-body", HelpView)
        assert help_view is not None
        assert help_view.query_one("#card-navigation") is not None
        assert help_view.query_one("#card-actions") is not None
        assert help_view.query_one("#card-modals") is not None
        assert help_view.query_one("#card-queue") is not None
        assert help_view.query_one("#card-mouse-guide") is not None
        assert help_view.query_one("#card-pro-tips") is not None

        # Verify footer
        assert screen.query_one("#help-footer") is not None
        assert screen.query_one("#help-footer-hint") is not None
        assert screen.query_one("#btn-close-bottom") is not None


@pytest.mark.asyncio
async def test_help_modal_dismiss_escape():
    """Verify dismissal with Escape key."""
    app = HelpTestApp()
    async with app.run_test() as pilot:
        screen = HelpModalScreen()
        await app.push_screen(screen)
        assert app.screen is screen
        await pilot.press("escape")
        assert app.screen is not screen


@pytest.mark.asyncio
async def test_help_modal_dismiss_question_mark():
    """Verify dismissal with '?' and 'question_mark' keys."""
    app = HelpTestApp()
    async with app.run_test() as pilot:
        # question_mark binding
        screen1 = HelpModalScreen()
        await app.push_screen(screen1)
        assert app.screen is screen1
        await pilot.press("question_mark")
        assert app.screen is not screen1

        # literal '?' binding / key
        screen2 = HelpModalScreen()
        await app.push_screen(screen2)
        assert app.screen is screen2
        await pilot.press("?")
        assert app.screen is not screen2


@pytest.mark.asyncio
async def test_help_modal_dismiss_buttons():
    """Verify dismissal with header close button and footer close button."""
    app = HelpTestApp()
    async with app.run_test() as pilot:
        # Top button
        screen1 = HelpModalScreen()
        await app.push_screen(screen1)
        assert app.screen is screen1
        await pilot.click("#btn-close")
        assert app.screen is not screen1

        # Bottom button
        screen2 = HelpModalScreen()
        await app.push_screen(screen2)
        assert app.screen is screen2
        await pilot.click("#btn-close-bottom")
        assert app.screen is not screen2
