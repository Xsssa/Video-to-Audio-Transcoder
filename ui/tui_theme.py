"""
ui/tui_theme.py - Enterprise Dark Monochrome Theme & Styling for Video-to-Audio Transcoder.

Design Philosophy:
- High-end, sleek, professional terminal aesthetic inspired by lazygit, btop, neovim, and k9s.
- Minimalist dark monochrome foundation (obsidian/slate charcoal) with subtle, tasteful accents.
- Zero gaudy or oversaturated rainbow colors ("tidak warna-warni").
- Clean whitespace, crisp typographic hierarchy, thin subtle borders, and smooth hover feedback.
- Tailored for long enterprise operations without visual fatigue.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

from rich.text import Text
from textual.theme import Theme

# =============================================================================
# THEME COLOR PALETTE (Minimalist Dark Slate / Charcoal Monochrome)
# =============================================================================

THEME_COLORS: Dict[str, str] = {
    # Base Canvas & Surface Backgrounds (Layered dark slate)
    "bg_root": "#0d0f14",           # Deepest root canvas
    "bg_screen": "#0d0f14",         # Application screen background
    "bg_surface": "#13161f",        # Primary container surface
    "bg_surface_alt": "#171a26",    # Alternate row / sub-container background
    "bg_panel": "#141722",          # Standard panel background
    "bg_sidebar": "#10121a",        # Sidebar docked panel background
    "bg_card": "#181c28",           # Elevated cards, popups, and dialogs
    "bg_modal_backdrop": "rgba(8, 10, 14, 0.85)", # Modal dimmed overlay
    "bg_header": "#10121a",         # Header bar background
    "bg_footer": "#10121a",         # Footer bar background
    "bg_hover": "#1d2332",          # Row and button hover highlight
    "bg_active": "#242d40",         # Pressed or active item background
    "bg_input": "#0f1118",          # Text field / input background
    "bg_cursor": "#222a3d",         # DataTable cursor line highlight

    # Borders & Dividers (Subtle, thin, non-distracting)
    "border_dim": "#1b1f2b",        # Very subtle internal divider
    "border_subtle": "#242938",     # Standard panel border
    "border_focus": "#3d5470",      # Border when panel or input has focus
    "border_active": "#4ba3be",     # Active accent border (muted cyan)
    "border_modal": "#364157",      # Dialog / modal window border

    # Text & Foreground Typography (Clear hierarchy, no harsh white)
    "text_primary": "#e1e4ec",      # Primary crisp text (clean off-white)
    "text_secondary": "#9aa2b4",    # Secondary information, labels
    "text_muted": "#5e6678",        # Dim comments, brackets, disabled hints
    "text_dim": "#484f60",          # Inactive timestamps, faint separators
    "text_inverse": "#0d0f14",      # Dark text on bright badge highlights
    "text_highlight": "#ffffff",    # Strong emphasis / cursor item

    # Tasteful Accents (Muted Slate Cyan / Steel - Low Saturation)
    "accent_primary": "#4ba3be",    # Muted cyan/slate - main functional accent
    "accent_secondary": "#60728c",  # Slate steel - secondary accent
    "accent_hover": "#5cbcdb",      # Slightly brighter cyan for hover
    "accent_subtle": "#1e3745",     # Dark muted cyan background tint
    "accent_focus": "#4f98b2",      # Input/button focus ring

    # Semantic Status Indicators (Subtle & Desaturated, never neon)
    "status_pending_fg": "#9e9b86",       # Desaturated warm sand
    "status_pending_bg": "#1f1f1c",
    "status_pending_border": "#36362e",

    "status_probing_fg": "#759bb0",       # Muted slate ice
    "status_probing_bg": "#152028",
    "status_probing_border": "#223847",

    "status_converting_fg": "#58aeca",    # Muted active cyan
    "status_converting_bg": "#142531",
    "status_converting_border": "#214257",

    "status_completed_fg": "#72a37d",     # Muted sage / olive green
    "status_completed_bg": "#16241a",
    "status_completed_border": "#243c2b",

    "status_failed_fg": "#b36262",        # Muted dusky rust / clay
    "status_failed_bg": "#28171a",
    "status_failed_border": "#472327",

    "status_paused_fg": "#828996",        # Neutral slate gray
    "status_paused_bg": "#191c23",
    "status_paused_border": "#2a303d",

    "status_warning_fg": "#b5935d",       # Muted amber / wheat
    "status_warning_bg": "#252016",
    "status_warning_border": "#423720",

    # Audio Telemetry & Visualizer (Luminance gradient instead of rainbow)
    "vu_low": "#3e6f88",                  # Normal audio level (muted cyan)
    "vu_mid": "#58aeca",                  # Mid level (clean cyan)
    "vu_high": "#b5935d",                 # Approaching peak (muted amber)
    "vu_peak": "#b36262",                 # Peak clip (muted rust)
}

# Individual color constants for direct convenience
BG_ROOT: str = THEME_COLORS["bg_root"]
BG_SCREEN: str = THEME_COLORS["bg_screen"]
BG_SURFACE: str = THEME_COLORS["bg_surface"]
BG_SURFACE_ALT: str = THEME_COLORS["bg_surface_alt"]
BG_PANEL: str = THEME_COLORS["bg_panel"]
BG_SIDEBAR: str = THEME_COLORS["bg_sidebar"]
BG_CARD: str = THEME_COLORS["bg_card"]
BG_HOVER: str = THEME_COLORS["bg_hover"]
BG_ACTIVE: str = THEME_COLORS["bg_active"]
BG_INPUT: str = THEME_COLORS["bg_input"]
BG_CURSOR: str = THEME_COLORS["bg_cursor"]

BORDER_DIM: str = THEME_COLORS["border_dim"]
BORDER_SUBTLE: str = THEME_COLORS["border_subtle"]
BORDER_FOCUS: str = THEME_COLORS["border_focus"]
BORDER_ACTIVE: str = THEME_COLORS["border_active"]
BORDER_MODAL: str = THEME_COLORS["border_modal"]

TEXT_PRIMARY: str = THEME_COLORS["text_primary"]
TEXT_SECONDARY: str = THEME_COLORS["text_secondary"]
TEXT_MUTED: str = THEME_COLORS["text_muted"]
TEXT_DIM: str = THEME_COLORS["text_dim"]
TEXT_HIGHLIGHT: str = THEME_COLORS["text_highlight"]

ACCENT_PRIMARY: str = THEME_COLORS["accent_primary"]
ACCENT_SECONDARY: str = THEME_COLORS["accent_secondary"]
ACCENT_HOVER: str = THEME_COLORS["accent_hover"]
ACCENT_SUBTLE: str = THEME_COLORS["accent_subtle"]

# 7-Band Equalizer Gradient: Sleek Monochromatic Slate Cyan -> Soft Ice
SPECTRUM_GRADIENT: List[str] = [
    "#253d4b",  # 60 Hz   - deep slate teal
    "#2c4c5e",  # 150 Hz  - dark teal
    "#355d72",  # 400 Hz  - muted slate cyan
    "#3e6f88",  # 1 kHz   - slate cyan
    "#4982a0",  # 2.5 kHz - medium cyan
    "#5597b8",  # 6 kHz   - soft bright cyan
    "#64aed3",  # 15 kHz  - crisp ice cyan
]


# =============================================================================
# TEXTUAL THEME SPECIFICATION
# =============================================================================

SLATE_DARK_THEME: Theme = Theme(
    name="slate-dark",
    primary=THEME_COLORS["accent_primary"],
    secondary=THEME_COLORS["accent_secondary"],
    accent=THEME_COLORS["accent_hover"],
    foreground=THEME_COLORS["text_primary"],
    background=THEME_COLORS["bg_root"],
    surface=THEME_COLORS["bg_surface"],
    panel=THEME_COLORS["bg_panel"],
    warning=THEME_COLORS["status_warning_fg"],
    error=THEME_COLORS["status_failed_fg"],
    success=THEME_COLORS["status_completed_fg"],
    dark=True,
    variables={
        "border-subtle": THEME_COLORS["border_subtle"],
        "border-dim": THEME_COLORS["border_dim"],
        "border-focus": THEME_COLORS["border_focus"],
        "text-muted": THEME_COLORS["text_muted"],
        "text-dim": THEME_COLORS["text_dim"],
        "bg-card": THEME_COLORS["bg_card"],
        "bg-sidebar": THEME_COLORS["bg_sidebar"],
        "bg-hover": THEME_COLORS["bg_hover"],
        "bg-active": THEME_COLORS["bg_active"],
    },
)


# =============================================================================
# COMPLETE TEXTUAL CSS STYLING (TUI_CSS)
# =============================================================================

TUI_CSS: str = """
/* =========================================================================
   ENTERPRISE SLATE-DARK TUI THEME
   Inspired by: lazygit, btop, neovim, k9s
   ========================================================================= */

