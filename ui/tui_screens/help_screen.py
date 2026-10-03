"""
help_screen.py - Sleek Minimalist Help & Keybinding Modal Dialog for Textual TUI.

Provides the `HelpModalScreen` modal dialog and `HelpView` component for the
enterprise Video-to-Audio Transcoder CLI TUI:
1. Keyboard Shortcuts cheat-sheet formatted in clean tables/cards:
   - Navigation: `j`/`k`/Arrows (Navigate Queue), `Enter` (Inspect item)
   - Actions: `a` (Add / Browse Files), `v` (Paste Clipboard), `s` (Start / Pause Conversion)
   - Modals: `p` (Presets & Formats), `f` (Audio Filters & DSP), `h` (Conversion History), `?` (This Help)
   - Queue Control: `d` / `Delete` (Remove task), `c` (Clear completed), `q` (Quit)
2. Mouse actions guide: Click buttons in sidebar, click table rows to select, drag borders to resize.
3. Tips on Hardware Acceleration, Watch Folder daemon, and Lossless Stream Copy.
4. Dismiss with `Escape`, `?`, or "Close" button.

Design Constraint:
- Sleek minimalist dark styling utilizing muted zinc/slate shades (#121214, #18181b,
  #27272a, #3f3f46, #52525b, #71717a, #a1a1aa, #f4f4f5) with zero gaudy colors.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


# =============================================================================
# Structured Reference Data
# =============================================================================

SHORTCUT_CATEGORIES: Dict[str, List[Tuple[str, str]]] = {
    "NAVIGATION": [
        ("j / k / Arrows", "Navigate Queue"),
        ("Enter", "Inspect item"),
    ],
    "ACTIONS": [
        ("a", "Add / Browse Files"),
        ("v", "Paste Clipboard"),
        ("s", "Start / Pause Conversion"),
    ],
    "MODALS & OVERLAYS": [
        ("p", "Presets & Formats"),
        ("f", "Audio Filters & DSP"),
        ("h", "Conversion History"),
        ("?", "This Help"),
    ],
    "QUEUE CONTROL": [
        ("d / Delete", "Remove task"),
        ("c", "Clear completed"),
        ("q", "Quit"),
    ],
}

MOUSE_ACTIONS_GUIDE: List[Tuple[str, str]] = [
    (
        "Click buttons in sidebar",
        "Trigger primary controls: Add Files, Paste Clipboard, Start/Pause, Clear Done, Quit.",
    ),
    (
        "Click table rows to select",
        "Select active task in queue to inspect audio streams, codec info, or trigger removal.",
    ),
    (
        "Drag borders to resize",
        "Adjust proportional splitters between Task Queue, Spectrum Monitor, and Event Log.",
    ),
]

PRO_TIPS_GUIDE: List[Tuple[str, str]] = [
    (
        "Hardware Acceleration",
        "NVENC (NVIDIA), QuickSync (Intel), and AMF (AMD) auto-detected for hardware-accelerated "
        "video decoding. Offloads demux/decode overhead to GPU ASIC engines, keeping CPU cores "
        "dedicated for audio DSP filters and multichannel resampling.",
    ),
    (
        "Watch Folder daemon",
        "Continuous headless background watcher monitors designated intake folders. Any video file "
        "dropped into the directory is automatically detected, queued, and transcoded with active presets.",
    ),
    (
        "Lossless Stream Copy",
        "When target audio format matches the source video stream codec (e.g. AAC in MP4 to .m4a/.aac, "
        "or FLAC in MKV to .flac), conversion uses zero-re-encoding pass-through (-c:a copy) for 0% quality "
        "loss at near-instant speed.",
    ),
]


# =============================================================================
# Render Helpers (Rich Tables with Minimalist Keycap Badges)
# =============================================================================

def format_key_badges(key_str: str) -> str:
    """
    Renders keyboard shortcuts as styled keycaps with muted dark backgrounds.
    Example: 'j / k / Arrows' -> '[bold #f4f4f5 on #27272a] j [/] ...'
    """
    tokens = [t.strip() for t in key_str.split("/")]
    badges = [f"[bold #f4f4f5 on #27272a] {t} [/]" for t in tokens]
    return " [dim #52525b]/[/] ".join(badges)


def build_shortcut_table(shortcuts: Sequence[Tuple[str, str]]) -> Table:
    """
    Builds a compact Rich Table displaying keycap badges and action descriptions.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=False,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Key", width=22, no_wrap=True)
    table.add_column("Description", style="#a1a1aa")
    for key, desc in shortcuts:
        table.add_row(format_key_badges(key), f"[#a1a1aa]{desc}[/]")
    return table


