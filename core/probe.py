"""
probe.py - High-precision media analyzer using FFprobe.

Executes ffprobe with '-v quiet -print_format json -show_format -show_streams' to extract
comprehensive stream and format telemetry:
- Detailed audio stream specs (codec, channels, sample rate, bit rate, duration, tags)
- Video stream identification and cover art / attached picture detection
- Container metadata, duration, size, and layout
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.ffmpeg_finder import _get_creation_flags, find_ffprobe


class ProbeError(RuntimeError):
    """Raised when ffprobe execution fails or output cannot be parsed."""
    pass


@dataclass(frozen=True)
class AudioStreamInfo:
    """Detailed metadata and encoding parameters for an audio stream."""
    index: int
    codec_name: str
    codec_long_name: str
    channels: int
    channel_layout: Optional[str]
    sample_rate: int
    bit_rate: Optional[int]
    duration: Optional[float]
    tags: dict[str, str] = field(default_factory=dict)
    disposition: dict[str, int] = field(default_factory=dict)
    is_default: bool = False

    @property
    def language(self) -> Optional[str]:
        """Extract language tag if present."""
        return self.tags.get("language") or self.tags.get("LANGUAGE")

    @property
    def title(self) -> Optional[str]:
        """Extract stream title tag if present."""
        return self.tags.get("title") or self.tags.get("TITLE")


@dataclass(frozen=True)
class VideoStreamInfo:
    """Metadata for a video stream or attached picture."""
    index: int
    codec_name: str
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    is_attached_pic: bool
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaProbeResult:
    """Complete probe analysis of a media container."""
    file_path: Path
    format_name: str
    format_long_name: str
    duration: float
    size_bytes: int
    bit_rate: Optional[int]
    audio_streams: list[AudioStreamInfo]
    video_streams: list[VideoStreamInfo]
    has_video: bool
    has_cover_art: bool
    cover_art_stream_index: Optional[int]
    tags: dict[str, str] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def primary_audio_stream(self) -> Optional[AudioStreamInfo]:
        """
        Returns the preferred audio stream:
        1. First stream with 'default' disposition.
        2. First audio stream in container.
        3. None if no audio stream exists.
        """
        if not self.audio_streams:
            return None
        for stream in self.audio_streams:
            if stream.is_default:
                return stream
        return self.audio_streams[0]

    @property
    def has_audio(self) -> bool:
        """Indicates whether at least one audio stream is present."""
        return len(self.audio_streams) > 0

    def get_audio_streams_summary(self) -> list[dict[str, Any]]:
        """
        Returns a structured summary list of all audio streams in the media container.

        Each dictionary contains:
        - index (int): Audio stream index
        - codec (str): Audio codec name (e.g. 'aac', 'mp3', 'flac')
        - channels (int): Audio channels count (e.g. 2, 6)
        - language (Optional[str]): Language code if present (e.g. 'eng', 'jpn')
        - title (Optional[str]): Track title if tagged
        - is_default (bool): Whether the stream has default disposition
        """
        summary: list[dict[str, Any]] = []
        for s in self.audio_streams:
            summary.append(
                {
                    "index": s.index,
                    "codec": s.codec_name,
                    "codec_name": s.codec_name,
                    "channels": s.channels,
                    "language": s.language,
                    "title": s.title,
                    "is_default": s.is_default,
                }
            )
        return summary

    def to_dict(self) -> dict[str, Any]:
        """Serialize probe analysis to structured dictionary."""
        return {
            "file_path": str(self.file_path),
            "format_name": self.format_name,
            "format_long_name": self.format_long_name,
            "duration": self.duration,
            "size_bytes": self.size_bytes,
            "bit_rate": self.bit_rate,
            "has_video": self.has_video,
            "has_cover_art": self.has_cover_art,
            "cover_art_stream_index": self.cover_art_stream_index,
            "tags": self.tags,
            "audio_streams": [
                {
                    "index": s.index,
                    "codec_name": s.codec_name,
                    "codec_long_name": s.codec_long_name,
                    "channels": s.channels,
                    "channel_layout": s.channel_layout,
                    "sample_rate": s.sample_rate,
                    "bit_rate": s.bit_rate,
                    "duration": s.duration,
                    "tags": s.tags,
                    "is_default": s.is_default,
                }
                for s in self.audio_streams
            ],
            "video_streams": [
                {
                    "index": v.index,
                    "codec_name": v.codec_name,
                    "width": v.width,
                    "height": v.height,
                    "fps": v.fps,
                    "is_attached_pic": v.is_attached_pic,
                    "tags": v.tags,
                }
                for v in self.video_streams
            ],
        }


def _safe_int(val: Any) -> Optional[int]:
    """Helper to parse integer values from probe strings."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> Optional[float]:
    """Helper to parse float values from probe strings."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_fps(fps_str: Optional[str]) -> Optional[float]:
    """Helper to parse framerate from ratio string e.g. '30000/1001' or '25/1'."""
    if not fps_str or fps_str == "0/0":
        return None
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/", 1)
            denominator = float(den)
            return float(num) / denominator if denominator != 0 else None
        return float(fps_str)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def probe_media(
    file_path: str | Path,
    ffprobe_path: Optional[str | Path] = None,
) -> MediaProbeResult:
    """
    Analyzes a media file with ffprobe and returns structured metadata.

    Args:
        file_path: Path to the media file.
        ffprobe_path: Optional path to ffprobe binary.

    Returns:
        MediaProbeResult: Comprehensive probe information.

    Raises:
        FileNotFoundError: If input file doesn't exist.
        ProbeError: If ffprobe fails or output is malformed.
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.is_file():
        raise FileNotFoundError(f"Input media file not found: {path_obj}")

    probe_bin = find_ffprobe(custom_path=ffprobe_path)

    cmd = [
        str(probe_bin),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path_obj),
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_get_creation_flags(),
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() if exc.stderr else f"Exit code {exc.returncode}"
        raise ProbeError(f"ffprobe failed for '{path_obj}': {err}") from exc
    except Exception as exc:
        raise ProbeError(f"Error executing ffprobe for '{path_obj}': {exc}") from exc

    try:
        raw_data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Failed to parse ffprobe JSON output: {exc}") from exc

    format_data: dict[str, Any] = raw_data.get("format", {})
    streams_data: list[dict[str, Any]] = raw_data.get("streams", [])

    # Format info
    format_name = format_data.get("format_name", "unknown")
    format_long_name = format_data.get("format_long_name", "")
    container_duration = _safe_float(format_data.get("duration")) or 0.0
    container_size = _safe_int(format_data.get("size")) or (path_obj.stat().st_size if path_obj.exists() else 0)
    container_bitrate = _safe_int(format_data.get("bit_rate"))
    container_tags: dict[str, str] = {
        str(k): str(v) for k, v in format_data.get("tags", {}).items()
    }

    audio_streams: list[AudioStreamInfo] = []
    video_streams: list[VideoStreamInfo] = []
    has_real_video = False
    has_cover = False
    cover_index: Optional[int] = None

    for s in streams_data:
        codec_type = s.get("codec_type", "").lower()
        idx = int(s.get("index", len(audio_streams) + len(video_streams)))
        disposition = {
            k: int(v) for k, v in s.get("disposition", {}).items() if str(v).isdigit()
        }
        tags = {str(k): str(v) for k, v in s.get("tags", {}).items()}

        if codec_type == "audio":
            sample_rate = _safe_int(s.get("sample_rate")) or 44100
            channels = _safe_int(s.get("channels")) or 2
            channel_layout = s.get("channel_layout")
            bit_rate = _safe_int(s.get("bit_rate"))
            duration = _safe_float(s.get("duration")) or container_duration
            is_default = disposition.get("default", 0) == 1

            audio_streams.append(
                AudioStreamInfo(
                    index=idx,
                    codec_name=s.get("codec_name", "unknown"),
                    codec_long_name=s.get("codec_long_name", ""),
                    channels=channels,
                    channel_layout=channel_layout,
                    sample_rate=sample_rate,
                    bit_rate=bit_rate,
                    duration=duration,
                    tags=tags,
                    disposition=disposition,
                    is_default=is_default,
                )
            )

        elif codec_type == "video":
            is_attached = disposition.get("attached_pic", 0) == 1
            codec_name = s.get("codec_name", "unknown").lower()

            # Some files don't mark attached_pic disposition but contain a single mjpeg/png frame
            if not is_attached and codec_name in ("mjpeg", "png", "bmp", "jpeg"):
                nb_frames = _safe_int(s.get("nb_frames"))
                if nb_frames == 1 or s.get("avg_frame_rate") == "0/0":
                    is_attached = True

            width = _safe_int(s.get("width"))
            height = _safe_int(s.get("height"))
            fps = _parse_fps(s.get("avg_frame_rate") or s.get("r_frame_rate"))

            if is_attached:
                has_cover = True
                if cover_index is None:
                    cover_index = idx
            else:
                has_real_video = True

            video_streams.append(
                VideoStreamInfo(
                    index=idx,
                    codec_name=codec_name,
                    width=width,
                    height=height,
                    fps=fps,
                    is_attached_pic=is_attached,
                    tags=tags,
                )
            )

    # Fallback duration if container duration was 0
    if container_duration == 0.0 and audio_streams:
        for a_s in audio_streams:
            if a_s.duration and a_s.duration > 0.0:
                container_duration = a_s.duration
                break

    return MediaProbeResult(
        file_path=path_obj,
        format_name=format_name,
        format_long_name=format_long_name,
        duration=container_duration,
        size_bytes=container_size,
        bit_rate=container_bitrate,
        audio_streams=audio_streams,
        video_streams=video_streams,
        has_video=has_real_video,
        has_cover_art=has_cover,
        cover_art_stream_index=cover_index,
        tags=container_tags,
        raw_data=raw_data,
    )