/* --- 1. Global Reset, Root Screen & Scrollbars --- */
Screen {
    background: #0d0f14;
    color: #e1e4ec;
    overflow: hidden hidden;
}

Screen:focus {
    background: #0d0f14;
}

* {
    scrollbar-background: #10121a;
    scrollbar-color: #242938;
    scrollbar-color-hover: #3d5470;
    scrollbar-color-active: #4ba3be;
    scrollbar-size: 1 1;
    scrollbar-corner-color: #10121a;
}

VerticalScroll, HorizontalScroll, ScrollableContainer, DataTable, RichLog, DirectoryTree, ListView, OptionList {
    scrollbar-background: #10121a;
    scrollbar-color: #242938;
    scrollbar-color-hover: #3d5470;
    scrollbar-color-active: #4ba3be;
    scrollbar-size: 1 1;
}

/* --- 2. Header, Footer & Status Bar --- */
Header {
    dock: top;
    height: 1;
    background: #10121a;
    color: #9aa2b4;
    border-bottom: solid #1b1f2b;
}

HeaderTitle {
    color: #4ba3be;
    text-style: bold;
    padding-left: 1;
}

HeaderClock {
    color: #5e6678;
    padding-right: 1;
}

Footer {
    dock: bottom;
    height: 1;
    background: #10121a;
    color: #5e6678;
    border-top: solid #1b1f2b;
}

