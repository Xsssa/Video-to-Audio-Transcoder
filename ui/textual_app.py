import os
import sys
import time
from pathlib import Path
from typing import Any, List

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, DataTable, Button, Static, RichLog, Label
from textual.reactive import reactive
from textual.css.query import NoMatches
from rich.text import Text

# Import core components
from processing.queue_manager import QueueManager, ConversionTask, TaskStatus
from core.audio_profiles import get_profile, AudioFormat
from ui.ascii_visualizer import AsciiVisualizer
from ui.input_handler import SUPPORTED_VIDEO_EXTENSIONS, get_clipboard_files

class MonitorWidget(Static):
    """A Textual widget that renders the AsciiVisualizer from Rich."""
    
    def on_mount(self) -> None:
        self.visualizer = AsciiVisualizer()
        self.start_time = time.time()
        self.is_active = False
        self.current_progress = 0.0
        self.current_speed = 0.0
        self.update_timer = self.set_interval(0.1, self.update_monitor)

    def update_monitor(self) -> None:
        elapsed = time.time() - self.start_time
        # Render the rich table
        renderable = self.visualizer.render_monitor_widget(
            time_sec=elapsed,
            progress=self.current_progress,
            speed=self.current_speed,
            is_active=self.is_active,
            workers=4 if self.is_active else 0,
            spectrum_height=5,
            compact=False
        )
        self.update(renderable)

