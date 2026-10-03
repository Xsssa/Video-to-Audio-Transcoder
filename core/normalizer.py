"""
normalizer.py - EBU R128 professional loudness normalization module.

Implements both Single-Pass and Two-Pass EBU R128 loudness normalization
using FFmpeg's 'loudnorm' audio filter.

Target standards:
- Integrated Loudness (I): -16.0 LUFS (optimal for modern streaming/podcasts)
- True Peak (TP): -1.5 dBFS (prevents inter-sample clipping during lossy encoding)
- Loudness Range (LRA): 11.0 LU

Two-pass normalization analyzes the entire audio file in the first pass to measure
integrated loudness, true peak, LRA, and threshold, and applies linear normalization
in the second pass to avoid unnecessary dynamic compression.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg


class LoudnessError(RuntimeError):
    """Raised when loudness measurement or normalization fails."""
    pass


@dataclass
class LoudnessConfig:
    """Configuration parameters for EBU R128 loudness normalization."""
    target_i: float = -16.0    # Integrated Loudness Target (LUFS)
    target_tp: float = -1.5   # Maximum True Peak (dBFS)
    target_lra: float = 11.0  # Loudness Range Target (LU)
    two_pass: bool = True     # Use 2-pass analysis for linear normalization
    dual_mono: bool = False   # Treat mono audio as dual mono


@dataclass(frozen=True)
class LoudnessMeasurement:
    """Measured loudness statistics returned from FFmpeg first-pass analysis."""
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    output_i: float
    output_tp: float
    output_lra: float
    output_thresh: float
    normalization_type: str
    target_offset: float
    raw_json: dict[str, Any] = field(default_factory=dict, repr=False)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Parse float safely, handling null or invalid numbers."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_loudnorm_json(output_text: str) -> Optional[dict[str, Any]]:
    """
    Locates and parses the JSON telemetry emitted by loudnorm in FFmpeg stderr.
    FFmpeg outputs a JSON object starting with { and containing "input_i".
    """
    match = re.search(r"\{\s*\"input_i\"[\s\S]*?\}", output_text)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def measure_loudness(
    input_path: str | Path,
    ffmpeg_path: Optional[str | Path] = None,
    config: Optional[LoudnessConfig] = None,
) -> LoudnessMeasurement:
    """
    Executes Pass 1 of EBU R128 loudness measurement on the source media file.

    Args:
        input_path: Path to the input media file.
        ffmpeg_path: Optional path to ffmpeg binary.
        config: Optional LoudnessConfig.

    Returns:
        LoudnessMeasurement containing the analyzed loudness metrics.

    Raises:
        FileNotFoundError: If input file is not found.
        LoudnessError: If FFmpeg fails or cannot measure loudness.
    """
    path_obj = Path(input_path).resolve()
    if not path_obj.is_file():
        raise FileNotFoundError(f"Source file not found for loudness measurement: {path_obj}")

    cfg = config or LoudnessConfig()
    ffmpeg_bin = find_ffmpeg(custom_path=ffmpeg_path)

    # First pass loudnorm filter string with print_format=json
    dual_mono_str = ":dual_mono=true" if cfg.dual_mono else ""
    filter_expr = f"loudnorm=I={cfg.target_i}:TP={cfg.target_tp}:LRA={cfg.target_lra}{dual_mono_str}:print_format=json"

    cmd = [
        str(ffmpeg_bin),
        "-nostats",
        "-i",
        str(path_obj),
        "-vn",
        "-sn",
        "-dn",
        "-filter:a",
        filter_expr,
        "-f",
        "null",
        "-",
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
        )
    except subprocess.CalledProcessError as exc:
        raise LoudnessError(
            f"Loudness measurement failed for '{path_obj}': {exc.stderr.strip()}"
        ) from exc
    except Exception as exc:
        raise LoudnessError(f"Error executing FFmpeg loudness measurement: {exc}") from exc

    raw_json = parse_loudnorm_json(res.stderr)
    if not raw_json:
        raise LoudnessError(
            f"Could not parse loudnorm JSON measurement from FFmpeg output for '{path_obj}'."
        )

    return LoudnessMeasurement(
        input_i=_safe_float(raw_json.get("input_i")),
        input_tp=_safe_float(raw_json.get("input_tp")),
        input_lra=_safe_float(raw_json.get("input_lra")),
        input_thresh=_safe_float(raw_json.get("input_thresh")),
        output_i=_safe_float(raw_json.get("output_i")),
        output_tp=_safe_float(raw_json.get("output_tp")),
        output_lra=_safe_float(raw_json.get("output_lra")),
        output_thresh=_safe_float(raw_json.get("output_thresh")),
        normalization_type=str(raw_json.get("normalization_type", "dynamic")),
        target_offset=_safe_float(raw_json.get("target_offset")),
        raw_json=raw_json,
    )


def build_loudnorm_filter_string(
    config: Optional[LoudnessConfig] = None,
    measurement: Optional[LoudnessMeasurement] = None,
) -> str:
    """
    Constructs the FFmpeg audio filter string for loudnorm.

    If `measurement` is provided, generates the Second Pass filter string
    with measured parameters and `linear=true`.
    Otherwise, generates the Single Pass filter string.
    """
    cfg = config or LoudnessConfig()
    dual_mono_str = ":dual_mono=true" if cfg.dual_mono else ""

    if measurement is not None:
        # Pass 2 filter with exact measured metrics
        return (
            f"loudnorm=I={cfg.target_i}:TP={cfg.target_tp}:LRA={cfg.target_lra}:"
            f"measured_I={measurement.input_i:.2f}:"
            f"measured_TP={measurement.input_tp:.2f}:"
            f"measured_LRA={measurement.input_lra:.2f}:"
            f"measured_thresh={measurement.input_thresh:.2f}:"
            f"offset={measurement.target_offset:.2f}:"
            f"linear=true{dual_mono_str}"
        )

    # Single pass filter
    return f"loudnorm=I={cfg.target_i}:TP={cfg.target_tp}:LRA={cfg.target_lra}{dual_mono_str}"


def build_normalizer_filter(
    input_path: Optional[str | Path] = None,
    config: Optional[LoudnessConfig] = None,
    ffmpeg_path: Optional[str | Path] = None,
) -> str:
    """
    High-level helper to generate the optimal loudnorm filter.
    Performs pass-1 measurement if two_pass is enabled and input_path is supplied.
    Falls back to single-pass if two-pass measurement is disabled or fails gracefully.
    """
    cfg = config or LoudnessConfig()

    if cfg.two_pass and input_path:
        try:
            stats = measure_loudness(input_path, ffmpeg_path=ffmpeg_path, config=cfg)
            return build_loudnorm_filter_string(cfg, stats)
        except Exception:
            # Fall back to single pass if 2-pass fails
            return build_loudnorm_filter_string(cfg, None)

    return build_loudnorm_filter_string(cfg, None)
