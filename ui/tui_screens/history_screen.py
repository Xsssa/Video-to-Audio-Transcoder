"""
history_screen.py - Sleek Minimalist Batch Conversion History & Inspector Screen.

Provides comprehensive post-conversion telemetry and inspection:
1. History of converted files in current session.
2. For completed files: Source format, target audio format, bit rate, output duration,
   original size vs audio size (compression ratio, e.g. -88%), and EBU R128 integrated loudness (LUFS).
3. For failed files: Detailed error traceback and exact FFmpeg command line executed.
4. Export Report actions (JSON, CSV, TXT via processing.reporter.create_report_from_tasks).
5. Dismiss with Escape or 'Close' button.

Design Constraint:
- Sleek minimalist dark styling, no gaudy colors.
- Enterprise-grade telemetry visualization.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Label,
    RichLog,
    Static,
    TabPane,
)

from processing.queue_manager import ConversionTask, QueueManager, TaskStatus
from processing.reporter import (
    BatchReport,
    TaskReportItem,
    create_report_from_tasks,
    format_bytes_human,
    format_duration_human,
)


# =============================================================================
# Helper Utilities & Telemetry Extractors
# =============================================================================

def format_compression_ratio(input_bytes: int, output_bytes: int) -> Tuple[str, float]:
    """
    Computes compression ratio percentage and formatted indicator.
    Returns (ratio_str, ratio_percent).
    Example: ("-88.4%", -88.4)
    """
    if input_bytes <= 0:
        return "--", 0.0
    if output_bytes <= 0:
        return "--", 0.0

    ratio = ((output_bytes - input_bytes) / input_bytes) * 100.0
    return f"{ratio:+.1f}%", ratio


def reconstruct_ffmpeg_command(task: Any) -> list[str]:
    """
    Retrieves or accurately reconstructs the exact FFmpeg command line executed for a task.
    """
    # 1. Directly stored command list
    cmd_list = getattr(task, "ffmpeg_cmd", None)
    if cmd_list and isinstance(cmd_list, (list, tuple)):
        return [str(x) for x in cmd_list]

    # 2. Directly stored command attribute or options
    for attr in ("command", "cmd", "executed_command"):
        val = getattr(task, attr, None)
        if val:
            if isinstance(val, (list, tuple)):
                return [str(x) for x in val]
            return [str(val)]

    opts = getattr(task, "options", {}) or {}
    if "ffmpeg_cmd" in opts and isinstance(opts["ffmpeg_cmd"], (list, tuple)):
        return [str(x) for x in opts["ffmpeg_cmd"]]

    # 3. Deterministic reconstruction based on task parameters
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-progress", "pipe:1",
        "-nostats",
        "-i", str(getattr(task, "source_file", "source_media")),
        "-map", "0:a:0",
    ]

    fmt = str(getattr(task, "target_format", "mp3")).lower().lstrip(".")
    from processing.queue_manager import AUDIO_CODEC_MAP
    codec = opts.get("codec") or opts.get("audio_codec") or AUDIO_CODEC_MAP.get(fmt, "aac")
    cmd.extend(["-c:a", str(codec)])

    bitrate = opts.get("bitrate")
    if bitrate:
        b_str = str(bitrate)
        if b_str.isdigit():
            b_int = int(b_str)
            b_str = f"{b_int // 1000}k" if b_int > 1000 else f"{b_int}k"
        cmd.extend(["-b:a", b_str])

    if opts.get("sample_rate"):
        cmd.extend(["-ar", str(opts["sample_rate"])])
    if opts.get("channels"):
        cmd.extend(["-ac", str(opts["channels"])])

    # Loudnorm filter
    if opts.get("ebu_r128") or opts.get("loudnorm"):
        target_i = opts.get("target_i", -16.0)
        target_tp = opts.get("target_tp", -1.5)
        target_lra = opts.get("target_lra", 11.0)
        cmd.extend(["-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"])

    cmd.extend(["-map_metadata", "0", "-vn"])

    out_file = getattr(task, "output_file", None)
    if out_file:
        cmd.append(str(out_file))
    else:
        src = getattr(task, "source_file", "output")
        cmd.append(f"{Path(str(src)).stem}.{fmt}")

    return cmd


def format_command_line(cmd_tokens: Sequence[str]) -> str:
    """Formats argument list into a single cross-platform shell command line."""
    if not cmd_tokens:
        return "N/A"
    return subprocess.list2cmdline([str(t) for t in cmd_tokens])


def extract_task_traceback(task: Any) -> str:
    """Extracts detailed error traceback or exception description for a failed task."""
    for attr in ("traceback", "error_traceback", "stack_trace"):
        tb = getattr(task, attr, None)
        if tb and str(tb).strip():
            return str(tb).strip()

    err = getattr(task, "error", None) or getattr(task, "error_message", None)
    if err and str(err).strip():
        return str(err).strip()

    ver = getattr(task, "verification_result", None)
    if ver and hasattr(ver, "errors") and ver.errors:
        return "\n".join(ver.errors)

    return "No traceback recorded."


def extract_loudness_info(task: Any) -> Tuple[str, str]:
    """
    Extracts EBU R128 integrated loudness metrics.
    Returns (summary_badge, detail_description).
    Example: ("-16.0 LUFS", "Integrated: -16.0 LUFS | True Peak: -1.5 dBFS | Target: EBU R128 Normalized")
    """
    opts = getattr(task, "options", {}) or {}

    # Check for direct measurements
    meas = getattr(task, "loudness_measurement", None) or opts.get("loudness_measurement")
    if meas:
        i = getattr(meas, "input_i", getattr(meas, "output_i", None))
        tp = getattr(meas, "input_tp", getattr(meas, "output_tp", None))
        lra = getattr(meas, "input_lra", getattr(meas, "output_lra", None))
        if i is not None:
            sum_str = f"{float(i):.1f} LUFS"
            det_str = f"Integrated: {float(i):.1f} LUFS | TP: {float(tp):.1f} dBFS | LRA: {float(lra):.1f} LU (Measured)"
            return sum_str, det_str

    # Check if EBU R128 normalization was activated
    if opts.get("ebu_r128") or opts.get("loudnorm"):
        target_i = float(opts.get("target_i", -16.0))
        target_tp = float(opts.get("target_tp", -1.5))
        target_lra = float(opts.get("target_lra", 11.0))
        sum_str = f"{target_i:.1f} LUFS"
        det_str = (
            f"Integrated Target: {target_i:.1f} LUFS | Max Peak: {target_tp:.1f} dBFS | "
            f"LRA: {target_lra:.1f} LU (EBU R128 Active)"
        )
        return sum_str, det_str

    # Check verification details
    ver = getattr(task, "verification_result", None)
    if ver and hasattr(ver, "details") and ver.details:
        lufs = ver.details.get("loudness_lufs") or ver.details.get("integrated_loudness")
        if lufs is not None:
            return f"{float(lufs):.1f} LUFS", f"Integrated: {float(lufs):.1f} LUFS (Verified)"

    return "N/A", "Standard Audio Profile (EBU R128 Normalization Not Requested)"


def extract_compression_details(task: Any) -> Dict[str, Any]:
    """Computes file sizes and compression metrics."""
    in_size = getattr(task, "input_size_bytes", 0)
    if in_size == 0 and hasattr(task, "source_file"):
        try:
            p = Path(task.source_file)
            if p.exists():
                in_size = p.stat().st_size
        except OSError:
            in_size = 0

    out_size = getattr(task, "output_size_bytes", 0)
    if out_size == 0 and getattr(task, "output_file", None):
        try:
            p = Path(task.output_file)
            if p.exists():
                out_size = p.stat().st_size
        except OSError:
            out_size = 0

    ratio_str, ratio_val = format_compression_ratio(in_size, out_size)
    space_saved = in_size - out_size

    return {
        "input_bytes": in_size,
        "output_bytes": out_size,
        "input_human": format_bytes_human(in_size),
        "output_human": format_bytes_human(out_size),
        "space_saved_bytes": space_saved,
        "space_saved_human": format_bytes_human(space_saved),
        "ratio_str": ratio_str,
        "ratio_percent": ratio_val,
    }


def extract_task_format_specs(task: Any) -> Dict[str, Any]:
    """Extracts container formats, audio specs, bitrate, and duration."""
    src_fmt = "MEDIA"
    probe = getattr(task, "probe_result", None)
    if probe:
        if probe.format_name:
            src_fmt = probe.format_name.split(",")[0].upper()
        if probe.primary_audio_stream:
            src_fmt = f"{src_fmt} ({probe.primary_audio_stream.codec_name})"
    else:
        src_path = getattr(task, "source_file", None)
        if src_path:
            ext = Path(src_path).suffix.lstrip(".").upper()
            src_fmt = ext or "MEDIA"

    tgt_fmt = str(getattr(task, "target_format", "MP3")).upper().lstrip(".")

    ver = getattr(task, "verification_result", None)
    bitrate_str = "Auto"
    sample_rate_str = "Default"
    channels_str = "Default"
    actual_codec = "auto"

    if ver:
        actual_codec = ver.codec or actual_codec
        if ver.bitrate:
            bitrate_str = f"{int(round(ver.bitrate / 1000))} kbps"
        if ver.sample_rate:
            sample_rate_str = f"{ver.sample_rate} Hz"
        if ver.channels:
            ch_map = {1: "Mono (1ch)", 2: "Stereo (2ch)", 6: "5.1 Surround"}
            channels_str = ch_map.get(ver.channels, f"{ver.channels}ch")
    else:
        opts = getattr(task, "options", {}) or {}
        if opts.get("bitrate"):
            b = str(opts["bitrate"])
            if b.isdigit():
                bitrate_str = f"{int(b) // 1000} kbps"
            elif not b.endswith("bps") and not b.endswith("k"):
                bitrate_str = f"{b}k"
            else:
                bitrate_str = b
        elif probe and probe.primary_audio_stream and probe.primary_audio_stream.bit_rate:
            bitrate_str = f"{int(probe.primary_audio_stream.bit_rate / 1000)} kbps"

        if opts.get("sample_rate"):
            sample_rate_str = f"{opts['sample_rate']} Hz"
        if opts.get("channels"):
            channels_str = f"{opts['channels']}ch"

    dur_sec = 0.0
    if ver and ver.duration > 0:
        dur_sec = ver.duration
    elif probe and probe.duration > 0:
        dur_sec = probe.duration
    elif getattr(task, "duration_seconds", 0) > 0:
        dur_sec = getattr(task, "duration_seconds", 0)

    dur_str = format_duration_human(dur_sec)

    return {
        "source_format": src_fmt,
        "target_format": tgt_fmt,
        "bitrate": bitrate_str,
        "sample_rate": sample_rate_str,
        "channels": channels_str,
        "codec": actual_codec,
        "duration_seconds": dur_sec,
        "duration_str": dur_str,
    }


# =============================================================================
# Custom Textual Widgets & Inspector View
# =============================================================================

class HistoryInspectorWidget(Container):
    """
    Renders detailed inspection telemetry for a selected task.
    Displays comprehensive metrics for completed tasks and deep diagnostic
    tracebacks + exact FFmpeg command lines for failed tasks.
    """

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="inspector-scroll-area"):
            yield Static(id="inspector-content", markup=True)

    def set_empty_state(self, message: str = "Select a task from the history table to inspect.") -> None:
        """Displays neutral empty state placeholder."""
        static = self.query_one("#inspector-content", Static)
        render_text = (
            f"\n\n"
            f"[dim #555d6e]────────────────────────────────────────────────────────────────[/]\n"
            f"  [dim #7a8292]{escape(message)}[/]\n"
            f"[dim #555d6e]────────────────────────────────────────────────────────────────[/]\n"
        )
        static.update(render_text)

    def display_task(self, task: Any) -> None:
        """Formats and renders complete telemetry for the specified task."""
        static = self.query_one("#inspector-content", Static)

        status_str = task.status.value if hasattr(task.status, "value") else str(task.status)
        status_upper = status_str.upper()

        src_path = str(getattr(task, "source_file", "Unknown"))
        src_name = Path(src_path).name
        out_path = str(getattr(task, "output_file", "")) or "Not generated"

        comp_info = extract_compression_details(task)
        fmt_info = extract_task_format_specs(task)
        lufs_sum, lufs_det = extract_loudness_info(task)

        # Subtle dark status badges
        if status_upper == "COMPLETED":
            status_badge = "[#70a27f on #18261e] COMPLETED [/]"
            status_color = "#70a27f"
        elif status_upper == "FAILED":
            status_badge = "[#d06e6e on #2e1717] FAILED [/]"
            status_color = "#d06e6e"
        elif status_upper == "CANCELLED":
            status_badge = "[#b8985c on #262118] CANCELLED [/]"
            status_color = "#b8985c"
        else:
            status_badge = f"[#8a92a2 on #1e222a] {status_upper} [/]"
            status_color = "#8a92a2"

        lines: list[str] = []

        # Card Title Header
        lines.append(f"{status_badge}  [bold #e2e6ed]{escape(src_name)}[/]")
        lines.append(f"[dim #3a4150]Task ID: {task.task_id}  |  Updated: {datetime.datetime.now().strftime('%H:%M:%S')}[/]")
        lines.append("[dim #2a313d]────────────────────────────────────────────────────────────────────────────[/]")

        if status_upper == "COMPLETED":
            # Completed File Inspection
            lines.append(f"[bold #8a93a4]► AUDIO CONVERSION TELEMETRY[/]")
            lines.append(f"  [dim #6e7687]Source File:[/]      [#c8ccd6]{escape(src_path)}[/]")
            lines.append(f"  [dim #6e7687]Output File:[/]      [#c8ccd6]{escape(out_path)}[/]")
            lines.append("")

            lines.append(f"[bold #8a93a4]► FORMAT & CODEC SPECIFICATIONS[/]")
            lines.append(f"  [dim #6e7687]Source Format:[/]    [#c8ccd6]{fmt_info['source_format']}[/]")
            lines.append(f"  [dim #6e7687]Target Format:[/]    [#c8ccd6]{fmt_info['target_format']}[/]")
            lines.append(f"  [dim #6e7687]Audio Codec:[/]      [#c8ccd6]{fmt_info['codec']}[/]")
            lines.append(f"  [dim #6e7687]Bit Rate:[/]         [#c8ccd6]{fmt_info['bitrate']}[/]")
            lines.append(f"  [dim #6e7687]Sample Rate / Ch:[/] [#c8ccd6]{fmt_info['sample_rate']} | {fmt_info['channels']}[/]")
            lines.append("")

            lines.append(f"[bold #8a93a4]► OUTPUT DURATION & COMPRESSION[/]")
            lines.append(f"  [dim #6e7687]Output Duration:[/]  [#c8ccd6]{fmt_info['duration_str']}[/] ({fmt_info['duration_seconds']:.2f}s)")
            lines.append(f"  [dim #6e7687]Original Size:[/]    [#c8ccd6]{comp_info['input_human']}[/]")
            lines.append(f"  [dim #6e7687]Audio Size:[/]       [#c8ccd6]{comp_info['output_human']}[/]")

            ratio_disp = comp_info['ratio_str']
            if comp_info['ratio_percent'] < 0:
                ratio_badge = f"[#70a27f]{ratio_disp}[/]"
            else:
                ratio_badge = f"[#c8ccd6]{ratio_disp}[/]"
            lines.append(f"  [dim #6e7687]Compression Ratio:[/] {ratio_badge} (Saved {comp_info['space_saved_human']})")
            lines.append("")

            lines.append(f"[bold #8a93a4]► EBU R128 LOUDNESS METRICS[/]")
            lines.append(f"  [dim #6e7687]Integrated Loudness:[/] [bold #c8ccd6]{lufs_sum}[/]")
            lines.append(f"  [dim #6e7687]Analysis Detail:[/]     [#989fae]{escape(lufs_det)}[/]")
            lines.append("")

            # Speed and Verification
            speed_val = getattr(task, "speed", "1.0x")
            wall_time = getattr(task, "duration_seconds", 0.0)
            lines.append(f"[bold #8a93a4]► EXECUTION TELEMETRY[/]")
            lines.append(f"  [dim #6e7687]Transcode Speed:[/]  [#c8ccd6]{speed_val}[/]")
            lines.append(f"  [dim #6e7687]Processing Time:[/]  [#c8ccd6]{wall_time:.2f}s[/]")

            ver = getattr(task, "verification_result", None)
            if ver:
                ver_status = ver.status_label
                v_color = "#70a27f" if ver.is_valid else "#d06e6e"
                lines.append(f"  [dim #6e7687]Integrity Check:[/]  [{v_color}]{ver_status}[/]")
                if ver.warnings:
                    lines.append(f"  [dim #b8985c]Warnings:[/]          {escape('; '.join(ver.warnings))}")

        elif status_upper == "FAILED":
            # Failed File Inspection
            err_msg = getattr(task, "error", None) or "Unknown error"
            tb_str = extract_task_traceback(task)
            cmd_tokens = reconstruct_ffmpeg_command(task)
            full_cmd = format_command_line(cmd_tokens)

            lines.append(f"[bold #d06e6e]► FAILURE DIAGNOSTIC OVERVIEW[/]")
            lines.append(f"  [dim #6e7687]Source File:[/]      [#c8ccd6]{escape(src_path)}[/]")
            lines.append(f"  [dim #6e7687]Target Format:[/]    [#c8ccd6]{fmt_info['target_format']}[/]")
            lines.append(f"  [dim #6e7687]Error Summary:[/]    [#d06e6e]{escape(str(err_msg))}[/]")
            lines.append("")

            lines.append(f"[bold #8a93a4]► EXACT FFMPEG COMMAND EXECUTED[/]")
            lines.append("[dim #2a313d]┌──────────────────────────────────────────────────────────────────────────┐[/]")
            lines.append(f"[#a6adb9]{escape(full_cmd)}[/]")
            lines.append("[dim #2a313d]└──────────────────────────────────────────────────────────────────────────┘[/]")
            lines.append("")

            lines.append(f"[bold #8a93a4]► DETAILED ERROR TRACEBACK[/]")
            lines.append("[dim #2a313d]┌──────────────────────────────────────────────────────────────────────────┐[/]")
            # Format traceback with muted red-gray
            tb_lines = tb_str.splitlines()
            for tl in tb_lines:
                lines.append(f"[#c47878]{escape(tl)}[/]")
            lines.append("[dim #2a313d]└──────────────────────────────────────────────────────────────────────────┘[/]")

        else:
            # Cancelled or Pending
            lines.append(f"[bold #8a93a4]► TASK STATE INFORMATION[/]")
            lines.append(f"  [dim #6e7687]Source File:[/]      [#c8ccd6]{escape(src_path)}[/]")
            lines.append(f"  [dim #6e7687]Target Format:[/]    [#c8ccd6]{fmt_info['target_format']}[/]")
            lines.append(f"  [dim #6e7687]Input Size:[/]       [#c8ccd6]{comp_info['input_human']}[/]")
            if task.error:
                lines.append(f"  [dim #6e7687]Reason:[/]           [#b8985c]{escape(str(task.error))}[/]")
            lines.append(f"  [dim #6e7687]Progress:[/]         [#c8ccd6]{task.progress:.1f}%[/]")

        static.update("\n".join(lines))


# =============================================================================
# Core History View (Embeddable in Screens or Tabs)
# =============================================================================

class HistoryView(Container):
    """
    Core History and Inspector widget layout.
    Can be used standalone in a screen, inside tabs, or embedded in dashboards.
    """

    def __init__(
        self,
        tasks: Optional[Sequence[Any]] = None,
        queue_manager: Optional[QueueManager] = None,
        on_export_callback: Optional[Any] = None,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.queue_manager = queue_manager
        self._raw_tasks = list(tasks) if tasks is not None else []
        self._filter_mode = "ALL"  # "ALL", "COMPLETED", "FAILED"
        self._selected_task_id: Optional[str] = None
        self._on_export_callback = on_export_callback
        self._last_exported_path: Optional[Path] = None

    def compose(self) -> ComposeResult:
        # Header Status & Summary Metrics
        with Horizontal(id="history-stats-bar"):
            yield Label("[dim #7a8292]Total:[/] [bold #e2e6ed]0[/]", id="stat-total")
            yield Label("[dim #7a8292]Completed:[/] [#70a27f]0[/]", id="stat-completed")
            yield Label("[dim #7a8292]Failed:[/] [#d06e6e]0[/]", id="stat-failed")
            yield Label("[dim #7a8292]Saved:[/] [#70a27f]0 B[/]", id="stat-saved")
            yield Label("[dim #7a8292]Wall Time:[/] [#c8ccd6]00:00[/]", id="stat-time")

        # Filter & Action Control Strip
        with Horizontal(id="history-controls-bar"):
            with Horizontal(id="filter-buttons"):
                yield Button("All", id="btn-filter-all", classes="filter-btn active")
                yield Button("Completed", id="btn-filter-completed", classes="filter-btn")
                yield Button("Failed", id="btn-filter-failed", classes="filter-btn")
            with Horizontal(id="export-buttons"):
                yield Button("Export JSON", id="btn-export-json", classes="action-btn")
                yield Button("Export CSV", id="btn-export-csv", classes="action-btn")
                yield Button("Export TXT", id="btn-export-txt", classes="action-btn")

        # Notification / Status Toast
        yield Label("", id="history-notification", classes="hidden")

        # Main Split Workspace
        with Horizontal(id="history-main-split"):
            with Container(id="table-pane"):
                yield DataTable(id="history-data-table")
            with Container(id="inspector-pane"):
                yield HistoryInspectorWidget(id="task-inspector")

    def on_mount(self) -> None:
        """Configures data table and loads initial tasks."""
        table = self.query_one("#history-data-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True

        # Columns: #, Status, Source File, Target, Duration, Bitrate, Compression, LUFS
        table.add_column("#", key="idx", width=4)
        table.add_column("Status", key="status", width=12)
        table.add_column("Source File", key="source", width=22)
        table.add_column("Target", key="target", width=7)
        table.add_column("Bitrate", key="bitrate", width=9)
        table.add_column("Duration", key="duration", width=9)
        table.add_column("Ratio", key="ratio", width=9)
        table.add_column("LUFS", key="lufs", width=10)

        self.refresh_tasks()

    def set_tasks(self, tasks: Sequence[Any]) -> None:
        """Updates internal task collection and refreshes table."""
        self._raw_tasks = list(tasks)
        self.refresh_tasks()

    def get_current_tasks(self) -> list[Any]:
        """Resolves active tasks from input or connected QueueManager."""
        if self.queue_manager:
            return self.queue_manager.get_all_tasks()
        if self._raw_tasks:
            return self._raw_tasks
        if hasattr(self.app, "queue_manager") and self.app.queue_manager:
            return self.app.queue_manager.get_all_tasks()
        return []

    def refresh_tasks(self) -> None:
        """Refreshes summary statistics and populates the data table according to filter."""
        tasks = self.get_current_tasks()
        table = self.query_one("#history-data-table", DataTable)
        inspector = self.query_one("#task-inspector", HistoryInspectorWidget)

        # Update aggregated metrics
        total = len(tasks)
        completed = sum(1 for t in tasks if str(t.status).upper().endswith("COMPLETED"))
        failed = sum(1 for t in tasks if str(t.status).upper().endswith("FAILED"))
        cancelled = sum(1 for t in tasks if str(t.status).upper().endswith("CANCELLED"))

        total_in = sum(getattr(t, "input_size_bytes", 0) for t in tasks)
        total_out = sum(getattr(t, "output_size_bytes", 0) for t in tasks)
        saved = max(0, total_in - total_out)

        wall_time = sum(getattr(t, "duration_seconds", 0.0) for t in tasks)

        self.query_one("#stat-total", Label).update(f"[dim #7a8292]Total:[/] [bold #e2e6ed]{total}[/]")
        self.query_one("#stat-completed", Label).update(f"[dim #7a8292]Completed:[/] [#70a27f]{completed}[/]")
        self.query_one("#stat-failed", Label).update(f"[dim #7a8292]Failed:[/] [#d06e6e]{failed}[/]")
        self.query_one("#stat-saved", Label).update(f"[dim #7a8292]Saved:[/] [#70a27f]{format_bytes_human(saved)}[/]")
        self.query_one("#stat-time", Label).update(f"[dim #7a8292]Wall Time:[/] [#c8ccd6]{format_duration_human(wall_time)}[/]")

        # Apply filtering
        filtered: list[Any] = []
        for t in tasks:
            st = str(t.status).upper()
            if self._filter_mode == "COMPLETED" and not st.endswith("COMPLETED"):
                continue
            if self._filter_mode == "FAILED" and not st.endswith("FAILED"):
                continue
            filtered.append(t)

        table.clear()

        task_map: dict[str, Any] = {}
        for idx, task in enumerate(filtered, start=1):
            tid = str(getattr(task, "task_id", f"task_{idx}"))
            task_map[tid] = task

            st = str(task.status).upper()
            if st.endswith("COMPLETED"):
                st_markup = "[#70a27f]PASS[/]"
            elif st.endswith("FAILED"):
                st_markup = "[#d06e6e]FAIL[/]"
            elif st.endswith("CANCELLED"):
                st_markup = "[#b8985c]CANCEL[/]"
            else:
                st_markup = f"[#8a92a2]{st[:6]}[/]"

            src_name = Path(str(getattr(task, "source_file", ""))).name or tid
            if len(src_name) > 20:
                src_name = src_name[:17] + "..."

            fmt_info = extract_task_format_specs(task)
            comp_info = extract_compression_details(task)
            lufs_sum, _ = extract_loudness_info(task)

            table.add_row(
                str(idx),
                Text.from_markup(st_markup),
                src_name,
                fmt_info["target_format"],
                fmt_info["bitrate"],
                fmt_info["duration_str"],
                comp_info["ratio_str"],
                lufs_sum,
                key=tid,
            )

        # Retain selection or select first entry
        self._task_map = task_map
        if filtered:
            first_id = str(getattr(filtered[0], "task_id", "task_1"))
            target_id = self._selected_task_id if (self._selected_task_id in task_map) else first_id
            self._selected_task_id = target_id
            target_task = task_map.get(target_id)
            if target_task:
                inspector.display_task(target_task)
        else:
            inspector.set_empty_state(f"No {self._filter_mode.lower()} tasks recorded in session history.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Updates inspector when user navigates rows with arrow keys."""
        if not event.row_key or not event.row_key.value:
            return
        tid = str(event.row_key.value)
        self._selected_task_id = tid
        task = getattr(self, "_task_map", {}).get(tid)
        if task:
            self.query_one("#task-inspector", HistoryInspectorWidget).display_task(task)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Updates inspector when row is clicked or activated with Enter."""
        if not event.row_key or not event.row_key.value:
            return
        tid = str(event.row_key.value)
        self._selected_task_id = tid
        task = getattr(self, "_task_map", {}).get(tid)
        if task:
            self.query_one("#task-inspector", HistoryInspectorWidget).display_task(task)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handles filter selection and export actions."""
        bid = event.button.id
        if bid == "btn-filter-all":
            self._set_filter("ALL", event.button)
        elif bid == "btn-filter-completed":
            self._set_filter("COMPLETED", event.button)
        elif bid == "btn-filter-failed":
            self._set_filter("FAILED", event.button)
        elif bid == "btn-export-json":
            self.export_report("json")
        elif bid == "btn-export-csv":
            self.export_report("csv")
        elif bid == "btn-export-txt":
            self.export_report("txt")

    def _set_filter(self, mode: str, button: Button) -> None:
        self._filter_mode = mode
        for b in self.query(".filter-btn"):
            b.remove_class("active")
        button.add_class("active")
        self.refresh_tasks()

    def show_notification(self, message: str, is_error: bool = False) -> None:
        """Displays temporary unobtrusive notification banner."""
        label = self.query_one("#history-notification", Label)
        color = "#d06e6e" if is_error else "#70a27f"
        label.update(f"[{color}]{message}[/]")
        label.remove_class("hidden")
        self.set_timer(5.0, lambda: label.add_class("hidden"))

    def export_report(self, format_type: str = "json") -> Optional[Path]:
        """
        Executes export via processing.reporter.create_report_from_tasks.
        Saves report as .json, .csv, or .txt.
        """
        tasks = self.get_current_tasks()
        if not tasks:
            self.show_notification("Cannot export report: No conversion tasks recorded in session.", is_error=True)
            return None

        try:
            wall_time = sum(getattr(t, "duration_seconds", 0.0) for t in tasks)
            report = create_report_from_tasks(tasks, wall_time_seconds=wall_time)

            fmt = format_type.lower()
            if fmt == "json":
                dest = report.export_json()
            elif fmt == "csv":
                dest = report.export_csv()
            elif fmt == "txt":
                dest = report.export_txt()
            else:
                dest = report.export_json()

            self._last_exported_path = dest
            msg = f"Report saved successfully: {dest.name} in exports/"
            self.show_notification(msg)

            if callable(self._on_export_callback):
                self._on_export_callback(dest)

            return dest
        except Exception as exc:
            self.show_notification(f"Export failed: {exc}", is_error=True)
            return None