def build_mouse_guide_table(items: Sequence[Tuple[str, str]]) -> Table:
    """
    Builds a clean Rich Table for mouse interactions and gestures.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=False,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Action", width=28, no_wrap=True)
    table.add_column("Description", style="#a1a1aa")
    for action, desc in items:
        action_markup = f"[bold #e4e4e7 on #27272a] {action} [/]"
        table.add_row(action_markup, f"[#a1a1aa]{desc}[/]")
    return table


def build_tips_table(tips: Sequence[Tuple[str, str]]) -> Table:
    """
    Builds a structured Rich Table for pro tips and technical features.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=False,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Feature", width=26, no_wrap=True)
    table.add_column("Details", style="#a1a1aa")
    for title, desc in tips:
        title_markup = f"[bold #f4f4f5]► {title}[/]"
        table.add_row(title_markup, f"[#a1a1aa]{desc}[/]")
    return table


# =============================================================================
# Reusable Help Content View
# =============================================================================

class HelpView(VerticalScroll):
    """
    Scrollable help content container displaying the keyboard shortcuts cheat-sheet,
    mouse actions guide, and pro tips.
    """

    DEFAULT_CSS = """
    HelpView {
        height: 1fr;
        padding: 1 2;
        background: transparent;
    }

    .section-label {
        color: #8a93a4;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
        padding-bottom: 0;
    }

    .first-section {
        margin-top: 0;
    }

    .card-row {
        width: 100%;
        height: auto;
        layout: horizontal;
        margin-bottom: 1;
    }

    .help-card {
        width: 1fr;
        height: auto;
        background: #18181b;
        border: solid #27272a;
        padding: 0 1;
        margin-right: 1;
    }

    .help-card:last-child {
        margin-right: 0;
    }

    .card-title {
        color: #d4d4d8;
        text-style: bold;
        border-bottom: solid #27272a;
        padding-bottom: 0;
        margin-bottom: 0;
    }

    .full-width-card {
        width: 100%;
        height: auto;
        background: #18181b;
        border: solid #27272a;
        padding: 1 1;
        margin-bottom: 1;
    }

    .card-content {
        margin-top: 0;
        padding-top: 0;
    }
    """

    def compose(self) -> ComposeResult:
        # Section 1: Keyboard Shortcuts Cheat-Sheet
        yield Label("► KEYBOARD SHORTCUTS CHEAT-SHEET", classes="section-label first-section")

        # Row 1: Navigation + Actions
        with Horizontal(classes="card-row"):
            with Container(id="card-navigation", classes="help-card"):
                yield Label("NAVIGATION", classes="card-title")
                yield Static(
                    build_shortcut_table(SHORTCUT_CATEGORIES["NAVIGATION"]),
                    classes="card-content",
                )
            with Container(id="card-actions", classes="help-card"):
                yield Label("ACTIONS", classes="card-title")
                yield Static(
                    build_shortcut_table(SHORTCUT_CATEGORIES["ACTIONS"]),
                    classes="card-content",
                )

        # Row 2: Modals & Overlays + Queue Control
        with Horizontal(classes="card-row"):
            with Container(id="card-modals", classes="help-card"):
                yield Label("MODALS & OVERLAYS", classes="card-title")
                yield Static(
                    build_shortcut_table(SHORTCUT_CATEGORIES["MODALS & OVERLAYS"]),
                    classes="card-content",
                )
            with Container(id="card-queue", classes="help-card"):
                yield Label("QUEUE CONTROL", classes="card-title")
                yield Static(
                    build_shortcut_table(SHORTCUT_CATEGORIES["QUEUE CONTROL"]),
                    classes="card-content",
                )

        # Section 2: Mouse Actions Guide
        yield Label("► MOUSE ACTIONS GUIDE", classes="section-label")
        with Container(id="card-mouse-guide", classes="full-width-card"):
            yield Static(build_mouse_guide_table(MOUSE_ACTIONS_GUIDE), classes="card-content")

        # Section 3: Pro Tips & Architecture
        yield Label("► PRO TIPS & ARCHITECTURE", classes="section-label")
        with Container(id="card-pro-tips", classes="full-width-card"):
            yield Static(build_tips_table(PRO_TIPS_GUIDE), classes="card-content")


