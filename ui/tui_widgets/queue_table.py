"""
queue_table.py - Sleek Minimalist Dark Queue Table Widget for Textual TUI.

Provides an interactive QueueTableWidget based on Textual's DataTable and Container
for managing the video-to-audio conversion batch queue:
1. Columns: `#`, `Status`, `Filename`, `Format`, `Size`, `Progress`, `Speed`, `ETA`.
2. Clean status indicators: `[Pending]`, `[Converting]`, `[Completed]`, `[Failed]`, `[Paused]`
   in sleek monochrome / subtle dim styling.
3. Interactive keyboard & mouse navigation:
   - Row selection (cursor_type="row")
   - Delete selected task keybinding (`d` or `Delete`)
   - Move task up/down in queue (`k`/`j`, `ctrl+k`/`ctrl+j`, `ctrl+up`/`ctrl+down`, `shift+up`/`shift+down`)
   - View details of selected task (`Enter` or double click)
4. Batch statistics header or summary footer showing:
   Total files staged, Completed count, Failed count, Total runtime, Estimated remaining time.
5. Smooth refresh method that updates cell values efficiently without resetting user scroll
   position or selection.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static
from textual.widgets.data_table import RowKey

from processing.queue_manager import ConversionTask, QueueManager, QueueStats, TaskStatus

# Standard column schema
COLUMN_KEYS: Tuple[str, ...] = (
    "col_num",
    "col_status",
    "col_filename",
    "col_format",
    "col_size",
    "col_progress",
    "col_speed",
    "col_eta",
)

COLUMN_HEADERS: Tuple[str, ...] = (
    "#",
    "Status",
    "Filename",
    "Format",
    "Size",
    "Progress",
    "Speed",
    "ETA",
)


# =============================================================================
# Helper Utilities (Monochrome / Minimalist Dark Theme)
# =============================================================================

def format_status_indicator(
    status: Union[TaskStatus, str],
    is_queue_paused: bool = False,
) -> Text:
    """
    Renders status indicators in sleek minimalist dark styling (subtle monochrome / dim).
    Never uses bright or gaudy colors.
    """
    if isinstance(status, TaskStatus):
        raw_status = status.value.upper()
    else:
        raw_status = str(status).upper()

    if is_queue_paused and raw_status in ("PENDING", "PAUSED"):
        return Text("[Paused]", style="dim #7a7a7a")

    if raw_status == "PENDING":
        return Text("[Pending]", style="dim #7a7a7a")
    elif raw_status == "PROBING":
        return Text("[Probing]", style="italic #9e9e9e")
    elif raw_status == "CONVERTING":
        return Text("[Converting]", style="bold #ffffff")
    elif raw_status == "COMPLETED":
        return Text("[Completed]", style="#a8a8a8")
    elif raw_status == "FAILED":
        return Text("[Failed]", style="dim #616161")
    elif raw_status == "PAUSED":
        return Text("[Paused]", style="dim #7a7a7a")
    elif raw_status == "CANCELLED":
        return Text("[Cancelled]", style="dim #505050")
    else:
        return Text(f"[{raw_status.capitalize()}]", style="dim #7a7a7a")


def format_file_size(size_bytes: int) -> str:
    """Formats raw bytes into a human-readable string (e.g. '12.4 MB')."""
    if size_bytes <= 0:
        return "-"
    val = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


def format_duration_hms(seconds: Optional[Union[float, int]]) -> str:
    """Formats numeric seconds into HH:MM:SS format."""
    if seconds is None or seconds < 0:
        return "--:--:--"
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def truncate_string(value: str, max_chars: int = 28) -> str:
    """Truncates string with ellipsis if exceeding max characters."""
    if len(value) <= max_chars:
        return value
    keep = max(1, max_chars - 3)
    return value[:keep] + "..."


# =============================================================================
# Custom Event Messages
# =============================================================================

class TaskDetailsRequested(Message):
    """Fired when the user requests details of a task (Enter or double-click)."""

    def __init__(self, task: Optional[ConversionTask], task_id: str) -> None:
        super().__init__()
        self.task = task
        self.task_id = task_id


class TaskDeleted(Message):
    """Fired when a task is deleted from the queue."""

    def __init__(self, task_id: str, task: Optional[ConversionTask] = None) -> None:
        super().__init__()
        self.task_id = task_id
        self.task = task


class TaskMoved(Message):
    """Fired when a task is moved up or down in the queue."""

    def __init__(self, task_id: str, direction: str, old_index: int, new_index: int) -> None:
        super().__init__()
        self.task_id = task_id
        self.direction = direction  # 'up' or 'down'
        self.old_index = old_index
        self.new_index = new_index


class TaskSelected(Message):
    """Fired when user moves row selection to another task."""

    def __init__(self, task: Optional[ConversionTask], task_id: str, row_index: int) -> None:
        super().__init__()
        self.task = task
        self.task_id = task_id
        self.row_index = row_index


# =============================================================================
# Task Detail Modal
# =============================================================================

class TaskDetailModal(ModalScreen[None]):
    """
    Sleek minimalist dark modal screen displaying technical inspection details
    for a selected conversion task.
    """

    DEFAULT_CSS = """
    TaskDetailModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #detail-container {
        width: 78;
        height: auto;
        max-height: 85%;
        background: #141414;
        border: solid #2f2f2f;
        padding: 1 2;
    }

    #detail-header-label {
        width: 100%;
        text-style: bold;
        color: #ffffff;
        border-bottom: solid #222222;
        padding-bottom: 1;
        margin-bottom: 1;
    }

    #detail-content-area {
        height: auto;
        color: #b0b0b0;
        margin-bottom: 1;
    }

    #detail-action-bar {
        height: 3;
        align: right middle;
        border-top: solid #222222;
        padding-top: 1;
    }

    #btn-detail-close {
        background: #222222;
        color: #e0e0e0;
        border: none;
    }

    #btn-detail-close:hover {
        background: #333333;
        color: #ffffff;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True),
        Binding("enter", "dismiss", "Close", show=False),
        Binding("q", "dismiss", "Close", show=False),
    ]

    def __init__(self, task: Optional[ConversionTask], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._conversion_task = task

    @property
    def conversion_task(self) -> Optional[ConversionTask]:
        return self._conversion_task

    def compose(self) -> ComposeResult:
        with Container(id="detail-container"):
            yield Label("TASK INSPECTOR // TELEMETRY", id="detail-header-label")
            yield Static(self._build_content(), id="detail-content-area")
            with Horizontal(id="detail-action-bar"):
                yield Button("Close (Esc)", id="btn-detail-close")

    def _build_content(self) -> Text:
        t = self._conversion_task
        if not t:
            return Text("No task data available.", style="dim")

        text = Text()

        # Task ID & Status
        text.append("Task ID:       ", style="dim #888888")
        text.append(f"{t.task_id}\n", style="bold #ffffff")

        text.append("Status:        ", style="dim #888888")
        status_disp = format_status_indicator(t.status)
        text.append_text(status_disp)
        text.append("\n")

        # Files
        text.append("Source:        ", style="dim #888888")
        text.append(f"{t.source_file}\n", style="#d0d0d0")

        text.append("Target Format: ", style="dim #888888")
        text.append(f"{t.target_format.upper()}\n", style="bold #cccccc")

        if t.output_file:
            text.append("Output File:   ", style="dim #888888")
            text.append(f"{t.output_file}\n", style="#d0d0d0")

        # Sizes
        in_size = format_file_size(t.input_size_bytes)
        out_size = format_file_size(t.output_size_bytes)
        text.append("Input Size:    ", style="dim #888888")
        text.append(f"{in_size}\n", style="#bbbbbb")

        if t.output_size_bytes > 0:
            text.append("Output Size:   ", style="dim #888888")
            text.append(f"{out_size}\n", style="#bbbbbb")

        # Metrics
        text.append("Progress:      ", style="dim #888888")
        text.append(f"{t.progress:.1f}%\n", style="bold #ffffff")

        text.append("Speed:         ", style="dim #888888")
        text.append(f"{t.speed}   ", style="#cccccc")
        text.append("ETA: ", style="dim #888888")
        text.append(f"{t.eta}\n", style="#cccccc")

        if t.duration_seconds > 0:
            text.append("Duration:      ", style="dim #888888")
            text.append(f"{t.duration_seconds:.2f}s\n", style="#bbbbbb")

        # Audio stream probe info
        if t.probe_result and t.probe_result.primary_audio_stream:
            astream = t.probe_result.primary_audio_stream
            text.append("\nAudio Stream Info:\n", style="dim #888888 underline")
            text.append(f"  Codec:       {astream.codec_name or '-'}\n", style="#b8b8b8")
            text.append(f"  Sample Rate: {astream.sample_rate or '-'} Hz\n", style="#b8b8b8")
            text.append(f"  Channels:    {astream.channels or '-'} ({astream.channel_layout or '-'})\n", style="#b8b8b8")
            if astream.bit_rate:
                text.append(f"  Bitrate:     {int(astream.bit_rate)//1000} kbps\n", style="#b8b8b8")

        # Errors if any
        if t.error:
            text.append("\nError Information:\n", style="dim #888888 underline")
            text.append(f"  {t.error}\n", style="dim #777777")

        return text

    @on(Button.Pressed, "#btn-detail-close")
    def on_close_button(self) -> None:
        self.dismiss()


# =============================================================================
# Batch Statistics Summary Widgets
# =============================================================================

class QueueSummaryFooter(Static):
    """
    Sleek summary footer displaying batch metrics:
    Total staged, Completed count, Failed count, Total runtime, Estimated remaining time.
    """

    DEFAULT_CSS = """
    QueueSummaryFooter {
        height: 1;
        min-height: 1;
        background: #121212;
        color: #888888;
        border-top: solid #222222;
        padding: 0 1;
    }
    """

    staged: reactive[int] = reactive(0)
    completed: reactive[int] = reactive(0)
    failed: reactive[int] = reactive(0)
    runtime_str: reactive[str] = reactive("00:00:00")
    remaining_str: reactive[str] = reactive("--:--:--")

    def render(self) -> Text:
        t = Text()
        t.append("STAGED: ", style="dim #777777")
        t.append(f"{self.staged:<3} ", style="bold #ffffff")
        t.append("│ ", style="dim #383838")

        t.append("COMPLETED: ", style="dim #777777")
        t.append(f"{self.completed:<3} ", style="bold #b0b0b0")
        t.append("│ ", style="dim #383838")

        t.append("FAILED: ", style="dim #777777")
        t.append(f"{self.failed:<3} ", style="bold #777777")
        t.append("│ ", style="dim #383838")

        t.append("RUNTIME: ", style="dim #777777")
        t.append(f"{self.runtime_str} ", style="bold #d0d0d0")
        t.append("│ ", style="dim #383838")

        t.append("EST. REMAINING: ", style="dim #777777")
        t.append(f"{self.remaining_str}", style="bold #d0d0d0")
        return t

    def update_stats(
        self,
        staged: int,
        completed: int,
        failed: int,
        runtime_seconds: Optional[float],
        remaining_seconds: Optional[float],
    ) -> None:
        """Updates reactive metric fields smoothly."""
        self.staged = staged
        self.completed = completed
        self.failed = failed
        self.runtime_str = format_duration_hms(runtime_seconds) if runtime_seconds is not None else "00:00:00"
        self.remaining_str = format_duration_hms(remaining_seconds) if remaining_seconds is not None else "--:--:--"


class QueueStatsHeader(Static):
    """
    Optional sleek statistics header bar for batch progress overview.
    """

    DEFAULT_CSS = """
    QueueStatsHeader {
        height: 1;
        min-height: 1;
        background: #141414;
        color: #888888;
        border-bottom: solid #222222;
        padding: 0 1;
    }
    """

    staged: reactive[int] = reactive(0)
    completed: reactive[int] = reactive(0)
    failed: reactive[int] = reactive(0)
    runtime_str: reactive[str] = reactive("00:00:00")
    remaining_str: reactive[str] = reactive("--:--:--")

    def render(self) -> Text:
        t = Text()
        t.append("BATCH QUEUE // ", style="dim #666666")
        t.append("STAGED: ", style="dim #777777")
        t.append(f"{self.staged}  ", style="bold #ffffff")
        t.append("COMPLETED: ", style="dim #777777")
        t.append(f"{self.completed}  ", style="bold #b0b0b0")
        t.append("FAILED: ", style="dim #777777")
        t.append(f"{self.failed}  ", style="bold #777777")
        t.append("ELAPSED: ", style="dim #777777")
        t.append(f"{self.runtime_str}  ", style="bold #d0d0d0")
        t.append("REMAINING: ", style="dim #777777")
        t.append(f"{self.remaining_str}", style="bold #d0d0d0")
        return t

    def update_stats(
        self,
        staged: int,
        completed: int,
        failed: int,
        runtime_seconds: Optional[float],
        remaining_seconds: Optional[float],
    ) -> None:
        self.staged = staged
        self.completed = completed
        self.failed = failed
        self.runtime_str = format_duration_hms(runtime_seconds) if runtime_seconds is not None else "00:00:00"
        self.remaining_str = format_duration_hms(remaining_seconds) if remaining_seconds is not None else "--:--:--"


# =============================================================================
# Queue DataTable Widget
# =============================================================================

class QueueDataTable(DataTable):
    """
    Specialized DataTable implementation displaying batch tasks with:
    - 8 columns: `#`, `Status`, `Filename`, `Format`, `Size`, `Progress`, `Speed`, `ETA`.
    - Sleek monochrome / subtle dim styling.
    - Keyboard & mouse navigation (Row selection, Delete, Move Up/Down, View Details).
    - Smooth diff-based cell refresh that preserves scroll position and cursor selection.
    """

    DEFAULT_CSS = """
    QueueDataTable {
        background: #111111;
        color: #d0d0d0;
        height: 1fr;
        border: none;
    }

    QueueDataTable > .datatable--header {
        background: #171717;
        color: #888888;
        text-style: bold;
    }

    QueueDataTable > .datatable--cursor {
        background: #262626;
        color: #ffffff;
        text-style: bold;
    }

    QueueDataTable > .datatable--hover {
        background: #1c1c1c;
    }
    """

    BINDINGS = [
        Binding("d", "delete_task", "Delete", show=True, key_display="d"),
        Binding("delete", "delete_task", "Delete", show=False),
        Binding("k", "move_task_up", "Move Up", show=True, key_display="k"),
        Binding("j", "move_task_down", "Move Down", show=True, key_display="j"),
        Binding("K", "move_task_up", "Move Up", show=False),
        Binding("J", "move_task_down", "Move Down", show=False),
        Binding("ctrl+k", "move_task_up", "Move Up", show=False),
        Binding("ctrl+j", "move_task_down", "Move Down", show=False),
        Binding("alt+k", "move_task_up", "Move Up", show=False),
        Binding("alt+j", "move_task_down", "Move Down", show=False),
        Binding("shift+up", "move_task_up", "Move Up", show=False),
        Binding("shift+down", "move_task_down", "Move Down", show=False),
        Binding("ctrl+up", "move_task_up", "Move Up", show=False),
        Binding("ctrl+down", "move_task_down", "Move Down", show=False),
        Binding("enter", "view_details", "Details", show=True, key_display="Enter"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._columns_initialized = False

        # State tracking for smooth updates
        self._current_task_ids: List[str] = []
        self._task_cache: Dict[str, ConversionTask] = {}
        self._cell_render_cache: Dict[str, Dict[str, str]] = {}
        self._queue_manager: Optional[QueueManager] = None

    def on_mount(self) -> None:
        """Configures columns on widget mount."""
        if not self._columns_initialized:
            self._setup_columns()

    def _setup_columns(self) -> None:
        """Initializes the 8 standard columns."""
        if self._columns_initialized:
            return
        for col_name, col_key in zip(COLUMN_HEADERS, COLUMN_KEYS):
            self.add_column(col_name, key=col_key)
        self._columns_initialized = True

    # -------------------------------------------------------------------------
    # Formatting Helpers
    # -------------------------------------------------------------------------

    def _format_task_cells(
        self,
        index: int,
        task: ConversionTask,
        is_queue_paused: bool = False,
    ) -> Tuple[Text, Text, Text, Text, Text, Text, Text, Text]:
        """
        Formats an individual task's 8 columns using sleek minimalist dark styling.
        """
        # 1. Number
        num_cell = Text(str(index + 1), style="dim #666666")

        # 2. Status
        status_cell = format_status_indicator(task.status, is_queue_paused)

        # 3. Filename
        fname = task.source_file.name
        trunc_fname = truncate_string(fname, max_chars=30)
        if task.status == TaskStatus.CONVERTING:
            fname_cell = Text(trunc_fname, style="bold #ffffff")
        elif task.status == TaskStatus.COMPLETED:
            fname_cell = Text(trunc_fname, style="#b8b8b8")
        elif task.status == TaskStatus.FAILED:
            fname_cell = Text(trunc_fname, style="dim #6e6e6e")
        else:
            fname_cell = Text(trunc_fname, style="#d0d0d0")

        # 4. Format
        fmt = (task.target_format or "MP3").upper()
        format_cell = Text(fmt, style="dim #a0a0a0")

        # 5. Size
        if task.output_size_bytes > 0 and task.status == TaskStatus.COMPLETED:
            size_str = format_file_size(task.output_size_bytes)
        elif task.input_size_bytes > 0:
            size_str = format_file_size(task.input_size_bytes)
        elif task.source_file.exists():
            try:
                size_str = format_file_size(task.source_file.stat().st_size)
            except OSError:
                size_str = "-"
        else:
            size_str = "-"
        size_cell = Text(size_str, style="dim #888888")

        # 6. Progress
        if task.status == TaskStatus.COMPLETED:
            prog_cell = Text("100.0%", style="#b0b0b0")
        elif task.status == TaskStatus.CONVERTING:
            prog_cell = Text(f"{task.progress:5.1f}%", style="bold #ffffff")
        elif task.status == TaskStatus.FAILED:
            prog_cell = Text(f"{task.progress:5.1f}%", style="dim #616161")
        elif task.status == TaskStatus.PROBING:
            prog_cell = Text("  0.0%", style="italic #888888")
        else:
            prog_cell = Text("  0.0%", style="dim #555555")

        # 7. Speed
        if task.status == TaskStatus.CONVERTING:
            speed_str = task.speed if task.speed else "1.0x"
            speed_cell = Text(speed_str, style="#dcdcdc")
        elif task.status == TaskStatus.COMPLETED and task.speed and task.speed != "0.0x":
            speed_cell = Text(task.speed, style="dim #888888")
        else:
            speed_cell = Text("-", style="dim #505050")

        # 8. ETA
        if task.status == TaskStatus.CONVERTING:
            eta_str = task.eta if task.eta else "--:--"
            eta_cell = Text(eta_str, style="bold #ffffff")
        elif task.status == TaskStatus.COMPLETED:
            eta_cell = Text("00:00", style="dim #666666")
        elif task.status == TaskStatus.PENDING and task.eta and task.eta != "--:--":
            eta_cell = Text(task.eta, style="dim #777777")
        else:
            eta_cell = Text("--:--", style="dim #505050")

        return (
            num_cell,
            status_cell,
            fname_cell,
            format_cell,
            size_cell,
            prog_cell,
            speed_cell,
            eta_cell,
        )

    # -------------------------------------------------------------------------
    # Smooth Refresh (In-place cell update preserving cursor & scroll)
    # -------------------------------------------------------------------------

    def smooth_refresh(
        self,
        tasks: Sequence[ConversionTask],
        is_queue_paused: bool = False,
    ) -> None:
        """
        Updates task display smoothly.
        If row identifiers and ordering match existing rows, updates cells in-place
        without resetting cursor or scroll position.
        """
        if not self._columns_initialized:
            self._setup_columns()

        new_ids = [t.task_id for t in tasks]

        # Case 1: Exact same tasks in exact same order (standard progress updates)
        if new_ids == self._current_task_ids:
            for idx, task in enumerate(tasks):
                tid = task.task_id
                self._task_cache[tid] = task
                new_cells = self._format_task_cells(idx, task, is_queue_paused)
                task_cache = self._cell_render_cache.setdefault(tid, {})

                for col_key, cell_val in zip(COLUMN_KEYS, new_cells):
                    rendered_str = cell_val.plain if hasattr(cell_val, "plain") else str(cell_val)
                    if task_cache.get(col_key) != rendered_str:
                        self.update_cell(tid, col_key, cell_val, update_width=False)
                        task_cache[col_key] = rendered_str
            return

        # Case 2: Append-only optimization (e.g. new tasks added at end)
        curr_len = len(self._current_task_ids)
        if len(new_ids) > curr_len and new_ids[:curr_len] == self._current_task_ids:
            # Update existing rows
            for idx in range(curr_len):
                task = tasks[idx]
                tid = task.task_id
                self._task_cache[tid] = task
                new_cells = self._format_task_cells(idx, task, is_queue_paused)
                task_cache = self._cell_render_cache.setdefault(tid, {})

                for col_key, cell_val in zip(COLUMN_KEYS, new_cells):
                    rendered_str = cell_val.plain if hasattr(cell_val, "plain") else str(cell_val)
                    if task_cache.get(col_key) != rendered_str:
                        self.update_cell(tid, col_key, cell_val, update_width=False)
                        task_cache[col_key] = rendered_str

            # Append new rows
            for idx in range(curr_len, len(tasks)):
                task = tasks[idx]
                tid = task.task_id
                self._task_cache[tid] = task
                new_cells = self._format_task_cells(idx, task, is_queue_paused)
                self.add_row(*new_cells, key=tid)
                self._cell_render_cache[tid] = {
                    ck: (cv.plain if hasattr(cv, "plain") else str(cv))
                    for ck, cv in zip(COLUMN_KEYS, new_cells)
                }

            self._current_task_ids = list(new_ids)
            return

        # Case 3: Reordering or arbitrary additions / removals
        saved_selected_id = self.get_selected_task_id()
        saved_cursor_row = self.cursor_row
        saved_scroll_x = self.scroll_offset.x
        saved_scroll_y = self.scroll_offset.y

        self.clear()
        self._cell_render_cache.clear()
        self._task_cache.clear()

        for idx, task in enumerate(tasks):
            tid = task.task_id
            self._task_cache[tid] = task
            cells = self._format_task_cells(idx, task, is_queue_paused)
            self.add_row(*cells, key=tid)
            self._cell_render_cache[tid] = {
                ck: (cv.plain if hasattr(cv, "plain") else str(cv))
                for ck, cv in zip(COLUMN_KEYS, cells)
            }

        self._current_task_ids = list(new_ids)

        # Restore cursor selection
        if saved_selected_id and saved_selected_id in new_ids:
            target_idx = new_ids.index(saved_selected_id)
            self.move_cursor(row=target_idx, animate=False)
        elif saved_cursor_row is not None and new_ids:
            clamped = min(saved_cursor_row, len(new_ids) - 1)
            self.move_cursor(row=clamped, animate=False)

        # Restore scroll position
        self.scroll_to(x=saved_scroll_x, y=saved_scroll_y, animate=False)

    # -------------------------------------------------------------------------
    # Selection and Actions
    # -------------------------------------------------------------------------

    def get_selected_task_id(self) -> Optional[str]:
        """Returns the task_id of the currently highlighted row."""
        try:
            row_idx = self.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self._current_task_ids):
                return self._current_task_ids[row_idx]
        except Exception:
            pass
        return None

    def get_selected_task(self) -> Optional[ConversionTask]:
        """Returns the ConversionTask of the currently highlighted row."""
        tid = self.get_selected_task_id()
        if tid:
            return self._task_cache.get(tid)
        return None

    def action_delete_task(self) -> None:
        """Deletes the currently selected task."""
        tid = self.get_selected_task_id()
        if not tid:
            return
        if isinstance(self.parent, QueueTableWidget):
            self.parent.delete_selected_task()
        else:
            self.post_message(TaskDeleted(tid, self._task_cache.get(tid)))

    def action_move_task_up(self) -> None:
        """Moves the currently selected task up in the queue."""
        tid = self.get_selected_task_id()
        if not tid:
            return
        if isinstance(self.parent, QueueTableWidget):
            self.parent.move_selected_task_up()
        else:
            self._move_task_locally(tid, "up")

    def action_move_task_down(self) -> None:
        """Moves the currently selected task down in the queue."""
        tid = self.get_selected_task_id()
        if not tid:
            return
        if isinstance(self.parent, QueueTableWidget):
            self.parent.move_selected_task_down()
        else:
            self._move_task_locally(tid, "down")

    def action_view_details(self) -> None:
        """Views details of the selected task."""
        tid = self.get_selected_task_id()
        if not tid:
            return
        task = self._task_cache.get(tid)
        if isinstance(self.parent, QueueTableWidget):
            self.parent.view_selected_task_details()
        else:
            self.post_message(TaskDetailsRequested(task, tid))
            if self.app:
                self.app.push_screen(TaskDetailModal(task))

    def _move_task_locally(self, task_id: str, direction: str) -> None:
        """Fallback local reorder if no parent QueueTableWidget attached."""
        if task_id not in self._current_task_ids:
            return
        idx = self._current_task_ids.index(task_id)
        new_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= new_idx < len(self._current_task_ids):
            tasks = [self._task_cache[tid] for tid in self._current_task_ids if tid in self._task_cache]
            tasks[idx], tasks[new_idx] = tasks[new_idx], tasks[idx]
            self.smooth_refresh(tasks)
            self.move_cursor(row=new_idx, animate=False)
            self.post_message(TaskMoved(task_id, direction, idx, new_idx))


# =============================================================================
# Main Interactive Queue Table Widget (Container)
# =============================================================================

class QueueTableWidget(Container):
    """
    Enterprise-grade Interactive Queue Management Widget for Video-to-Audio Transcoder.
    Encapsulates QueueDataTable, batch telemetry statistics footer, and optional header.

    Key Features:
    - 8 Standard columns: `#`, `Status`, `Filename`, `Format`, `Size`, `Progress`, `Speed`, `ETA`.
    - Sleek monochrome / subtle dim styling (zero gaudy colors).
    - Keyboard & mouse navigation:
      - Row selection (`cursor_type="row"`)
      - Delete (`d`, `Delete`)
      - Move task up (`k`, `K`, `ctrl+k`, `ctrl+up`, `shift+up`)
      - Move task down (`j`, `J`, `ctrl+j`, `ctrl+down`, `shift+down`)
      - Task Details (`Enter` or double click)
    - Batch statistics footer showing:
      Total staged, Completed count, Failed count, Total runtime, Estimated remaining time.
    - Diff-based smooth refresh that preserves selection and scroll position.
    """

    DEFAULT_CSS = """
    QueueTableWidget {
        layout: vertical;
        height: 100%;
        background: #111111;
        border: none;
        padding: 0;
    }

    QueueTableWidget > #queue-data-table {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("d", "delete_task", "Delete", show=True, key_display="d"),
        Binding("delete", "delete_task", "Delete", show=False),
        Binding("k", "move_task_up", "Move Up", show=True, key_display="k"),
        Binding("j", "move_task_down", "Move Down", show=True, key_display="j"),
        Binding("K", "move_task_up", "Move Up", show=False),
        Binding("J", "move_task_down", "Move Down", show=False),
        Binding("ctrl+k", "move_task_up", "Move Up", show=False),
        Binding("ctrl+j", "move_task_down", "Move Down", show=False),
        Binding("alt+k", "move_task_up", "Move Up", show=False),
        Binding("alt+j", "move_task_down", "Move Down", show=False),
        Binding("shift+up", "move_task_up", "Move Up", show=False),
        Binding("shift+down", "move_task_down", "Move Down", show=False),
        Binding("ctrl+up", "move_task_up", "Move Up", show=False),
        Binding("ctrl+down", "move_task_down", "Move Down", show=False),
        Binding("enter", "view_details", "Details", show=True, key_display="Enter"),
    ]

    def __init__(
        self,
        queue_manager: Optional[QueueManager] = None,
        show_header: bool = False,
        show_footer: bool = True,
        on_task_selected: Optional[Callable[[ConversionTask], None]] = None,
        on_task_deleted: Optional[Callable[[str], None]] = None,
        on_task_moved: Optional[Callable[[str, str], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.queue_manager = queue_manager
        self.show_header = show_header
        self.show_footer = show_footer
        self.on_task_selected = on_task_selected
        self.on_task_deleted = on_task_deleted
        self.on_task_moved = on_task_moved

        self._local_tasks: List[ConversionTask] = []
        self._start_time: float = time.monotonic()

    def compose(self) -> ComposeResult:
        if self.show_header:
            yield QueueStatsHeader(id="queue-stats-header")
        yield QueueDataTable(id="queue-data-table")
        if self.show_footer:
            yield QueueSummaryFooter(id="queue-summary-footer")

    def on_mount(self) -> None:
        """Synchronizes initial state on mount."""
        if self.queue_manager:
            self.refresh_from_manager()

    # -------------------------------------------------------------------------
    # Properties & Table Delegation
    # -------------------------------------------------------------------------

    @property
    def table(self) -> QueueDataTable:
        """Returns the underlying QueueDataTable widget."""
        return self.query_one("#queue-data-table", QueueDataTable)

    @property
    def summary_footer(self) -> Optional[QueueSummaryFooter]:
        """Returns the summary footer if composed."""
        try:
            return self.query_one("#queue-summary-footer", QueueSummaryFooter)
        except Exception:
            return None

    @property
    def stats_header(self) -> Optional[QueueStatsHeader]:
        """Returns the stats header if composed."""
        try:
            return self.query_one("#queue-stats-header", QueueStatsHeader)
        except Exception:
            return None

    @property
    def cursor_row(self) -> Optional[int]:
        """Delegates cursor_row to inner table."""
        return self.table.cursor_row

    @property
    def row_count(self) -> int:
        """Delegates row_count to inner table."""
        return self.table.row_count

    # -------------------------------------------------------------------------
    # Refresh Methods
    # -------------------------------------------------------------------------

    def update_tasks(
        self,
        tasks: Sequence[ConversionTask],
        stats: Optional[QueueStats] = None,
        is_queue_paused: bool = False,
        elapsed_seconds: Optional[float] = None,
    ) -> None:
        """
        Smoothly updates tasks and batch statistics.
        Does not reset cursor position or scroll offset.
        """
        self._local_tasks = list(tasks)
        table = self.table
        table.smooth_refresh(tasks, is_queue_paused=is_queue_paused)

        # Compute batch statistics
        staged_count = len(tasks)
        completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        failed_count = sum(1 for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.CANCELLED))

        # Runtime calculation
        if elapsed_seconds is not None:
            runtime_sec = elapsed_seconds
        elif stats:
            runtime_sec = stats.elapsed_time_seconds
        else:
            runtime_sec = time.monotonic() - self._start_time

        # Remaining time calculation
        remaining_sec = self._calculate_estimated_remaining(tasks, stats)

        # Update summary footer / header
        footer = self.summary_footer
        if footer:
            footer.update_stats(
                staged=staged_count,
                completed=completed_count,
                failed=failed_count,
                runtime_seconds=runtime_sec,
                remaining_seconds=remaining_sec,
            )

        header = self.stats_header
        if header:
            header.update_stats(
                staged=staged_count,
                completed=completed_count,
                failed=failed_count,
                runtime_seconds=runtime_sec,
                remaining_seconds=remaining_sec,
            )

    def refresh_from_manager(self) -> None:
        """Fetches the latest snapshot from QueueManager and refreshes smoothly."""
        if not self.queue_manager:
            return
        tasks = self.queue_manager.get_all_tasks()
        stats = self.queue_manager.get_stats()
        is_paused = getattr(self.queue_manager, "_is_paused", False)
        self.update_tasks(tasks, stats=stats, is_queue_paused=is_paused)

    def _calculate_estimated_remaining(
        self,
        tasks: Sequence[ConversionTask],
        stats: Optional[QueueStats],
    ) -> Optional[float]:
        """Calculates estimated remaining seconds across unfinished tasks."""
        pending_or_active = [t for t in tasks if not t.is_terminal]
        if not pending_or_active:
            return 0.0

        # Check for task ETAs
        total_remaining = 0.0
        has_valid_eta = False

        for t in pending_or_active:
            if t.eta and t.eta != "--:--":
                parts = t.eta.split(":")
                try:
                    if len(parts) == 2:
                        sec = int(parts[0]) * 60 + int(parts[1])
                        total_remaining += sec
                        has_valid_eta = True
                    elif len(parts) == 3:
                        sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        total_remaining += sec
                        has_valid_eta = True
                except ValueError:
                    pass

        if has_valid_eta:
            # Adjust for concurrency if multiple workers active
            workers = stats.max_workers if stats else 1
            return max(0.0, total_remaining / max(1, workers))

        # Fallback based on average completed duration
        completed_durations = [t.duration_seconds for t in tasks if t.status == TaskStatus.COMPLETED and t.duration_seconds > 0]
        if completed_durations:
            avg_dur = sum(completed_durations) / len(completed_durations)
            workers = stats.max_workers if stats else 1
            return (avg_dur * len(pending_or_active)) / max(1, workers)

        return None

    # -------------------------------------------------------------------------
    # Interactive Actions
    # -------------------------------------------------------------------------

    def get_selected_task_id(self) -> Optional[str]:
        """Returns the task_id of the currently selected row."""
        return self.table.get_selected_task_id()

    def get_selected_task(self) -> Optional[ConversionTask]:
        """Returns the ConversionTask of the currently selected row."""
        return self.table.get_selected_task()

    def delete_selected_task(self) -> None:
        """Deletes the highlighted task from queue."""
        tid = self.get_selected_task_id()
        if not tid:
            return

        task = self.table._task_cache.get(tid)

        if self.queue_manager:
            if hasattr(self.queue_manager, "remove_task"):
                self.queue_manager.remove_task(tid)
            else:
                self.queue_manager.cancel_task(tid)
            self.refresh_from_manager()
        else:
            self._local_tasks = [t for t in self._local_tasks if t.task_id != tid]
            self.update_tasks(self._local_tasks)

        self.post_message(TaskDeleted(tid, task))
        if self.on_task_deleted:
            self.on_task_deleted(tid)

    def move_selected_task_up(self) -> None:
        """Moves the selected task up by one position."""
        tid = self.get_selected_task_id()
        if not tid:
            return

        if self.queue_manager and hasattr(self.queue_manager, "move_task_up"):
            success = self.queue_manager.move_task_up(tid)
            if success:
                self.refresh_from_manager()
                # Ensure cursor tracks the moved item
                curr_ids = self.table._current_task_ids
                if tid in curr_ids:
                    self.table.move_cursor(row=curr_ids.index(tid), animate=False)
                self.post_message(TaskMoved(tid, "up", -1, -1))
                if self.on_task_moved:
                    self.on_task_moved(tid, "up")
        else:
            ids = [t.task_id for t in self._local_tasks]
            if tid in ids:
                idx = ids.index(tid)
                if idx > 0:
                    self._local_tasks[idx], self._local_tasks[idx - 1] = (
                        self._local_tasks[idx - 1],
                        self._local_tasks[idx],
                    )
                    self.update_tasks(self._local_tasks)
                    self.table.move_cursor(row=idx - 1, animate=False)
                    self.post_message(TaskMoved(tid, "up", idx, idx - 1))
                    if self.on_task_moved:
                        self.on_task_moved(tid, "up")

    def move_selected_task_down(self) -> None:
        """Moves the selected task down by one position."""
        tid = self.get_selected_task_id()
        if not tid:
            return

        if self.queue_manager and hasattr(self.queue_manager, "move_task_down"):
            success = self.queue_manager.move_task_down(tid)
            if success:
                self.refresh_from_manager()
                # Ensure cursor tracks the moved item
                curr_ids = self.table._current_task_ids
                if tid in curr_ids:
                    self.table.move_cursor(row=curr_ids.index(tid), animate=False)
                self.post_message(TaskMoved(tid, "down", -1, -1))
                if self.on_task_moved:
                    self.on_task_moved(tid, "down")
        else:
            ids = [t.task_id for t in self._local_tasks]
            if tid in ids:
                idx = ids.index(tid)
                if idx < len(ids) - 1:
                    self._local_tasks[idx], self._local_tasks[idx + 1] = (
                        self._local_tasks[idx + 1],
                        self._local_tasks[idx],
                    )
                    self.update_tasks(self._local_tasks)
                    self.table.move_cursor(row=idx + 1, animate=False)
                    self.post_message(TaskMoved(tid, "down", idx, idx + 1))
                    if self.on_task_moved:
                        self.on_task_moved(tid, "down")

    def view_selected_task_details(self) -> None:
        """Opens the deep telemetry inspector modal for selected task."""
        tid = self.get_selected_task_id()
        if not tid:
            return
        task = self.get_selected_task()
        self.post_message(TaskDetailsRequested(task, tid))
        if self.on_task_selected and task:
            self.on_task_selected(task)
        if self.app:
            self.app.push_screen(TaskDetailModal(task))

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Triggers task details inspection on Enter or double-click."""
        tid = str(event.row_key.value)
        task = self.table._task_cache.get(tid)
        self.post_message(TaskDetailsRequested(task, tid))
        if self.on_task_selected and task:
            self.on_task_selected(task)
        if self.app:
            self.app.push_screen(TaskDetailModal(task))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Posts TaskSelected when cursor row changes."""
        if event.row_key and event.row_key.value:
            tid = str(event.row_key.value)
            task = self.table._task_cache.get(tid)
            row_idx = self.table.cursor_row or 0
            self.post_message(TaskSelected(task, tid, row_idx))

    # Action Handlers bound to Keybindings
    def action_delete_task(self) -> None:
        self.delete_selected_task()

    def action_move_task_up(self) -> None:
        self.move_selected_task_up()

    def action_move_task_down(self) -> None:
        self.move_selected_task_down()

    def action_view_details(self) -> None:
        self.view_selected_task_details()
