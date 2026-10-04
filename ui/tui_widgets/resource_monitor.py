"""
resource_monitor.py - Minimalist Hardware & Telemetry Monitor for Textual TUI.

Provides:
- ResourceMonitorWidget: Real-time Textual widget displaying system health, CPU per-core usage,
  RAM consumption, transcoding worker concurrency, GPU acceleration, and FFmpeg binary status.
- ResourceTelemetrySnapshot: Data model representing live system metrics.
- Sleek, minimalist monochrome dark styling with zero gaudy colors.
- Automatic background timer using Textual's set_interval(1.0, ...) for smooth, flicker-free telemetry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import psutil
from rich import box
from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg, get_ffmpeg_version
from ui.ascii_visualizer import is_unicode_supported

try:
    from ui.tui_theme import (
        BORDER_SUBTLE,
        BORDER_FOCUS,
        ACCENT_PRIMARY,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        TEXT_MUTED,
        THEME_COLORS,
    )
except ImportError:
    BORDER_SUBTLE = "#242938"
    BORDER_FOCUS = "#3d5470"
    ACCENT_PRIMARY = "#4ba3be"
    TEXT_PRIMARY = "#e1e4ec"
    TEXT_SECONDARY = "#9aa2b4"
    TEXT_MUTED = "#5e6678"
    THEME_COLORS = {}


# =============================================================================
# Hardware & Telemetry Data Models
# =============================================================================

@dataclass
class GpuInfo:
    """Represents detected GPU hardware acceleration capabilities."""
    available: bool = False
    name: str = "Unknown"
    engine: str = "None"
    hwaccels: List[str] = field(default_factory=list)
    status_text: str = "GPU: Auto-Accelerated"


@dataclass
class FFmpegInfo:
    """Represents detected FFmpeg binary presence and version."""
    is_ok: bool = False
    path: str = "Not Found"
    version: str = "N/A"
    status_text: str = "[FAIL] FFmpeg Not Found"


@dataclass
class ResourceTelemetrySnapshot:
    """Snapshot of real-time system and transcoder resource utilization."""
    timestamp: float = field(default_factory=time.time)
    cpu_percent_overall: float = 0.0
    cpu_per_core: List[float] = field(default_factory=list)
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_percent: float = 0.0
    active_workers: int = 0
    max_workers: int = 4
    gpu_info: GpuInfo = field(default_factory=GpuInfo)
    ffmpeg_info: FFmpegInfo = field(default_factory=FFmpegInfo)


# Global caches for costly operations (GPU and FFmpeg lookups)
_CACHED_GPU_INFO: Optional[GpuInfo] = None
_CACHED_FFMPEG_INFO: Optional[FFmpegInfo] = None
_CACHE_LOCK = threading.Lock()


# =============================================================================
# Detection & Sampling Functions
# =============================================================================

def detect_gpu_acceleration(force_refresh: bool = False) -> GpuInfo:
    """
    Detects hardware acceleration availability (NVENC, QSV, AMF, D3D11VA, etc.).
    Caches results to avoid spawning subprocesses on every 1-second refresh tick.
    """
    global _CACHED_GPU_INFO
    with _CACHE_LOCK:
        if _CACHED_GPU_INFO is not None and not force_refresh:
            return _CACHED_GPU_INFO

        hwaccels: List[str] = []
        try:
            ffmpeg_exe = find_ffmpeg()
            res = subprocess.run(
                [str(ffmpeg_exe), "-hwaccels"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_get_creation_flags(),
                timeout=4,
            )
            for line in res.stdout.splitlines():
                line = line.strip().lower()
                if line and "hardware acceleration" not in line and not line.startswith("="):
                    hwaccels.append(line)
        except Exception:
            pass

        has_nvidia = False
        has_intel = False
        has_amd = False
        gpu_name = ""

        # Check for NVIDIA NVENC
        if shutil.which("nvidia-smi"):
            has_nvidia = True
            try:
                smi_res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=_get_creation_flags(),
                    timeout=3,
                )
                if smi_res.stdout.strip():
                    gpu_name = smi_res.stdout.splitlines()[0].strip()
            except Exception:
                pass
        elif any(k in hwaccels for k in ("cuda", "nvenc", "cuvid")):
            has_nvidia = True

        # Check for Intel QSV
        if "qsv" in hwaccels:
            has_intel = True

        # Check for AMD AMF
        if "amf" in hwaccels:
            has_amd = True

        # Construct concise, professional monochrome status text
        if has_nvidia:
            engine = "NVIDIA NVENC"
            status_text = f"GPU: Auto-Accelerated ({gpu_name or 'NVIDIA NVENC'})"
            available = True
        elif has_intel:
            engine = "Intel QSV"
            status_text = "GPU: Auto-Accelerated (Intel QSV)"
            available = True
        elif has_amd:
            engine = "AMD AMF"
            status_text = "GPU: Auto-Accelerated (AMD AMF)"
            available = True
        elif any(k in hwaccels for k in ("d3d11va", "dxva2", "vaapi", "videotoolbox")):
            engine = "Hardware Accel"
            status_text = "GPU: Auto-Accelerated"
            available = True
        else:
            engine = "Software"
            status_text = "GPU: Auto-Accelerated"  # Clean fallback
            available = False

        info = GpuInfo(
            available=available,
            name=gpu_name or engine,
            engine=engine,
            hwaccels=hwaccels,
            status_text=status_text,
        )
        _CACHED_GPU_INFO = info
        return info


def detect_ffmpeg_status(force_refresh: bool = False) -> FFmpegInfo:
    """
    Detects FFmpeg executable status, absolute binary path, and version string.
    Caches results to maintain zero-latency UI refreshes.
    """
    global _CACHED_FFMPEG_INFO
    with _CACHE_LOCK:
        if _CACHED_FFMPEG_INFO is not None and not force_refresh:
            return _CACHED_FFMPEG_INFO

        try:
            exe_path = find_ffmpeg()
            ver = get_ffmpeg_version(exe_path) or "Unknown"
            status_text = f"[OK] {exe_path}"
            info = FFmpegInfo(
                is_ok=True,
                path=str(exe_path),
                version=ver,
                status_text=status_text,
            )
        except Exception:
            info = FFmpegInfo(
                is_ok=False,
                path="Not Located",
                version="N/A",
                status_text="[FAIL] FFmpeg Binary Not Found",
            )

        _CACHED_FFMPEG_INFO = info
        return info


def sample_resource_telemetry(
    active_workers: int = 0,
    max_workers: int = 4,
    queue_manager: Optional[Any] = None,
) -> ResourceTelemetrySnapshot:
    """
    Samples live system CPU, RAM, active workers, GPU acceleration, and FFmpeg telemetry.
    Thread-safe and fast (<1ms runtime).
    """
    # CPU Sampling
    try:
        cpu_overall = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        if not isinstance(per_core, list):
            per_core = [cpu_overall]
    except Exception:
        cpu_overall = 0.0
        per_core = [0.0] * (os.cpu_count() or 4)

    # RAM Sampling
    try:
        ram = psutil.virtual_memory()
        ram_used_gb = (ram.total - ram.available) / (1024**3)
        ram_total_gb = ram.total / (1024**3)
        ram_percent = ram.percent
    except Exception:
        ram_used_gb = 0.0
        ram_total_gb = 16.0
        ram_percent = 0.0

    # Worker concurrency
    resolved_active = active_workers
    resolved_max = max_workers
    if queue_manager is not None:
        try:
            stats = queue_manager.get_stats()
            resolved_active = getattr(stats, "converting_tasks", 0) + getattr(stats, "probing_tasks", 0)
            resolved_max = getattr(stats, "max_workers", max_workers)
        except Exception:
            try:
                active_set = getattr(queue_manager, "_active_tasks", set())
                resolved_active = len(active_set)
                resolved_max = getattr(queue_manager, "max_workers", max_workers)
            except Exception:
                pass

    gpu_info = detect_gpu_acceleration()
    ffmpeg_info = detect_ffmpeg_status()

    return ResourceTelemetrySnapshot(
        timestamp=time.time(),
        cpu_percent_overall=cpu_overall,
        cpu_per_core=per_core,
        ram_used_gb=ram_used_gb,
        ram_total_gb=ram_total_gb,
        ram_percent=ram_percent,
        active_workers=resolved_active,
        max_workers=resolved_max,
        gpu_info=gpu_info,
        ffmpeg_info=ffmpeg_info,
    )


# =============================================================================
# Visual Rendering Utilities (Minimalist Monochrome Dark Theme)
# =============================================================================

# Sparkline blocks
# Ensure index 0 uses visible baseline block (U+2581) to prevent Rich word wrapping on low-usage cores
_UNICODE_SPARKLINE = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
_ASCII_SPARKLINE = [".", ":", "-", "=", "+", "*", "#", "#", "@"]

# Bar characters
_UNICODE_FILL = "█"
_UNICODE_EMPTY = "░"
_ASCII_FILL = "#"
_ASCII_EMPTY = "-"


def render_sparkline(values: List[float], safe_ascii: bool = False, max_items: int = 16) -> Text:
    """
    Renders a compact, monochrome sparkline text block representing per-core utilization.
    Uses subdued shades (dim slate -> crisp silver white) with zero gaudy neon.
    """
    steps = _ASCII_SPARKLINE if safe_ascii else _UNICODE_SPARKLINE
    text = Text("[", style="dim #5e6678", no_wrap=True)
    trimmed = values[:max_items]

    for val in trimmed:
        norm = max(0.0, min(1.0, val / 100.0))
        idx = int(round(norm * (len(steps) - 1)))
        char = steps[idx]

        # Monochrome styling: muted slate for low, clean white for medium, bright silver for heavy
        if norm < 0.50:
            char_style = "#94a3b8"
        elif norm < 0.85:
            char_style = "#e2e8f0"
        else:
            char_style = "bold #f8fafc"

        text.append(char, style=char_style)

    text.append("]", style="dim #5e6678")
    text.no_wrap = True
    return text


def render_mini_bar(
    percent: float,
    width: int = 10,
    safe_ascii: bool = False,
    fill_style: str = "#cbd5e1",
    empty_style: str = "#242938",
) -> Text:
    """
    Renders a sleek monochrome progress bar of specified character width.
    """
    fill_char = _ASCII_FILL if safe_ascii else _UNICODE_FILL
    empty_char = _ASCII_EMPTY if safe_ascii else _UNICODE_EMPTY

    clamped = max(0.0, min(100.0, percent))
    fill_count = int(round((clamped / 100.0) * width))
    empty_count = max(0, width - fill_count)

    bar = Text("[", style="dim #5e6678", no_wrap=True)
    if fill_count > 0:
        bar.append(fill_char * fill_count, style=fill_style)
    if empty_count > 0:
        bar.append(empty_char * empty_count, style=empty_style)
    bar.append("]", style="dim #5e6678")
    bar.no_wrap = True
    return bar


def render_resource_monitor_table(
    snapshot: ResourceTelemetrySnapshot,
    safe_ascii: bool = False,
    compact: bool = False,
    width: Optional[int] = None,
) -> Table:
    """
    Constructs a clean Rich Table with telemetry metrics.
    Implements sleek minimalist dark styling (charcoal/slate/crisp silver).
    Left column labels have consistent width.
    Right column value cells never wrap awkwardly even in narrow panels.
    """
    is_compact = compact or (width is not None and width < 45)

    # Left column width: 8 for compact/narrow (CPU:, RAM:, WORKERS:, GPU:, FFMPEG:),
    # or 11 for standard/wide (CPU CORES:, RAM USAGE:, WORKERS:, GPU ACCEL:, FFMPEG:).
    col_metric_width = 8 if is_compact else 11

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column("Metric", style="bold #94a3b8", width=col_metric_width, no_wrap=True)
    table.add_column("Value", style="#e2e8f0", ratio=1, no_wrap=True, overflow="ellipsis")

    if is_compact:
        lbl_cpu = "CPU:"
        lbl_ram = "RAM:"
        lbl_workers = "WORKERS:"
        lbl_gpu = "GPU:"
        lbl_ffmpeg = "FFMPEG:"
    else:
        lbl_cpu = "CPU CORES:"
        lbl_ram = "RAM USAGE:"
        lbl_workers = "WORKERS:"
        lbl_gpu = "GPU ACCEL:"
        lbl_ffmpeg = "FFMPEG:"

    # -------------------------------------------------------------------------
    # Row 1: CPU core usage with sparkline and percentage
    # -------------------------------------------------------------------------
    core_count = len(snapshot.cpu_per_core)
    cpu_val = Text(no_wrap=True)
    cpu_val.append(f"{snapshot.cpu_percent_overall:>4.1f}% ", style="bold #f8fafc")

    spark_items = 6 if (width is not None and width < 34) else (8 if is_compact else 16)
    spark = render_sparkline(snapshot.cpu_per_core, safe_ascii=safe_ascii, max_items=spark_items)
    cpu_val.append_text(spark)

    if width is not None and width < 33:
        cpu_val.append(f" {core_count}C", style="dim #64748b")
    else:
        cpu_val.append(f" ({core_count}C)", style="dim #64748b")

    table.add_row(lbl_cpu, cpu_val)

    # -------------------------------------------------------------------------
    # Row 2: RAM usage with progress bar and GB ratio
    # -------------------------------------------------------------------------
    ram_val = Text(no_wrap=True)
    ram_bar_w = 6 if (width is not None and width < 38) else (8 if is_compact else 12)
    ram_bar = render_mini_bar(snapshot.ram_percent, width=ram_bar_w, safe_ascii=safe_ascii)

    if is_compact:
        ram_ratio = f"{snapshot.ram_used_gb:.1f}/{snapshot.ram_total_gb:.0f}G "
    else:
        ram_ratio = f"{snapshot.ram_used_gb:.1f}/{snapshot.ram_total_gb:.0f} GB "

    ram_val.append(ram_ratio, style="#e2e8f0")
    ram_val.append_text(ram_bar)
    ram_val.append(f" {snapshot.ram_percent:.0f}%", style="dim #94a3b8")
    table.add_row(lbl_ram, ram_val)

    # -------------------------------------------------------------------------
    # Row 3: Active workers / concurrency bar
    # -------------------------------------------------------------------------
    workers_val = Text(no_wrap=True)
    w_bar_w = 6 if (width is not None and width < 38) else (8 if is_compact else 10)
    concurrency_pct = (snapshot.active_workers / max(1, snapshot.max_workers)) * 100.0
    workers_bar = render_mini_bar(
        concurrency_pct,
        width=w_bar_w,
        safe_ascii=safe_ascii,
        fill_style="bold #f8fafc",
    )
    worker_status = "BUSY" if snapshot.active_workers > 0 else "IDLE"
    status_style = "bold #cbd5e1" if snapshot.active_workers > 0 else "dim #5e6678"

    if is_compact:
        workers_val.append(f"{snapshot.active_workers}/{snapshot.max_workers} ", style="#e2e8f0")
    else:
        workers_val.append(f"{snapshot.active_workers}/{snapshot.max_workers} workers ", style="#e2e8f0")

    workers_val.append_text(workers_bar)
    workers_val.append(f" [{worker_status}]", style=status_style)
    table.add_row(lbl_workers, workers_val)

    # -------------------------------------------------------------------------
    # Row 4: GPU acceleration status
    # -------------------------------------------------------------------------
    gpu_val = Text(no_wrap=True)
    if snapshot.gpu_info.available:
        gpu_val.append("[OK] ", style="bold #72a37d")
        raw_gpu = snapshot.gpu_info.status_text
        if raw_gpu.startswith("GPU: "):
            raw_gpu = raw_gpu[5:]

        if is_compact:
            disp_gpu = (
                snapshot.gpu_info.engine
                if (snapshot.gpu_info.engine and snapshot.gpu_info.engine != "None")
                else raw_gpu
            )
            if width is not None and width < 32 and len(disp_gpu) > 12:
                disp_gpu = disp_gpu[:12]
            gpu_val.append(disp_gpu, style="#e2e8f0")
        else:
            gpu_val.append(raw_gpu, style="#e2e8f0")
    else:
        gpu_val.append("[--] ", style="dim #5e6678")
        gpu_val.append("Software (CPU)", style="#94a3b8")
    table.add_row(lbl_gpu, gpu_val)

    # -------------------------------------------------------------------------
    # Row 5: FFmpeg binary status
    # -------------------------------------------------------------------------
    ffmpeg_val = Text(no_wrap=True)
    if snapshot.ffmpeg_info.is_ok:
        ffmpeg_val.append("[OK] ", style="bold #72a37d")
        path_str = snapshot.ffmpeg_info.path
        ver_str = (
            snapshot.ffmpeg_info.version
            if (snapshot.ffmpeg_info.version and snapshot.ffmpeg_info.version != "N/A")
            else ""
        )
        if ver_str.lower().startswith("n"):
            ver_str = ver_str[1:]
        if "-" in ver_str:
            ver_str = ver_str.split("-")[0]
        if len(ver_str) > 8:
            ver_str = ver_str[:8]

        if is_compact:
            base_name = "ffmpeg" if (width is not None and width < 38) else (Path(path_str).name if path_str else "ffmpeg")
            if ver_str:
                ffmpeg_val.append(f"{base_name} ", style="#cbd5e1")
                ffmpeg_val.append(f"({ver_str})", style="dim #64748b")
            else:
                ffmpeg_val.append(base_name, style="#cbd5e1")
        else:
            if len(path_str) > 30:
                path_str = "..." + path_str[-27:]
            ffmpeg_val.append(f"{path_str} ", style="#cbd5e1")
            if ver_str:
                ffmpeg_val.append(f"({ver_str})", style="dim #64748b")
    else:
        ffmpeg_val.append("[FAIL] ", style="bold #b36262")
        ffmpeg_val.append("Binary Not Found", style="#b36262")
    table.add_row(lbl_ffmpeg, ffmpeg_val)

    return table


def render_resource_monitor_panel(
    snapshot: ResourceTelemetrySnapshot,
    safe_ascii: bool = False,
    compact: bool = False,
    border_style: str = BORDER_SUBTLE,
    width: Optional[int] = None,
) -> Panel:
    """
    Renders the telemetry table inside a sleek minimalist dark Panel.
    Uses dark slate border styling matching the global theme (#242938).
    """
    is_compact = compact or (width is not None and width < 45)
    table = render_resource_monitor_table(
        snapshot=snapshot,
        safe_ascii=safe_ascii,
        compact=is_compact,
        width=width,
    )
    box_type = box.ASCII if safe_ascii else box.ROUNDED
    title = (
        "[bold #cbd5e1]SYSTEM TELEMETRY[/]"
        if (width is not None and width < 40)
        else "[bold #cbd5e1]SYSTEM & HARDWARE TELEMETRY[/]"
    )

    return Panel(
        table,
        title=title,
        title_align="left",
        border_style=border_style,
        box=box_type,
        padding=(0, 1),
    )


# =============================================================================
# Textual Widget Implementation
# =============================================================================

class ResourceMonitorWidget(Static):
    """
    Enterprise Textual Widget displaying real-time system and hardware telemetry.
    Refreshes smoothly via background timer at 1.0s interval without flickering.
    """

    DEFAULT_CSS = """
    ResourceMonitorWidget {
        width: 100%;
        height: auto;
        background: transparent;
        color: #e2e8f0;
        padding: 0;
        margin: 0;
    }
    """

    # Reactive attributes for live data tracking
    cpu_percent = reactive(0.0)
    ram_percent = reactive(0.0)
    active_workers = reactive(0)
    max_workers = reactive(4)

    def __init__(
        self,
        queue_manager: Optional[Any] = None,
        refresh_interval: float = 1.0,
        compact: bool = False,
        safe_ascii: Optional[bool] = None,
        wrap_in_panel: bool = True,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
        disabled: bool = False,
    ) -> None:
        """
        Initializes the ResourceMonitorWidget.

        Args:
            queue_manager: Optional QueueManager instance to query active workers and concurrency.
            refresh_interval: Update period in seconds (default 1.0s).
            compact: Whether to use compact column widths and truncated paths.
            safe_ascii: True to enforce 7-bit ASCII, False for Unicode, None for auto-detection.
            wrap_in_panel: Whether to enclose the table in a minimalist rounded border panel.
        """
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.queue_manager = queue_manager
        self.refresh_interval = refresh_interval
        self.compact = compact
        self.wrap_in_panel = wrap_in_panel
        self.safe_ascii = (not is_unicode_supported()) if safe_ascii is None else safe_ascii

        self._timer: Optional[Any] = None
        self._last_snapshot: Optional[ResourceTelemetrySnapshot] = None

        # Prime psutil baseline call
        try:
            psutil.cpu_percent(percpu=True)
        except Exception:
            pass

    def on_mount(self) -> None:
        """Called when widget is added to Textual app screen. Starts smooth 1.0s timer."""
        # Initial telemetry render
        self.refresh_telemetry()

        # Setup 1.0s background timer for smooth flicker-free telemetry refresh
        self._timer = self.set_interval(
            self.refresh_interval,
            self.refresh_telemetry,
            name="resource_monitor_ticker",
        )

    def on_unmount(self) -> None:
        """Clean up timer when widget is unmounted."""
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    def set_queue_manager(self, queue_manager: Any) -> None:
        """Sets or replaces the active QueueManager reference."""
        self.queue_manager = queue_manager
        self.refresh_telemetry()

    def set_workers(self, active: int, max_workers: int) -> None:
        """Manually sets active and max worker counts."""
        self.active_workers = active
        self.max_workers = max_workers
        self.refresh_telemetry()

    def get_snapshot(self) -> ResourceTelemetrySnapshot:
        """Returns the most recent sampled telemetry snapshot."""
        if self._last_snapshot is None:
            self._last_snapshot = sample_resource_telemetry(
                active_workers=self.active_workers,
                max_workers=self.max_workers,
                queue_manager=self._resolve_queue_manager(),
            )
        return self._last_snapshot

    def _resolve_queue_manager(self) -> Optional[Any]:
        """Resolves queue manager from widget parameter or parent Textual App instance."""
        if self.queue_manager is not None:
            return self.queue_manager
        try:
            if hasattr(self, "app") and self.app is not None:
                return getattr(self.app, "queue_manager", None)
        except Exception:
            pass
        return None

    def on_resize(self, event: Any = None) -> None:
        """Dynamically re-render layout when widget dimensions change."""
        self.refresh_telemetry()

    def refresh_telemetry(self) -> None:
        """
        Samples live telemetry and updates widget content smoothly without flickering.
        Invoked automatically every 1.0s by background timer and on resize.
        """
        qm = self._resolve_queue_manager()
        snapshot = sample_resource_telemetry(
            active_workers=self.active_workers,
            max_workers=self.max_workers,
            queue_manager=qm,
        )
        self._last_snapshot = snapshot

        # Update reactive attributes
        self.cpu_percent = snapshot.cpu_percent_overall
        self.ram_percent = snapshot.ram_percent
        self.active_workers = snapshot.active_workers
        self.max_workers = snapshot.max_workers

        # Determine widget available width
        current_width = self.size.width if self.size.width > 0 else None
        effective_compact = self.compact or (current_width is not None and current_width < 45)

        # Build clean renderable
        if self.wrap_in_panel:
            renderable: RenderableType = render_resource_monitor_panel(
                snapshot=snapshot,
                safe_ascii=self.safe_ascii,
                compact=effective_compact,
                border_style=BORDER_SUBTLE,
                width=current_width,
            )
        else:
            renderable = render_resource_monitor_table(
                snapshot=snapshot,
                safe_ascii=self.safe_ascii,
                compact=effective_compact,
                width=current_width,
            )

        # Update Static widget content in place
        self.update(renderable)


__all__ = [
    "GpuInfo",
    "FFmpegInfo",
    "ResourceTelemetrySnapshot",
    "detect_gpu_acceleration",
    "detect_ffmpeg_status",
    "sample_resource_telemetry",
    "render_sparkline",
    "render_mini_bar",
    "render_resource_monitor_table",
    "render_resource_monitor_panel",
    "ResourceMonitorWidget",
    "BORDER_SUBTLE",
    "BORDER_FOCUS",
]