class TranscoderTUI(App):
    """Advanced Textual TUI for Video to Audio Transcoder."""
    
    CSS = """
    Screen {
        background: $surface-darken-2;
    }
    
    #sidebar {
        width: 20;
        dock: left;
        padding: 1;
        background: $surface-darken-1;
        border-right: vkey $background-lighten-1;
    }
    
    #sidebar Button {
        width: 100%;
        margin-bottom: 1;
        background: $surface;
        color: $text;
        border: none;
    }
    
    #sidebar Button:hover {
        background: $primary;
        color: $text-muted;
    }
    
    #main-content {
        layout: vertical;
        height: 100%;
    }
    
    #top-row {
        height: 1fr;
        layout: horizontal;
    }
    
    #queue-container {
        width: 3fr;
        height: 100%;
        border-right: vkey $background-lighten-1;
        background: $surface-darken-2;
    }
    
    #monitor-container {
        width: 2fr;
        height: 100%;
        padding: 1;
        background: $surface-darken-2;
    }
    
    #log-container {
        height: 10;
        border-top: hkey $background-lighten-1;
        background: $surface-darken-2;
        dock: bottom;
    }
    
    DataTable {
        height: 100%;
        background: $surface-darken-2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("a", "add_files", "Add Files (Clipboard)"),
        ("s", "toggle_start", "Start/Pause"),
    ]

    def __init__(self, initial_paths: List[Path] = None):
        super().__init__()
        self.queue_manager = QueueManager()
        self.queue_manager.pause_queue()
        self.initial_paths = initial_paths or []
        self.current_format = "mp3"
        self.current_options = {"bitrate": "320k"}
        
        # Wire queue callbacks
        self.queue_manager.on_task_started = self._on_task_started
        self.queue_manager.on_task_progress = self._on_task_progress
        self.queue_manager.on_task_completed = self._on_task_completed
        self.queue_manager.on_task_failed = self._on_task_failed
        self.queue_manager.on_queue_completed = self._on_queue_completed

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)
        
        with Container(id="sidebar"):
            yield Label("TRANSCODER", id="app-title", classes="text-bold text-center")
            yield Button("Paste Files", id="btn-add")
            yield Button("Start", id="btn-start")
            yield Button("Pause", id="btn-pause", disabled=True)
            yield Button("Clear Done", id="btn-clear")
            yield Button("Quit", id="btn-quit")
            
        with Container(id="main-content"):
            with Horizontal(id="top-row"):
                with Container(id="queue-container"):
                    yield DataTable(id="queue-table")
                with Container(id="monitor-container"):
                    yield MonitorWidget(id="monitor")
            with Container(id="log-container"):
                yield RichLog(id="event-log", highlight=False, markup=True)
                
        yield Footer()

    def on_mount(self) -> None:
        """Called when app starts."""
        self.title = "Neural Transcode Matrix"
        self.sub_title = "Minimal Workspace"
        
        table = self.query_one("#queue-table", DataTable)
        table.add_columns("ID", "Status", "Filename", "Target", "Progress", "Speed")
        
        log = self.query_one("#event-log", RichLog)
        log.write("[dim]System Initialized. Awaiting operations...[/]")
        
        if self.initial_paths:
            self._add_paths(self.initial_paths)
            
        self.set_interval(0.5, self.refresh_table)

    def action_add_files(self) -> None:
        self.on_button_pressed(Button.Pressed(self.query_one("#btn-add", Button)))
        
    def action_toggle_start(self) -> None:
        if self.query_one("#btn-start", Button).disabled:
            self.on_button_pressed(Button.Pressed(self.query_one("#btn-pause", Button)))
        else:
            self.on_button_pressed(Button.Pressed(self.query_one("#btn-start", Button)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Event handler for button presses."""
        btn_id = event.button.id
        if btn_id == "btn-quit":
            self.exit()
        elif btn_id == "btn-add":
            paths = get_clipboard_files()
            if paths:
                self._add_paths(paths)
                self.query_one("#event-log", RichLog).write(f"[dim]Added {len(paths)} files from clipboard.[/]")
            else:
                self.query_one("#event-log", RichLog).write("[dim]No valid video files found in clipboard.[/]")
        elif btn_id == "btn-start":
            self.queue_manager.resume_queue()
            self.query_one("#btn-start", Button).disabled = True
            self.query_one("#btn-pause", Button).disabled = False
            self.query_one("#event-log", RichLog).write("[dim]Conversion Started.[/]")
        elif btn_id == "btn-pause":
            self.queue_manager.pause_queue()
            self.query_one("#btn-start", Button).disabled = False
            self.query_one("#btn-pause", Button).disabled = True
            self.query_one("#event-log", RichLog).write("[dim]Conversion Paused.[/]")
        elif btn_id == "btn-clear":
            self.queue_manager.clear_queue(cancel_active=False)
            self.refresh_table()
            self.query_one("#event-log", RichLog).write("[dim]Cleared completed tasks.[/]")

    def _add_paths(self, paths: List[Path]) -> None:
        for p in paths:
            self.queue_manager.add_task(
                source_file=p,
                target_format=self.current_format,
                options=self.current_options
            )
        self.refresh_table()

    def refresh_table(self) -> None:
        try:
            table = self.query_one("#queue-table", DataTable)
            table.clear()
            
            tasks = self.queue_manager.get_all_tasks()
            
            # Update monitor status based on active tasks
            monitor = self.query_one("#monitor", MonitorWidget)
            active_task = None
            
            for i, task in enumerate(tasks):
                status_str = f"[{task.status.value}]"
                if task.status == TaskStatus.CONVERTING:
                    status_str = f"[white]{status_str}[/]"
                    active_task = task
                elif task.status == TaskStatus.COMPLETED:
                    status_str = f"[dim white]{status_str}[/]"
                elif task.status == TaskStatus.FAILED:
                    status_str = f"[dim]{status_str}[/]"
                elif task.status == TaskStatus.PROBING:
                    status_str = f"[dim]{status_str}[/]"
                else:
                    status_str = f"[dim]{status_str}[/]"

                fname = task.source_file.name
                if len(fname) > 30:
                    fname = fname[:27] + "..."
                    
                prog = f"{task.progress:.1f}%"
                spd = task.speed
                
                table.add_row(
                    str(i+1),
                    Text.from_markup(status_str),
                    fname,
                    task.target_format.upper(),
                    prog,
                    spd
                )
                
            if active_task:
                monitor.is_active = True
                monitor.current_progress = active_task.progress
                try:
                    monitor.current_speed = float(active_task.speed.replace("x", ""))
                except:
                    monitor.current_speed = 1.0
            else:
                monitor.is_active = False
                
        except NoMatches:
            pass

    # Callbacks from QueueManager (executed in worker threads, use call_from_thread)
    def _on_task_started(self, task: ConversionTask):
        self.call_from_thread(self._log_event, f"Started: {task.source_file.name}", "dim")
        
    def _on_task_progress(self, task: ConversionTask, snap: Any):
        pass # Handled by interval
        
    def _on_task_completed(self, task: ConversionTask, ver: Any):
        self.call_from_thread(self._log_event, f"Completed: {task.source_file.name}", "dim white")
        
    def _on_task_failed(self, task: ConversionTask, err: str):
        self.call_from_thread(self._log_event, f"Failed: {task.source_file.name} - {err}", "dim")
        
    def _on_queue_completed(self, stats: Any):
        self.call_from_thread(self._on_queue_done)
        
    def _on_queue_done(self):
        self.query_one("#btn-start", Button).disabled = False
        self.query_one("#btn-pause", Button).disabled = True
        self.query_one("#event-log", RichLog).write("[dim]Batch Queue Completed.[/]")

    def _log_event(self, msg: str, color: str = "dim"):
        try:
            log = self.query_one("#event-log", RichLog)
            tstamp = time.strftime("%H:%M:%S")
            log.write(f"[dim]{tstamp} {msg}[/]")
        except NoMatches:
            pass

def run_textual_app(initial_paths: List[Path] = None):
    app = TranscoderTUI(initial_paths)
    app.run()
