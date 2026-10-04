"""
tests/test_tui_theme.py - Unit tests for Enterprise Slate-Dark TUI Theme & Styling.
"""

import pytest
from textual.css.stylesheet import Stylesheet

from ui.tui_theme import (
    THEME_COLORS,
    BG_ROOT,
    BG_SURFACE,
    BORDER_SUBTLE,
    ACCENT_PRIMARY,
    SLATE_DARK_THEME,
    TUI_CSS,
    get_theme_color,
    get_status_style,
    get_status_badge_markup,
    get_status_badge_text,
    format_log_entry,
    get_spectrum_palette,
    get_vu_meter_color,
    get_tui_css,
    apply_tui_theme,
)


class TestTuiTheme:
    """Test suite for TUI theme colors, CSS validity, and helper functions."""

    def test_css_syntax_validity(self):
        """Verifies that TUI_CSS compiles cleanly with Textual Stylesheet parser."""
        sheet = Stylesheet()
        sheet.add_source(TUI_CSS)
        sheet.parse()
        assert len(sheet.rules) > 0

    def test_theme_colors_integrity(self):
        """Verifies that all core palette keys are defined with valid hex or rgba colors."""
        required_keys = [
            "bg_root",
            "bg_surface",
            "bg_sidebar",
            "bg_card",
            "border_subtle",
            "border_focus",
            "text_primary",
            "text_secondary",
            "text_muted",
            "accent_primary",
            "status_pending_fg",
            "status_converting_fg",
            "status_completed_fg",
            "status_failed_fg",
            "status_probing_fg",
        ]
        for key in required_keys:
            assert key in THEME_COLORS, f"Missing color key: {key}"
            assert THEME_COLORS[key].startswith("#") or THEME_COLORS[key].startswith("rgba")

    def test_textual_theme_object(self):
        """Verifies the registered Textual Theme instance."""
        assert SLATE_DARK_THEME.name == "slate-dark"
        assert SLATE_DARK_THEME.dark is True
        assert SLATE_DARK_THEME.primary == ACCENT_PRIMARY

    def test_get_theme_color(self):
        """Verifies color retrieval with default fallback."""
        assert get_theme_color("bg_root") == BG_ROOT
        assert get_theme_color("non_existent_key", "#123456") == "#123456"

    def test_status_styles(self):
        """Verifies status styles return muted, cohesive styling."""
        for status in ["CONVERTING", "converting", "COMPLETED", "FAILED", "PROBING", "PAUSED", "PENDING"]:
            style = get_status_style(status)
            assert "fg" in style
            assert "bg" in style
            assert "label" in style
            assert style["fg"].startswith("#")
            assert style["bg"].startswith("#")

    def test_status_badge_markup(self):
        """Verifies Rich markup formatting for status badges."""
        markup = get_status_badge_markup("converting")
        assert "CONVERTING" in markup
        assert "[" in markup and "]" in markup

        markup_bracketed = get_status_badge_markup("completed", include_brackets=True)
        assert "[COMPLETED]" in markup_bracketed or "[ COMPLETED ]" in markup_bracketed

    def test_status_badge_text(self):
        """Verifies rich.text.Text creation for badges."""
        text = get_status_badge_text("failed")
        assert "FAILED" in text.plain
        assert text.style is not None

    def test_format_log_entry(self):
        """Verifies terminal log entry markup."""
        entry = format_log_entry("TRANSCODE", "Task finished", "14:22:01")
        assert "TRANSCODE" in entry
        assert "Task finished" in entry
        assert "14:22:01" in entry

    def test_spectrum_palette(self):
        """Verifies 7-band monochromatic gradient palette."""
        palette = get_spectrum_palette()
        assert len(palette) == 7
        for color in palette:
            assert color.startswith("#")

    def test_vu_meter_levels(self):
        """Verifies non-rainbow progression for audio meter."""
        low = get_vu_meter_color(0.3)
        mid = get_vu_meter_color(0.8)
        high = get_vu_meter_color(0.92)
        peak = get_vu_meter_color(0.99)
        assert low != peak
        assert all(c.startswith("#") for c in [low, mid, high, peak])

    def test_apply_tui_theme_mock(self):
        """Verifies apply_tui_theme sets CSS on an app-like object."""
        class MockApp:
            CSS = ""
            def register_theme(self, t):
                self.registered = t

        app = MockApp()
        apply_tui_theme(app)
        assert hasattr(app, "registered")
        assert app.theme == "slate-dark"
        assert "ENTERPRISE SLATE-DARK TUI THEME" in app.CSS

    def test_unified_button_styles(self):
        """Verifies unified Button styles, states, and variants are defined in TUI_CSS."""
        assert "Button {" in TUI_CSS
        assert "Button:hover {" in TUI_CSS
        assert "Button:focus {" in TUI_CSS
        assert "Button.-active" in TUI_CSS
        assert "Button:disabled {" in TUI_CSS
        assert "Button.-primary" in TUI_CSS
        assert "Button.-success" in TUI_CSS
        assert "Button.-error" in TUI_CSS
        assert "Button.-warning" in TUI_CSS
        assert "#btn-cancel" in TUI_CSS

    def test_unified_modal_dialogs(self):
        """Verifies ModalScreen and dialog containers across screens are unified."""
        assert "ModalScreen" in TUI_CSS
        assert ".modal-dialog" in TUI_CSS
        assert "#picker-dialog" in TUI_CSS
        assert "#filter-dialog-container" in TUI_CSS
        assert "#help-dialog-container" in TUI_CSS
        assert "#history-modal-dialog" in TUI_CSS
        assert "#preset-dialog-box" in TUI_CSS

    def test_unified_scrollbars(self):
        """Verifies scrollbar rules are slim 1x1 and slate-themed across all containers."""
        assert "scrollbar-size: 1 1;" in TUI_CSS
        assert "scrollbar-color: #242938;" in TUI_CSS
        assert "scrollbar-background: #10121a;" in TUI_CSS
        assert "VerticalScroll" in TUI_CSS
        assert "DataTable" in TUI_CSS

    def test_unified_card_borders(self):
        """Verifies cards and panels share unified subtle borders."""
        assert ".panel" in TUI_CSS
        assert ".card" in TUI_CSS
        assert "#queue-container" in TUI_CSS
        assert "#monitor-container" in TUI_CSS
        assert "#log-container" in TUI_CSS
        assert "border: round #242938;" in TUI_CSS

    def test_status_bar_global_rules(self):
        """Verifies TUIStatusBar and its 3 sections are included in global TUI_CSS."""
        assert "TUIStatusBar {" in TUI_CSS
        assert "#status-bar-left {" in TUI_CSS
        assert "#status-bar-center {" in TUI_CSS
        assert "#status-bar-right {" in TUI_CSS
        assert "text-wrap: nowrap;" in TUI_CSS
        assert "text-overflow: ellipsis;" in TUI_CSS

    def test_dark_aesthetic_zero_gaudy_colors(self):
        """Verifies all theme colors follow a clean, low-saturation dark palette."""
        # Ensure deep dark background values
        assert THEME_COLORS["bg_root"] == "#0d0f14"
        assert THEME_COLORS["bg_surface"] == "#13161f"
        assert THEME_COLORS["bg_header"] == "#10121a"
        assert THEME_COLORS["bg_footer"] == "#10121a"

        # Ensure no pure neon saturated primaries (e.g. #ff0000, #00ff00, #0000ff)
        gaudy_colors = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff"]
        for color_val in THEME_COLORS.values():
            assert color_val.lower() not in gaudy_colors

