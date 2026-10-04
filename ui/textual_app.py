"""
ui/textual_app.py - Master Unified Textual TUI Application for Video-to-Audio Transcoder.

Integrates all 10 specialized subagent components:
1. ui.tui_theme: Slate Dark minimalist monochrome styling, zero gaudy colors.
2. ui.tui_screens.file_picker: Interactive visual directory/video browser modal.
3. ui.tui_screens.preset_dialog: Interactive format & quality preset modal.
4. ui.tui_screens.filter_dialog: Interactive DSP audio filters modal (EBU R128, lossless copy, sample rate, channels).
5. ui.tui_widgets.resource_monitor: Real-time per-core CPU, RAM, GPU acceleration, and worker telemetry.
6. ui.tui_widgets.queue_table: Interactive queue table with row selection, reorder, delete, and task inspector.
7. ui.tui_widgets.visualizer_widget: Smooth 3-mode audio visualizer (Braille Oscilloscope, Spectrum, VU Meter).
8. ui.tui_screens.history_screen: Session conversion history, compression ratios, and report export.
9. ui.tui_screens.help_screen: Interactive keyboard shortcuts and tips cheat-sheet.
10. ui.tui_widgets.status_bar: Bottom status bar with preset badges, transient notifications, and live clock.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, RichLog

# Core processing components
from processing.queue_manager import ConversionTask, QueueManager, TaskStatus
from ui.input_handler import SUPPORTED_VIDEO_EXTENSIONS, get_clipboard_files, parse_input_paths

# Theme
from ui.tui_theme import TUI_CSS, apply_tui_theme

# Widgets
from ui.tui_widgets.queue_table import QueueTableWidget, TaskDetailsRequested
from ui.tui_widgets.resource_monitor import ResourceMonitorWidget
from ui.tui_widgets.status_bar import TUIStatusBar
from ui.tui_widgets.visualizer_widget import AudioVisualizerWidget, VisualizerMode

# Screens & Modals
from ui.tui_screens.file_picker import FilePickerModal
from ui.tui_screens.filter_dialog import FilterDialogModal
from ui.tui_screens.help_screen import HelpModalScreen
from ui.tui_screens.history_screen import HistoryModalScreen
from ui.tui_screens.preset_dialog import PresetDialogModal


class TranscoderTUI(App):
    """
    Enterprise Textual TUI Application for Video to Audio Transcoding.
    Sleek, minimalist dark monochrome interface with complete mouse and keyboard ergonomics.
    """

    CSS = TUI_CSS + """
    Screen {
        background: #0d0f14;
        color: #e1e4ec;
        layout: vertical;
        overflow: hidden hidden;
    }

    #app-body {
        layout: horizontal;
        height: 1fr;
        width: 100%;
        overflow: hidden hidden;
    }

    #sidebar {
        width: 22;
        dock: left;
        padding: 0 1;
        background: #10121a;
        border-right: solid #1b1f2b;
        overflow-y: auto;
        overflow-x: hidden;
    }

    #sidebar-title {
        text-align: center;
        text-style: bold;
        color: #e1e4ec;
        margin-top: 1;
        margin-bottom: 1;
        border-bottom: solid #1b1f2b;
        padding-bottom: 1;
    }

    #sidebar Button {
        width: 100%;
        height: 3;
        min-height: 3;
        max-height: 3;
        padding: 0;
        margin-bottom: 1;
        background: #141722;
        color: #9aa2b4;
        border: solid #1b1f2b;
        content-align: center middle;
    }

    #sidebar Button:hover {
        background: #1d2332;
        color: #ffffff;
        border: solid #3d5470;
    }

    #sidebar Button:focus {
        background: #222a3d;
        color: #ffffff;
        border: solid #3d5470;
        border-left: thick #4ba3be;
    }

    #sidebar Button:disabled {
        background: #10121a;
        color: #3b4252;
        border: solid #171a26;
    }

    #center-pane {
        width: 5fr;
        height: 100%;
        layout: vertical;
        padding: 0 1;
        overflow: hidden hidden;
    }

    #queue-wrapper {
        height: 2fr;
        border: round #242938;
        background: #13161f;
        margin-bottom: 1;
        overflow: hidden;
    }

    #queue-wrapper:focus-within {
        border: round #3d5470;
    }

    #log-wrapper {
        height: 1fr;
        border: round #242938;
        background: #13161f;
        overflow: hidden;
    }

    #log-wrapper:focus-within {
        border: round #3d5470;
    }

    #right-pane {
        width: 3fr;
        height: 100%;
        layout: vertical;
        padding: 0 1 0 0;
        overflow: hidden hidden;
    }

    #telemetry-wrapper {
        height: 1fr;
        border: round #242938;
        background: #13161f;
        margin-bottom: 1;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
    }

    #telemetry-wrapper:focus-within {
        border: round #3d5470;
    }

    #visualizer-wrapper {
        height: 1fr;
        border: round #242938;
        background: #13161f;
        padding: 0 1;
        overflow: hidden;
    }

    #visualizer-wrapper:focus-within {
        border: round #3d5470;
    }

    #event-log {
        height: 100%;
        background: transparent;
        color: #9aa2b4;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True, key_display="q"),
        Binding("b", "browse_files", "Browse Files", show=True, key_display="b"),
        Binding("a", "browse_files", "Add Files", show=False),
        Binding("v", "paste_clipboard", "Paste", show=True, key_display="v"),
        Binding("s", "toggle_start", "Start/Pause", show=True, key_display="s"),
        Binding("p", "open_presets", "Presets", show=True, key_display="p"),
        Binding("f", "open_filters", "Filters", show=True, key_display="f"),
        Binding("h", "open_history", "History", show=True, key_display="h"),
        Binding("question_mark", "open_help", "Help", show=True, key_display="?"),
        Binding("c", "clear_completed", "Clear Done", show=False),
        Binding("m", "cycle_visualizer", "Visualizer Mode", show=False),
    ]

    def __init__(self, initial_paths: Optional[List[Path]] = None):
        super().__init__()
        self.queue_manager = QueueManager()
        self.queue_manager.pause_queue()
        self.initial_paths = initial_paths or []

        # Active configuration
        self.current_format: str = "mp3"
        self.current_bitrate: str = "320k"
        self.current_options: Dict[str, Any] = {
            "bitrate": "320k",
            "preserve_cover_art": True,
            "ebu_r128": False,
            "lossless_copy_if_match": False,
        }

        # Wire queue callbacks
        self.queue_manager.on_task_started = self._on_task_started
        self.queue_manager.on_task_progress = self._on_task_progress
        self.queue_manager.on_task_completed = self._on_task_completed
        self.queue_manager.on_task_failed = self._on_task_failed
        self.queue_manager.on_queue_completed = self._on_queue_completed

    def compose(self) -> ComposeResult:
        """Compose child widgets in responsive layout."""
        yield Header(show_clock=False)

        with Horizontal(id="app-body"):
            # 1. Left Sidebar
            with Container(id="sidebar"):
                yield Label("TRANSCODER", id="sidebar-title")
                yield Button("Browse Files (b)", id="btn-browse")
                yield Button("Paste Files (v)", id="btn-paste")
                yield Button("Start Batch (s)", id="btn-start")
                yield Button("Pause Queue", id="btn-pause", disabled=True)
                yield Button("Presets (p)", id="btn-presets")
                yield Button("DSP Filters (f)", id="btn-filters")
                yield Button("History (h)", id="btn-history")
                yield Button("Clear Done (c)", id="btn-clear")
                yield Button("Help (?)", id="btn-help")
                yield Button("Quit (q)", id="btn-quit")

            # 2. Center Column: Queue Table + Event Log
            with Vertical(id="center-pane"):
                with Container(id="queue-wrapper"):
                    yield QueueTableWidget(
                        queue_manager=self.queue_manager,
                        show_header=True,
                        show_footer=True,
                        id="queue-table-widget",
                    )
                with Container(id="log-wrapper"):
                    yield RichLog(id="event-log", highlight=False, markup=True)

            # 3. Right Column: Telemetry + Audio Visualizer
            with Vertical(id="right-pane"):
                with Container(id="telemetry-wrapper"):
                    yield ResourceMonitorWidget(
                        queue_manager=self.queue_manager,
                        id="resource-monitor",
                    )
                with Container(id="visualizer-wrapper"):
                    yield AudioVisualizerWidget(
                        queue_manager=self.queue_manager,
                        id="audio-visualizer",
                    )

        # 4. Bottom Status Bar & Keybinding Footer
        yield TUIStatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Called once the app is mounted."""
        self.title = "Neural Transcode Matrix"
        self.sub_title = "Enterprise Video-to-Audio Transcoder"

        apply_tui_theme(self)

        status_bar = self.query_one("#status-bar", TUIStatusBar)
        status_bar.set_status("READY")
        self._update_preset_status_badge()

        log = self.query_one("#event-log", RichLog)
        tstamp = time.strftime("%H:%M:%S")
        log.write(f"[dim]{tstamp}[/] [white]Enterprise Transcoder initialized.[/] Ready for operations.")

        # If user passed initial files via CLI, stage them
        if self.initial_paths:
            self._stage_paths(self.initial_paths)

        # Timer to keep status bar synchronized with queue
        self.set_interval(0.5, self._sync_telemetry)

        # Initial responsive check for compact telemetry on smaller terminals
        try:
            rm = self.query_one("#resource-monitor", ResourceMonitorWidget)
            rm.compact = (self.size.width < 100)
            rm.refresh_telemetry()
        except Exception:
            pass

    def on_resize(self, event: Any) -> None:
        """Adapts widget presentation dynamically across terminal sizes."""
        try:
            rm = self.query_one("#resource-monitor", ResourceMonitorWidget)
            rm.compact = (event.size.width < 100)
            rm.refresh_telemetry()
        except Exception:
            pass

    # =========================================================================
    # User Actions & Keybindings
    # =========================================================================

    def action_browse_files(self) -> None:
        """Opens the interactive FilePickerModal."""
        def on_files_picked(selected_paths: Optional[List[Path]]) -> None:
            if selected_paths:
                self._stage_paths(selected_paths)

        self.push_screen(FilePickerModal(), on_files_picked)

    def action_paste_clipboard(self) -> None:
        """Pastes video files or folder paths from system clipboard."""
        paths = get_clipboard_files()
        if paths:
            self._stage_paths(paths)
        else:
            self._notify("No valid video files found in clipboard.", level="warning")

    def action_toggle_start(self) -> None:
        """Toggles between starting and pausing queue execution."""
        stats = self.queue_manager.get_stats()
        if not stats.is_running or self.queue_manager._pause_event.is_set():
            # Start / Resume
            self.queue_manager.resume_queue()
            self.query_one("#btn-start", Button).disabled = True
            self.query_one("#btn-pause", Button).disabled = False
            self.query_one("#status-bar", TUIStatusBar).set_status("CONVERTING")
            self._notify("Queue processing started.", level="info")
            self._log("Queue processing resumed.", "white")
        else:
            # Pause
            self.queue_manager.pause_queue()
            self.query_one("#btn-start", Button).disabled = False
            self.query_one("#btn-pause", Button).disabled = True
            self.query_one("#status-bar", TUIStatusBar).set_status("PAUSED")
            self._notify("Queue paused.", level="warning")
            self._log("Queue paused by user.", "dim")

    def action_open_presets(self) -> None:
        """Opens the PresetDialogModal."""
        def on_preset_selected(result: Optional[Dict[str, Any]]) -> None:
            if result:
                self.current_format = result.get("format", "mp3")
                self.current_bitrate = result.get("bitrate", "320k")
                if "options" in result:
                    self.current_options.update(result["options"])
                self.current_options["bitrate"] = self.current_bitrate
                self._update_preset_status_badge()
                self._notify(f"Preset updated: {self.current_format.upper()} ({self.current_bitrate})", level="success")
                self._log(f"Profile set to {self.current_format.upper()} ({self.current_bitrate})", "white")

        self.push_screen(PresetDialogModal(), on_preset_selected)

    def action_open_filters(self) -> None:
        """Opens the FilterDialogModal."""
        def on_filters_selected(options: Optional[Dict[str, Any]]) -> None:
            if options:
                self.current_options.update(options)
                self._update_preset_status_badge()
                norm_str = "EBU R128" if options.get("ebu_r128") else "Standard"
                copy_str = "Copy" if options.get("lossless_copy_if_match") else "Encode"
                self._notify(f"Filters updated: {norm_str} | {copy_str}", level="info")
                self._log(f"DSP filters updated: {options}", "dim")

        self.push_screen(FilterDialogModal(current_options=self.current_options), on_filters_selected)

    def action_open_history(self) -> None:
        """Opens the HistoryModalScreen."""
        self.push_screen(HistoryModalScreen(queue_manager=self.queue_manager))

    def action_open_help(self) -> None:
        """Opens the HelpModalScreen."""
        self.push_screen(HelpModalScreen())

    def action_clear_completed(self) -> None:
        """Clears completed and cancelled tasks from queue."""
        self.queue_manager.clear_queue(cancel_active=False)
        self.query_one("#queue-table-widget", QueueTableWidget).refresh_tasks()
        self._notify("Cleared completed tasks.", level="info")

    def action_cycle_visualizer(self) -> None:
        """Cycles audio visualizer modes."""
        vis = self.query_one("#audio-visualizer", AudioVisualizerWidget)
        vis.action_cycle_mode()

    # =========================================================================
    # Button Event Handlers
    # =========================================================================

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-browse":
            self.action_browse_files()
        elif btn_id == "btn-paste":
            self.action_paste_clipboard()
        elif btn_id == "btn-start":
            self.action_toggle_start()
        elif btn_id == "btn-pause":
            self.action_toggle_start()
        elif btn_id == "btn-presets":
            self.action_open_presets()
        elif btn_id == "btn-filters":
            self.action_open_filters()
        elif btn_id == "btn-history":
            self.action_open_history()
        elif btn_id == "btn-clear":
            self.action_clear_completed()
        elif btn_id == "btn-help":
            self.action_open_help()
        elif btn_id == "btn-quit":
            self.exit()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _stage_paths(self, paths: List[Path]) -> None:
        """Stages video files into QueueManager."""
        added = 0
        for p in paths:
            if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                self.queue_manager.add_task(
                    source_file=p,
                    target_format=self.current_format,
                    options=dict(self.current_options),
                )
                added += 1
            elif p.is_dir():
                discovered = parse_input_paths(str(p), recursive=True)
                for f in discovered:
                    self.queue_manager.add_task(
                        source_file=f,
                        target_format=self.current_format,
                        options=dict(self.current_options),
                    )
                    added += 1

        if added > 0:
            self.query_one("#queue-table-widget", QueueTableWidget).refresh_tasks()
            self._notify(f"Staged {added} video file(s) for conversion.", level="success")
            self._log(f"Added {added} file(s) to conversion batch.", "white")
        else:
            self._notify("No supported video files discovered.", level="warning")

    def _update_preset_status_badge(self) -> None:
        """Updates preset label on status bar."""
        try:
            status_bar = self.query_one("#status-bar", TUIStatusBar)
            loudnorm_flag = " | EBU R128" if self.current_options.get("ebu_r128") else ""
            status_bar.set_preset(f"{self.current_format.upper()} {self.current_bitrate}{loudnorm_flag}")
        except Exception:
            pass

    def _sync_telemetry(self) -> None:
        """Keeps status bar state in sync with QueueManager."""
        try:
            stats = self.queue_manager.get_stats()
            status_bar = self.query_one("#status-bar", TUIStatusBar)
            vis = self.query_one("#audio-visualizer", AudioVisualizerWidget)

            if stats.active_tasks > 0:
                status_bar.set_status("CONVERTING", current=stats.completed_tasks, total=stats.total_tasks)
                vis.is_active = True
            elif stats.total_tasks > 0 and stats.pending_tasks == 0 and stats.active_tasks == 0:
                status_bar.set_status("ALL DONE")
                vis.is_active = False
            elif self.queue_manager._pause_event.is_set():
                status_bar.set_status("PAUSED")
                vis.is_active = False
            else:
                status_bar.set_status("READY")
                vis.is_active = False
        except Exception:
            pass

    def _notify(self, message: str, level: str = "info") -> None:
        """Posts a clean transient message to status bar."""
        try:
            self.query_one("#status-bar", TUIStatusBar).notify_status(message, level=level, duration=4.0)
        except Exception:
            pass

    def _log(self, message: str, color: str = "dim") -> None:
        """Writes timestamped entry to event log."""
        try:
            log = self.query_one("#event-log", RichLog)
            tstamp = time.strftime("%H:%M:%S")
            log.write(f"[dim]{tstamp}[/] [{color}]{message}[/]")
        except Exception:
            pass

    # =========================================================================
    # QueueManager Background Thread Callbacks
    # =========================================================================

    def _on_task_started(self, task: ConversionTask) -> None:
        self.call_from_thread(self._log, f"Started: {task.source_file.name}", "white")

    def _on_task_progress(self, task: ConversionTask, snap: Any) -> None:
        pass

    def _on_task_completed(self, task: ConversionTask, ver: Any) -> None:
        self.call_from_thread(self._log, f"Finished: {task.source_file.name}", "dim white")

    def _on_task_failed(self, task: ConversionTask, err: str) -> None:
        self.call_from_thread(self._log, f"Failed: {task.source_file.name} - {err}", "dim")

    def _on_queue_completed(self, stats: Any) -> None:
        self.call_from_thread(self._on_queue_done)

    def _on_queue_done(self) -> None:
        self.query_one("#btn-start", Button).disabled = False
        self.query_one("#btn-pause", Button).disabled = True
        self._notify("Batch queue completed.", level="success")
        self._log("All tasks completed.", "white")


def run_textual_app(initial_paths: Optional[List[Path]] = None) -> None:
    """Launches the master Textual TUI Application."""
    app = TranscoderTUI(initial_paths)
    app.run()