FooterKey {
    background: #171a26;
    color: #9aa2b4;
    text-style: none;
}

FooterKey:hover {
    background: #242d40;
    color: #ffffff;
}

/* Bottom Status Bar */
TUIStatusBar {
    dock: bottom;
    height: 1;
    min-height: 1;
    max-height: 1;
    width: 100%;
    layout: horizontal;
    background: #10121a;
    color: #9aa2b4;
    overflow: hidden hidden;
}

#status-bar-left {
    width: auto;
    max-width: 45%;
    height: 1;
    content-align: left middle;
    padding-left: 1;
    padding-right: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    overflow: hidden hidden;
}

#status-bar-center {
    width: 1fr;
    min-width: 0;
    height: 1;
    content-align: center middle;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    overflow: hidden hidden;
    padding-left: 1;
    padding-right: 1;
}

#status-bar-right {
    width: auto;
    max-width: 45%;
    height: 1;
    content-align: right middle;
    padding-left: 1;
    padding-right: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    overflow: hidden hidden;
}

/* --- 3. Unified Buttons --- */
Button {
    height: 3;
    min-height: 3;
    min-width: 10;
    padding: 0 2;
    background: #181c28;
    color: #9aa2b4;
    border: solid #242938;
    text-style: none;
}

Button:hover {
    background: #222a3d;
    color: #ffffff;
    border: solid #3d5470;
}

Button:focus {
    background: #242d40;
    color: #ffffff;
    border: solid #4ba3be;
    text-style: bold;
}

Button.-active, Button.active {
    background: #2a354d;
    color: #5cbcdb;
    border: solid #4ba3be;
}

Button:disabled {
    background: #11131c;
    color: #484f60;
    border: solid #1b1f2b;
    text-style: none;
}

/* Button Variants */
Button.-primary, Button.primary, Button.action-btn, #btn-apply, #btn-save, #btn-select {
    background: #1c3340;
    color: #5cbcdb;
    border: solid #2c5468;
}

Button.-primary:hover, Button.primary:hover, Button.action-btn:hover, #btn-apply:hover, #btn-save:hover, #btn-select:hover {
    background: #244456;
    color: #ffffff;
    border: solid #4ba3be;
}

Button.-primary:focus, Button.primary:focus, Button.action-btn:focus, #btn-apply:focus, #btn-save:focus, #btn-select:focus {
    background: #284c60;
    color: #ffffff;
    border: solid #5cbcdb;
}

Button.-success, Button.success {
    background: #192b1e;
    color: #72a37d;
    border: solid #274530;
}

