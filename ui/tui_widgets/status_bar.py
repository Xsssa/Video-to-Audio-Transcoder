"""
status_bar.py - Sleek Minimalist Bottom Status Bar & Notification Widget for Textual.

Enterprise TUI component featuring:
1. Left section: System status badge (READY, CONVERTING [3/10], PAUSED, ALL DONE) and active audio preset.
2. Center section: Live transient notification messages with 4-second fade/dismiss lifecycle.
3. Right section: Hardware indicators (GPU, WATCH, THREADS) and real-time mini clock.
4. Clean helper methods: `notify_status(message, level="info")`, `set_status(...)`, `set_preset(...)`, etc.

Style Constraint: Sleek minimalist dark styling with slate/charcoal tones, zero gaudy colors.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple, Union

from rich.text import Text
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

# Attempt importing centralized enterprise palette from ui.tui_theme
try:
    from ui.tui_theme import THEME_COLORS
except ImportError:
    THEME_COLORS: Dict[str, str] = {
        "bg_root": "#0d0f14",
        "bg_surface": "#13161f",
        "bg_footer": "#10121a",
        "bg_card": "#181c28",
        "border_dim": "#1b1f2b",
        "border_subtle": "#242938",
        "text_primary": "#e1e4ec",
        "text_secondary": "#9aa2b4",
        "text_muted": "#5e6678",
        "text_dim": "#484f60",
        "text_inverse": "#0d0f14",
        "accent_primary": "#4ba3be",
        "accent_secondary": "#60728c",
        "status_pending_fg": "#9e9b86",
        "status_pending_bg": "#1f1f1c",
        "status_probing_fg": "#759bb0",
        "status_probing_bg": "#152028",
        "status_converting_fg": "#58aeca",
        "status_converting_bg": "#142531",
        "status_completed_fg": "#72a37d",
        "status_completed_bg": "#16241a",
        "status_failed_fg": "#b36262",
        "status_failed_bg": "#28171a",
        "status_paused_fg": "#828996",
        "status_paused_bg": "#191c23",
        "status_warning_fg": "#b5935d",
        "status_warning_bg": "#252016",
    }


class TUIStatusBar(Widget):
    """Sleek bottom Status Bar Widget for Textual.

    Provides three distinct sections:
    - Left: System status badge (e.g. READY, CONVERTING [3/10], PAUSED, ALL DONE)
            and active audio preset (e.g. MP3 320k | EBU R128).
    - Center: Live transient notification messages with 4-second fade and dismiss.
    - Right: Hardware telemetry indicators (GPU: ON, WATCH: OFF, THREADS: 4/12)
             and a live mini clock (12:35:00).
    """

    DEFAULT_CSS = """
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
    """

    # Reactive attributes
    status: reactive[str] = reactive("READY")
    preset: reactive[str] = reactive("MP3 320k | EBU R128")
    gpu_status: reactive[Union[bool, str]] = reactive(False)
    watch_status: reactive[Union[bool, str]] = reactive(False)
    threads_status: reactive[str] = reactive("0/0")
    clock_time: reactive[str] = reactive("")
    show_clock: reactive[bool] = reactive(True)
    badge_style: reactive[str] = reactive("solid")  # "solid", "subtle", "bracket"

    def __init__(
        self,
        status: str = "READY",
        preset: str = "MP3 320k | EBU R128",
        gpu: Union[bool, str] = False,
        watch: Union[bool, str] = False,
        threads: str = "0/0",
        show_clock: bool = True,
        badge_style: str = "solid",
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.status = status
        self.preset = preset
        self.gpu_status = gpu
        self.watch_status = watch
        self.threads_status = threads
        self.show_clock = show_clock
        self.badge_style = badge_style

        # Transient notification internal state
        self._current_notification: str = ""
        self._current_level: str = "info"
        self._fade_timer: Optional[Timer] = None
        self._dismiss_timer: Optional[Timer] = None
        self._clock_timer: Optional[Timer] = None
        self._pending_notification: Optional[Tuple[str, str, float]] = None

    def compose(self) -> ComposeResult:
        """Compose the three status bar sections."""
        yield Static("", id="status-bar-left")
        yield Static("", id="status-bar-center")
        yield Static("", id="status-bar-right")

    def on_mount(self) -> None:
        """Initialize widgets, render initial state, and start clock timer."""
        self._update_clock_text()
        self._refresh_left()
        self._refresh_right()

        if self._pending_notification:
            msg, lvl, dur = self._pending_notification
            self._pending_notification = None
            self._apply_notification(msg, lvl, dur)
        else:
            self._refresh_center()

        if self.show_clock:
            self._clock_timer = self.set_interval(1.0, self._on_clock_tick)

    def on_unmount(self) -> None:
        """Cancel timers on unmount."""
        self._cancel_notification_timers()
        if self._clock_timer is not None:
            self._clock_timer.stop()
            self._clock_timer = None

    # =========================================================================
    # Reactive Observers
    # =========================================================================

    def watch_status(self, old: str, new: str) -> None:
        if self.is_mounted:
            self._refresh_left()

    def watch_preset(self, old: str, new: str) -> None:
        if self.is_mounted:
            self._refresh_left()

    def watch_badge_style(self, old: str, new: str) -> None:
        if self.is_mounted:
            self._refresh_left()

    def watch_gpu_status(self, old: Union[bool, str], new: Union[bool, str]) -> None:
        if self.is_mounted:
            self._refresh_right()

    def watch_watch_status(self, old: Union[bool, str], new: Union[bool, str]) -> None:
        if self.is_mounted:
            self._refresh_right()

    def watch_threads_status(self, old: str, new: str) -> None:
        if self.is_mounted:
            self._refresh_right()

    def watch_clock_time(self, old: str, new: str) -> None:
        if self.is_mounted:
            self._refresh_right()

    def watch_show_clock(self, old: bool, new: bool) -> None:
        if self.is_mounted:
            if new and self._clock_timer is None:
                self._update_clock_text()
                self._clock_timer = self.set_interval(1.0, self._on_clock_tick)
            elif not new and self._clock_timer is not None:
                self._clock_timer.stop()
                self._clock_timer = None
            self._refresh_right()

    # =========================================================================
    # Public Control API
    # =========================================================================

    def notify_status(
        self,
        message: str,
        level: str = "info",
        duration: float = 4.0,
    ) -> None:
        """Display a live transient notification message in the center section.

        Thread-safe: Can be called from background worker threads or
        directly from the Textual event loop.

        Args:
            message: The notification string to display.
            level: Notification severity ('info', 'success', 'warning', 'error', 'dim').
            duration: Time in seconds before auto-dismissing (default: 4.0).
                      Fades at ~75% of duration, then dismisses completely.
                      Pass 0 or negative to keep the notification persistent.
        """
        if not self.is_mounted:
            self._pending_notification = (message, level, duration)
            return

        try:
            current_thread = threading.get_ident()
            app_thread = getattr(self.app, "_thread_id", None)
            if app_thread is not None and current_thread != app_thread:
                self.app.call_from_thread(self._apply_notification, message, level, duration)
                return
        except Exception:
            pass

        self._apply_notification(message, level, duration)

    def clear_notification(self) -> None:
        """Immediately clear any active transient notification and cancel timers."""
        self._cancel_notification_timers()
        self._current_notification = ""
        self._refresh_center()

    def set_status(
        self,
        status: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        """Update system status badge.

        Examples:
            set_status("READY")
            set_status("CONVERTING", 3, 10) -> "CONVERTING [3/10]"
            set_status("PAUSED")
            set_status("ALL DONE")
        """
        if current is not None and total is not None:
            self.status = f"{status} [{current}/{total}]"
        else:
            self.status = status

    def set_preset(self, preset: str) -> None:
        """Update current active audio preset."""
        self.preset = preset

    def set_hardware_info(
        self,
        gpu: Optional[Union[bool, str]] = None,
        watch: Optional[Union[bool, str]] = None,
        threads: Optional[Union[str, Tuple[int, int]]] = None,
    ) -> None:
        """Convenience method to update hardware indicators simultaneously."""
        if gpu is not None:
            self.gpu_status = gpu
        if watch is not None:
            self.watch_status = watch
        if threads is not None:
            if isinstance(threads, tuple):
                self.threads_status = f"{threads[0]}/{threads[1]}"
            else:
                self.threads_status = str(threads)

    def set_gpu(self, active: Union[bool, str]) -> None:
        """Update GPU acceleration indicator."""
        self.gpu_status = active

    def set_watch(self, active: Union[bool, str]) -> None:
        """Update directory watch mode indicator."""
        self.watch_status = active

    def set_threads(
        self,
        active: Union[str, int],
        total: Optional[int] = None,
    ) -> None:
        """Update thread telemetry (e.g. set_threads(4, 12) or set_threads("4/12"))."""
        if total is not None:
            self.threads_status = f"{active}/{total}"
        else:
            self.threads_status = str(active)

    # =========================================================================
    # Internal Rendering & Formatting
    # =========================================================================

    def _on_clock_tick(self) -> None:
        """Tick event for live mini clock."""
        self._update_clock_text()

    def _update_clock_text(self) -> None:
        """Update clock_time reactive with current HH:MM:SS."""
        self.clock_time = time.strftime("%H:%M:%S")

    def _cancel_notification_timers(self) -> None:
        """Cancel any running fade or dismiss timers."""
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None
        if self._dismiss_timer is not None:
            self._dismiss_timer.stop()
            self._dismiss_timer = None

    def _apply_notification(self, message: str, level: str, duration: float) -> None:
        """Apply notification, set up fade and auto-dismiss timers."""
        self._cancel_notification_timers()
        self._current_notification = message
        self._current_level = level

        # Render fresh notification
        self._refresh_center(faded=False)

        if duration > 0:
            fade_delay = max(0.5, duration - 1.0) if duration >= 2.0 else duration * 0.75
            self._fade_timer = self.set_timer(fade_delay, self._on_notification_fade)
            self._dismiss_timer = self.set_timer(duration, self._on_notification_dismiss)

    def _on_notification_fade(self) -> None:
        """Fade the active notification by dimming its presentation."""
        self._refresh_center(faded=True)

    def _on_notification_dismiss(self) -> None:
        """Dismiss the notification completely."""
        self._current_notification = ""
        self._refresh_center()

    def _get_status_colors(self, status_text: str) -> Tuple[str, str]:
        """Return (fg, bg) color tuple from the enterprise palette for a status."""
        st_upper = status_text.strip().upper()

        if "CONVERT" in st_upper or "ENCOD" in st_upper:
            return THEME_COLORS["status_converting_fg"], THEME_COLORS["status_converting_bg"]
        elif "READY" in st_upper or "IDLE" in st_upper:
            return THEME_COLORS["status_probing_fg"], THEME_COLORS["status_probing_bg"]
        elif "PAUSE" in st_upper:
            return THEME_COLORS["status_paused_fg"], THEME_COLORS["status_paused_bg"]
        elif "ALL DONE" in st_upper or "COMPLET" in st_upper or "DONE" in st_upper:
            return THEME_COLORS["status_completed_fg"], THEME_COLORS["status_completed_bg"]
        elif "PROB" in st_upper or "SCAN" in st_upper:
            return THEME_COLORS["status_probing_fg"], THEME_COLORS["status_probing_bg"]
        elif "FAIL" in st_upper or "ERR" in st_upper:
            return THEME_COLORS["status_failed_fg"], THEME_COLORS["status_failed_bg"]
        elif "WARN" in st_upper:
            return THEME_COLORS["status_warning_fg"], THEME_COLORS["status_warning_bg"]
        elif "PEND" in st_upper or "WAIT" in st_upper or "QUEUE" in st_upper:
            return THEME_COLORS.get("status_pending_fg", "#9e9b86"), THEME_COLORS.get("status_pending_bg", "#1f1f1c")
        else:
            return THEME_COLORS.get("text_secondary", "#9aa2b4"), THEME_COLORS.get("bg_card", "#181c28")

    def _format_status_badge(self, status_text: str) -> str:
        """Generate a sleek minimalist badge for the given status string.

        Supports styles:
        - 'solid': High-contrast inverted block badge (sleek & legible)
        - 'subtle': Dark pill background with subtle accent text
        - 'bracket': Minimalist bracket badge [READY]
        """
        fg, bg = self._get_status_colors(status_text)
        style = self.badge_style.lower().strip()

        if style == "subtle":
            return f"[{fg} on {bg}] {status_text} [/]"
        elif style == "bracket":
            return f"[{fg}][{status_text}][/]"
        else:  # "solid" (default)
            inv_text = THEME_COLORS.get("text_inverse", "#0d0f14")
            return f"[bold {inv_text} on {fg}] {status_text} [/]"

    def _format_notification_markup(self, message: str, level: str, faded: bool = False) -> str:
        """Format transient notification with level-appropriate minimalist accents."""
        if not message:
            return ""

        if faded:
            return f"[dim]{message}[/]"

        lvl = level.lower().strip()
        text_primary = THEME_COLORS.get("text_primary", "#e1e4ec")

        if lvl == "success":
            succ_fg = THEME_COLORS.get("status_completed_fg", "#72a37d")
            return f"[{succ_fg}]+[/] [{succ_fg}]{message}[/]"
        elif lvl in ("warning", "warn"):
            warn_fg = THEME_COLORS.get("status_warning_fg", "#b5935d")
            return f"[{warn_fg}]![/] [{warn_fg}]{message}[/]"
        elif lvl in ("error", "err", "danger"):
            fail_fg = THEME_COLORS.get("status_failed_fg", "#b36262")
            return f"[{fail_fg}]x[/] [{fail_fg}]{message}[/]"
        elif lvl == "dim":
            return f"[dim]{message}[/]"
        else:  # "info" or default
            info_fg = THEME_COLORS.get("accent_primary", "#4ba3be")
            return f"[{info_fg}]*[/] [{text_primary}]{message}[/]"

    def _format_hardware_markup(self) -> str:
        """Format hardware indicators and mini clock with clean spacing."""
        parts: list[str] = []
        active_accent = THEME_COLORS.get("status_completed_fg", "#72a37d")
        cyan_accent = THEME_COLORS.get("status_converting_fg", "#58aeca")
        text_primary = THEME_COLORS.get("text_primary", "#e1e4ec")
        text_dim = THEME_COLORS.get("text_muted", "#5e6678")

        # GPU indicator pill
        gpu_val = self.gpu_status
        if isinstance(gpu_val, bool):
            gpu_str = f"[{active_accent}]ON[/]" if gpu_val else "[dim]OFF[/]"
        else:
            val_clean = str(gpu_val).strip()
            if val_clean.upper() in ("OFF", "0", "FALSE", "NONE", ""):
                gpu_str = "[dim]OFF[/]"
            else:
                gpu_str = f"[{active_accent}]{val_clean.upper()}[/]"
        parts.append(f"[dim]GPU:[/] {gpu_str}")

        # WATCH indicator pill
        watch_val = self.watch_status
        if isinstance(watch_val, bool):
            watch_str = f"[{cyan_accent}]ON[/]" if watch_val else "[dim]OFF[/]"
        else:
            w_clean = str(watch_val).strip().upper()
            watch_str = f"[{cyan_accent}]ON[/]" if w_clean in ("ON", "1", "TRUE") else "[dim]OFF[/]"
        parts.append(f"[dim]WATCH:[/] {watch_str}")

        # THREADS indicator pill
        th_val = self.threads_status.strip() or "0/0"
        if th_val.startswith("0/") or th_val == "0":
            parts.append(f"[dim]THREADS:[/] [dim]{th_val}[/]")
        else:
            parts.append(f"[dim]THREADS:[/] [{text_primary}]{th_val}[/]")

        # Mini Clock
        if self.show_clock and self.clock_time:
            parts.append(f"[dim]|[/] [{text_dim}]{self.clock_time}[/]")

        return "  ".join(parts)

    def _refresh_left(self) -> None:
        """Render Left Section: Status Badge + Active Preset."""
        try:
            badge = self._format_status_badge(self.status)
            sec_fg = THEME_COLORS.get("text_secondary", "#9aa2b4")
            if self.preset.strip():
                markup = f"{badge}  [dim]|[/]  [{sec_fg}]{self.preset.strip()}[/]"
            else:
                markup = badge
            self.query_one("#status-bar-left", Static).update(markup)
        except NoMatches:
            pass

    def _refresh_center(self, faded: bool = False) -> None:
        """Render Center Section: Live transient notification."""
        try:
            markup = self._format_notification_markup(
                self._current_notification,
                self._current_level,
                faded=faded,
            )
            self.query_one("#status-bar-center", Static).update(markup)
        except NoMatches:
            pass

    def _refresh_right(self) -> None:
        """Render Right Section: Hardware telemetry + Mini Clock."""
        try:
            markup = self._format_hardware_markup()
            self.query_one("#status-bar-right", Static).update(markup)
        except NoMatches:
            pass

    # =========================================================================
    # Pure Rich / String Render Helpers (for testing or logging)
    # =========================================================================

    def render_markup(self, width: Optional[int] = None) -> str:
        """Return full status bar Rich markup string.

        Useful for unit tests, console logging, or integration with Rich tables.
        If width is specified, ensures sections fit cleanly without collision or wrapping.
        """
        badge = self._format_status_badge(self.status)
        sec_fg = THEME_COLORS.get("text_secondary", "#9aa2b4")
        left = badge
        if self.preset.strip():
            left += f"  [dim]|[/]  [{sec_fg}]{self.preset.strip()}[/]"

        center = self._format_notification_markup(
            self._current_notification,
            self._current_level,
            faded=False,
        )

        right = self._format_hardware_markup()

        if width is None:
            return f"{left}    {center}    {right}"

        # If width is specified, format layout so sections never collide or wrap
        left_t = Text.from_markup(left)
        right_t = Text.from_markup(right)
        center_t = Text.from_markup(center) if center else Text("")

        left_len = left_t.cell_len
        right_len = right_t.cell_len
        center_len = center_t.cell_len

        # If everything fits with at least 4 chars padding
        if left_len + center_len + right_len + 4 <= width:
            remaining = width - left_len - right_len - center_len
            pad_left = remaining // 2
            pad_right = remaining - pad_left
            return f"{left}" + (" " * pad_left) + f"{center}" + (" " * pad_right) + f"{right}"

        # Try truncating center notification
        avail_center = width - left_len - right_len - 4
        if avail_center >= 6 and center:
            trunc_c = center_t.plain[: avail_center - 1] + "…"
            center_markup = f"[dim]{trunc_c}[/]"
            center_len = len(trunc_c)
            remaining = width - left_len - right_len - center_len
            pad_left = remaining // 2
            pad_right = remaining - pad_left
            return f"{left}" + (" " * pad_left) + f"{center_markup}" + (" " * pad_right) + f"{right}"

        # Drop center notification
        if left_len + right_len + 2 <= width:
            pad = width - left_len - right_len
            return f"{left}" + (" " * pad) + f"{right}"

        # Truncate preset from left
        badge_len = Text.from_markup(badge).cell_len
        if badge_len + right_len + 2 <= width:
            avail_preset = width - badge_len - right_len - 6
            if avail_preset > 4:
                trunc_preset = self.preset.strip()[: avail_preset - 1] + "…"
                left = f"{badge}  [dim]|[/]  [{sec_fg}]{trunc_preset}[/]"
                pad = width - Text.from_markup(left).cell_len - right_len
                return f"{left}" + (" " * max(1, pad)) + f"{right}"
            else:
                pad = width - badge_len - right_len
                return f"{badge}" + (" " * max(1, pad)) + f"{right}"

        # Truncate right pills if necessary
        avail_for_right = max(0, width - badge_len - 2)
        if avail_for_right > 8:
            right_trunc = right_t.plain[: avail_for_right - 1] + "…"
            pad = max(1, width - badge_len - len(right_trunc))
            return f"{badge}" + (" " * pad) + f"{right_trunc}"
        return badge

    def render_rich_text(self, width: Optional[int] = None) -> Text:
        """Return status bar as a styled Rich Text object."""
        return Text.from_markup(self.render_markup(width))


# =============================================================================
# Standalone Interactive Demo
# =============================================================================

if __name__ == "__main__":
    from textual.containers import Container, Horizontal
    from textual.widgets import Button, Header, Label

    class StatusBarDemoApp(App):
        """Interactive test application demonstrating TUIStatusBar features."""

        CSS = """
        Screen {
            background: #0d0f14;
        }

        #demo-container {
            width: 100%;
            height: 1fr;
            align: center middle;
            padding: 2;
        }

        #demo-title {
            text-align: center;
            color: #4ba3be;
            text-style: bold;
            margin-bottom: 1;
        }

        #demo-desc {
            text-align: center;
            color: #5e6678;
            margin-bottom: 2;
        }

        #button-row {
            width: auto;
            height: auto;
            layout: horizontal;
            align: center middle;
        }

        #button-row Button {
            margin: 0 1;
            background: #181c28;
            color: #e1e4ec;
            border: none;
        }

        #button-row Button:hover {
            background: #242d40;
            color: #58aeca;
        }
        """

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Container(id="demo-container"):
                yield Label("TUIStatusBar Enterprise Component Demo", id="demo-title")
                yield Label("Click buttons below to test status badges, notifications, and hardware indicators", id="demo-desc")
                with Horizontal(id="button-row"):
                    yield Button("Status: READY", id="btn-ready")
                    yield Button("Status: CONV [3/10]", id="btn-conv")
                    yield Button("Status: PAUSED", id="btn-pause")
                    yield Button("Status: ALL DONE", id="btn-done")
                    yield Button("Notify: Added 4 files", id="btn-notify-info")
                    yield Button("Notify: Saved Report", id="btn-notify-success")
                    yield Button("Toggle GPU", id="btn-gpu")
                    yield Button("Toggle Watch", id="btn-watch")
            yield TUIStatusBar(
                status="READY",
                preset="MP3 320k | EBU R128",
                gpu=True,
                watch=False,
                threads="4/12",
                id="main-status-bar",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            sb = self.query_one("#main-status-bar", TUIStatusBar)
            bid = event.button.id
            if bid == "btn-ready":
                sb.set_status("READY")
                sb.set_preset("MP3 320k | EBU R128")
                sb.set_threads("0/12")
            elif bid == "btn-conv":
                sb.set_status("CONVERTING", 3, 10)
                sb.set_preset("FLAC 24-bit 96kHz")
                sb.set_threads("4/12")
            elif bid == "btn-pause":
                sb.set_status("PAUSED")
                sb.notify_status("Queue processing paused by user", level="warning")
            elif bid == "btn-done":
                sb.set_status("ALL DONE")
                sb.set_threads("0/12")
                sb.notify_status("All 10 audio transcodes completed successfully", level="success")
            elif bid == "btn-notify-info":
                sb.notify_status("Added 4 files from clipboard", level="info")
            elif bid == "btn-notify-success":
                sb.notify_status("Saved report to batch.json", level="success")
            elif bid == "btn-gpu":
                sb.set_gpu(not sb.gpu_status)
            elif bid == "btn-watch":
                sb.set_watch(not sb.watch_status)

    demo = StatusBarDemoApp()
    demo.run()
