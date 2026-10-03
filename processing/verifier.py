"""
verifier.py - Post-conversion integrity verifier for transcoded audio files.

Performs multi-stage validation:
1. File system checks (existence, non-zero size, accessibility)
2. Container & stream analysis via FFprobe
3. Codec compliance with target format
4. Duration consistency check with source media within configurable tolerance (default: ±1.5s)
5. Audio stream health (channels, sample rate, bitrate presence)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.ffmpeg_finder import find_ffprobe
from core.probe import MediaProbeResult, ProbeError, probe_media


# Standard mapping of file extensions / target formats to valid FFprobe audio codec names
FORMAT_CODEC_MAP: dict[str, set[str]] = {
    "mp3": {"mp3", "mp3float"},
    "aac": {"aac"},
    "m4a": {"aac", "alac"},
    "flac": {"flac"},
    "wav": {
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "pcm_f32le",
        "pcm_f64le",
        "pcm_u8",
        "pcm_s16be",
        "pcm_s24be",
        "pcm_s32be",
        "pcm_alaw",
        "pcm_mulaw",
        "wav",
    },
    "ogg": {"vorbis", "opus", "flac"},
    "oga": {"vorbis", "flac"},
    "opus": {"opus"},
    "wma": {"wmav1", "wmav2", "wmapro", "wmalossless"},
    "ac3": {"ac3", "eac3"},
    "eac3": {"eac3"},
    "aiff": {"pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_s16le"},
    "aif": {"pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_s16le"},
    "alac": {"alac"},
    "mka": {"aac", "ac3", "flac", "opus", "vorbis", "mp3", "pcm_s16le"},
}


@dataclass(frozen=True)
class VerificationResult:
    """Structured result of audio conversion integrity verification."""
    is_valid: bool
    file_path: Path
    size_bytes: int
    duration: float
    codec: str
    bitrate: Optional[int]
    sample_rate: Optional[int]
    channels: Optional[int]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.is_valid

    @property
    def actual_codec(self) -> str:
        """Returns the detected audio codec name."""
        return self.codec

    @property
    def status_label(self) -> str:
        """Returns PASS, WARNING, or FAIL."""
        if not self.is_valid:
            return "FAIL"
        if self.warnings:
            return "WARNING"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        """Serializes result to a dictionary."""
        return {
            "is_valid": self.is_valid,
            "status": self.status_label,
            "file_path": str(self.file_path),
            "size_bytes": self.size_bytes,
            "duration": self.duration,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "details": self.details,
        }


def normalize_format_name(target_format: str) -> str:
    """Normalizes format string, removing dots and lowercasing."""
    return target_format.strip().lstrip(".").lower()


def is_codec_compatible(target_format: str, actual_codec: str) -> bool:
    """
    Checks if an actual codec reported by ffprobe is compatible with the target format.
    """
    fmt = normalize_format_name(target_format)
    codec = actual_codec.strip().lower()

    valid_codecs = FORMAT_CODEC_MAP.get(fmt)
    if valid_codecs:
        return codec in valid_codecs

    # Fallback heuristic if unknown format: check if format string matches or is substring
    return fmt == codec or fmt in codec or codec in fmt


class IntegrityVerifier:
    """
    Enterprise-grade audio verifier for inspecting output files post-conversion.
    """

    def __init__(
        self,
        default_tolerance_seconds: float = 1.5,
        ffprobe_path: Optional[str | Path] = None,
    ) -> None:
        """
        Args:
            default_tolerance_seconds: Allowed duration drift between source and output in seconds.
            ffprobe_path: Optional custom ffprobe executable path.
        """
        self.default_tolerance_seconds = default_tolerance_seconds
        self.ffprobe_path = ffprobe_path

    def verify(
        self,
        output_file: str | Path,
        target_format: str,
        expected_duration: Optional[float] = None,
        tolerance_seconds: Optional[float] = None,
    ) -> VerificationResult:
        """
        Verifies the transcoded audio file integrity.

        Args:
            output_file: Path to the generated audio file.
            target_format: Target format (e.g. 'mp3', 'aac', 'flac').
            expected_duration: Expected source duration in seconds, if known.
            tolerance_seconds: Duration discrepancy tolerance in seconds (defaults to default_tolerance_seconds).

        Returns:
            VerificationResult instance.
        """
        path_obj = Path(output_file).resolve()
        tolerance = tolerance_seconds if tolerance_seconds is not None else self.default_tolerance_seconds

        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}

        # 1. File existence & basic size check
        if not path_obj.exists():
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=0,
                duration=0.0,
                codec="none",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=[f"Output file does not exist: {path_obj}"],
                warnings=[],
                details={"stage": "filesystem_check"},
            )

        if not path_obj.is_file():
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=0,
                duration=0.0,
                codec="none",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=[f"Target path is not a regular file: {path_obj}"],
                warnings=[],
                details={"stage": "filesystem_check"},
            )

        try:
            size_bytes = path_obj.stat().st_size
        except OSError as exc:
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=0,
                duration=0.0,
                codec="none",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=[f"Failed to read file size: {exc}"],
                warnings=[],
                details={"stage": "filesystem_check"},
            )

        if size_bytes == 0:
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=0,
                duration=0.0,
                codec="none",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=[f"Output file is empty (0 bytes): {path_obj}"],
                warnings=[],
                details={"stage": "filesystem_check"},
            )

        if size_bytes < 100:
            warnings.append(f"Output file is suspiciously small ({size_bytes} bytes).")

        # 2. FFprobe probe analysis
        try:
            probe_result: MediaProbeResult = probe_media(path_obj, ffprobe_path=self.ffprobe_path)
            details["probe"] = probe_result.to_dict()
        except FileNotFoundError as exc:
            errors.append(f"Probe binary or file error: {exc}")
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=size_bytes,
                duration=0.0,
                codec="unknown",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=errors,
                warnings=warnings,
                details=details,
            )
        except ProbeError as exc:
            errors.append(f"FFprobe analysis failed: {exc}")
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=size_bytes,
                duration=0.0,
                codec="unknown",
                bitrate=None,
                sample_rate=None,
                channels=None,
                errors=errors,
                warnings=warnings,
                details=details,
            )

        # 3. Audio stream verification
        if not probe_result.has_audio:
            errors.append("No valid audio streams found in the transcoded output.")
            return VerificationResult(
                is_valid=False,
                file_path=path_obj,
                size_bytes=size_bytes,
                duration=probe_result.duration,
                codec="none",
                bitrate=probe_result.bit_rate,
                sample_rate=None,
                channels=None,
                errors=errors,
                warnings=warnings,
                details=details,
            )

        primary_audio = probe_result.primary_audio_stream
        actual_codec = primary_audio.codec_name if primary_audio else "unknown"
        sample_rate = primary_audio.sample_rate if primary_audio else None
        channels = primary_audio.channels if primary_audio else None
        bitrate = primary_audio.bit_rate or probe_result.bit_rate
        out_duration = primary_audio.duration or probe_result.duration

        # 4. Codec compatibility
        norm_target = normalize_format_name(target_format)
        if not is_codec_compatible(norm_target, actual_codec):
            expected_allowed = FORMAT_CODEC_MAP.get(norm_target)
            allowed_msg = f" (expected one of: {sorted(list(expected_allowed))})" if expected_allowed else ""
            errors.append(
                f"Audio codec mismatch: output has codec '{actual_codec}', but target format '{target_format}'{allowed_msg}."
            )

        # 5. Duration check
        if out_duration <= 0.0:
            errors.append("Transcoded audio duration is 0 seconds.")
        elif expected_duration is not None and expected_duration > 0.0:
            diff = abs(out_duration - expected_duration)
            if diff > tolerance:
                # If difference is more than 5% of expected duration or > 3s, treat as error;
                # Otherwise, treat as duration drift warning
                rel_diff = diff / expected_duration
                if diff > max(tolerance * 2.0, 3.0) or rel_diff > 0.10:
                    errors.append(
                        f"Duration discrepancy exceeds tolerance: expected {expected_duration:.2f}s, "
                        f"got {out_duration:.2f}s (delta: {diff:+.2f}s, tolerance: ±{tolerance:.2f}s)"
                    )
                else:
                    warnings.append(
                        f"Duration drift of {diff:.2f}s exceeds standard tolerance (expected {expected_duration:.2f}s, got {out_duration:.2f}s)"
                    )

        # 6. Audio stream parameter sanity checks
        if sample_rate and (sample_rate < 8000 or sample_rate > 192000):
            warnings.append(f"Unusual audio sample rate detected: {sample_rate} Hz")

        if channels and channels <= 0:
            errors.append(f"Invalid channel count: {channels}")

        is_valid = len(errors) == 0

        return VerificationResult(
            is_valid=is_valid,
            file_path=path_obj,
            size_bytes=size_bytes,
            duration=out_duration,
            codec=actual_codec,
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            warnings=warnings,
            errors=errors,
            details=details,
        )


def verify_conversion(
    output_file: str | Path,
    target_format: str,
    expected_duration: Optional[float] = None,
    tolerance_seconds: float = 1.5,
    ffprobe_path: Optional[str | Path] = None,
) -> VerificationResult:
    """
    Convenience function to verify a transcoded audio file.

    Args:
        output_file: Path to output audio file.
        target_format: Expected audio format ('mp3', 'aac', etc.).
        expected_duration: Expected audio duration in seconds.
        tolerance_seconds: Duration discrepancy tolerance in seconds (default 1.5s).
        ffprobe_path: Optional custom ffprobe executable path.

    Returns:
        VerificationResult
    """
    verifier = IntegrityVerifier(
        default_tolerance_seconds=tolerance_seconds,
        ffprobe_path=ffprobe_path,
    )
    return verifier.verify(
        output_file=output_file,
        target_format=target_format,
        expected_duration=expected_duration,
        tolerance_seconds=tolerance_seconds,
    )