Button.-success:hover, Button.success:hover {
    background: #223c2a;
    color: #ffffff;
    border: solid #72a37d;
}

Button.-error, Button.-danger, Button.error, Button.danger {
    background: #2c1a1d;
    color: #b36262;
    border: solid #4a282d;
}

Button.-error:hover, Button.-danger:hover, Button.error:hover, Button.danger:hover {
    background: #3e2227;
    color: #ffffff;
    border: solid #b36262;
}

Button.-warning, Button.warning {
    background: #282218;
    color: #b5935d;
    border: solid #453823;
}

Button.-warning:hover, Button.warning:hover {
    background: #382e1e;
    color: #ffffff;
    border: solid #b5935d;
}

/* Neutral Dismiss & Cancel Buttons */
#btn-cancel, #btn-close, #btn-close-modal, #btn-close-bottom, Button.dismiss-btn {
    background: #181c28;
    color: #9aa2b4;
    border: solid #242938;
}

#btn-cancel:hover, #btn-close:hover, #btn-close-modal:hover, #btn-close-bottom:hover, Button.dismiss-btn:hover {
    background: #222a3d;
    color: #e1e4ec;
    border: solid #3d5470;
}

/* Flat, Toolbar, Nav, and Filter Buttons */
Button.flat-btn, .nav-btn, .drive-btn, .side-btn, .filter-btn {
    background: #151824;
    color: #9aa2b4;
    border: none;
    padding: 0 1;
}

Button.flat-btn:hover, .nav-btn:hover, .drive-btn:hover, .side-btn:hover, .filter-btn:hover {
    background: #1d2332;
    color: #e1e4ec;
}

Button.filter-btn.active, .filter-btn.-active, #filter-buttons Button.active {
    background: #273142;
    color: #5cbcdb;
    border: solid #3d5470;
}

/* --- 4. Sidebar & Navigation Column --- */
#sidebar, .sidebar {
    width: 24;
    dock: left;
    height: 100%;
    background: #10121a;
    border-right: solid #1b1f2b;
    padding: 1;
    layout: vertical;
}

#app-title, .sidebar-title {
    text-align: center;
    text-style: bold;
    color: #4ba3be;
    margin-bottom: 1;
    border-bottom: solid #1b1f2b;
    padding-bottom: 1;
    height: 3;
}

.sidebar-section-title {
    color: #5e6678;
    text-style: bold;
    margin-top: 1;
    margin-bottom: 0;
    padding-left: 1;
}

#sidebar Button, .sidebar Button {
    width: 100%;
    height: 3;
    min-height: 3;
    margin-bottom: 1;
    background: #151824;
    color: #9aa2b4;
    border: none;
    text-style: none;
    padding: 0 1;
}

#sidebar Button:hover, .sidebar Button:hover {
    background: #1d2332;
    color: #e1e4ec;
}

#sidebar Button:focus, .sidebar Button:focus {
    background: #222a3d;
    color: #ffffff;
    text-style: bold;
    border-left: thick #4ba3be;
}

#sidebar Button.-active, .sidebar Button.-active {
    background: #2a354d;
    color: #5cbcdb;
}

#sidebar Button:disabled, .sidebar Button:disabled {
    background: #11131c;
    color: #3b4252;
    border-left: none;
}

/* --- 5. Main Layout Panels & Card Containers --- */
#main-content {
    layout: vertical;
    height: 100%;
    background: #0d0f14;
    padding: 0 1;
}

#top-row {
    height: 1fr;
    layout: horizontal;
    margin-top: 0;
}

.panel, .card, .container-box, .column-box, #queue-container, #monitor-container, #log-container {
    background: #13161f;
    border: round #242938;
    padding: 0 1;
}

.panel:focus-within, .card:focus-within, .container-box:focus-within, #queue-container:focus-within, #monitor-container:focus-within, #log-container:focus-within {
    border: round #3d5470;
}

.card-elevated, .dialog-card {
    background: #181c28;
    border: round #364157;
}

.card-elevated:focus-within, .dialog-card:focus-within {
    border: round #4f98b2;
}

.panel-subtle, .column-box, .option-row, .select-row {
    background: #141722;
    border: solid #1b1f2b;
}

#queue-container {
    width: 3fr;
    height: 100%;
    margin-right: 1;
}