# =============================================================================
# Modal Screen Implementation
# =============================================================================

class HistoryModalScreen(ModalScreen[Optional[Path]]):
    """
    Sleek Minimalist ModalScreen for inspecting session conversion history.

    Features:
    - History table with row inspection.
    - Full telemetry: source, format, bitrate, duration, compression ratio (-88%), LUFS.
    - Failure inspector: detailed traceback & exact FFmpeg command line.
    - Export Report (JSON, CSV, TXT).
    - Dismiss with Escape or Close button.
    """

    CSS = """
    HistoryModalScreen {
        align: center middle;
        background: rgba(10, 12, 16, 0.88);
    }

    #history-modal-dialog {
        width: 95%;
        height: 94%;
        background: #14171d;
        border: solid #2a313d;
        layout: vertical;
        padding: 0;
    }

    #history-modal-header {
        height: 3;
        background: #181c23;
        border-bottom: solid #262c37;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #modal-title {
        width: 1fr;
        text-style: bold;
        color: #e2e6ed;
    }

    #btn-close-modal {
        min-width: 10;
        height: 3;
        background: #20252e;
        color: #cfd4de;
        border: tall #2e3543;
    }

    #btn-close-modal:hover {
        background: #2a313d;
        color: #ffffff;
        border: tall #3e4758;
    }

    HistoryView {
        height: 1fr;
        layout: vertical;
        background: #14171d;
    }

    #history-stats-bar {
        height: 3;
        background: #161920;
        border-bottom: solid #222731;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #history-stats-bar Label {
        margin-right: 3;
    }

    #history-controls-bar {
        height: 3;
        background: #191d24;
        border-bottom: solid #222731;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #filter-buttons {
        width: 1fr;
        layout: horizontal;
    }

    #export-buttons {
        layout: horizontal;
    }

    Button {
        height: 3;
        min-width: 9;
        margin-right: 1;
        background: #1e232b;
        color: #aeb4bf;
        border: tall #2a313d;
    }

    Button:hover {
        background: #272e39;
        color: #ffffff;
        border: tall #384252;
    }

    Button.active {
        background: #273142;
        color: #e2e6ed;
        border: tall #3f4e66;
    }

    #history-notification {
        height: 2;
        background: #171b22;
        padding: 0 1;
        text-style: italic;
    }

    .hidden {
        display: none;
    }

    #history-main-split {
        height: 1fr;
        layout: horizontal;
    }

    #table-pane {
        width: 53%;
        height: 100%;
        border-right: solid #222731;
        background: #13151b;
    }

    #inspector-pane {
        width: 47%;
        height: 100%;
        background: #14171d;
    }

    DataTable {
        background: #13151b;
        border: none;
        height: 100%;
    }

    DataTable > .datatable--header {
        background: #181c23;
        color: #8a93a3;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #232a35;
        color: #ffffff;
    }

    #inspector-scroll-area {
        height: 100%;
        padding: 1 2;
    }

    #inspector-content {
        color: #c8ccd6;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=True),
        Binding("q", "dismiss_modal", "Close", show=False),
        Binding("r", "refresh_view", "Refresh", show=True),
        Binding("j", "export_json", "Export JSON", show=False),
        Binding("c", "export_csv", "Export CSV", show=False),
        Binding("t", "export_txt", "Export TXT", show=False),
    ]

    def __init__(
        self,
        tasks: Optional[Sequence[Any]] = None,
        queue_manager: Optional[QueueManager] = None,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.initial_tasks = tasks
        self.queue_manager = queue_manager

    def compose(self) -> ComposeResult:
        with Container(id="history-modal-dialog"):
            with Horizontal(id="history-modal-header"):
                yield Label("SESSION CONVERSION HISTORY & INSPECTOR", id="modal-title")
                yield Button("Close (Esc)", id="btn-close-modal")

            yield HistoryView(
                tasks=self.initial_tasks,
                queue_manager=self.queue_manager,
                id="history-view-inner",
            )

    def action_dismiss_modal(self) -> None:
        """Dismisses the modal screen."""
        view = self.query_one("#history-view-inner", HistoryView)
        self.dismiss(view._last_exported_path)

    def action_refresh_view(self) -> None:
        """Refreshes tasks from queue manager."""
        self.query_one("#history-view-inner", HistoryView).refresh_tasks()

    def action_export_json(self) -> None:
        self.query_one("#history-view-inner", HistoryView).export_report("json")

    def action_export_csv(self) -> None:
        self.query_one("#history-view-inner", HistoryView).export_report("csv")

    def action_export_txt(self) -> None:
        self.query_one("#history-view-inner", HistoryView).export_report("txt")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-modal":
            self.action_dismiss_modal()


# =============================================================================
# Tab Pane Integration Wrapper
# =============================================================================

class HistoryTabPane(TabPane):
    """
    Embeddable TabPane for integration into TabbedContent dashboards.
    """

    def __init__(
        self,
        title: str = "History & Inspector",
        id: str = "tab-history",
        tasks: Optional[Sequence[Any]] = None,
        queue_manager: Optional[QueueManager] = None,
    ) -> None:
        super().__init__(title=title, id=id)
        self._tasks = tasks
        self._queue_manager = queue_manager

    def compose(self) -> ComposeResult:
        yield HistoryView(
            tasks=self._tasks,
            queue_manager=self._queue_manager,
            id="tab-history-view",
        )

    def refresh_history(self) -> None:
        """Refreshes the inner history view."""
        self.query_one("#tab-history-view", HistoryView).refresh_tasks()
