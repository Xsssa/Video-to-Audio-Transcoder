"""
dashboard_layout.py - Advanced Terminal Dashboard Layout & Visual Telemetry for Video-to-Audio Transcoder.

Features:
- TerminalDashboardLayout class using rich.layout.Layout and rich.panel.Panel
- Header Panel: Application branding, version, CPU cores per-core usage bar, RAM bar, active worker threads, and FFmpeg binary status
- Main Split Area:
  - Left Panel (Queue View): Table of tasks with status badges ([PENDING] yellow, [PROBING] cyan, [CONVERTING] blue, [COMPLETED] green, [FAILED] red),
    filename, target format, mini progress bar, speed factor, ETA
  - Right Panel (Live Monitor & Inspector):
    - Upper: ASCII VU Meter & 7-band dynamic dancing spectrum visualizer
    - Lower: Media Inspector card (video resolution, duration, audio stream details, codec, sample rate, channels, cover art)
- Bottom Split Area:
  - Left: Scrolling Event Log buffer (last N events with timestamps and color-coded tags: [PROBE], [TRANSCODE], [VERIFY], [LOG])
  - Right: Hotkey & Shortcut Legend bar:
    [A] Add Files | [V] Paste | [P] Presets | [F] Filter Wizard | [I] Inspect | [S] Start/Pause | [C] Clear | [R] Reports | [Q] Quit
- Thread-safe function render_dashboard(state: DashboardState) -> Layout
- Backward compatibility with VUMeter, HardwareTelemetry, EventLogStream, and DashboardLayout
"""

from __future__ import annotations

import math
import os
import random
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import psutil
from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.ffmpeg_finder import find_ffmpeg, get_ffmpeg_version
from processing.reporter import format_bytes_human, format_duration_human
from ui.ascii_visualizer import AsciiVisualizer, is_unicode_supported

# Attempt importing queue / probe models if available
try:
    from processing.queue_manager import ConversionTask, TaskStatus
except ImportError:
    ConversionTask = Any  # type: ignore
    TaskStatus = Any  # type: ignore

try:
    from core.probe import MediaProbeResult
except ImportError:
    MediaProbeResult = Any  # type: ignore


# =============================================================================
# Telemetry and State Data Models for Enterprise Terminal UI
# =============================================================================

@dataclass
class DashboardEvent:
    """Represents a discrete lifecycle or diagnostic event in the transcoder."""
    timestamp: float = field(default_factory=time.time)
    tag: str = "LOG"  # "PROBE", "TRANSCODE", "VERIFY", "LOG", "ERROR", "WARN"
    message: str = ""
    task_id: Optional[str] = None

    def formatted_time(self) -> str:
        """Returns HH:MM:SS string."""
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