#monitor-container {
    width: 2fr;
    height: 100%;
}

#log-container {
    height: 10;
    dock: bottom;
    margin-top: 1;
    margin-bottom: 0;
}

.panel-header {
    height: 1;
    background: #171a26;
    color: #7d879e;
    text-style: bold;
    padding: 0 1;
    border-bottom: solid #242938;
}

/* --- 6. DataTable Styling (Crisp, Borderless, Alternating) --- */
DataTable {
    background: #13161f;
    color: #c4cad6;
    border: none;
    height: 100%;
    width: 100%;
}

DataTable:focus {
    border: none;
}

DataTable > .datatable--header {
    background: #171a26;
    color: #788296;
    text-style: bold;
    border-bottom: solid #242938;
}

DataTable > .datatable--hover {
    background: #1d2332;
    color: #e1e4ec;
}

DataTable > .datatable--cursor {
    background: #222a3d;
    color: #ffffff;
    text-style: bold;
}

DataTable > .datatable--even-row {
    background: #13161f;
}

DataTable > .datatable--odd-row {
    background: #161924;
}

/* --- 7. Status Badges & Pill Labels --- */
.badge {
    height: 1;
    padding: 0 1;
    text-style: bold;
    content-align: center middle;
}

.badge-pending {
    background: #1f1f1c;
    color: #9e9b86;
}

.badge-probing {
    background: #152028;
    color: #759bb0;
}

.badge-converting, .badge-active {
    background: #142531;
    color: #58aeca;
}

.badge-completed, .badge-success {
    background: #16241a;
    color: #72a37d;
}

.badge-failed, .badge-error {
    background: #28171a;
    color: #b36262;
}

.badge-paused {
    background: #191c23;
    color: #828996;
}

.badge-warning {
    background: #252016;
    color: #b5935d;
}

/* --- 8. Modal Dialogs & Shaded Popups --- */
ModalScreen, .modal-backdrop, FilePickerModal, FilterDialogModal, HelpModalScreen, HistoryModalScreen, PresetDialogModal {
    align: center middle;
    background: rgba(8, 10, 14, 0.85);
}

.modal-dialog, .dialog-card, #dialog, #picker-dialog, #filter-dialog-container, #help-dialog-container, #history-modal-dialog, #preset-dialog-box {
    width: 78;
    max-width: 95%;
    height: auto;
    max-height: 90%;
    background: #181c28;
    border: round #364157;
    padding: 1 2;
    layout: vertical;
}

.modal-dialog:focus-within, .dialog-card:focus-within, #dialog:focus-within, #picker-dialog:focus-within, #filter-dialog-container:focus-within, #help-dialog-container:focus-within, #history-modal-dialog:focus-within, #preset-dialog-box:focus-within {
    border: round #4f98b2;
}

.dialog-title, .modal-title, #dialog-title, #modal-title, #help-dialog-title {
    text-style: bold;
    color: #e1e4ec;
    text-align: center;
    margin-bottom: 1;
    border-bottom: solid #242938;
    padding-bottom: 1;
}

.dialog-header, .modal-header, #dialog-header, #picker-header, #help-header, #history-modal-header {
    border-bottom: solid #242938;
    margin-bottom: 1;
    padding-bottom: 1;
}

.dialog-body, .modal-content {
    margin-bottom: 1;
    color: #9aa2b4;
}

.dialog-footer, .dialog-actions, #dialog-footer, #button-row, #dialog-buttons {
    layout: horizontal;
    align-horizontal: right;
    height: 3;
    margin-top: 1;
}

.dialog-footer Button, .dialog-actions Button, #dialog-buttons Button, #button-row Button {
    margin-left: 1;
    min-width: 10;
    height: 3;
}

/* --- 9. Form Inputs & Interactive Controls --- */
Input {
    background: #0f1118;
    border: solid #242938;
    color: #e1e4ec;
    padding: 0 1;
    height: 3;
}

Input:focus {
    border: solid #4ba3be;
    background: #121520;
}

Input.-valid {
    border: solid #72a37d;
}

Input.-invalid {
    border: solid #b36262;
}

Select {
    background: #13161f;
    border: solid #242938;
    color: #e1e4ec;
}

Select:focus {
    border: solid #4ba3be;
}

