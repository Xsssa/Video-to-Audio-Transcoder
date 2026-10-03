"""
transcoder.py - Enterprise-grade FFmpeg transcoding engine.

Features:
- Real-time progress monitoring via '-progress pipe:1' with percentage, speed, ETA, and size.
- Cancellation token support (threading.Event) with safe process termination and cleanup.
- Intelligent lossless stream copy detection (-c:a copy) vs full re-encoding.
- Integrated EBU R128 loudness normalization (single and two-pass).
- Automated metadata and cover artwork extraction and embedding via Mutagen.
- Robust Windows process handling (preventing console popups and orphan processes).
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator, Optional

from core.audio_filters import AudioFilterConfig, build_filter_string
from core.audio_profiles import (
    AudioFormat,
    AudioProfile,
    can_lossless_copy,
    get_profile,
)
from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg, find_ffprobe
from core.metadata import (
    MediaMetadata,
    apply_metadata,
    build_ffmpeg_metadata_args,
    extract_metadata,
)
from core.normalizer import LoudnessConfig, build_normalizer_filter
from core.probe import MediaProbeResult, probe_media


class TranscodeError(RuntimeError):
    """Raised when an error occurs during transcoding."""
    pass


class TranscodeCancelled(TranscodeError):
    """Raised when the transcoding task is cancelled by user/token."""
    pass


@dataclass
class TranscodeProgress:
    """Real-time progress telemetry from the transcoding worker."""
    percent: float = 0.0
    current_time: float = 0.0
    total_duration: float = 0.0
    speed: float = 0.0
    bitrate: str = "N/A"
    size_bytes: int = 0
    eta_seconds: Optional[float] = None
    fps: float = 0.0
    status: str = "processing"  # "processing", "completed", "cancelled", "error"


@dataclass
class TranscodeOptions:
    """Options and parameters governing the transcode operation."""
    input_path: str | Path
    output_path: Optional[str | Path] = None
    format: str | AudioFormat = "mp3"
    preset: Optional[str] = None
    profile: Optional[AudioProfile] = None
    threads: int = 4
    ebu_r128: bool = False
    two_pass_loudnorm: bool = True
    lossless_copy_if_match: bool = True
    embed_metadata: bool = True
    extract_cover_art: bool = True
    custom_metadata: Optional[MediaMetadata] = None
    cancellation_event: Optional[threading.Event] = None
    ffmpeg_path: Optional[str | Path] = None
    ffprobe_path: Optional[str | Path] = None
    audio_filter_config: Optional[AudioFilterConfig] = None


@dataclass
class TranscodeResult:
    """Comprehensive outcome of a transcode execution."""
    success: bool
    cancelled: bool
    input_path: Path
    output_path: Path
    target_format: str
    was_lossless_copy: bool
    duration: float
    file_size: int
    elapsed_time: float
    error_message: Optional[str] = None
    metadata_embedded: bool = False


def _parse_time_str(time_str: str) -> float:
    """Converts HH:MM:SS.microsec string to total seconds."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        return float(time_str)
    except Exception:
        return 0.0


def _parse_speed(speed_str: str) -> float:
    """Parses speed factor like '2.45x' or '0.98x'."""
    try:
        clean = speed_str.replace("x", "").strip()
        return float(clean)
    except Exception:
        return 0.0


