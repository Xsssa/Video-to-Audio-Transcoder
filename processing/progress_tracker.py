"""
progress_tracker.py - Real-time parser for FFmpeg -progress pipe:1 key-value output.

Parses stream metrics including:
- out_time_us, out_time_ms, out_time
- speed (e.g. '15.2x')
- total_size (bytes)
- bitrate (e.g. '192.0kbits/s')
- fps

Computes:
- Percentage completed based on media duration
- Normalized current speed factor
- Estimated time remaining (ETA)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, IO, Optional


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot of transcoding progress at a specific instant."""
    percent: float                # 0.0 to 100.0
    out_time_seconds: float       # Converted audio time in seconds
    speed_factor: float           # Multiplier (e.g. 15.2 for 15.2x real-time)
    speed_str: str                # Human-readable speed string (e.g. "15.2x")
    eta_seconds: Optional[float]  # Estimated remaining seconds
    eta_str: str                  # Human-readable ETA (e.g. "00:01:23" or "--:--")
    total_size_bytes: int         # Output file size in bytes so far
    bitrate_str: str              # Bitrate string (e.g. "192.0kbits/s")
    fps: float                    # Processing frames per second (if applicable)
    is_completed: bool            # True if progress == 'end'
    raw_data: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def formatted_size(self) -> str:
        """Human-readable size format (e.g. '4.2 MB')."""
        size = float(self.total_size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{size:.1f} GB"


def parse_time_string_to_seconds(time_str: str) -> Optional[float]:
    """
    Parses FFmpeg out_time string 'HH:MM:SS.mmmmmm' into fractional seconds.
    Example: '00:01:23.456000' -> 83.456
    """
    if not time_str or time_str == "N/A":
        return None
    try:
        parts = time_str.strip().split(":")
        if len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600.0 + minutes * 60.0 + seconds
        elif len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return minutes * 60.0 + seconds
        return float(time_str)
    except (ValueError, TypeError):
        return None


def format_seconds_to_eta(seconds: Optional[float]) -> str:
    """
    Formats a duration in seconds into 'HH:MM:SS' or 'MM:SS'.
    Returns '--:--' if None or negative.
    """
    if seconds is None or seconds < 0:
        return "--:--"
    sec_int = int(round(seconds))
    hours = sec_int // 3600
    minutes = (sec_int % 3600) // 60
    secs = sec_int % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class FFmpegProgressTracker:
    """
    Real-time parser and state tracker for FFmpeg '-progress pipe:1' output.

    Usage:
        tracker = FFmpegProgressTracker(total_duration=120.5)
        for line in ffmpeg_stdout:
            snapshot = tracker.feed_line(line)
            if snapshot:
                print(f"{snapshot.percent:.1f}% @ {snapshot.speed_str} ETA: {snapshot.eta_str}")
    """

    def __init__(
        self,
        total_duration: float = 0.0,
        start_time: Optional[float] = None,
    ) -> None:
        """
        Args:
            total_duration: Total expected duration of source media in seconds (from ffprobe).
            start_time: Optional monotonic start time for dynamic speed fallback.
        """
        self.total_duration = max(0.0, float(total_duration))
        self.start_time = start_time if start_time is not None else time.monotonic()
        self._accumulator: dict[str, str] = {}
        self._last_snapshot: ProgressSnapshot = self._create_initial_snapshot()
        self._last_out_time_seconds: float = 0.0

    def _create_initial_snapshot(self) -> ProgressSnapshot:
        """Initial snapshot before any FFmpeg progress lines are processed."""
        return ProgressSnapshot(
            percent=0.0,
            out_time_seconds=0.0,
            speed_factor=0.0,
            speed_str="0.0x",
            eta_seconds=None,
            eta_str="--:--",
            total_size_bytes=0,
            bitrate_str="N/A",
            fps=0.0,
            is_completed=False,
            raw_data={},
        )

    @property
    def current_snapshot(self) -> ProgressSnapshot:
        """Returns the most recent progress snapshot."""
        return self._last_snapshot

    def reset(self, total_duration: Optional[float] = None) -> None:
        """Resets tracker state, optionally with a new media duration."""
        if total_duration is not None:
            self.total_duration = max(0.0, float(total_duration))
        self.start_time = time.monotonic()
        self._accumulator.clear()
        self._last_snapshot = self._create_initial_snapshot()
        self._last_out_time_seconds = 0.0

    def feed_line(self, line: str) -> Optional[ProgressSnapshot]:
        """
        Feeds a single line of FFmpeg -progress output.
        Returns a ProgressSnapshot when a progress boundary ('progress=continue' or 'progress=end')
        is encountered, or None if the key/value pair is still accumulating.
        """
        clean_line = line.strip()
        if not clean_line or "=" not in clean_line:
            return None

        key, _, val = clean_line.partition("=")
        key = key.strip()
        val = val.strip()

        self._accumulator[key] = val

        # FFmpeg signals completion of a progress block with 'progress=continue' or 'progress=end'
        if key == "progress":
            is_end = (val == "end")
            snapshot = self._build_snapshot(self._accumulator, is_end=is_end)
            self._accumulator.clear()
            self._last_snapshot = snapshot
            return snapshot

        return None

    def feed_chunk(self, chunk: str) -> list[ProgressSnapshot]:
        """
        Feeds a block or stream chunk containing multiple newline-delimited lines.
        Returns all completed snapshots generated by the chunk.
        """
        snapshots: list[ProgressSnapshot] = []
        for line in chunk.splitlines():
            res = self.feed_line(line)
            if res is not None:
                snapshots.append(res)
        return snapshots

    def _extract_out_time_seconds(self, data: dict[str, str]) -> float:
        """
        Extracts current media out_time in seconds with multi-tier fallback:
        1. out_time_us (microseconds)
        2. out_time (HH:MM:SS.mmmmmm)
        3. out_time_ms (FFmpeg legacy: often microseconds despite the 'ms' suffix)
        """
        if "out_time_us" in data:
            try:
                us = int(data["out_time_us"])
                return max(0.0, us / 1_000_000.0)
            except (ValueError, TypeError):
                pass

        if "out_time" in data:
            t = parse_time_string_to_seconds(data["out_time"])
            if t is not None:
                return max(0.0, t)

        if "out_time_ms" in data:
            try:
                val = int(data["out_time_ms"])
                # In FFmpeg, out_time_ms is notoriously in microseconds in many versions.
                # If val is huge relative to total_duration * 1000, treat as microseconds.
                if self.total_duration > 0 and val > (self.total_duration * 10_000):
                    return max(0.0, val / 1_000_000.0)
                # Else check magnitude: if > 10_000_000 (10000 seconds in ms), likely us
                if val > 10_000_000:
                    return max(0.0, val / 1_000_000.0)
                return max(0.0, val / 1_000.0)
            except (ValueError, TypeError):
                pass

        return self._last_out_time_seconds

    def _extract_speed_factor(self, data: dict[str, str], current_out_time: float) -> tuple[float, str]:
        """
        Extracts speed multiplier factor from 'speed=15.2x' or calculates dynamically.
        Returns: (speed_float, speed_str)
        """
        raw_speed = data.get("speed", "").strip()
        speed_float = 0.0

        if raw_speed and raw_speed != "N/A":
            # Match digits and decimals before optional 'x'
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw_speed)
            if m:
                try:
                    speed_float = float(m.group(1))
                except ValueError:
                    speed_float = 0.0

        # Fallback to dynamic speed if raw_speed is unavailable or invalid
        elapsed_wall = time.monotonic() - self.start_time
        if speed_float <= 0.0 and elapsed_wall > 0.2 and current_out_time > 0.0:
            speed_float = current_out_time / elapsed_wall

        speed_str = f"{speed_float:.1f}x" if speed_float > 0.0 else (raw_speed if raw_speed else "0.0x")
        return speed_float, speed_str

    def _extract_total_size(self, data: dict[str, str]) -> int:
        """Extracts total_size in bytes."""
        try:
            return max(0, int(data.get("total_size", 0)))
        except (ValueError, TypeError):
            return 0

    def _extract_fps(self, data: dict[str, str]) -> float:
        """Extracts fps value."""
        try:
            return max(0.0, float(data.get("fps", 0.0)))
        except (ValueError, TypeError):
            return 0.0

    def _calculate_eta(
        self,
        current_out_time: float,
        speed_factor: float,
        percent: float,
        is_end: bool,
    ) -> tuple[Optional[float], str]:
        """
        Calculates remaining seconds and formats into a string.
        """
        if is_end or percent >= 100.0:
            return 0.0, "00:00"

        if self.total_duration <= 0.0:
            return None, "--:--"

        remaining_media_seconds = max(0.0, self.total_duration - current_out_time)
        if remaining_media_seconds <= 0.01:
            return 0.0, "00:00"

        if speed_factor > 0.01:
            eta_sec = remaining_media_seconds / speed_factor
            return eta_sec, format_seconds_to_eta(eta_sec)

        return None, "--:--"

    def _build_snapshot(self, data: dict[str, str], is_end: bool = False) -> ProgressSnapshot:
        """Constructs a ProgressSnapshot from the accumulated key-value dictionary."""
        current_out_time = self._extract_out_time_seconds(data)
        self._last_out_time_seconds = current_out_time

        # Calculate percentage
        if is_end:
            percent = 100.0
        elif self.total_duration > 0.0:
            percent = min(99.9, max(0.0, (current_out_time / self.total_duration) * 100.0))
        else:
            percent = 0.0

        speed_factor, speed_str = self._extract_speed_factor(data, current_out_time)
        total_size = self._extract_total_size(data)
        bitrate_str = data.get("bitrate", "N/A").strip()
        fps = self._extract_fps(data)

        eta_seconds, eta_str = self._calculate_eta(current_out_time, speed_factor, percent, is_end)

        return ProgressSnapshot(
            percent=percent,
            out_time_seconds=current_out_time,
            speed_factor=speed_factor,
            speed_str=speed_str,
            eta_seconds=eta_seconds,
            eta_str=eta_str,
            total_size_bytes=total_size,
            bitrate_str=bitrate_str,
            fps=fps,
            is_completed=is_end,
            raw_data=dict(data),
        )


def parse_progress_stream(
    stream: IO[str],
    callback: Callable[[ProgressSnapshot], None],
    total_duration: float = 0.0,
) -> ProgressSnapshot:
    """
    Convenience helper that synchronously consumes an FFmpeg progress text stream
    (such as proc.stdout), calls callback on every snapshot, and returns the final snapshot.
    """
    tracker = FFmpegProgressTracker(total_duration=total_duration)
    last_snap = tracker.current_snapshot

    for line in iter(stream.readline, ""):
        snapshot = tracker.feed_line(line)
        if snapshot is not None:
            last_snap = snapshot
            try:
                callback(snapshot)
            except Exception:
                pass

    return last_snap
