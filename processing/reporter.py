"""
reporter.py - Batch transcoding reporting and analytics.

Features:
- Generates JSON and CSV structured reports stored in exports/ or logs/ directories.
- Computes aggregated batch metrics (success rate, space saved, total throughput).
- Formats comprehensive human-readable ASCII summary tables.
- Safely handles Windows paths, special characters, and formatting.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence


def format_bytes_human(num_bytes: int | float) -> str:
    """Formats raw byte counts into human-readable strings (e.g. 14.5 MB, 1.2 GB)."""
    val = float(num_bytes)
    sign = "-" if val < 0 else ""
    val = abs(val)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024.0 or unit == "TB":
            return f"{sign}{val:.1f} {unit}" if unit != "B" else f"{sign}{int(val)} B"
        val /= 1024.0
    return f"{sign}{val:.1f} TB"


def format_duration_human(seconds: float) -> str:
    """Formats duration in seconds into 'HH:MM:SS' or 'MM:SS'."""
    if seconds < 0:
        return "00:00"
    sec_int = int(round(seconds))
    h = sec_int // 3600
    m = (sec_int % 3600) // 60
    s = sec_int % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class TaskReportItem:
    """Structured telemetry for a single converted task."""
    task_id: str
    source_file: str
    output_file: str
    target_format: str
    status: str
    processing_time_seconds: float
    audio_duration_seconds: float
    input_size_bytes: int
    output_size_bytes: int
    space_saved_bytes: int
    compression_percent: float
    speed_factor: str
    codec: str
    bitrate_kbps: Optional[int]
    sample_rate: Optional[int]
    channels: Optional[int]
    error_message: Optional[str]
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_task(cls, task: Any) -> TaskReportItem:
        """Constructs an item from a ConversionTask instance."""
        source_str = str(task.source_file)
        output_str = str(task.output_file) if task.output_file else ""

        # Compute file sizes
        in_size = getattr(task, "input_size_bytes", 0)
        if in_size == 0 and task.source_file and Path(task.source_file).exists():
            try:
                in_size = Path(task.source_file).stat().st_size
            except OSError:
                in_size = 0

        out_size = getattr(task, "output_size_bytes", 0)
        if out_size == 0 and task.output_file and Path(task.output_file).exists():
            try:
                out_size = Path(task.output_file).stat().st_size
            except OSError:
                out_size = 0

        space_saved = in_size - out_size
        compression = (
            ((in_size - out_size) / in_size * 100.0)
            if in_size > 0
            else 0.0
        )

        # Durations
        proc_time = getattr(task, "duration_seconds", 0.0)
        if proc_time == 0.0 and getattr(task, "started_at", None) and getattr(task, "completed_at", None):
            proc_time = max(0.0, task.completed_at - task.started_at)

        audio_dur = 0.0
        if getattr(task, "probe_result", None) and task.probe_result:
            audio_dur = task.probe_result.duration
        elif getattr(task, "verification_result", None) and task.verification_result:
            audio_dur = task.verification_result.duration

        # Codec / Stream details
        codec = "unknown"
        bitrate_kbps = None
        sample_rate = None
        channels = None
        warnings: list[str] = []

        ver_res = getattr(task, "verification_result", None)
        if ver_res:
            codec = ver_res.codec
            if ver_res.bitrate:
                bitrate_kbps = int(ver_res.bitrate / 1000)
            sample_rate = ver_res.sample_rate
            channels = ver_res.channels
            warnings.extend(ver_res.warnings)

        return cls(
            task_id=str(task.task_id),
            source_file=source_str,
            output_file=output_str,
            target_format=str(task.target_format),
            status=task.status.value if hasattr(task.status, "value") else str(task.status),
            processing_time_seconds=round(proc_time, 2),
            audio_duration_seconds=round(audio_dur, 2),
            input_size_bytes=in_size,
            output_size_bytes=out_size,
            space_saved_bytes=space_saved,
            compression_percent=round(compression, 1),
            speed_factor=str(getattr(task, "speed", "0.0x")),
            codec=codec,
            bitrate_kbps=bitrate_kbps,
            sample_rate=sample_rate,
            channels=channels,
            error_message=task.error if task.error else None,
            warnings=warnings,
        )


@dataclass
class BatchReport:
    """Comprehensive batch conversion report with analytics and export methods."""
    report_id: str
    timestamp: str
    total_wall_time_seconds: float
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    success_rate_percent: float
    total_input_bytes: int
    total_output_bytes: int
    total_space_saved_bytes: int
    space_saved_percent: float
    items: list[TaskReportItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializes report to dictionary."""
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "total_wall_time_seconds": round(self.total_wall_time_seconds, 2),
            "total_tasks": self.total_tasks,
            "successful_tasks": self.successful_tasks,
            "failed_tasks": self.failed_tasks,
            "cancelled_tasks": self.cancelled_tasks,
            "success_rate_percent": round(self.success_rate_percent, 1),
            "total_input_bytes": self.total_input_bytes,
            "total_output_bytes": self.total_output_bytes,
            "total_space_saved_bytes": self.total_space_saved_bytes,
            "space_saved_percent": round(self.space_saved_percent, 1),
            "items": [asdict(item) for item in self.items],
        }

    def _resolve_export_path(
        self,
        extension: str,
        output_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
    ) -> Path:
        """Determines destination file path, ensuring parent directory exists."""
        if output_path:
            target = Path(output_path).resolve()
        else:
            base_dir = (
                Path(output_dir).resolve()
                if output_dir
                else Path(__file__).resolve().parent.parent / "exports"
            )
            base_dir.mkdir(parents=True, exist_ok=True)
            sanitized_id = self.report_id.replace(":", "-").replace(" ", "_")
            target = base_dir / f"batch_report_{sanitized_id}.{extension}"

        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def export_json(
        self,
        output_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
        indent: int = 2,
    ) -> Path:
        """
        Exports full batch report to a JSON file.

        Args:
            output_path: Specific destination file path.
            output_dir: Directory where the report will be created (defaults to exports/).
            indent: JSON indentation.

        Returns:
            Resolved Path of the saved JSON report.
        """
        dest = self._resolve_export_path("json", output_path, output_dir)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
        return dest

    def export_csv(
        self,
        output_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
    ) -> Path:
        """
        Exports task item records to a CSV file.

        Args:
            output_path: Specific destination file path.
            output_dir: Directory where the report will be created (defaults to exports/).

        Returns:
            Resolved Path of the saved CSV report.
        """
        dest = self._resolve_export_path("csv", output_path, output_dir)

        fieldnames = [
            "task_id",
            "status",
            "source_file",
            "output_file",
            "target_format",
            "processing_time_seconds",
            "audio_duration_seconds",
            "input_size_bytes",
            "output_size_bytes",
            "space_saved_bytes",
            "compression_percent",
            "speed_factor",
            "codec",
            "bitrate_kbps",
            "sample_rate",
            "channels",
            "error_message",
            "warnings_count",
        ]

        with open(dest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for it in self.items:
                row = {
                    "task_id": it.task_id,
                    "status": it.status,
                    "source_file": it.source_file,
                    "output_file": it.output_file,
                    "target_format": it.target_format,
                    "processing_time_seconds": it.processing_time_seconds,
                    "audio_duration_seconds": it.audio_duration_seconds,
                    "input_size_bytes": it.input_size_bytes,
                    "output_size_bytes": it.output_size_bytes,
                    "space_saved_bytes": it.space_saved_bytes,
                    "compression_percent": it.compression_percent,
                    "speed_factor": it.speed_factor,
                    "codec": it.codec,
                    "bitrate_kbps": it.bitrate_kbps if it.bitrate_kbps is not None else "",
                    "sample_rate": it.sample_rate if it.sample_rate is not None else "",
                    "channels": it.channels if it.channels is not None else "",
                    "error_message": it.error_message or "",
                    "warnings_count": len(it.warnings),
                }
                writer.writerow(row)

        return dest

    def export_txt(
        self,
        output_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
    ) -> Path:
        """
        Exports human-readable summary table to a plain text file.

        Args:
            output_path: Specific destination file path.
            output_dir: Directory where the report will be created (defaults to exports/).

        Returns:
            Resolved Path of the saved TXT report.
        """
        dest = self._resolve_export_path("txt", output_path, output_dir)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(self.format_summary_table())
        return dest

    def format_summary_table(self) -> str:
        """
        Formats a clean, enterprise ASCII summary table displaying batch metrics
        and per-file conversion status.
        """
        lines: list[str] = []
        width = 100
        sep = "=" * width
        thin_sep = "-" * width

        lines.append(sep)
        lines.append(" ADVANCED VIDEO-TO-AUDIO TRANSCODER BATCH REPORT ".center(width, "="))
        lines.append(sep)

        # Overview Metrics
        wall_time_str = format_duration_human(self.total_wall_time_seconds)
        lines.append(f" Report ID      : {self.report_id}")
        lines.append(f" Generated At   : {self.timestamp} | Total Wall Time: {wall_time_str}")
        lines.append(
            f" Tasks          : {self.total_tasks} Total | "
            f"{self.successful_tasks} Succeeded | "
            f"{self.failed_tasks} Failed | "
            f"{self.cancelled_tasks} Cancelled"
        )
        lines.append(f" Success Rate   : {self.success_rate_percent:.1f}%")

        in_size_str = format_bytes_human(self.total_input_bytes)
        out_size_str = format_bytes_human(self.total_output_bytes)
        saved_str = format_bytes_human(self.total_space_saved_bytes)
        saved_sign = "+" if self.total_space_saved_bytes < 0 else "-"
        lines.append(
            f" Storage        : In: {in_size_str} -> Out: {out_size_str} "
            f"(Saved: {saved_str} / {saved_sign}{abs(self.space_saved_percent):.1f}%)"
        )
        lines.append(thin_sep)

        # Table Header
        # Columns: Status(10), Source(26), Target(6), Time(8), Size Change(24), Speed(8)
        header = f" {'Status':<10} | {'Source File':<26} | {'Fmt':<5} | {'Time':<8} | {'Size (In -> Out)':<24} | {'Speed':<8}"
        lines.append(header)
        lines.append(thin_sep)

        for item in self.items:
            # File basename truncated to fit
            src_name = Path(item.source_file).name
            if len(src_name) > 26:
                src_name = src_name[:23] + "..."

            status_str = item.status[:10]
            fmt_str = item.target_format[:5]
            time_str = format_duration_human(item.processing_time_seconds)
            size_in = format_bytes_human(item.input_size_bytes)
            size_out = format_bytes_human(item.output_size_bytes)
            size_str = f"{size_in} -> {size_out}"
            speed_str = item.speed_factor[:8]

            row = f" {status_str:<10} | {src_name:<26} | {fmt_str:<5} | {time_str:<8} | {size_str:<24} | {speed_str:<8}"
            lines.append(row)

            if item.error_message:
                clean_err = item.error_message.replace("\n", " ").strip()
                if len(clean_err) > 85:
                    clean_err = clean_err[:82] + "..."
                lines.append(f"   |-- ERROR: {clean_err}")

        lines.append(sep)
        return "\n".join(lines)

    def print_summary(self, file: Any = None) -> None:
        """Prints the summary table to stdout or designated file stream."""
        target_stream = file if file is not None else sys.stdout
        print(self.format_summary_table(), file=target_stream)


def create_report_from_tasks(
    tasks: Sequence[Any],
    wall_time_seconds: float = 0.0,
    report_id: Optional[str] = None,
) -> BatchReport:
    """
    Constructs a comprehensive BatchReport from a sequence of ConversionTasks.

    Args:
        tasks: Sequence of ConversionTask objects.
        wall_time_seconds: Overall wall-clock time elapsed for the batch.
        report_id: Optional unique report identifier.

    Returns:
        BatchReport instance.
    """
    rid = report_id or f"rep_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    iso_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

    items = [TaskReportItem.from_task(t) for t in tasks]

    total_tasks = len(items)
    successful = sum(1 for it in items if it.status.upper() == "COMPLETED")
    failed = sum(1 for it in items if it.status.upper() == "FAILED")
    cancelled = sum(1 for it in items if it.status.upper() == "CANCELLED")

    success_rate = (successful / total_tasks * 100.0) if total_tasks > 0 else 0.0

    total_in = sum(it.input_size_bytes for it in items)
    total_out = sum(it.output_size_bytes for it in items)
    total_saved = total_in - total_out
    saved_percent = (total_saved / total_in * 100.0) if total_in > 0 else 0.0

    return BatchReport(
        report_id=rid,
        timestamp=iso_time,
        total_wall_time_seconds=max(0.0, wall_time_seconds),
        total_tasks=total_tasks,
        successful_tasks=successful,
        failed_tasks=failed,
        cancelled_tasks=cancelled,
        success_rate_percent=success_rate,
        total_input_bytes=total_in,
        total_output_bytes=total_out,
        total_space_saved_bytes=total_saved,
        space_saved_percent=saved_percent,
        items=items,
    )