Checkbox {
    background: transparent;
    color: #9aa2b4;
    padding: 0 1;
}

Checkbox:focus {
    color: #58aeca;
}

OptionList, ListView {
    background: #13161f;
    border: solid #242938;
}

OptionList > .option-list--option-highlighted, ListItem:focus {
    background: #222a3d;
    color: #ffffff;
}

/* --- 10. Logs & Visual Telemetry --- */
RichLog, #event-log {
    background: #10121a;
    color: #8b94a7;
    border: none;
    padding: 0 1;
}

ProgressBar {
    padding: 0 1;
}

ProgressBar > .progressbar--bar {
    color: #4ba3be;
    background: #171a26;
}

ProgressBar > .progressbar--complete {
    color: #58aeca;
}

/* --- 11. Tabs and Switchers --- */
Tabs {
    background: #10121a;
    border-bottom: solid #1b1f2b;
}

Tab {
    background: #10121a;
    color: #687184;
    padding: 0 2;
}

Tab:hover {
    color: #9aa2b4;
}

Tab.-active {
    color: #e1e4ec;
    text-style: bold;
    border-bottom: thick #4ba3be;
}
"""


# =============================================================================
# THEME HELPER FUNCTIONS & TELEMETRY UTILITIES
# =============================================================================

def get_theme_color(key: str, default: str = "#e1e4ec") -> str:
    """Retrieves a hex color code from THEME_COLORS with fallback."""
    return THEME_COLORS.get(key, default)


def get_status_style(status: Union[str, Any]) -> Dict[str, str]:
    """
    Returns the foreground, background, and border styling for a task or queue status.
    Values are calibrated to be subtle, desaturated, and professional.
    """
    raw_status = str(getattr(status, "value", status)).strip().upper()

    if raw_status in ("CONVERTING", "RUNNING", "ACTIVE"):
        return {
            "fg": THEME_COLORS["status_converting_fg"],
            "bg": THEME_COLORS["status_converting_bg"],
            "border": THEME_COLORS["status_converting_border"],
            "label": "CONVERTING",
        }
    elif raw_status in ("COMPLETED", "SUCCESS", "DONE"):
        return {
            "fg": THEME_COLORS["status_completed_fg"],
            "bg": THEME_COLORS["status_completed_bg"],
            "border": THEME_COLORS["status_completed_border"],
            "label": "COMPLETED",
        }
    elif raw_status in ("FAILED", "ERROR"):
        return {
            "fg": THEME_COLORS["status_failed_fg"],
            "bg": THEME_COLORS["status_failed_bg"],
            "border": THEME_COLORS["status_failed_border"],
            "label": "FAILED",
        }
    elif raw_status in ("PROBING", "INSPECTING", "SCANNING"):
        return {
            "fg": THEME_COLORS["status_probing_fg"],
            "bg": THEME_COLORS["status_probing_bg"],
            "border": THEME_COLORS["status_probing_border"],
            "label": "PROBING",
        }
    elif raw_status in ("PAUSED", "HOLD"):
        return {
            "fg": THEME_COLORS["status_paused_fg"],
            "bg": THEME_COLORS["status_paused_bg"],
            "border": THEME_COLORS["status_paused_border"],
            "label": "PAUSED",
        }
    elif raw_status in ("WARNING", "WARN"):
        return {
            "fg": THEME_COLORS["status_warning_fg"],
            "bg": THEME_COLORS["status_warning_bg"],
            "border": THEME_COLORS["status_warning_border"],
            "label": "WARNING",
        }
    else:  # PENDING, QUEUED, WAITING
        return {
            "fg": THEME_COLORS["status_pending_fg"],
            "bg": THEME_COLORS["status_pending_bg"],
            "border": THEME_COLORS["status_pending_border"],
            "label": "PENDING",
        }


def get_status_badge_markup(
    status: Union[str, Any],
    include_brackets: bool = False,
    pad_spaces: bool = True,
) -> str:
    """
    Generates a clean Rich markup badge string with subtle styling.
    Example: `[#58aeca on #142531] CONVERTING [/]`
    """
    style = get_status_style(status)
    fg = style["fg"]
    bg = style["bg"]
    label = style["label"]

    if pad_spaces:
        content = f" {label} "
    else:
        content = label

    if include_brackets:
        return f"[{fg} on {bg}][{content}][/]"
    return f"[{fg} on {bg}]{content}[/]"


def get_status_badge_text(status: Union[str, Any]) -> Text:
    """Returns a rich.text.Text object formatted with subtle status badge styling."""
    style = get_status_style(status)
    return Text(f" {style['label']} ", style=f"{style['fg']} on {style['bg']}")


def format_log_entry(tag: str, message: str, timestamp_str: Optional[str] = None) -> str:
    """
    Formats a log message with clean, muted styling for terminal display.
    """
    t_style = THEME_COLORS["text_dim"]
    msg_style = THEME_COLORS["text_secondary"]
    tag_upper = tag.strip().upper()

    tag_color = THEME_COLORS["accent_secondary"]
    if tag_upper in ("ERR", "ERROR", "FAIL"):
        tag_color = THEME_COLORS["status_failed_fg"]
    elif tag_upper in ("WARN", "WARNING"):
        tag_color = THEME_COLORS["status_warning_fg"]
    elif tag_upper in ("DONE", "OK", "VERIFY"):
        tag_color = THEME_COLORS["status_completed_fg"]
    elif tag_upper in ("PROBE", "INSPECT"):
        tag_color = THEME_COLORS["status_probing_fg"]
    elif tag_upper in ("TRANSCODE", "CONVERT"):
        tag_color = THEME_COLORS["status_converting_fg"]

    prefix = f"[{t_style}]{timestamp_str}[/] " if timestamp_str else ""
    return f"{prefix}[{tag_color}][{tag_upper}][/] [{msg_style}]{message}[/]"


def get_spectrum_palette() -> List[str]:
    """
    Returns the curated 7-band monochromatic slate-cyan frequency spectrum colors.
    Avoids rainbow clutter by providing a smooth perceptual luminance gradient.
    """
    return list(SPECTRUM_GRADIENT)


def get_vu_meter_color(ratio: float) -> str:
    """
    Returns the appropriate hex color for a VU meter level (0.0 to 1.0).
    Muted, professional progression from slate cyan to soft rust at peak clipping.
    """
    if ratio < 0.70:
        return THEME_COLORS["vu_low"]
    elif ratio < 0.88:
        return THEME_COLORS["vu_mid"]
    elif ratio < 0.96:
        return THEME_COLORS["vu_high"]
    else:
        return THEME_COLORS["vu_peak"]


def apply_tui_theme(app: Any) -> None:
    """
    Applies the enterprise slate-dark theme to a Textual App instance.
    Registers SLATE_DARK_THEME and appends or sets TUI_CSS.
    """
    if hasattr(app, "register_theme"):
        try:
            app.register_theme(SLATE_DARK_THEME)
            app.theme = "slate-dark"
        except Exception:
            pass

    # Append or configure CSS
    if hasattr(app, "CSS") and app.CSS:
        if "ENTERPRISE SLATE-DARK TUI THEME" not in app.CSS:
            app.CSS = app.CSS + "\n" + TUI_CSS
    else:
        app.CSS = TUI_CSS


def get_tui_css() -> str:
    """Returns the full Textual CSS string."""
    return TUI_CSS


__all__ = [
    "THEME_COLORS",
    "BG_ROOT",
    "BG_SCREEN",
    "BG_SURFACE",
    "BG_SURFACE_ALT",
    "BG_PANEL",
    "BG_SIDEBAR",
    "BG_CARD",
    "BG_HOVER",
    "BG_ACTIVE",
    "BG_INPUT",
    "BG_CURSOR",
    "BORDER_DIM",
    "BORDER_SUBTLE",
    "BORDER_FOCUS",
    "BORDER_ACTIVE",
    "BORDER_MODAL",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "TEXT_MUTED",
    "TEXT_DIM",
    "TEXT_HIGHLIGHT",
    "ACCENT_PRIMARY",
    "ACCENT_SECONDARY",
    "ACCENT_HOVER",
    "ACCENT_SUBTLE",
    "SPECTRUM_GRADIENT",
    "SLATE_DARK_THEME",
    "TUI_CSS",
    "get_theme_color",
    "get_status_style",
    "get_status_badge_markup",
    "get_status_badge_text",
    "format_log_entry",
    "get_spectrum_palette",
    "get_vu_meter_color",
    "apply_tui_theme",
    "get_tui_css",
]