@dataclass
class MediaInspectorData:
    """Detailed stream telemetry for selected or currently converting media file."""
    filename: str = "No media selected"
    file_path: Optional[Path] = None
    container_format: str = "--"
    file_size_str: str = "--"
    duration_str: str = "--"
    video_resolution: str = "--"
    video_codec: str = "--"
    video_fps_str: str = "--"
    audio_codec: str = "--"
    audio_sample_rate: str = "--"
    audio_channels: str = "--"
    audio_bit_rate: str = "--"
    audio_title: str = "--"
    has_cover_art: bool = False
    cover_art_details: str = "None detected"
    target_preset: str = "--"
    target_format: str = "--"

    @classmethod
    def from_task(cls, task: Any) -> MediaInspectorData:
        """Constructs inspector telemetry from a ConversionTask or similar object."""
        if not task:
            return cls()

        source_file = getattr(task, "source_file", None)
        filename = source_file.name if isinstance(source_file, Path) else str(source_file or "Unknown")

        # Container & File size
        container = source_file.suffix.lstrip(".").upper() if isinstance(source_file, Path) else "--"
        file_size_str = "--"
        if isinstance(source_file, Path) and source_file.exists():
            try:
                sz_mb = source_file.stat().st_size / (1024 * 1024)
                file_size_str = f"{sz_mb:.1f} MB"
            except Exception:
                pass

        # Target & Options
        target_fmt = str(getattr(task, "target_format", "--")).upper()
        options = getattr(task, "options", {}) or {}
        opt_parts = []
        if "bitrate" in options:
            opt_parts.append(str(options["bitrate"]))
        if options.get("ebu_r128"):
            opt_parts.append("EBU R128")
        if options.get("lossless_copy_if_match"):
            opt_parts.append("Lossless Copy")
        preset_summary = f"{target_fmt} ({', '.join(opt_parts)})" if opt_parts else target_fmt

        data = cls(
            filename=filename,
            file_path=source_file if isinstance(source_file, Path) else None,
            container_format=container,
            file_size_str=file_size_str,
            target_format=target_fmt,
            target_preset=preset_summary,
        )

        # Inspect probe_result if available
        probe: Optional[MediaProbeResult] = getattr(task, "probe_result", None)
        if probe:
            # Duration
            dur = getattr(probe, "duration", 0.0)
            mins = int(dur // 60)
            secs = int(dur % 60)
            data.duration_str = f"{mins:02d}:{secs:02d} ({dur:.1f}s)"

            # Video Streams
            v_streams = getattr(probe, "video_streams", [])
            if v_streams:
                v0 = v_streams[0]
                data.video_codec = getattr(v0, "codec_name", "--").upper()
                w = getattr(v0, "width", None)
                h = getattr(v0, "height", None)
                if w and h:
                    data.video_resolution = f"{w}x{h}"
                fps = getattr(v0, "fps", None)
                if fps:
                    data.video_fps_str = f"{fps:.2f} fps"

            # Audio Streams
            a_streams = getattr(probe, "audio_streams", [])
            if a_streams:
                a0 = a_streams[0]
                data.audio_codec = getattr(a0, "codec_name", "--").upper()
                sr = getattr(a0, "sample_rate", None)
                if sr:
                    data.audio_sample_rate = f"{sr} Hz"
                ch = getattr(a0, "channels", None)
                layout = getattr(a0, "channel_layout", None)
                if ch:
                    ch_label = "Mono" if ch == 1 else ("Stereo" if ch == 2 else f"{ch}ch")
                    if layout:
                        ch_label = f"{ch_label} ({layout})"
                    data.audio_channels = ch_label

                br = getattr(a0, "bit_rate", None)
                if br:
                    data.audio_bit_rate = f"{br // 1000} kbps"
                else:
                    data.audio_bit_rate = "Variable"

                title = getattr(a0, "title", None) or getattr(a0, "language", None)
                if title:
                    data.audio_title = title

            # Cover Art
            if getattr(probe, "has_cover_art", False):
                data.has_cover_art = True
                stream_idx = getattr(probe, "cover_art_stream_index", None)
                idx_str = f"Stream #{stream_idx}" if stream_idx is not None else "Attached"
                data.cover_art_details = f"[YES] {idx_str} (Embedded)"
            else:
                data.cover_art_details = "[NO] None detected"

        elif getattr(task, "duration_seconds", 0.0) > 0:
            dur = task.duration_seconds
            mins = int(dur // 60)
            secs = int(dur % 60)
            data.duration_str = f"{mins:02d}:{secs:02d}"

        return data


@dataclass
class SystemTelemetry:
    """Hardware and environment telemetry (CPU, RAM, worker threads, FFmpeg)."""
    app_name: str = "ENTERPRISE VIDEO TO AUDIO TRANSCODER"
    version: str = "v2.5.0 Enterprise"
    cpu_percent: float = 0.0
    cpu_per_core: List[float] = field(default_factory=list)
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_percent: float = 0.0
    active_workers: int = 0
    max_workers: int = 4
    ffmpeg_status: str = "Available"
    ffmpeg_version: str = "6.1"
    ffmpeg_ok: bool = True

    @classmethod
    def sample(cls, active_workers: int = 0, max_workers: int = 4) -> SystemTelemetry:
        """Samples live system hardware telemetry using psutil if available."""
        telemetry = cls(active_workers=active_workers, max_workers=max_workers)
        try:
            telemetry.cpu_percent = psutil.cpu_percent(interval=None)
            per_core = psutil.cpu_percent(interval=None, percpu=True)
            telemetry.cpu_per_core = per_core if isinstance(per_core, list) else []

            ram = psutil.virtual_memory()
            telemetry.ram_used_gb = (ram.total - ram.available) / (1024**3)
            telemetry.ram_total_gb = ram.total / (1024**3)
            telemetry.ram_percent = ram.percent
        except Exception:
            telemetry.cpu_percent = 25.0
            telemetry.cpu_per_core = [20.0, 30.0, 25.0, 25.0]
            telemetry.ram_used_gb = 4.2
            telemetry.ram_total_gb = 16.0
            telemetry.ram_percent = 26.2

        # FFmpeg status check
        try:
            ffmpeg_path = find_ffmpeg()
            telemetry.ffmpeg_ok = bool(ffmpeg_path)
            telemetry.ffmpeg_status = "Available"
            telemetry.ffmpeg_version = get_ffmpeg_version(ffmpeg_path) or "6.1"
        except Exception:
            telemetry.ffmpeg_ok = False
            telemetry.ffmpeg_status = "Not Found"
            telemetry.ffmpeg_version = "N/A"

        return telemetry


@dataclass
class DashboardState:
    """Thread-safe snapshot of entire application state for dashboard rendering."""
    system: SystemTelemetry = field(default_factory=SystemTelemetry)
    tasks: List[Any] = field(default_factory=list)
    selected_index: int = 0
    inspector_data: Optional[MediaInspectorData] = None
    events: List[DashboardEvent] = field(default_factory=list)
    active_preset_name: str = "MP3 (320kbps High Quality)"
    output_dir_str: str = "Source File Directory (In-Place)"
    is_transcoding: bool = False
    is_watching: bool = False
    active_speed: float = 0.0
    active_progress: float = 0.0
    time_seconds: float = field(default_factory=time.time)
    safe_ascii: bool = False
    is_watching: bool = False


# =============================================================================
# Terminal Dashboard Layout Implementation
# =============================================================================

class TerminalDashboardLayout:
    """
    Renders an interactive, responsive Rich Layout for terminal dashboards.
    Supports flexible splitting, status badges, live spectrum monitoring,
    event logging, media inspector telemetry, and shortcut legends.
    """

    def __init__(self, safe_ascii: Optional[bool] = None):
        """
        Initialize TerminalDashboardLayout.
        :param safe_ascii: True to enforce pure 7-bit ASCII characters. If None, auto-detects.
        """
        if safe_ascii is None:
            self.safe_ascii: bool = not is_unicode_supported()
        else:
            self.safe_ascii = safe_ascii

        self.visualizer = AsciiVisualizer(safe_ascii=self.safe_ascii)
        self._lock = threading.Lock()

    @property
    def box_style(self) -> box.Box:
        """Returns ASCII box style in safe_ascii mode, otherwise ROUNDED."""
        return box.ASCII if self.safe_ascii else box.ROUNDED

    # -------------------------------------------------------------------------
    # Sub-Panel Renderers
    # -------------------------------------------------------------------------

    def build_header_panel(self, state: DashboardState, compact: bool = False) -> Panel:
        sys_info = state.system
        grid = Table.grid(expand=True)
        
        # Cyberpunk RGB Pulse based on time
        colors = ["bright_magenta", "bright_cyan", "bright_green", "bright_yellow", "bright_red"]
        pulse_idx = int(state.time_seconds * 3.0) % len(colors)
        neon = colors[pulse_idx]
        
        logo_text = Text()
        if not compact:
            logo = (
                " █ █ █ █▀▄ █▀▀ █▀█   ▀█▀ █▀█   █▀█ █ █ █▀▄ █ █▀█ \n"
                " ▀▄▀▄▀ █▄▀ █▀  █▄█    █  █▄█   █▀█ █ █ █▄▀ █ █▄█ \n"
                "  ▀ ▀  ▀▀  ▀▀▀ ▀ ▀    ▀  ▀ ▀   ▀ ▀ ▀▀▀ ▀▀  ▀ ▀▀▀ "
            )
            logo_text.append(logo, style=f"bold {neon}")
            
            grid.add_column("Logo", justify="left", ratio=2)
            grid.add_column("Stats", justify="right", ratio=3)
            
            stats_grid = Table.grid(expand=True)
            stats_grid.add_column("Key", style="bold cyan")
            stats_grid.add_column("Value")
            
            cpu_txt = Text(f"{sys_info.cpu_percent:>3.0f}% [", style="bold white")
            self._render_cpu_sparkline(cpu_txt, sys_info.cpu_per_core)
            cpu_txt.append("]", style="dim white")
            stats_grid.add_row("CPU CORE:", cpu_txt)
            
            ram_txt = Text(f"{sys_info.ram_used_gb:.1f}/{sys_info.ram_total_gb:.0f}GB [", style="white")
            self._render_mini_bar(ram_txt, sys_info.ram_percent, width=12)
            ram_txt.append(f"] {sys_info.ram_percent:.0f}%", style="dim white")
            stats_grid.add_row("SYS RAM:", ram_txt)
            
            ffmpeg_txt = Text(f"{sys_info.ffmpeg_version[:14]} ", style="dim green" if sys_info.ffmpeg_ok else "red")
            ffmpeg_txt.append("[OK]" if sys_info.ffmpeg_ok else "[MISSING]", style="bold bright_green" if sys_info.ffmpeg_ok else "bold bright_red")
            stats_grid.add_row("FFMPEG:", ffmpeg_txt)
            
            grid.add_row(logo_text, stats_grid)
        else:
            grid.add_column("Left")
            grid.add_column("Right", justify="right")
            bolt = "[*]" if self.safe_ascii else "⚡"
            title = Text(f"{bolt} ENTERPRISE TRANSCODER ", style=f"bold {neon}")
            title.append(sys_info.version, style="dim white")
            
            ffmpeg_str = " [OK]" if sys_info.ffmpeg_ok else " [MISSING]"
            stats = Text(f"CPU: {sys_info.cpu_percent:.0f}% | RAM: {sys_info.ram_percent:.0f}% | FFmpeg:{ffmpeg_str}")
            grid.add_row(title, stats)
            
        return Panel(
            grid,
            box=self.box_style,
            border_style=neon,
            padding=(0, 1),
            title=f"[bold {neon}]>> ENTERPRISE TRANSCODER // NEURAL MATRIX <<[/]",
            title_align="center",
        )

    def _render_cpu_sparkline(self, text_obj: Text, per_core: List[float]) -> None:
        """Renders per-core CPU usage indicator."""
        if not per_core:
            per_core = [0.0, 0.0, 0.0, 0.0]

        steps = AsciiVisualizer.ASCII_BLOCK_STEPS if self.safe_ascii else AsciiVisualizer.UNICODE_BLOCK_STEPS
        for core_pct in per_core[:16]:
            frac = max(0.0, min(1.0, core_pct / 100.0))
            idx = int(round(frac * (len(steps) - 1)))
            char = steps[idx]
            style = "bright_green" if frac < 0.6 else ("bright_yellow" if frac < 0.85 else "bold red")
            text_obj.append(char, style=style)

    def _render_mini_bar(self, text_obj: Text, percent: float, width: int = 8) -> None:
        """Renders a mini progress bar inside Text."""
        fill_char = "#" if self.safe_ascii else "█"
        empty_char = "." if self.safe_ascii else "░"
        frac = max(0.0, min(1.0, percent / 100.0))
        filled = int(round(frac * width))
        for i in range(width):
            if i < filled:
                style = "bright_cyan" if frac < 0.8 else "bold bright_yellow"
                text_obj.append(fill_char, style=style)
            else:
                text_obj.append(empty_char, style="dim white")

    def build_queue_panel(self, state: DashboardState, compact: bool = False) -> Panel:
        """
        Renders Queue View Panel:
        - Table of tasks with status badges:
          [PENDING] yellow, [PROBING] cyan, [CONVERTING] blue, [COMPLETED] green, [FAILED] red
        - Filename, target format, mini progress bar, speed factor, ETA
        """
        tasks = state.tasks
        completed_count = sum(1 for t in tasks if str(getattr(t, "status", "")).upper() == "COMPLETED")
        title = f"Batch Queue ({len(tasks)} items | {completed_count} completed)"

        if not tasks:
            empty_table = Table.grid(expand=True)
            empty_table.add_column("Msg", justify="center")
            empty_table.add_row(Text(""))
            empty_table.add_row(Text("Queue is empty. Drop video files or press [A] to add files.", style="dim yellow"))
            empty_table.add_row(Text("Press [V] to paste file paths from clipboard.", style="dim cyan"))
            return Panel(empty_table, title=title, box=self.box_style, border_style="bright_blue")

        table = Table(
            expand=True,
            box=None,
            border_style="dim white",
            pad_edge=False,
            padding=(0, 1),
            row_styles=["none", "dim"],
        )
        table.add_column("#", justify="right", width=2)
        table.add_column("Status", justify="center", width=14)
        table.add_column("Filename", style="bold white", ratio=1, overflow="ellipsis", no_wrap=True)
        table.add_column("Fmt", justify="center", width=5)
        table.add_column("Progress", justify="left", width=10 if compact else 18)
        table.add_column("Speed", justify="right", width=5, style="yellow")
        if not compact:
            table.add_column("ETA", justify="right", width=6, style="dim cyan")

        for idx, task in enumerate(tasks):
            is_selected = idx == state.selected_index
            row_prefix = ">" if is_selected else str(idx + 1)
            row_style = "bold white" if is_selected else ""

            # 1. Status Badge
            raw_status = str(getattr(task, "status", "PENDING")).upper()
            status_badge = self._format_status_badge(raw_status)

            # 2. Filename
            src_file = getattr(task, "source_file", None)
            fname = src_file.name if isinstance(src_file, Path) else str(src_file or f"Task_{idx+1}")
            if is_selected:
                fname = f"• {fname}"

            # 3. Target
            target_fmt = str(getattr(task, "target_format", "MP3")).upper()

            # 4. Mini Progress Bar
            prog_val = float(getattr(task, "progress", 0.0))
            bar_text = Text()
            bar_text.append("[", style="dim white")
            self._render_mini_bar(bar_text, prog_val, width=4 if compact else 9)
            bar_text.append(f"] {prog_val:>3.0f}%", style="bold cyan" if prog_val >= 100 else "white")

            # 5. Speed & ETA
            spd = str(getattr(task, "speed", "--"))
            eta_val = str(getattr(task, "eta", "--"))
            if spd == "0.0x" or not spd:
                spd = "--"
            if not eta_val:
                eta_val = "--"

            row_items = [
                row_prefix,
                status_badge,
                Text(fname, style="bold cyan" if is_selected else "white", overflow="ellipsis"),
                target_fmt,
                bar_text,
                spd,
            ]
            if not compact:
                row_items.append(eta_val)

            table.add_row(*row_items, style=row_style)

        return Panel(table, title=title, box=self.box_style, border_style="bright_blue", padding=(0, 1))

    def _format_status_badge(self, status: str) -> Text:
        """Renders color-coded status badge."""
        t = Text()
        if "CONVERT" in status:
            t.append("[CONVERTING]", style="bold bright_blue")
        elif "PROB" in status:
            t.append("[PROBING]", style="bold bright_cyan")
        elif "COMPLET" in status:
            t.append("[COMPLETED]", style="bold bright_green")
        elif "FAIL" in status:
            t.append("[FAILED]", style="bold bright_red")
        elif "CANCEL" in status:
            t.append("[CANCELLED]", style="dim red")
        else:
            t.append("[PENDING]", style="bold bright_yellow")
        return t

    def build_monitor_panel(self, state: DashboardState, compact: bool = False) -> Panel:
        """
        Renders Upper-Right Panel:
        ASCII Stereo VU Meter & 7-Band Dynamic Spectrum Visualizer.
        """
        is_active = state.is_transcoding or any(
            str(getattr(t, "status", "")).upper() == "CONVERTING" for t in state.tasks
        )

        active_task = None
        for t in state.tasks:
            if str(getattr(t, "status", "")).upper() == "CONVERTING":
                active_task = t
                break

        progress = float(getattr(active_task, "progress", state.active_progress))
        speed_raw = getattr(active_task, "speed", f"{state.active_speed:.1f}x" if state.active_speed else "1.0x")
        try:
            speed_val = float(str(speed_raw).replace("x", ""))
        except Exception:
            speed_val = 1.0

        workers = state.system.active_workers or (1 if is_active else 0)

        monitor_widget = self.visualizer.render_monitor_widget(
            time_sec=state.time_seconds,
            progress=progress,
            speed=speed_val,
            is_active=is_active,
            workers=workers,
            spectrum_height=3 if compact else 4,
            compact=compact,
        )

        return Panel(
            monitor_widget,
            box=self.box_style,
            title="[bold magenta]Audio Telemetry & Spectral Monitor[/bold magenta]",
            border_style="magenta",
            padding=(0, 1),
        )

    def build_inspector_panel(self, state: DashboardState, compact: bool = False) -> Panel:
        """
        Renders Lower-Right Panel:
        Media Stream Inspector card (video resolution, duration, audio streams, codecs, cover art).
        """
        inspector = state.inspector_data
        if inspector is None:
            if state.tasks and 0 <= state.selected_index < len(state.tasks):
                inspector = MediaInspectorData.from_task(state.tasks[state.selected_index])
            else:
                inspector = MediaInspectorData()

        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column("Property", style="bold cyan", width=11 if compact else 13)
        grid.add_column("Value", style="white", ratio=1)

        grid.add_row("Source:", Text(inspector.filename, style="bold white", overflow="ellipsis"))
        size_part = f" | {inspector.file_size_str}" if inspector.file_size_str != "--" else ""
        grid.add_row("Format:", f"{inspector.container_format} ({inspector.duration_str}{size_part})")

        fps_part = f" @ {inspector.video_fps_str}" if inspector.video_fps_str != "--" else ""
        codec_part = f" ({inspector.video_codec})" if inspector.video_codec != "--" else ""
        grid.add_row("Video:", f"{inspector.video_resolution}{fps_part}{codec_part}")

        stream_spec = f"{inspector.audio_codec} | {inspector.audio_sample_rate} | {inspector.audio_channels}"
        if inspector.audio_bit_rate != "--":
            stream_spec += f" | {inspector.audio_bit_rate}"
        grid.add_row("Audio:", stream_spec)

        if inspector.audio_title != "--":
            grid.add_row("Track:", inspector.audio_title)

        art_style = "bold bright_green" if inspector.has_cover_art else "dim white"
        grid.add_row("Cover Art:", Text(inspector.cover_art_details, style=art_style))
        grid.add_row("Target:", Text(inspector.target_preset, style="bold bright_yellow"))

        return Panel(
            grid,
            box=self.box_style,
            title="[bold cyan]Media Stream Inspector[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    def build_events_panel(self, state: DashboardState) -> Panel:
        """
        Renders Bottom-Left Panel:
        Scrolling Event Log buffer (last N events with timestamps and color-coded tags).
        """
        events = state.events[-5:]
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column("Entry", style="white", overflow="ellipsis", no_wrap=True)

        if not events:
            grid.add_row(Text("No recent log events. Ready for batch operation.", style="dim white"))
        else:
            for ev in events:
                line = Text(no_wrap=True, overflow="ellipsis")
                line.append(f"{ev.formatted_time()} ", style="dim cyan")
                tag_style = self._get_event_tag_style(ev.tag)
                line.append(f"[{ev.tag}] ", style=tag_style)
                line.append(ev.message, style="white")
                grid.add_row(line)

        return Panel(
            grid,
            box=self.box_style,
            title="[bold green]Event Log Buffer[/bold green]",
            border_style="green",
            padding=(0, 1),
        )

    @staticmethod
    def _get_event_tag_style(tag: str) -> str:
        """Returns styling for event tag badge."""
        tag_upper = tag.upper()
        if "PROBE" in tag_upper:
            return "bold bright_cyan"
        if "TRANSCODE" in tag_upper:
            return "bold bright_blue"
        if "VERIFY" in tag_upper:
            return "bold bright_green"
        if "ERROR" in tag_upper:
            return "bold bright_red"
        if "WARN" in tag_upper:
            return "bold bright_yellow"
        return "dim white"

    def build_shortcuts_panel(self, state: DashboardState) -> Panel:
        """
        Renders Bottom-Right Panel:
        Hotkey & Shortcut Legend bar:
        [A] Add Files | [V] Paste | [P] Presets | [F] Filter Wizard | [I] Inspect | [S] Start/Pause | [C] Clear | [R] Reports | [Q] Quit
        """
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column("Shortcuts", style="white")

        line1 = Text()
        line1.append("[A]", style="bold bright_yellow")
        line1.append(" Add  ", style="white")
        line1.append("[V]", style="bold bright_yellow")
        line1.append(" Paste  ", style="white")
        line1.append("[P]", style="bold bright_yellow")
        line1.append(" Presets  ", style="white")
        line1.append("[F]", style="bold bright_yellow")
        line1.append(" Filter Wizard", style="white")
        grid.add_row(line1)

        line2 = Text()
        line2.append("[I]", style="bold bright_cyan")
        line2.append(" Inspect  ", style="white")
        line2.append("[S]", style="bold bright_green")
        line2.append(" Start/Pause  ", style="white")
        line2.append("[C]", style="bold bright_red")
        line2.append(" Clear  ", style="white")
        line2.append("[R]", style="bold bright_cyan")
        line2.append(" Reports  ", style="white")
        line2.append("[Q]", style="bold bright_red")
        line2.append(" Quit", style="white")
        grid.add_row(line2)

        return Panel(
            grid,
            box=self.box_style,
            title="[bold bright_yellow]Hotkeys & Commands[/bold bright_yellow]",
            border_style="yellow",
            padding=(0, 1),
        )

    # -------------------------------------------------------------------------
    # Master Render Pipeline
    # -------------------------------------------------------------------------

    def render(self, state: DashboardState, terminal_width: Optional[int] = None) -> Layout:
        """
        Thread-safe rendering pipeline that takes a DashboardState snapshot and
        assembles the complete multi-panel Rich Layout.

        :param state: Complete state snapshot.
        :param terminal_width: Terminal columns width (80, 120, etc.) for responsive adaptation.
        :return: Populated rich.layout.Layout object.
        """
        with self._lock:
            self.safe_ascii = state.safe_ascii
            self.visualizer.safe_ascii = state.safe_ascii

            # Auto-detect terminal width if omitted
            if terminal_width is None:
                try:
                    import shutil
                    terminal_width = shutil.get_terminal_size((100, 30)).columns
                except Exception:
                    terminal_width = 100

            is_compact = terminal_width < 100

            layout = Layout(name="root")
            layout.split_column(
                Layout(name="header", size=4 if is_compact else 3),
                Layout(name="main", ratio=1),
                Layout(name="bottom", size=7),
            )

            layout["main"].split_row(
                Layout(name="queue", ratio=3),
                Layout(name="right_split", ratio=2),
            )

            layout["right_split"].split_column(
                Layout(name="monitor", ratio=1),
                Layout(name="inspector", ratio=1),
            )

            layout["bottom"].split_row(
                Layout(name="events", ratio=3),
                Layout(name="shortcuts", ratio=2),
            )

            layout["header"].update(self.build_header_panel(state, compact=is_compact))
            layout["queue"].update(self.build_queue_panel(state, compact=is_compact))
            layout["monitor"].update(self.build_monitor_panel(state, compact=is_compact))
            layout["inspector"].update(self.build_inspector_panel(state, compact=is_compact))
            layout["events"].update(self.build_events_panel(state))
            layout["shortcuts"].update(self.build_shortcuts_panel(state))

            return layout


# Global default layout instance for thread-safe rendering
_default_dashboard_layout = TerminalDashboardLayout()
_render_lock = threading.Lock()


def render_dashboard(state: DashboardState, terminal_width: Optional[int] = None) -> Layout:
    """
    Thread-safe rendering function that takes a dataclass snapshot of the
    application state and produces a polished Rich Layout.

    :param state: DashboardState snapshot.
    :param terminal_width: Optional terminal column width for responsive layout optimization.
    :return: Rendered Rich Layout.
    """
    with _render_lock:
        return _default_dashboard_layout.render(state, terminal_width=terminal_width)


# =============================================================================
# Legacy & Backward Compatibility Layer
# =============================================================================

class VUMeter:
    """Legacy VUMeter compatibility wrapper delegating to AsciiVisualizer."""

    def __init__(self) -> None:
        self.visualizer = AsciiVisualizer()
        self.left_db: float = -60.0
        self.right_db: float = -60.0
        self.is_active: bool = False
        self._lock = threading.Lock()

    def update(
        self,
        left_db: Optional[float] = None,
        right_db: Optional[float] = None,
        is_active: bool = True,
    ) -> None:
        with self._lock:
            self.is_active = is_active
            if left_db is not None:
                self.left_db = left_db
            if right_db is not None:
                self.right_db = right_db

    def render(self, bar_width: int = 18) -> Panel:
        with self._lock:
            meter_text = self.visualizer.render_vu_meter(self.left_db, self.right_db, width=bar_width)
            return Panel(meter_text, title="Master VU Meter", border_style="cyan")


class HardwareTelemetry:
    """Legacy HardwareTelemetry compatibility wrapper."""

    def __init__(self) -> None:
        self.telemetry = SystemTelemetry.sample()

    def render(self) -> Panel:
        self.telemetry = SystemTelemetry.sample()
        table = Table.grid(padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Val", style="white")
        table.add_row("CPU:", f"{self.telemetry.cpu_percent:.1f}%")
        table.add_row("RAM:", f"{self.telemetry.ram_used_gb:.1f} / {self.telemetry.ram_total_gb:.1f} GB")
        table.add_row("FFmpeg:", f"{self.telemetry.ffmpeg_status} ({self.telemetry.ffmpeg_version})")
        return Panel(table, title="Hardware Telemetry", border_style="dim blue")


class EventLogStream:
    """Legacy EventLogStream compatibility wrapper."""

    def __init__(self, max_entries: int = 100) -> None:
        self.events: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def add(self, message: str, level: str = "INFO") -> None:
        with self._lock:
            self.events.append(DashboardEvent(tag=level.upper(), message=message))

    def render(self, count: int = 5) -> Panel:
        with self._lock:
            state = DashboardState(events=list(self.events)[-count:])
            return _default_dashboard_layout.build_events_panel(state)


class DashboardLayout(TerminalDashboardLayout):
    """Alias for TerminalDashboardLayout for backwards compatibility."""
    pass
