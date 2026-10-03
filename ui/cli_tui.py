"""
Interactive Terminal User Interface (CLI TUI) for Video to Audio Transcoder.
Built with Rich. Features Dual-Mode Live Interactive Dashboard, dynamic real-time
VU meter, 7-band audio spectrum visualizer, hardware telemetry, audio filter wizard,
preset selector, media stream inspector, clipboard watcher, multi-bar progress,
and verification reports.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    msvcrt = None

import psutil
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from core.ffmpeg_finder import find_ffmpeg, get_ffmpeg_version
from core.probe import AudioStreamInfo, MediaProbeResult, probe_media
from processing.progress_tracker import ProgressSnapshot
from processing.queue_manager import ConversionTask, QueueManager, TaskStatus
from processing.reporter import (
    create_report_from_tasks,
    format_bytes_human,
    format_duration_human,
)
from processing.verifier import VerificationResult
from ui.dashboard_layout import (
    DashboardEvent,
    DashboardState,
    MediaInspectorData,
    SystemTelemetry,
    TerminalDashboardLayout,
    render_dashboard,
)
from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    expand_path,
    get_clipboard_files,
    parse_input_paths,
)


# ============================================================================
# Audio Filter Chain Configuration
# ============================================================================

@dataclass
class AudioFilterChain:
    """
    Modular audio filter chain parameters for FFmpeg processing.
    Supports volume booster, EQ bass boost, vocal clarity, highpass rumble cut,
    tempo/speed without pitch shift, and EBU R128 loudness target.
    """
    volume_db: float = 0.0
    eq_mode: str = "none"  # "none", "bass_boost", "vocal_clarity", "both"
    bass_gain_db: float = 4.0
    vocal_gain_db: float = 3.5
    highpass_hz: int = 0  # 0 = disabled, e.g. 80, 100, 120
    tempo: float = 1.0  # 0.5x to 2.0x without pitch shift
    loudness_target_lufs: Optional[float] = None  # e.g. -14.0, -16.0, -23.0

    def to_ffmpeg_filter(self) -> str:
        """Constructs comma-separated FFmpeg -af filter string."""
        filters: List[str] = []

        # 1. Highpass rumble cut first (clean up mic/sub-bass rumble before other processing)
        if self.highpass_hz > 0:
            filters.append(f"highpass=f={self.highpass_hz}")

        # 2. EQ modes
        if self.eq_mode in ("bass_boost", "both"):
            filters.append(f"bass=g={self.bass_gain_db:.1f}:f=100")
        if self.eq_mode in ("vocal_clarity", "both"):
            filters.append(f"equalizer=f=3000:t=q:w=1.5:g={self.vocal_gain_db:.1f}")

        # 3. Volume Booster (dB)
        if self.volume_db != 0.0:
            filters.append(f"volume={self.volume_db:+.1f}dB")

        # 4. Tempo / Speed adjustment without pitch change
        if self.tempo != 1.0:
            clamped = max(0.5, min(2.0, self.tempo))
            filters.append(f"atempo={clamped:.2f}")

        # 5. EBU R128 integrated loudness target
        if self.loudness_target_lufs is not None:
            filters.append(f"loudnorm=I={self.loudness_target_lufs:.1f}:TP=-1.0:LRA=11")

        return ",".join(filters)

    def get_summary(self) -> str:
        """Returns clean human-readable summary of active filters."""
        parts: List[str] = []
        if self.volume_db != 0.0:
            parts.append(f"Vol: {self.volume_db:+.1f}dB")
        if self.eq_mode != "none":
            eq_title = self.eq_mode.replace("_", " ").title()
            parts.append(f"EQ: {eq_title}")
        if self.highpass_hz > 0:
            parts.append(f"Cut: <{self.highpass_hz}Hz")
        if self.tempo != 1.0:
            parts.append(f"Speed: {self.tempo:.2f}x")
        if self.loudness_target_lufs is not None:
            parts.append(f"Loudness: {self.loudness_target_lufs:.0f}LUFS")

        return " | ".join(parts) if parts else "Bypass (No Filters)"

    def is_active(self) -> bool:
        """Indicates whether any audio filter is enabled."""
        return bool(
            self.volume_db != 0.0
            or self.eq_mode != "none"
            or self.highpass_hz > 0
            or self.tempo != 1.0
            or self.loudness_target_lufs is not None
        )

    def reset(self) -> None:
        """Resets all filters to bypass state."""
        self.volume_db = 0.0
        self.eq_mode = "none"
        self.bass_gain_db = 4.0
        self.vocal_gain_db = 3.5
        self.highpass_hz = 0
        self.tempo = 1.0
        self.loudness_target_lufs = None


# ============================================================================
# Preset Model & Standard Presets Library
# ============================================================================

class CliPreset:
    """Audio configuration preset model for CLI."""

    def __init__(
        self,
        name: str,
        target_format: str,
        description: str,
        options: Optional[Dict[str, Any]] = None,
        default_filter: Optional[AudioFilterChain] = None,
    ):
        self.name = name
        self.target_format = target_format
        self.description = description
        self.options = options or {}
        self.default_filter = default_filter

    def get_summary(self) -> str:
        opts_desc: List[str] = []
        if "bitrate" in self.options:
            opts_desc.append(str(self.options["bitrate"]))
        if "sample_rate" in self.options:
            opts_desc.append(f"{self.options['sample_rate']}Hz")
        if self.options.get("ebu_r128"):
            opts_desc.append("EBU R128")
        if self.options.get("lossless_copy_if_match"):
            opts_desc.append("Lossless Copy")
        extra = f" ({', '.join(opts_desc)})" if opts_desc else ""
        return f"{self.target_format.upper()}{extra}"


# Standard presets dictionary preserving keys 1-7 for backward compatibility
CLI_PRESETS: Dict[int, CliPreset] = {
    1: CliPreset(
        "MP3 (320kbps High Quality)",
        "mp3",
        "320kbps High Quality Universal MP3 (libmp3lame)",
        {"bitrate": "320k", "preserve_cover_art": True},
    ),
    2: CliPreset(
        "FLAC (Lossless)",
        "flac",
        "Lossless Compression Level 8 (Bit-perfect Studio Quality)",
        {"compression_level": 8, "preserve_cover_art": True, "lossless_copy_if_match": True},
    ),
    3: CliPreset(
        "WAV (Studio Master 24-bit)",
        "wav",
        "Uncompressed PCM 24-bit 96kHz (Bit-perfect reference master)",
        {"codec": "pcm_s24le", "sample_rate": 96000, "preserve_cover_art": True},
    ),
    4: CliPreset(
        "AAC (Streaming -14LUFS)",
        "aac",
        "256kbps High Efficiency AAC normalized to -14 LUFS (Spotify / YouTube)",
        {
            "bitrate": "256k",
            "preserve_cover_art": True,
            "audio_filter": "loudnorm=I=-14:TP=-1.0:LRA=11",
        },
    ),
    5: CliPreset(
        "OPUS (128kbps Low Latency)",
        "opus",
        "128kbps Low Latency / Ultra High Efficiency OPUS (48kHz)",
        {"bitrate": "128k", "sample_rate": 48000},
    ),
    6: CliPreset(
        "OGG Vorbis",
        "ogg",
        "192kbps High Quality Ogg Vorbis",
        {"bitrate": "192k"},
    ),
    7: CliPreset(
        "M4A (Apple Audio AAC)",
        "m4a",
        "256kbps Apple Audio Container (AAC / ALAC)",
        {"bitrate": "256k", "preserve_cover_art": True},
    ),
}

# Named enterprise presets catalog
ENTERPRISE_PRESETS: Dict[str, CliPreset] = {
    "studio_master": CliPreset(
        "Studio Master (WAV 24-bit)",
        "wav",
        "24-bit 96kHz PCM Uncompressed (Studio Reference Master)",
        {"codec": "pcm_s24le", "sample_rate": 96000, "preserve_cover_art": True},
    ),
    "audiophile": CliPreset(
        "Audiophile Hi-Fi (FLAC 24-bit)",
        "flac",
        "Lossless FLAC Level 8 with Embedded Artwork & Stream Copy",
        {"compression_level": 8, "preserve_cover_art": True, "lossless_copy_if_match": True},
    ),
    "podcast": CliPreset(
        "Podcast Enhancer (MP3 192k)",
        "mp3",
        "Speech clarity (Rumble cut <80Hz, Vocal EQ boost, EBU R128 -16LUFS)",
        {
            "bitrate": "192k",
            "ebu_r128": True,
            "audio_filter": "highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=3.5",
            "preserve_cover_art": True,
        },
        default_filter=AudioFilterChain(
            highpass_hz=80,
            eq_mode="vocal_clarity",
            vocal_gain_db=3.5,
            loudness_target_lufs=-16.0,
        ),
    ),
    "streaming": CliPreset(
        "Streaming -14LUFS (AAC 256k)",
        "aac",
        "Normalized for Spotify, YouTube, Apple Music (-14 LUFS target)",
        {
            "bitrate": "256k",
            "audio_filter": "loudnorm=I=-14:TP=-1.0:LRA=11",
            "preserve_cover_art": True,
        },
        default_filter=AudioFilterChain(loudness_target_lufs=-14.0),
    ),
    "flac_lossless": CliPreset(
        "FLAC Lossless (Level 5)",
        "flac",
        "Fast lossless encoding with automatic stream copy",
        {"compression_level": 5, "preserve_cover_art": True, "lossless_copy_if_match": True},
    ),
    "opus": CliPreset(
        "OPUS (128kbps Low Latency)",
        "opus",
        "Modern ultra-efficient OPUS container (128kbps 48kHz)",
        {"bitrate": "128k", "sample_rate": 48000},
    ),
    "mp3_hq": CliPreset(
        "MP3 (320kbps High Quality)",
        "mp3",
        "Universal 320kbps MP3 (libmp3lame, max compatibility)",
        {"bitrate": "320k", "preserve_cover_art": True},
    ),
}


# ============================================================================
# CliTui: Main Dual-Mode Controller & Input Orchestrator
# ============================================================================

class CliTui:
    """
    World-class, interactive terminal user interface controller.
    Supports Dual-Mode operation:
    - Mode 1: Live Interactive Dashboard (rich.live.Live with real-time VU meter,
      7-band dynamic audio spectrum, task queue table, hardware telemetry,
      media stream inspector card, scrolling event log stream, and hotkey listeners).
    - Mode 2: Interactive Command / Wizard Mode (for configuring presets, audio filters,
      inspecting media streams, folders, and exporting reports).
    """

    def __init__(self, initial_paths: Optional[List[Path]] = None):
        self.console = Console(legacy_windows=False)
        self.dashboard_renderer = TerminalDashboardLayout()

        self.current_preset: CliPreset = CLI_PRESETS[1]
        self.filter_chain = AudioFilterChain()
        self.output_dir: Optional[Path] = None
        self.staged_files: List[Path] = []
        self.tasks: List[ConversionTask] = []
        self.file_stream_overrides: Dict[Path, int] = {}
        self.session_tasks: List[ConversionTask] = []
        self.events: List[DashboardEvent] = []
        self.inspector_data: Optional[MediaInspectorData] = None
        self.selected_index: int = 0

        self.is_converting: bool = False
        self.current_speed: float = 0.0
        self.current_progress: float = 0.0

        self._queue_lock = threading.Lock()
        self.clipboard_watcher_active: bool = False
        self._watcher_thread: Optional[threading.Thread] = None
        self._watcher_stop_event = threading.Event()

        self.interactive: bool = sys.stdin.isatty()

        # Log system boot
        self.log_event("Transcoder TUI Engine initialized.", "LOG")
        try:
            ffmpeg_path = find_ffmpeg()
            ver = get_ffmpeg_version(ffmpeg_path) or "Available"
            short_ver = ver.splitlines()[0] if ver else "Available"
            self.log_event(f"FFmpeg Engine: {short_ver[:35]}", "LOG")
        except Exception:
            self.log_event("FFmpeg: Not detected in environment PATH", "WARN")

        if initial_paths:
            self._stage_paths(initial_paths)

    def log_event(self, message: str, tag: str = "LOG") -> None:
        """Appends a new event to the dashboard event stream."""
        ev = DashboardEvent(timestamp=time.time(), tag=tag.upper(), message=message)
        self.events.append(ev)
        if len(self.events) > 80:
            self.events = self.events[-80:]

    def get_dashboard_state(self) -> DashboardState:
        """Constructs a thread-safe snapshot of the current application state."""
        with self._queue_lock:
            # Build list of display tasks
            display_tasks: List[Any] = []
            if self.is_converting and self.tasks:
                display_tasks = list(self.tasks)
            else:
                for idx, p in enumerate(self.staged_files):
                    # Represent staged files as task-like display objects
                    task_item = ConversionTask(
                        task_id=f"staged_{idx+1}",
                        source_file=p,
                        target_format=self.current_preset.target_format,
                        status=TaskStatus.PENDING,
                        options=self.current_preset.options,
                    )
                    display_tasks.append(task_item)

            events_snapshot = list(self.events)

        active_workers = 1 if self.is_converting else 0
        system_snapshot = SystemTelemetry.sample(active_workers=active_workers, max_workers=4)

        preset_str = f"{self.current_preset.name} [{self.current_preset.get_summary()}]"
        out_str = str(self.output_dir) if self.output_dir else "Source File Directory (In-Place)"

        return DashboardState(
            system=system_snapshot,
            tasks=display_tasks,
            selected_index=self.selected_index,
            inspector_data=self.inspector_data,
            events=events_snapshot,
            active_preset_name=preset_str,
            output_dir_str=out_str,
            is_transcoding=self.is_converting,
            active_speed=self.current_speed,
            active_progress=self.current_progress,
            time_seconds=time.time(),
            safe_ascii=self.dashboard_renderer.safe_ascii,
            is_watching=self.clipboard_watcher_active,
        )

    # -------------------------------------------------------------------------
    # Main Lifecycle & Dual-Mode Orchestration
    # -------------------------------------------------------------------------
    def run(self, single_iteration: bool = False) -> None:
        """
        Main TUI loop. In interactive consoles, runs Mode 1 (Live Dashboard)
        with non-blocking hotkey detection, seamlessly branching to Mode 2 wizards.
        """
        try:
            if not self.interactive or single_iteration:
                # Non-interactive / pipe / unittest mode
                self._run_prompt_loop(single_iteration=single_iteration)
            else:
                self._run_live_dashboard_loop()
        finally:
            self._stop_clipboard_watcher()

    def _run_live_dashboard_loop(self) -> None:
        """
        Mode 1: Full-screen Live Interactive Dashboard.
        Renders dynamic VU meter, 7-band audio spectrum, task queue, telemetry,
        inspector card, and event log stream, listening for single-key hotkeys.
        """
        refresh_fps = 8
        sleep_interval = 1.0 / refresh_fps

        initial_state = self.get_dashboard_state()
        term_width = self.console.width or 100

        with Live(
            render_dashboard(initial_state, terminal_width=term_width),
            console=self.console,
            refresh_per_second=refresh_fps,
            screen=False,
            transient=True,
        ) as live:
            while True:
                # Re-render live view
                state = self.get_dashboard_state()
                term_width = self.console.width or 100
                live.update(render_dashboard(state, terminal_width=term_width))

                # Check non-blocking hotkey
                key = self._read_nonblocking_key(timeout_sec=sleep_interval)
                if not key:
                    continue

                # Process hotkeys: exit live mode cleanly to run interactive wizard
                if key in ("q", "x"):
                    self.log_event("User requested application shutdown.", "LOG")
                    break

                elif key in ("a", "+"):
                    live.stop()
                    self.handle_add_paths()
                    live.start()

                elif key == "v":
                    # Instant clipboard extraction
                    self.handle_paste_clipboard()

                elif key == "p":
                    live.stop()
                    self.handle_preset_menu()
                    live.start()

                elif key == "f":
                    live.stop()
                    self.handle_filter_wizard()
                    live.start()

                elif key == "i":
                    live.stop()
                    self.handle_inspect_media()
                    live.start()

                elif key == "s":
                    live.stop()
                    self.handle_start_conversion()
                    live.start()

                elif key == "w":
                    self.handle_toggle_clipboard_watcher()

                elif key == "r":
                    live.stop()
                    self.handle_report_modal()
                    live.start()

                elif key == "o":
                    live.stop()
                    self.handle_set_output_dir()
                    live.start()

                elif key == "c":
                    self.handle_clear_queue()

                elif key in ("h", "?"):
                    live.stop()
                    self.print_help()
                    Prompt.ask("\n[dim]Press Enter to return to Dashboard[/dim]", default="")
                    live.start()

                elif key in ("m", "\r", "\n", " "):
                    # Switch to Mode 2 Command Prompt
                    live.stop()
                    self._run_single_command_prompt()
                    live.start()

        self.console.print("[bold yellow]Exited Transcoder TUI. Goodbye![/bold yellow]")

    def _read_nonblocking_key(self, timeout_sec: float = 0.08) -> Optional[str]:
        """Polls for a single keypress on Windows without blocking console output."""
        if sys.platform == "win32" and msvcrt and sys.stdin.isatty():
            start_t = time.monotonic()
            while time.monotonic() - start_t < timeout_sec:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    return ch.lower()
                time.sleep(0.01)
            return None
        else:
            time.sleep(timeout_sec)
            return None

    def _run_prompt_loop(self, single_iteration: bool = False) -> None:
        """Mode 2: Line-based interactive command prompt loop."""
        self.print_banner()
        self.print_system_status()

        if single_iteration:
            return

        while True:
            try:
                self.console.print()
                self._print_queue_summary_short()
                prompt_text = (
                    "[bold cyan]Transcoder[/bold cyan] "
                    "[dim](a:add, v:paste, p:preset, f:filter, i:inspect, s:start, w:watch, r:report, o:out, c:clear, q:quit)[/dim]\n"
                    "[bold green]>[/bold green] "
                )
                user_input = Prompt.ask(prompt_text).strip()
                if not user_input:
                    continue

                should_exit = self.dispatch_command(user_input)
                if should_exit:
                    break

            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Interrupted by user. Exiting...[/yellow]")
                break
            except Exception as e:
                self.console.print(f"[bold red]Error: {e}[/bold red]")
                if single_iteration:
                    break

    def _run_single_command_prompt(self) -> None:
        """Executes a single interactive command from Mode 1."""
        self.console.print()
        prompt_text = "[bold magenta]Command Mode[/bold magenta] [dim]('add', 'preset', 'filter', 'inspect', 'start', 'help', etc.)[/dim]\n[bold green]>[/bold green] "
        user_input = Prompt.ask(prompt_text).strip()
        if user_input:
            self.dispatch_command(user_input)

    def dispatch_command(self, raw_input: str) -> bool:
        """
        Dispatches typed command or hotkey to appropriate handler.
        Returns True if application should quit.
        """
        cmd = raw_input.strip().lower()

        if cmd in ("q", "quit", "exit"):
            self.console.print("[yellow]Exiting Transcoder. Goodbye![/yellow]")
            return True

        elif cmd in ("h", "help", "?"):
            self.print_help()

        elif cmd in ("status", "info"):
            self.print_system_status()

        elif cmd in ("a", "add"):
            self.handle_add_paths()

        elif cmd in ("v", "paste", "p_clip"):
            self.handle_paste_clipboard()

        elif cmd in ("p", "preset", "format"):
            self.handle_preset_menu()

        elif cmd in ("f", "filter", "fx"):
            self.handle_filter_wizard()

        elif cmd in ("i", "inspect", "probe"):
            self.handle_inspect_media()

        elif cmd in ("s", "start", "run"):
            self.handle_start_conversion()

        elif cmd in ("w", "watch"):
            self.handle_toggle_clipboard_watcher()

        elif cmd in ("r", "report", "stats"):
            self.handle_report_modal()

        elif cmd in ("o", "out", "output"):
            self.handle_set_output_dir()

        elif cmd in ("c", "clear"):
            self.handle_clear_queue()

        elif cmd in ("l", "list", "queue"):
            self.print_staged_table()

        elif cmd.startswith("dir") or cmd.startswith("d "):
            self.handle_load_directory(raw_input)

        else:
            # Treat as dropped path(s)
            self.handle_dropped_input(raw_input)

        return False

    # -------------------------------------------------------------------------
    # Command Handlers & Interactive Wizards
    # -------------------------------------------------------------------------
    def handle_add_paths(self, input_str: Optional[str] = None) -> List[Path]:
        """
        'a' or 'add': Drag & drop path prompt.
        Accepts multiple files, folders, quotes, and space-separated paths.
        """
        if input_str is None:
            if not self.interactive:
                return []
            self.console.print("\n[bold cyan]--- Add Video Files to Queue ---[/bold cyan]")
            self.console.print("[dim]Drag & drop files or folders here, or paste paths (press Enter to finish):[/dim]")
            input_str = Prompt.ask("[bold green]Input Path(s)[/bold green]").strip()

        if not input_str:
            return []

        paths = parse_input_paths(input_str, recursive=True)
        if not paths:
            self.console.print(f"[yellow]No supported video files found in: '{input_str}'[/yellow]")
            self.log_event(f"No video files found in: '{input_str[:30]}'", "WARN")
            return []

        added = self._stage_paths(paths)
        return added

    def handle_dropped_input(self, raw_input: str) -> List[Path]:
        """Directly parses dropped text from prompt."""
        paths = parse_input_paths(raw_input, recursive=True)
        if not paths:
            self.console.print(f"[yellow]Could not detect supported video files in input: '{raw_input}'[/yellow]")
            return []
        return self._stage_paths(paths)

    def handle_paste_clipboard(self) -> List[Path]:
        """
        'v' or 'paste': Instant clipboard extraction.
        Reads CF_HDROP Explorer copied files or text path strings.
        """
        self.console.print("[dim]Checking Windows clipboard for video files...[/dim]")
        paths = get_clipboard_files(recursive=True)
        if not paths:
            self.console.print("[yellow]No video files found on clipboard.[/yellow]")
            self.log_event("Clipboard inspected: No video files detected.", "WARN")
            return []

        added = self._stage_paths(paths)
        self.log_event(f"Clipboard extracted {len(added)} video file(s).", "PROBE")
        return added

    def handle_preset_menu(self, choice: Optional[str] = None) -> CliPreset:
        """
        'p' or 'preset': Preset selector.
        Presents Studio Master, Audiophile Hi-Fi, Podcast Enhancer, Streaming -14LUFS,
        FLAC Lossless, OPUS, Custom.
        """
        self.console.print("\n[bold magenta]════════════ AUDIO FORMAT & ENCODING PRESETS ════════════[/bold magenta]")
        table = Table(title="Select Encoding Preset", border_style="magenta", expand=True)
        table.add_column("Key", style="bold yellow", width=5)
        table.add_column("Preset Name", style="bold white", width=26)
        table.add_column("Target", style="bold cyan", width=8)
        table.add_column("Description & Tuning", style="dim white")

        options_map: Dict[str, Tuple[CliPreset, Optional[AudioFilterChain]]] = {
            "1": (
                ENTERPRISE_PRESETS["studio_master"],
                None,
            ),
            "2": (
                ENTERPRISE_PRESETS["audiophile"],
                None,
            ),
            "3": (
                ENTERPRISE_PRESETS["podcast"],
                ENTERPRISE_PRESETS["podcast"].default_filter,
            ),
            "4": (
                ENTERPRISE_PRESETS["streaming"],
                ENTERPRISE_PRESETS["streaming"].default_filter,
            ),
            "5": (
                ENTERPRISE_PRESETS["flac_lossless"],
                None,
            ),
            "6": (
                ENTERPRISE_PRESETS["opus"],
                None,
            ),
            "7": (
                ENTERPRISE_PRESETS["mp3_hq"],
                None,
            ),
        }

        for k, (preset, _) in options_map.items():
            table.add_row(k, preset.name, preset.target_format.upper(), preset.description)
        table.add_row("8", "Custom Configuration...", "CUSTOM", "Fine-tune format, bitrate, channels, loudnorm, filters")

        self.console.print(table)

        if choice is None:
            if not self.interactive:
                return self.current_preset
            choice = Prompt.ask("[bold magenta]Select preset [1-8][/bold magenta]", default="1").strip()

        if choice in options_map:
            sel_preset, def_filt = options_map[choice]
            self.current_preset = sel_preset
            if def_filt is not None:
                self.filter_chain = def_filt
            self.console.print(
                f"[bold green][OK] Active Preset set to:[/bold green] [bold yellow]{self.current_preset.name}[/bold yellow]"
            )
            self.log_event(f"Activated preset: {self.current_preset.name}", "LOG")
        elif choice == "8":
            self._configure_custom_preset()
        else:
            self.console.print("[red]Invalid choice. Preset left unchanged.[/red]")

        return self.current_preset

    def _configure_custom_preset(self) -> None:
        """Interactive custom format and encoding options wizard."""
        self.console.print("\n[bold cyan]--- Custom Audio Configuration Wizard ---[/bold cyan]")
        fmt = Prompt.ask(
            "Target Format",
            choices=["mp3", "flac", "wav", "aac", "opus", "ogg", "m4a"],
            default="mp3",
        )
        options: Dict[str, Any] = {"preserve_cover_art": True}

        if fmt not in ("flac", "wav"):
            bitrate = Prompt.ask("Bitrate", choices=["128k", "192k", "256k", "320k"], default="320k")
            options["bitrate"] = bitrate

        sr_choice = Prompt.ask(
            "Sample Rate",
            choices=["Original", "44100", "48000", "96000"],
            default="Original",
        )
        if sr_choice != "Original":
            options["sample_rate"] = int(sr_choice)

        ch_choice = Prompt.ask(
            "Channels",
            choices=["Original", "Mono", "Stereo", "5.1"],
            default="Original",
        )
        if ch_choice == "Mono":
            options["channels"] = 1
        elif ch_choice == "Stereo":
            options["channels"] = 2
        elif ch_choice == "5.1":
            options["channels"] = 6

        options["ebu_r128"] = Confirm.ask("Apply EBU R128 Loudness Normalization?", default=False)
        options["lossless_copy_if_match"] = Confirm.ask("Enable Lossless Stream Copy (if codec matches)?", default=False)
        options["preserve_cover_art"] = Confirm.ask("Preserve Metadata & Album Artwork?", default=True)

        self.current_preset = CliPreset(
            name=f"Custom ({fmt.upper()})",
            target_format=fmt,
            description="User custom configuration",
            options=options,
        )
        self.console.print(
            f"[bold green][OK] Custom profile activated:[/bold green] [bold yellow]{self.current_preset.get_summary()}[/bold yellow]"
        )
        self.log_event(f"Custom preset activated: {fmt.upper()}", "LOG")

    def handle_filter_wizard(self) -> AudioFilterChain:
        """
        'f' or 'filter': Audio Filter Wizard.
        Configures Volume booster dB, EQ Bass Boost / Vocal Clarity, Highpass rumble cut,
        Tempo/Speed without pitch shift, and EBU R128 loudness target.
        """
        if not self.interactive:
            return self.filter_chain

        while True:
            self.console.print("\n[bold magenta]════════════ AUDIO DSP FILTER CHAIN WIZARD ════════════[/bold magenta]")
            status_table = Table(border_style="magenta", expand=True)
            status_table.add_column("Filter Component", style="bold cyan", width=24)
            status_table.add_column("Current Setting", style="white")

            vol_str = f"{self.filter_chain.volume_db:+.1f} dB" if self.filter_chain.volume_db != 0 else "0.0 dB (Neutral)"
            status_table.add_row("1. Volume Booster", vol_str)

            eq_str = self.filter_chain.eq_mode.replace("_", " ").title()
            if self.filter_chain.eq_mode in ("bass_boost", "both"):
                eq_str += f" [Bass: +{self.filter_chain.bass_gain_db:.1f}dB @ 100Hz]"
            if self.filter_chain.eq_mode in ("vocal_clarity", "both"):
                eq_str += f" [Vocal: +{self.filter_chain.vocal_gain_db:.1f}dB @ 3kHz]"
            status_table.add_row("2. EQ Preset", eq_str)

            hp_str = f"Cut sub-frequencies < {self.filter_chain.highpass_hz} Hz" if self.filter_chain.highpass_hz > 0 else "Disabled (Full spectrum)"
            status_table.add_row("3. Highpass Rumble Cut", hp_str)

            speed_str = f"{self.filter_chain.tempo:.2f}x (Pitch Preserved)" if self.filter_chain.tempo != 1.0 else "1.0x (Original Speed)"
            status_table.add_row("4. Tempo / Playback Speed", speed_str)

            lufs_str = f"{self.filter_chain.loudness_target_lufs:.1f} LUFS" if self.filter_chain.loudness_target_lufs is not None else "Disabled"
            status_table.add_row("5. EBU R128 Loudness Target", lufs_str)

            chain_str = self.filter_chain.to_ffmpeg_filter() or "Bypass (No DSP Filters)"
            status_table.add_row("Active FFmpeg Filter", f"[bold green]{chain_str}[/bold green]")

            self.console.print(status_table)

            self.console.print("[bold yellow]Options:[/bold yellow] [1-5] Edit Filter | [6] Quick Podcast Chain | [7] Quick Bass Boost | [8] Reset (Bypass) | [9/Enter] Apply & Return")
            choice = Prompt.ask("[bold green]Select Option[/bold green]", default="9").strip()

            if choice == "1":
                val = Prompt.ask("Enter volume gain in dB (e.g. +3, -2, 6)", default=str(self.filter_chain.volume_db))
                try:
                    self.filter_chain.volume_db = float(val)
                except ValueError:
                    self.console.print("[red]Invalid number.[/red]")

            elif choice == "2":
                eq_choice = Prompt.ask(
                    "Select EQ Mode",
                    choices=["none", "bass_boost", "vocal_clarity", "both"],
                    default=self.filter_chain.eq_mode,
                )
                self.filter_chain.eq_mode = eq_choice

            elif choice == "3":
                hp_choice = Prompt.ask(
                    "Highpass rumble cutoff frequency (Hz)",
                    choices=["0", "80", "100", "120"],
                    default=str(self.filter_chain.highpass_hz),
                )
                self.filter_chain.highpass_hz = int(hp_choice)

            elif choice == "4":
                tempo_str = Prompt.ask("Playback speed multiplier (0.5 to 2.0)", default=f"{self.filter_chain.tempo:.2f}")
                try:
                    t = float(tempo_str)
                    self.filter_chain.tempo = max(0.5, min(2.0, t))
                except ValueError:
                    self.console.print("[red]Invalid speed multiplier.[/red]")

            elif choice == "5":
                lufs_choice = Prompt.ask(
                    "Loudness Target",
                    choices=["off", "-14", "-16", "-23"],
                    default="-14" if self.filter_chain.loudness_target_lufs == -14 else "off",
                )
                self.filter_chain.loudness_target_lufs = None if lufs_choice == "off" else float(lufs_choice)

            elif choice == "6":
                # Quick Podcast Chain
                self.filter_chain.highpass_hz = 80
                self.filter_chain.eq_mode = "vocal_clarity"
                self.filter_chain.vocal_gain_db = 3.5
                self.filter_chain.loudness_target_lufs = -16.0
                self.console.print("[green][OK] Applied Podcast Voice clarity profile.[/green]")

            elif choice == "7":
                # Quick Bass Boost
                self.filter_chain.eq_mode = "bass_boost"
                self.filter_chain.bass_gain_db = 5.0
                self.console.print("[green][OK] Applied Bass Boost profile.[/green]")

            elif choice == "8":
                self.filter_chain.reset()
                self.console.print("[green][OK] All filters reset to bypass.[/green]")

            elif choice in ("9", "done", "save", ""):
                break

        summary = self.filter_chain.get_summary()
        self.console.print(f"[bold green][OK] Filter chain active:[/bold green] [bold magenta]{summary}[/bold magenta]")
        self.log_event(f"DSP Filter chain updated: {summary}", "LOG")
        return self.filter_chain

    def handle_inspect_media(self, file_idx: Optional[int] = None) -> Optional[MediaProbeResult]:
        """
        'i' or 'inspect': Detailed Media Stream Inspector for any queued video.
        Lists all audio streams, languages, video specs, and metadata.
        """
        with self._queue_lock:
            staged = list(self.staged_files)

        target_file: Optional[Path] = None

        if not staged:
            self.console.print("[yellow]No files in queue to inspect.[/yellow]")
            if not self.interactive:
                return None
            p_in = Prompt.ask("Enter or drag video file path to inspect (or leave empty to cancel)").strip()
            if not p_in:
                return None
            target_path = Path(p_in.strip('"').strip("'")).expanduser().resolve()
            if not target_path.is_file():
                self.console.print(f"[red]File not found: {target_path}[/red]")
                return None
            target_file = target_path
        elif len(staged) == 1:
            target_file = staged[0]
            self.selected_index = 0
        else:
            if file_idx is None:
                if not self.interactive:
                    target_file = staged[0]
                    self.selected_index = 0
                else:
                    self.console.print("\n[bold cyan]Select file from queue to inspect:[/bold cyan]")
                    for i, p in enumerate(staged, 1):
                        self.console.print(f"  [bold green][{i}][/bold green] {p.name}")
                    choice = Prompt.ask("Enter item number [1-{}]".format(len(staged)), default="1").strip()
                    try:
                        idx = int(choice) - 1
                        target_file = staged[idx]
                        self.selected_index = idx
                    except (ValueError, IndexError):
                        self.console.print("[red]Invalid index.[/red]")
                        return None
            else:
                target_file = staged[file_idx]
                self.selected_index = file_idx

        self.console.print(f"\n[dim]Probing media specs for: {target_file.name}...[/dim]")
        try:
            probe = probe_media(target_file)
        except Exception as exc:
            self.console.print(f"[bold red]Probe failed:[/bold red] {exc}")
            self.log_event(f"Probe failed on {target_file.name}: {exc}", "ERROR")
            return None

        # Build inspector telemetry card for dashboard
        self.inspector_data = MediaInspectorData(
            filename=target_file.name,
            file_path=target_file,
            container_format=probe.format_name.upper(),
            file_size_str=format_bytes_human(probe.size_bytes),
            duration_str=format_duration_human(probe.duration),
            target_preset=self.current_preset.name,
            target_format=self.current_preset.target_format.upper(),
        )

        if probe.video_streams:
            v0 = probe.video_streams[0]
            self.inspector_data.video_codec = v0.codec_name.upper()
            if v0.width and v0.height:
                self.inspector_data.video_resolution = f"{v0.width}x{v0.height}"
            if v0.fps:
                self.inspector_data.video_fps_str = f"{v0.fps:.2f} fps"

        if probe.audio_streams:
            a0 = probe.primary_audio_stream or probe.audio_streams[0]
            self.inspector_data.audio_codec = a0.codec_name.upper()
            self.inspector_data.audio_sample_rate = f"{a0.sample_rate:,} Hz"
            ch_label = "Mono" if a0.channels == 1 else ("Stereo" if a0.channels == 2 else f"{a0.channels}ch")
            if a0.channel_layout:
                ch_label += f" ({a0.channel_layout})"
            self.inspector_data.audio_channels = ch_label
            self.inspector_data.audio_bit_rate = f"{a0.bit_rate // 1000} kbps" if a0.bit_rate else "VBR/N/A"
            self.inspector_data.audio_title = a0.title or a0.language or "Track 1"

        if probe.has_cover_art:
            self.inspector_data.has_cover_art = True
            self.inspector_data.cover_art_details = "[YES] Embedded Artwork"

        # Display Container Overview
        overview_table = Table(title=f"Container Telemetry: {target_file.name}", border_style="cyan", expand=True)
        overview_table.add_column("Property", style="bold cyan", width=18)
        overview_table.add_column("Specification", style="white")

        overview_table.add_row("Format / Container", f"{probe.format_name.upper()} ({probe.format_long_name})")
        overview_table.add_row("Duration", format_duration_human(probe.duration))
        overview_table.add_row("File Size", format_bytes_human(probe.size_bytes))
        total_br = f"{probe.bit_rate // 1000} kbps" if probe.bit_rate else "Variable / N/A"
        overview_table.add_row("Overall Bitrate", total_br)
        overview_table.add_row("Embedded Cover Art", "[bold green]YES[/bold green]" if probe.has_cover_art else "[dim]NO[/dim]")
        self.console.print(overview_table)

        # Video Streams
        if probe.video_streams:
            vtable = Table(title="Video Stream(s)", border_style="dim blue", expand=True)
            vtable.add_column("Stream #", justify="center", width=8)
            vtable.add_column("Codec", style="bold white", width=12)
            vtable.add_column("Resolution", width=14)
            vtable.add_column("Framerate", width=10)
            vtable.add_column("Type", width=14)

            for v in probe.video_streams:
                res_str = f"{v.width}x{v.height}" if v.width and v.height else "N/A"
                fps_str = f"{v.fps:.1f} fps" if v.fps else "N/A"
                type_str = "Attached Pic" if v.is_attached_pic else "Main Video"
                vtable.add_row(str(v.index), v.codec_name.upper(), res_str, fps_str, type_str)
            self.console.print(vtable)

        # Audio Streams
        atable = Table(title=f"Audio Stream(s) ({len(probe.audio_streams)} detected)", border_style="bright_blue", expand=True)
        atable.add_column("Stream #", justify="center", width=8)
        atable.add_column("Codec", style="bold white", width=10)
        atable.add_column("Channels", width=12)
        atable.add_column("Sample Rate", width=12)
        atable.add_column("Bitrate", width=12)
        atable.add_column("Language", width=10)
        atable.add_column("Title / Disposition", style="dim")

        for a in probe.audio_streams:
            ch_str = f"{a.channels}ch ({a.channel_layout or 'stereo'})"
            sr_str = f"{a.sample_rate:,} Hz"
            br_str = f"{a.bit_rate // 1000} kbps" if a.bit_rate else "VBR/N/A"
            lang_str = a.language or "und"
            def_badge = "[bold green][DEFAULT][/bold green] " if a.is_default else ""
            title_str = f"{def_badge}{a.title or ''}"
            atable.add_row(str(a.index), a.codec_name.upper(), ch_str, sr_str, br_str, lang_str, title_str)

        self.console.print(atable)

        # Allow selecting non-default audio stream
        if self.interactive and len(probe.audio_streams) > 1:
            sel = Prompt.ask(
                "Select audio stream index to extract for this file (or press Enter for default primary)",
                default="",
            ).strip()
            if sel.isdigit():
                chosen_idx = int(sel)
                self.file_stream_overrides[target_file] = chosen_idx
                self.console.print(f"[green][OK] Stream 0:{chosen_idx} will be extracted for this file.[/green]")

        self.log_event(f"Inspected media streams for: {target_file.name}", "PROBE")
        return probe

    def handle_toggle_clipboard_watcher(self) -> bool:
        """
        'w' or 'watch': Toggle background clipboard watcher.
        Auto-stages video files copied in Windows Explorer.
        """
        if self.clipboard_watcher_active:
            self._stop_clipboard_watcher()
            self.console.print("[bold yellow]Clipboard watcher stopped.[/bold yellow]")
            self.log_event("Clipboard Watcher disabled.", "LOG")
            return False
        else:
            self._start_clipboard_watcher()
            self.console.print("[bold green]Clipboard watcher started. Copy videos in Explorer to stage them automatically.[/bold green]")
            self.log_event("Clipboard Watcher active (Auto-staging Explorer copies).", "LOG")
            return True

    def _start_clipboard_watcher(self) -> None:
        """Starts background clipboard polling thread."""
        self._watcher_stop_event.clear()
        self.clipboard_watcher_active = True
        self._watcher_thread = threading.Thread(
            target=self._clipboard_watcher_loop,
            daemon=True,
            name="ClipboardWatcherThread",
        )
        self._watcher_thread.start()

    def _stop_clipboard_watcher(self) -> None:
        """Gracefully stops background clipboard polling thread."""
        self.clipboard_watcher_active = False
        self._watcher_stop_event.set()
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=0.5)
        self._watcher_thread = None

    def _clipboard_watcher_loop(self) -> None:
        """Background thread polling clipboard for copied video files."""
        while not self._watcher_stop_event.is_set():
            try:
                paths = get_clipboard_files(recursive=False)
                if paths:
                    with self._queue_lock:
                        new_to_stage = [p for p in paths if p not in self.staged_files]
                    if new_to_stage:
                        added = self._stage_paths(new_to_stage, verbose=False)
                        if added:
                            names = ", ".join(p.name for p in added[:2])
                            if len(added) > 2:
                                names += f" (+{len(added) - 2} more)"
                            self.log_event(f"Clipboard watcher auto-staged: {names}", "LOG")
            except Exception:
                pass
            time.sleep(1.0)

    def handle_start_conversion(self) -> List[ConversionTask]:
        """
        's' or 'start': Runs batch conversion with real-time telemetry,
        live dynamic VU meter animations, and multi-bar progress.
        """
        with self._queue_lock:
            tasks_to_process = list(self.staged_files)

        if not tasks_to_process:
            self.console.print("[yellow]No files queued for conversion. Add video files first (press 'a' or 'v').[/yellow]")
            self.log_event("Conversion start skipped: Queue is empty.", "WARN")
            return []

        count = len(tasks_to_process)
        self.console.print(f"\n[bold green]Starting conversion of {count} file(s)...[/bold green]")
        self.console.print("[dim]Press Ctrl+C to cancel active conversions.[/dim]\n")

        self.log_event(f"Batch transcoding started ({count} files).", "TRANSCODE")
        self.is_converting = True

        qm = QueueManager(default_output_dir=self.output_dir)
        qm.pause_queue()

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=32),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("[yellow]{task.fields[speed]}"),
            console=self.console,
            transient=False,
        )

        overall_task = progress.add_task("[bold cyan]Batch Total Progress", total=count, speed="")
        task_progress_ids: Dict[str, TaskID] = {}
        completed_count = 0

        def on_started(task: ConversionTask) -> None:
            desc = f"[green]{task.source_file.name[:26]}"
            tid = progress.add_task(desc, total=100.0, speed="0.0x")
            task_progress_ids[task.task_id] = tid
            self.log_event(f"Started: {task.source_file.name}", "TRANSCODE")

        def on_prog(task: ConversionTask, snap: ProgressSnapshot) -> None:
            tid = task_progress_ids.get(task.task_id)
            if tid is not None:
                progress.update(tid, completed=snap.percent, speed=snap.speed_str)
            try:
                self.current_speed = float(snap.speed_str.replace("x", ""))
            except Exception:
                self.current_speed = 1.0
            self.current_progress = snap.percent

        def on_completed(task: ConversionTask, ver: VerificationResult) -> None:
            nonlocal completed_count
            completed_count += 1
            progress.update(overall_task, completed=completed_count)
            tid = task_progress_ids.get(task.task_id)
            if tid is not None:
                progress.update(
                    tid,
                    description=f"[green][OK] {task.source_file.name[:26]}",
                    completed=100.0,
                    speed=task.speed,
                )
            self.log_event(f"Completed: {task.source_file.name} ({task.speed})", "VERIFY")

        def on_failed(task: ConversionTask, err: str) -> None:
            nonlocal completed_count
            completed_count += 1
            progress.update(overall_task, completed=completed_count)
            tid = task_progress_ids.get(task.task_id)
            if tid is not None:
                progress.update(
                    tid,
                    description=f"[red][X] {task.source_file.name[:26]}",
                    completed=100.0,
                    speed="Err",
                )
            self.log_event(f"Failed: {task.source_file.name} - {err[:30]}", "ERROR")

        qm.on_task_started = on_started
        qm.on_task_progress = on_prog
        qm.on_task_completed = on_completed
        qm.on_task_failed = on_failed

        # Prepare task options
        fmt = self.current_preset.target_format
        base_opts = dict(self.current_preset.options)

        # Merge AudioFilterChain if active
        if self.filter_chain.is_active():
            dsp_filter = self.filter_chain.to_ffmpeg_filter()
            if dsp_filter:
                base_opts["audio_filter"] = dsp_filter
            if self.filter_chain.loudness_target_lufs is not None:
                base_opts["ebu_r128"] = True

        created_tasks: List[ConversionTask] = []
        for src in tasks_to_process:
            dest_file = None
            if self.output_dir:
                dest_file = self.output_dir / f"{src.stem}.{fmt}"

            task_opts = dict(base_opts)
            if src in self.file_stream_overrides:
                task_opts["audio_stream_index"] = self.file_stream_overrides[src]

            t = qm.add_task(
                source_file=src,
                target_format=fmt,
                output_file=dest_file,
                options=task_opts,
            )
            created_tasks.append(t)

        self.tasks = created_tasks
        qm.resume_queue()

        with progress:
            try:
                while True:
                    stats = qm.get_stats()
                    if not stats.is_running and stats.pending_tasks == 0 and stats.active_tasks == 0:
                        break
                    time.sleep(0.08)
            except KeyboardInterrupt:
                self.console.print("\n[bold red]Cancelling active conversion...[/bold red]")
                qm.clear_queue(cancel_active=True)
                self.log_event("Conversion cancelled by user.", "WARN")
                time.sleep(0.4)

        qm.shutdown(wait=True)
        self.is_converting = False
        self.current_speed = 0.0
        self.current_progress = 0.0

        all_tasks = qm.get_all_tasks()
        self.session_tasks.extend(all_tasks)
        self._display_summary_table(all_tasks)

        # Clear processed files from queue
        with self._queue_lock:
            self.staged_files.clear()
            self.tasks.clear()
            self.file_stream_overrides.clear()

        self.log_event(f"Batch finished: {completed_count}/{count} successful.", "TRANSCODE")
        return all_tasks

    def handle_report_modal(self, export_format: Optional[str] = None) -> Optional[Path]:
        """
        'r' or 'report': Summary statistics modal & export to JSON/CSV/TXT.
        """
        if not self.session_tasks:
            self.console.print("[yellow]No tasks have been completed in this session yet.[/yellow]")
            return None

        rep = create_report_from_tasks(self.session_tasks)

        # Display Metrics Panel
        self.console.print("\n[bold cyan]════════════ BATCH CONVERSION ANALYTICS ════════════[/bold cyan]")
        table = Table(border_style="cyan", expand=True)
        table.add_column("Metric", style="bold cyan", width=22)
        table.add_column("Value", style="bold white")

        table.add_row("Total Tasks", str(rep.total_tasks))
        table.add_row(
            "Success Rate",
            f"[bold green]{rep.successful_tasks}[/bold green] succeeded / [bold red]{rep.failed_tasks}[/bold red] failed ({rep.success_rate_percent:.1f}%)",
        )
        saved_str = format_bytes_human(rep.total_space_saved_bytes)
        table.add_row("Total Space Saved", f"{saved_str} ({rep.space_saved_percent:.1f}% ratio)")
        table.add_row("Total Input Size", format_bytes_human(rep.total_input_bytes))
        table.add_row("Total Output Size", format_bytes_human(rep.total_output_bytes))
        table.add_row("Total Wall Time", format_duration_human(rep.total_wall_time_seconds))
        self.console.print(table)

        if export_format is None:
            if not self.interactive:
                return None
            self.console.print("[bold yellow]Export format:[/bold yellow] [j] JSON | [c] CSV | [t] Plain Text | [Enter to skip]")
            export_format = Prompt.ask("[bold green]Export format[/bold green]", default="").strip().lower()

        if not export_format:
            return None

        export_dir = self.output_dir or Path("output").resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        t_stamp = time.strftime("%Y%m%d_%H%M%S")

        out_path: Optional[Path] = None
        if export_format in ("j", "json"):
            out_path = export_dir / f"transcode_report_{t_stamp}.json"
            rep.export_json(out_path)
        elif export_format in ("c", "csv"):
            out_path = export_dir / f"transcode_report_{t_stamp}.csv"
            rep.export_csv(out_path)
        elif export_format in ("t", "txt", "text"):
            out_path = export_dir / f"transcode_report_{t_stamp}.txt"
            out_path.write_text(rep.format_summary_table(), encoding="utf-8")

        if out_path:
            self.console.print(f"[bold green][OK] Report exported successfully:[/bold green] {out_path}")
            self.log_event(f"Exported report to: {out_path.name}", "LOG")

        return out_path

    def handle_set_output_dir(self, new_dir: Optional[str] = None) -> Optional[Path]:
        """'o' or 'out': Set destination folder."""
        current_str = str(self.output_dir) if self.output_dir else "Source File Directory (In-Place)"
        self.console.print(f"Current destination: [bold cyan]{current_str}[/bold cyan]")

        if new_dir is None:
            if not self.interactive:
                return self.output_dir
            new_dir = Prompt.ask(
                "Enter new output folder path (or leave empty to reset to source folder)"
            ).strip()

        if not new_dir:
            self.output_dir = None
            self.console.print("[green]Reset destination to source file directory.[/green]")
            self.log_event("Output destination reset to source file directory.", "LOG")
        else:
            p = Path(new_dir.strip('"').strip("'")).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self.output_dir = p
            self.console.print(f"[green][OK] Destination set to: [bold]{p}[/bold][/green]")
            self.log_event(f"Destination folder set: {p.name}", "LOG")

        return self.output_dir

    def handle_clear_queue(self) -> None:
        """'c' or 'clear': Clear queue."""
        with self._queue_lock:
            count = len(self.staged_files)
            self.staged_files.clear()
            self.tasks.clear()
            self.file_stream_overrides.clear()
        self.console.print("[bold red]Queue cleared.[/bold red]")
        self.log_event(f"Queue cleared ({count} items removed).", "LOG")

    def handle_load_directory(self, raw_cmd: str) -> List[Path]:
        """Prompts for or extracts folder path and recursively expands videos."""
        parts = raw_cmd.split(maxsplit=1)
        if len(parts) > 1:
            dir_str = parts[1]
        else:
            dir_str = Prompt.ask("Enter directory path to scan").strip()

        if not dir_str:
            return []

        clean_path = dir_str.strip('"').strip("'")
        p = Path(clean_path).expanduser().resolve()
        if not p.is_dir():
            self.console.print(f"[red]Directory not found: {p}[/red]")
            return []

        self.console.print(f"[dim]Scanning directory: {p}...[/dim]")
        paths = expand_path(p, recursive=True)
        if not paths:
            self.console.print(f"[yellow]No supported video files found in {p}[/yellow]")
            return []

        return self._stage_paths(paths)

    # -------------------------------------------------------------------------
    # Queue Management & Visual Table Helpers
    # -------------------------------------------------------------------------
    def _stage_paths(self, paths: List[Path], verbose: bool = True) -> List[Path]:
        """Deduplicates and adds paths to staged queue."""
        added: List[Path] = []
        with self._queue_lock:
            for p in paths:
                if p not in self.staged_files and p.is_file():
                    self.staged_files.append(p)
                    added.append(p)

        if not added:
            if verbose:
                self.console.print("[dim]Files are already staged or invalid.[/dim]")
            return []

        if verbose:
            self.console.print(f"[bold green][OK] Added {len(added)} file(s) to queue.[/bold green]")
            table = Table(title=f"Newly Queued ({len(added)} items)", border_style="green")
            table.add_column("#", justify="right", width=4)
            table.add_column("Filename", style="bold white")
            table.add_column("Format", style="cyan", width=8)
            table.add_column("Size", justify="right", width=12)

            for i, p in enumerate(added, 1):
                try:
                    sz_str = format_bytes_human(p.stat().st_size)
                except Exception:
                    sz_str = "Unknown"
                table.add_row(str(i), p.name, p.suffix.lstrip(".").upper(), sz_str)

            self.console.print(table)

        self.log_event(f"Added {len(added)} file(s) to queue.", "LOG")
        return added

    def _print_queue_summary_short(self) -> None:
        with self._queue_lock:
            count = len(self.staged_files)
        status_text = Text()
        status_text.append("Queue: ", style="bold")
        status_text.append(f"{count} files staged", style="cyan" if count else "dim")
        status_text.append(" | Target: ")
        status_text.append(self.current_preset.name, style="bold yellow")
        status_text.append(" | Filters: ")
        status_text.append(self.filter_chain.get_summary(), style="bold magenta")
        self.console.print(status_text)

    def print_staged_table(self) -> None:
        """Displays complete staged files table."""
        with self._queue_lock:
            staged = list(self.staged_files)

        if not staged:
            self.console.print("[dim]Queue is currently empty.[/dim]")
            return

        table = Table(title=f"Staged Video Files ({len(staged)} items)", border_style="cyan")
        table.add_column("#", width=4, justify="right")
        table.add_column("File Name", style="bold white")
        table.add_column("Format", style="cyan", width=8)
        table.add_column("Size", justify="right", width=12)
        table.add_column("Path", style="dim", overflow="fold")

        for i, p in enumerate(staged, 1):
            sz = format_bytes_human(p.stat().st_size) if p.exists() else "-"
            table.add_row(str(i), p.name, p.suffix.lstrip(".").upper(), sz, str(p.parent))

        self.console.print(table)

    def _display_summary_table(self, tasks: List[ConversionTask]) -> None:
        """Displays completion summary table with verification status."""
        if not tasks:
            return

        table = Table(title="Batch Conversion & Verification Summary", border_style="green", expand=True)
        table.add_column("#", width=3, justify="right")
        table.add_column("Source Video", style="bold white")
        table.add_column("Target", style="cyan", width=8)
        table.add_column("Duration", justify="right", width=10)
        table.add_column("Size", justify="right", width=12)
        table.add_column("Speed", justify="right", width=8)
        table.add_column("Verification", width=18)
        table.add_column("Status", width=12)

        succeeded = 0
        total_size = 0

        for i, t in enumerate(tasks, 1):
            dur_str = f"{t.duration_seconds:.1f}s" if t.duration_seconds > 0 else "-"
            sz_str = format_bytes_human(t.output_size_bytes) if t.output_size_bytes > 0 else "-"

            if t.status == TaskStatus.COMPLETED:
                succeeded += 1
                total_size += t.output_size_bytes
                status_cell = "[bold green]Completed[/bold green]"
                ver = t.verification_result
                if ver and ver.is_valid:
                    ver_cell = f"[green][OK] Verified ({ver.codec})[/green]"
                else:
                    ver_cell = "[yellow]Unverified[/yellow]"
            elif t.status == TaskStatus.FAILED:
                status_cell = "[bold red]Failed[/bold red]"
                ver_cell = f"[red][X] {t.error[:16] if t.error else 'Error'}[/red]"
            else:
                status_cell = f"[dim]{t.status.value}[/dim]"
                ver_cell = "-"

            table.add_row(
                str(i),
                t.source_file.name,
                t.target_format.upper(),
                dur_str,
                sz_str,
                t.speed,
                ver_cell,
                status_cell,
            )

        self.console.print(table)
        total_mb = total_size / (1024 * 1024)
        summary_panel = Panel(
            f"[bold]Batch Finished:[/bold] {succeeded}/{len(tasks)} successful | [bold]Total Output:[/bold] {total_mb:.2f} MB",
            border_style="bold green" if succeeded == len(tasks) else "bold yellow",
        )
        self.console.print(summary_panel)

    def print_banner(self) -> None:
        """Renders rich gradient header banner."""
        banner_text = Text()
        banner_text.append("⚡ ENTERPRISE VIDEO TO AUDIO TRANSCODER ⚡\n", style="bold magenta")
        banner_text.append(
            "Ultra-fast parallel FFmpeg audio extractor & normalizer\n",
            style="bold cyan",
        )
        banner_text.append(
            f"Supported containers: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}",
            style="dim white",
        )
        self.console.print(Panel(banner_text, border_style="bright_blue", expand=True))

    def print_system_status(self) -> None:
        """Displays FFmpeg binary, CPU cores, RAM, and active encoding configuration."""
        table = Table(title="System & Transcoder Environment", border_style="dim blue", expand=True)
        table.add_column("Property", style="bold cyan", width=22)
        table.add_column("Status / Value", style="white")

        try:
            ffmpeg_bin = find_ffmpeg()
            version_str = get_ffmpeg_version(ffmpeg_bin)
            table.add_row(
                "FFmpeg Binary",
                f"[bold green][OK] Available[/bold green] ({ffmpeg_bin})\n[dim]{version_str}[/dim]",
            )
        except Exception as exc:
            table.add_row("FFmpeg Binary", f"[bold red][X] Not Found[/bold red] ({exc})")

        cpu_phys = psutil.cpu_count(logical=False) or 4
        cpu_log = psutil.cpu_count(logical=True) or 8
        ram = psutil.virtual_memory()
        ram_gb = ram.total / (1024**3)
        ram_avail_gb = ram.available / (1024**3)
        table.add_row(
            "Hardware Resources",
            f"CPUs: [bold]{cpu_phys}[/bold] Phys / [bold]{cpu_log}[/bold] Logical | RAM: [bold]{ram_avail_gb:.1f} GB[/bold] free / [bold]{ram_gb:.1f} GB[/bold] total",
        )

        dest_display = str(self.output_dir) if self.output_dir else "Source File Directory (In-Place)"
        table.add_row(
            "Active Profile",
            f"[bold yellow]{self.current_preset.name}[/bold yellow] [dim]({self.current_preset.get_summary()})[/dim]",
        )
        table.add_row("DSP Audio Filters", f"[bold magenta]{self.filter_chain.get_summary()}[/bold magenta]")
        table.add_row("Output Destination", f"[dim]{dest_display}[/dim]")

        self.console.print(table)

    def print_help(self) -> None:
        """Displays interactive command and hotkey reference."""
        table = Table(title="Command & Hotkey Reference", border_style="cyan")
        table.add_column("Hotkey / Command", style="bold green", width=22)
        table.add_column("Action & Description", style="white")

        table.add_row("a (or add)", "Drag & drop paths (files, folders, wildcards, quotes, spaces)")
        table.add_row("v (or paste)", "Instant Windows clipboard extraction (CF_HDROP & text fallback)")
        table.add_row("p (or preset)", "Audio Preset Selector (Studio Master, Audiophile, Podcast, Streaming, etc.)")
        table.add_row("f (or filter)", "DSP Audio Filter Wizard (Volume, Bass, Vocals, Rumble Cut, Speed, Loudness)")
        table.add_row("i (or inspect)", "Media Stream Inspector (probe streams, channels, codec, language, metadata)")
        table.add_row("s (or start)", "Start parallel batch transcoding with live VU meter & progress")
        table.add_row("w (or watch)", "Toggle background clipboard watcher (auto-stage Explorer copied files)")
        table.add_row("r (or report)", "Summary statistics modal & export to JSON/CSV/TXT")
        table.add_row("o (or out)", "Set custom output destination directory")
        table.add_row("c (or clear)", "Clear queued files")
        table.add_row("l (or list)", "View complete staged files table")
        table.add_row("status", "View system hardware & FFmpeg engine specs")
        table.add_row("q (or quit)", "Exit application")

        self.console.print(table)


def run_cli_tui(initial_paths: Optional[List[Path]] = None) -> None:
    """Entry point for CLI TUI mode."""
    tui = CliTui(initial_paths=initial_paths)
    tui.run()