# =============================================================================
# Modal Screen Implementation
# =============================================================================

class HelpModalScreen(ModalScreen[None]):
    """
    Sleek, minimalist dark modal dialog displaying keyboard shortcuts,
    mouse navigation guide, and pro engine tips. Dismissible via Escape, '?',
    or Close buttons.
    """

    DEFAULT_CSS = """
    HelpModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.78);
    }

    #help-dialog-container {
        width: 86;
        max-width: 96%;
        height: 88%;
        max-height: 44;
        background: #121214;
        border: solid #27272a;
        layout: vertical;
        padding: 0;
    }

    #help-header {
        width: 100%;
        height: 3;
        dock: top;
        background: #18181b;
        border-bottom: solid #27272a;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #help-title-block {
        width: 1fr;
        height: auto;
    }

    #help-dialog-title {
        color: #f4f4f5;
        text-style: bold;
    }

    #btn-close {
        min-width: 12;
        height: 1;
        background: #18181b;
        color: #a1a1aa;
        border: solid #3f3f46;
    }

    #btn-close:hover {
        background: #27272a;
        color: #f4f4f5;
        border: solid #52525b;
    }

    #btn-close:focus {
        background: #3f3f46;
        border: double #a1a1aa;
    }

    #help-footer {
        width: 100%;
        height: 3;
        dock: bottom;
        background: #18181b;
        border-top: solid #27272a;
        padding: 0 2;
        layout: horizontal;
        align: center middle;
    }

    #help-footer-hint {
        width: 1fr;
        color: #52525b;
    }

    #btn-close-bottom {
        min-width: 14;
        height: 1;
        background: #27272a;
        color: #f4f4f5;
        border: solid #52525b;
        text-style: bold;
    }

    #btn-close-bottom:hover {
        background: #3f3f46;
        border: solid #71717a;
        color: #ffffff;
    }

    #btn-close-bottom:focus {
        background: #3f3f46;
        border: double #a1a1aa;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close Help", show=True),
        Binding("question_mark", "dismiss_modal", "Close Help", show=True),
        Binding("?", "dismiss_modal", "Close Help", show=False),
    ]

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog-container"):
            # Dialog Top Header Bar
            with Horizontal(id="help-header"):
                with Vertical(id="help-title-block"):
                    yield Label(
                        "HELP & KEYBINDINGS [dim #52525b]•[/] [dim #71717a]TRANSCODER MATRIX[/]",
                        id="help-dialog-title",
                    )
                yield Button("✕ Close", id="btn-close")

            # Scrollable Body View
            yield HelpView(id="help-view-body")

            # Dialog Bottom Action/Hint Footer
            with Horizontal(id="help-footer"):
                yield Label(
                    "Press [bold #8a93a4]Esc[/] or [bold #8a93a4]?[/] to close help",
                    id="help-footer-hint",
                )
                yield Button("Close Help", id="btn-close-bottom")

    def action_dismiss_modal(self) -> None:
        """Action handler triggered by keybindings (Esc or ?)."""
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handles Close button clicks."""
        if event.button.id in ("btn-close", "btn-close-bottom", "btn-close-help", "close"):
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        """Ensures Escape or '?' keys immediately dismiss the modal."""
        if event.key in ("escape", "question_mark", "?"):
            self.dismiss(None)
            event.stop()


# =============================================================================
# Standalone Interactive Preview
# =============================================================================

class _HelpPreviewApp(App[None]):
    """Small runner app for testing HelpModalScreen standalone."""

    CSS = """
    Screen {
        background: #09090b;
        align: center middle;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(HelpModalScreen())


if __name__ == "__main__":
    app = _HelpPreviewApp()
    app.run()