class Transcoder:
    """
    High-performance, observable audio transcoding engine.
    """

    def __init__(
        self,
        ffmpeg_path: Optional[str | Path] = None,
        ffprobe_path: Optional[str | Path] = None,
    ) -> None:
        self.ffmpeg_path = find_ffmpeg(custom_path=ffmpeg_path)
        self.ffprobe_path = find_ffprobe(custom_path=ffprobe_path)

    def build_command(
        self,
        options: TranscodeOptions,
        probe: MediaProbeResult,
        resolved_output: Path,
    ) -> tuple[list[str], bool]:
        """
        Builds the FFmpeg command line arguments and determines if lossless copy applies.

        Returns:
            tuple[list[str], bool]: (command_args, is_lossless_copy)
        """
        target_fmt = AudioFormat.from_string(options.format)
        primary_audio = probe.primary_audio_stream
        source_codec = primary_audio.codec_name if primary_audio else ""

        # Check if lossless stream copy is applicable and desired
        can_copy = can_lossless_copy(source_codec, target_fmt)
        should_copy = (
            options.lossless_copy_if_match
            and can_copy
            and not options.ebu_r128  # Loudness normalization requires re-encoding
            and not options.audio_filter_config  # Audio filters require re-encoding
        )

        cmd: list[str] = [
            str(self.ffmpeg_path),
            "-hwaccel",
            "auto", # Try GPU decode
            "-nostats",
            "-progress",
            "pipe:1",
            "-y",  # Overwrite output
            "-i",
            str(Path(options.input_path).resolve()),
        ]

        # Multi-threading
        if options.threads > 0:
            cmd.extend(["-threads", str(options.threads)])

        # Audio stream mapping (pick primary audio stream)
        audio_stream_idx = primary_audio.index if primary_audio else 0
        cmd.extend(["-map", f"0:{audio_stream_idx}"])

        if should_copy:
            # Lossless stream copy
            cmd.extend(["-c:a", "copy"])
        else:
            # Re-encoding path
            profile = options.profile or get_profile(
                target_fmt,
                preset=options.preset,
            )
            prof_args = profile.to_ffmpeg_args()

            # Separate out any profile -af / -filter:a flags for unified filter chaining
            final_audio_filters: list[str] = []
            i = 0
            while i < len(prof_args):
                arg = prof_args[i]
                if arg in ("-af", "-filter:a") and i + 1 < len(prof_args):
                    final_audio_filters.append(prof_args[i + 1])
                    i += 2
                else:
                    cmd.append(arg)
                    i += 1

            # Audio filter config if supplied
            if options.audio_filter_config:
                filt_str = build_filter_string(options.audio_filter_config)
                if filt_str:
                    final_audio_filters.append(filt_str)

            # EBU R128 Loudness Normalization
            if options.ebu_r128:
                norm_cfg = LoudnessConfig(two_pass=options.two_pass_loudnorm)
                loudnorm_filter = build_normalizer_filter(
                    input_path=options.input_path,
                    config=norm_cfg,
                    ffmpeg_path=self.ffmpeg_path,
                )
                final_audio_filters.append(loudnorm_filter)

            if final_audio_filters:
                cmd.extend(["-af", ",".join(final_audio_filters)])

        # Strip video/subtitles/data streams
        cmd.extend(["-vn", "-sn", "-dn"])

        # Base metadata via FFmpeg flags
        if options.embed_metadata:
            meta = options.custom_metadata or extract_metadata(
                options.input_path,
                ffprobe_path=self.ffprobe_path,
                ffmpeg_path=self.ffmpeg_path,
                extract_cover=False,  # Cover art embedded post-transcode via Mutagen
            )
            meta_args = build_ffmpeg_metadata_args(meta, target_fmt.value)
            cmd.extend(meta_args)

        # Output file path
        cmd.append(str(resolved_output.resolve()))

        return cmd, should_copy

    def transcode(
        self,
        options: TranscodeOptions,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
    ) -> TranscodeResult:
        """
        Executes the transcoding pipeline synchronously, dispatching progress
        updates to `progress_callback`.

        Args:
            options: Transcode configuration.
            progress_callback: Optional callable receiving TranscodeProgress updates.

        Returns:
            TranscodeResult describing the conversion outcome.
        """
        start_time = time.time()
        in_path = Path(options.input_path).resolve()
        if not in_path.is_file():
            raise FileNotFoundError(f"Input file not found: {in_path}")

        target_fmt = AudioFormat.from_string(options.format)

        # Determine output file path
        if options.output_path:
            out_path = Path(options.output_path).resolve()
        else:
            out_path = in_path.with_suffix(f".{target_fmt.value}")

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Probe input file
        probe = probe_media(in_path, ffprobe_path=self.ffprobe_path)
        if not probe.has_audio:
            raise TranscodeError(f"No audio stream detected in file: {in_path}")

        total_duration = probe.duration

        # Extract metadata and artwork upfront if requested
        extracted_meta: Optional[MediaMetadata] = None
        if options.embed_metadata:
            if options.custom_metadata:
                extracted_meta = options.custom_metadata
            else:
                extracted_meta = extract_metadata(
                    in_path,
                    ffprobe_path=self.ffprobe_path,
                    ffmpeg_path=self.ffmpeg_path,
                    extract_cover=options.extract_cover_art,
                )

        # Build FFmpeg command
        cmd, is_lossless = self.build_command(options, probe, out_path)

        # Execute process
        process: Optional[subprocess.Popen] = None
        stderr_buffer: list[str] = []
        is_cancelled = False
        error_msg: Optional[str] = None

        def read_stderr(proc: subprocess.Popen) -> None:
            if proc.stderr:
                for line in iter(proc.stderr.readline, ""):
                    if line:
                        stderr_buffer.append(line)
                proc.stderr.close()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_get_creation_flags(),
            )

            # Background thread to capture stderr
            stderr_thread = threading.Thread(
                target=read_stderr,
                args=(process,),
                daemon=True,
            )
            stderr_thread.start()

            current_time = 0.0
            speed = 0.0
            bitrate = "N/A"
            total_size = 0
            fps = 0.0

            # Process progress updates from stdout
            if process.stdout:
                for line in iter(process.stdout.readline, ""):
                    # Check cancellation token
                    if options.cancellation_event and options.cancellation_event.is_set():
                        is_cancelled = True
                        break

                    stripped = line.strip()
                    if not stripped:
                        continue

                    if "=" in stripped:
                        key, val = stripped.split("=", 1)
                        key = key.strip()
                        val = val.strip()

                        if key == "out_time":
                            current_time = _parse_time_str(val)
                        elif key == "speed":
                            speed = _parse_speed(val)
                        elif key == "bitrate":
                            bitrate = val
                        elif key == "total_size":
                            try:
                                total_size = int(val)
                            except ValueError:
                                pass
                        elif key == "fps":
                            try:
                                fps = float(val)
                            except ValueError:
                                pass
                        elif key == "progress":
                            # Compute percentage and ETA
                            percent = (
                                min(100.0, (current_time / total_duration) * 100.0)
                                if total_duration > 0
                                else 0.0
                            )
                            eta = None
                            if speed > 0 and total_duration > current_time:
                                eta = (total_duration - current_time) / speed

                            p = TranscodeProgress(
                                percent=round(percent, 2),
                                current_time=round(current_time, 2),
                                total_duration=round(total_duration, 2),
                                speed=round(speed, 2),
                                bitrate=bitrate,
                                size_bytes=total_size,
                                eta_seconds=round(eta, 1) if eta else None,
                                fps=round(fps, 1),
                                status="processing" if val != "end" else "completed",
                            )
                            if progress_callback:
                                progress_callback(p)

                process.stdout.close()

            # Handle cancellation
            if is_cancelled:
                self._terminate_process(process)
                stderr_thread.join(timeout=2)
                # Cleanup partial output
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                if progress_callback:
                    progress_callback(
                        TranscodeProgress(
                            percent=0.0,
                            current_time=current_time,
                            total_duration=total_duration,
                            status="cancelled",
                        )
                    )
                return TranscodeResult(
                    success=False,
                    cancelled=True,
                    input_path=in_path,
                    output_path=out_path,
                    target_format=target_fmt.value,
                    was_lossless_copy=is_lossless,
                    duration=total_duration,
                    file_size=0,
                    elapsed_time=round(time.time() - start_time, 2),
                    error_message="Transcode cancelled by user.",
                )

            # Wait for completion
            retcode = process.wait()
            stderr_thread.join(timeout=5)

            if retcode != 0:
                err_text = "".join(stderr_buffer).strip()
                error_msg = f"FFmpeg exited with error code {retcode}: {err_text[-500:]}"
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                if progress_callback:
                    progress_callback(
                        TranscodeProgress(
                            percent=0.0,
                            total_duration=total_duration,
                            status="error",
                        )
                    )
                return TranscodeResult(
                    success=False,
                    cancelled=False,
                    input_path=in_path,
                    output_path=out_path,
                    target_format=target_fmt.value,
                    was_lossless_copy=is_lossless,
                    duration=total_duration,
                    file_size=0,
                    elapsed_time=round(time.time() - start_time, 2),
                    error_message=error_msg,
                )

        except Exception as exc:
            if process:
                self._terminate_process(process)
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            raise TranscodeError(f"Transcoding failed for '{in_path}': {exc}") from exc

        # Post-transcode metadata & cover art application via Mutagen
        meta_applied = False
        if options.embed_metadata and extracted_meta and out_path.exists():
            try:
                meta_applied = apply_metadata(out_path, extracted_meta)
            except Exception:
                meta_applied = False

        # Final progress signal
        final_size = out_path.stat().st_size if out_path.exists() else total_size
        elapsed = round(time.time() - start_time, 2)
        if progress_callback:
            progress_callback(
                TranscodeProgress(
                    percent=100.0,
                    current_time=total_duration,
                    total_duration=total_duration,
                    size_bytes=final_size,
                    status="completed",
                )
            )

        return TranscodeResult(
            success=True,
            cancelled=False,
            input_path=in_path,
            output_path=out_path,
            target_format=target_fmt.value,
            was_lossless_copy=is_lossless,
            duration=total_duration,
            file_size=final_size,
            elapsed_time=elapsed,
            error_message=None,
            metadata_embedded=meta_applied,
        )

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        """Safely and decisively terminates a subprocess on Windows/POSIX."""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass
