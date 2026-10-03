"""
Unified Entry Point for Enterprise Video to Audio Transcoder.
Supports Desktop GUI, Rich Terminal TUI, and Headless CLI automation.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from core.ffmpeg_finder import find_ffmpeg, get_ffmpeg_version
from processing.progress_tracker import ProgressSnapshot
from processing.queue_manager import ConversionTask, QueueManager, TaskStatus
from processing.reporter import create_report_from_tasks
from processing.verifier import VerificationResult
from ui.textual_app import run_textual_app
from ui.gui_app import run_gui
from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    expand_path,
    parse_input_paths,
)


def is_gui_available() -> bool:
    """Checks whether graphical environment is available."""
    if sys.platform == "win32":
        # Windows desktop always provides GUI capabilities unless headless server
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def run_headless_transcode(
    inputs: List[str],
    target_format: str = "mp3",
    bitrate: Optional[str] = "320k",
    output_dir: Optional[str] = None,
    sample_rate: Optional[int] = None,
    channels: Optional[int] = None,
    loudnorm: bool = False,
    lossless_copy: bool = False,
    recursive: bool = True,
    report_path: Optional[str] = None,
) -> int:
    """Executes non-interactive headless batch transcoding with Rich telemetry."""
    console = Console(legacy_windows=False)
    console.print(Panel("[bold cyan]Enterprise Video to Audio Transcoder - Headless Mode[/bold cyan]", border_style="cyan"))

    # Resolve and expand input files
    all_files: List[Path] = []
    for item in inputs:
        all_files.extend(parse_input_paths(item, recursive=recursive))

    # Deduplicate
    unique_files: List[Path] = []
    seen = set()
    for f in all_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    if not unique_files:
        console.print("[bold red]Error: No supported video files found in the provided inputs.[/bold red]")
        return 1

    out_path = Path(output_dir).resolve() if output_dir else None
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)

    console.print(f"Discovered [bold green]{len(unique_files)}[/bold green] video file(s) for conversion.")
    console.print(f"Target Format: [bold yellow]{target_format.upper()}[/bold yellow] | Bitrate: [bold]{bitrate or 'Lossless'}[/bold]")
    if out_path:
        console.print(f"Destination: [dim]{out_path}[/dim]")

    qm = QueueManager(default_output_dir=out_path)
    qm.pause_queue()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=35),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("[yellow]{task.fields[speed]}"),
        console=console,
    )

    overall_task = progress.add_task("[bold cyan]Batch Progress", total=len(unique_files), speed="")
    task_progress_ids = {}

    def on_started(task: ConversionTask):
        tid = progress.add_task(f"[green]{task.source_file.name[:25]}", total=100.0, speed="0.0x")
        task_progress_ids[task.task_id] = tid

    def on_prog(task: ConversionTask, snap: ProgressSnapshot):
        tid = task_progress_ids.get(task.task_id)
        if tid is not None:
            progress.update(tid, completed=snap.percent, speed=snap.speed_str)

    def on_completed(task: ConversionTask, ver: VerificationResult):
        progress.update(overall_task, advance=1)
        tid = task_progress_ids.get(task.task_id)
        if tid is not None:
            progress.update(tid, description=f"[green][OK] {task.source_file.name[:25]}", completed=100.0, speed=task.speed)

    def on_failed(task: ConversionTask, err: str):
        progress.update(overall_task, advance=1)
        tid = task_progress_ids.get(task.task_id)
        if tid is not None:
            progress.update(tid, description=f"[red][X] {task.source_file.name[:25]}", completed=100.0, speed="Err")

    qm.on_task_started = on_started
    qm.on_task_progress = on_prog
    qm.on_task_completed = on_completed
    qm.on_task_failed = on_failed

    options = {
        "preserve_cover_art": True,
        "ebu_r128": loudnorm,
        "lossless_copy_if_match": lossless_copy,
    }
    if target_format.lower() not in ("flac", "wav") and bitrate:
        options["bitrate"] = bitrate
    if sample_rate:
        options["sample_rate"] = sample_rate
    if channels:
        options["channels"] = channels

    for f in unique_files:
        dest_file = (out_path / f"{f.stem}.{target_format.lower()}") if out_path else None
        qm.add_task(
            source_file=f,
            target_format=target_format,
            output_file=dest_file,
            options=options,
        )

    qm.resume_queue()

    with progress:
        try:
            while True:
                stats = qm.get_stats()
                if not stats.is_running and stats.pending_tasks == 0 and stats.active_tasks == 0:
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            console.print("\n[bold red]Interrupted by user. Terminating active tasks...[/bold red]")
            qm.clear_queue(cancel_active=True)

    qm.shutdown(wait=True)

    # Summary table
    tasks = qm.get_all_tasks()
    table = Table(title="Conversion Summary", border_style="green", expand=True)
    table.add_column("#", width=3, justify="right")
    table.add_column("Source Video", style="bold white")
    table.add_column("Target", style="cyan", width=8)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Verification", width=18)
    table.add_column("Status", width=12)

    succeeded = 0
    total_size = 0
    for i, t in enumerate(tasks, 1):
        dur = f"{t.duration_seconds:.1f}s" if t.duration_seconds > 0 else "-"
        sz = f"{t.output_size_bytes / (1024*1024):.2f} MB" if t.output_size_bytes > 0 else "-"
        if t.status == TaskStatus.COMPLETED:
            succeeded += 1
            total_size += t.output_size_bytes
            status_cell = "[bold green]Completed[/bold green]"
            ver_cell = f"[green][OK] Verified ({t.verification_result.codec})[/green]" if t.verification_result else "[yellow]Unverified[/yellow]"
        else:
            status_cell = f"[bold red]{t.status.value}[/bold red]"
            ver_cell = f"[red][X] {t.error[:18] if t.error else 'Error'}[/red]"

        table.add_row(str(i), t.source_file.name, t.target_format.upper(), dur, sz, ver_cell, status_cell)

    console.print(table)
    console.print(f"[bold]Batch Finished:[/bold] {succeeded}/{len(tasks)} succeeded | Total output: {total_size / (1024*1024):.2f} MB")

    if report_path:
        rep = create_report_from_tasks(tasks)
        rp = Path(report_path)
        if rp.suffix.lower() == ".json":
            rep.export_json(rp)
        elif rp.suffix.lower() == ".csv":
            rep.export_csv(rp)
        else:
            rp.write_text(rep.format_summary_table(), encoding="utf-8")
        console.print(f"[green][OK] Conversion report saved to: [bold]{rp}[/bold][/green]")

    return 0 if succeeded == len(tasks) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enterprise Video-to-Audio Transcoder - Modern GUI & CLI Transcoder",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    mode_group = parser.add_argument_group("Interface Mode")
    mode_group.add_argument("--gui", "-g", action="store_true", help="Launch Modern Desktop GUI")
    mode_group.add_argument("--cli", "-c", action="store_true", help="Launch Rich Terminal TUI")

    input_group = parser.add_argument_group("Input & Headless Options")
    input_group.add_argument(
        "--input",
        "-i",
        nargs="+",
        help="Input video file(s), directory, or wildcard pattern",
    )
    input_group.add_argument(
        "--format",
        "-f",
        default="mp3",
        choices=["mp3", "flac", "wav", "aac", "opus", "ogg", "m4a"],
        help="Target audio container format (default: mp3)",
    )
    input_group.add_argument(
        "--bitrate",
        "-b",
        default="320k",
        help="Audio bitrate (e.g. 128k, 192k, 256k, 320k, or 'vbr')",
    )
    input_group.add_argument(
        "--output",
        "-o",
        help="Destination directory for transcoded audio files",
    )
    input_group.add_argument(
        "--sample-rate",
        "-sr",
        type=int,
        choices=[44100, 48000, 96000],
        help="Target audio sample rate in Hz",
    )
    input_group.add_argument(
        "--channels",
        "-ch",
        type=int,
        choices=[1, 2, 6],
        help="Audio channels (1=Mono, 2=Stereo, 6=5.1)",
    )
    input_group.add_argument(
        "--loudnorm",
        action="store_true",
        help="Apply EBU R128 integrated loudness normalization (-16 LUFS)",
    )
    input_group.add_argument(
        "--lossless-copy",
        action="store_true",
        help="Stream copy audio without re-encoding if format matches",
    )
    input_group.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="Do not scan subdirectories when given a folder",
    )
    input_group.add_argument(
        "--report",
        help="Path to export batch report (.txt, .json, or .csv)",
    )

    args = parser.parse_args()

    # Pre-parse input paths if any
    initial_paths: List[Path] = []
    if args.input:
        for item in args.input:
            initial_paths.extend(parse_input_paths(item, recursive=args.recursive))

    # 1. Explicit GUI flag
    if args.gui:
        run_gui(initial_paths=initial_paths if initial_paths else None)
        return

    # 2. Explicit CLI TUI flag
    if args.cli:
        run_textual_app(initial_paths=initial_paths if initial_paths else None)
        return

    # 3. Headless conversion when --input is provided without --gui or --cli
    if args.input:
        exit_code = run_headless_transcode(
            inputs=args.input,
            target_format=args.format,
            bitrate=args.bitrate,
            output_dir=args.output,
            sample_rate=args.sample_rate,
            channels=args.channels,
            loudnorm=args.loudnorm,
            lossless_copy=args.lossless_copy,
            recursive=args.recursive,
            report_path=args.report,
        )
        sys.exit(exit_code)

    # 4. No arguments provided
    # If interactive terminal, present friendly choice; otherwise default to GUI if display available
    if sys.stdin.isatty() and is_gui_available():
        console = Console()
        console.print(Panel(
            "[bold cyan]Enterprise Video to Audio Converter[/bold cyan]\n"
            "Select interface mode:\n"
            "  [bold green][1][/bold green] Modern Desktop GUI (Default)\n"
            "  [bold yellow][2][/bold yellow] Rich Terminal UI (CLI TUI)\n"
            "  [bold red][q][/bold red] Quit",
            border_style="bright_blue",
        ))
        try:
            choice = input("Enter choice [1/2/q] (Press Enter for GUI): ").strip().lower()
            if choice == "2":
                run_textual_app()
            elif choice in ("q", "quit", "exit"):
                sys.exit(0)
            else:
                run_gui()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)
    elif is_gui_available():
        run_gui()
    else:
        run_textual_app()


if __name__ == "__main__":
    main()
