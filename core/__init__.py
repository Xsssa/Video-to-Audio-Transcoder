"""
Enterprise Video to Audio Converter Core Engine.

This package provides low-level media probing, audio encoding profiles,
EBU R128 loudness normalization, metadata extraction & embedding,
and a robust FFmpeg execution pipeline with real-time telemetry and cancellation.
"""

from __future__ import annotations

# FFmpeg discovery and verification
from core.ffmpeg_finder import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    FFmpegVerification,
    FFprobeNotFoundError,
    find_config_file,
    find_ffmpeg,
    find_ffprobe,
    get_ffmpeg_version,
    get_ffprobe_version,
    load_config,
    verify_ffmpeg_installation,
)

# Media analysis & probing
from core.probe import (
    AudioStreamInfo,
    MediaProbeResult,
    ProbeError,
    VideoStreamInfo,
    probe_media,
)

# Audio profiles & formats
from core.audio_profiles import (
    PRESETS,
    AudioFormat,
    AudioProfile,
    BitrateMode,
    can_lossless_copy,
    get_profile,
)

# Loudness normalization
from core.normalizer import (
    LoudnessConfig,
    LoudnessError,
    LoudnessMeasurement,
    build_loudnorm_filter_string,
    build_normalizer_filter,
    measure_loudness,
    parse_loudnorm_json,
)

# Metadata & artwork
from core.metadata import (
    MediaMetadata,
    apply_metadata,
    build_ffmpeg_metadata_args,
    detect_image_mime,
    embed_metadata_with_mutagen,
    extract_cover_art_bytes,
    extract_metadata,
)

# Audio filters & DSP
from core.audio_filters import (
    EQ_PRESETS,
    AudioFilterConfig,
    build_audio_filter_chain,
    build_filter_string,
    generate_itur_bs775_matrix,
    get_itur_bs775_downmix_filter,
)

# Transcoder engine
from core.transcoder import (
    TranscodeCancelled,
    TranscodeError,
    TranscodeOptions,
    TranscodeProgress,
    TranscodeResult,
    Transcoder,
)

__all__ = [
    # FFmpeg finder
    "FFmpegExecutionError",
    "FFmpegNotFoundError",
    "FFmpegVerification",
    "FFprobeNotFoundError",
    "find_config_file",
    "find_ffmpeg",
    "find_ffprobe",
    "get_ffmpeg_version",
    "get_ffprobe_version",
    "load_config",
    "verify_ffmpeg_installation",
    # Probe
    "AudioStreamInfo",
    "MediaProbeResult",
    "ProbeError",
    "VideoStreamInfo",
    "probe_media",
    # Audio profiles
    "PRESETS",
    "AudioFormat",
    "AudioProfile",
    "BitrateMode",
    "can_lossless_copy",
    "get_profile",
    # Normalizer
    "LoudnessConfig",
    "LoudnessError",
    "LoudnessMeasurement",
    "build_loudnorm_filter_string",
    "build_normalizer_filter",
    "measure_loudness",
    "parse_loudnorm_json",
    # Metadata
    "MediaMetadata",
    "apply_metadata",
    "build_ffmpeg_metadata_args",
    "detect_image_mime",
    "embed_metadata_with_mutagen",
    "extract_cover_art_bytes",
    "extract_metadata",
    # Audio filters & DSP
    "EQ_PRESETS",
    "AudioFilterConfig",
    "build_audio_filter_chain",
    "build_filter_string",
    "generate_itur_bs775_matrix",
    "get_itur_bs775_downmix_filter",
    # Transcoder
    "TranscodeCancelled",
    "TranscodeError",
    "TranscodeOptions",
    "TranscodeProgress",
    "TranscodeResult",
    "Transcoder",
]
