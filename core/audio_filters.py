"""
audio_filters.py - Professional Digital Signal Processing (DSP) and Audio Filter Chain System.

Provides:
- AudioFilterConfig dataclass with parameters for volume, EQ presets, highpass/lowpass,
  tempo scaling, pitch shifting, dynamic range compression, limiter, loudness normalization,
  and multichannel downmixing.
- EQ_PRESETS dictionary covering studio and broadcast equalization curves:
  * bass_boost: lowshelf filter at 100Hz (+6dB)
  * vocal_clarity: peaking filter at 3kHz (+3.5dB), highpass at 120Hz
  * podcast_enhancer: highpass at 80Hz (anti-rumble), compressor (acompressor), de-esser / vocal shelf
  * treble_boost: highshelf at 8kHz (+4dB)
  * flat: neutral
- build_audio_filter_chain(): Assembles a unified FFmpeg -af filter string argument list.
  Automatically incorporates lookahead brickwall limiter (alimiter=limit=-0.5dB) when
  volume boosting or when explicit limiter is requested to prevent digital clipping/inter-sample peaks.
- generate_itur_bs775_matrix(): Generates ITU-R BS.775 compliant downmix matrices for 5.1 and 7.1
  surround sound to stereo with peak-normalized coefficients to guarantee zero clipping.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional


# ============================================================================
# Equalization Presets
# ============================================================================

EQ_PRESETS: dict[str, str] = {
    "bass_boost": "lowshelf=f=100:g=6",
    "vocal_clarity": "highpass=f=120,equalizer=f=3000:t=q:w=1:g=3.5",
    "podcast_enhancer": "highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50,highshelf=f=6000:g=-3",
    "treble_boost": "highshelf=f=8000:g=4",
    "flat": "anull",
}


# ============================================================================
# ITU-R BS.775 Multichannel Downmix Matrix Generator
# ============================================================================

def generate_itur_bs775_matrix(
    channels_or_layout: int | str = 6,
    normalize: bool = True,
) -> str:
    """
    Generates an ITU-R BS.775 compliant FFmpeg 'pan' audio filter string for
    downmixing multi-channel audio (5.1 or 7.1) into stereo without clipping.

    ITU-R BS.775 specifications:
    - 5.1 downmix:
        Left  = FL + 0.7071 * FC + 0.7071 * BL
        Right = FR + 0.7071 * FC + 0.7071 * BR
        Sum of weights: 1.0 + 2 * (1 / sqrt(2)) = 1 + sqrt(2) ≈ 2.414214
        When normalize=True, all coefficients are scaled by 1 / 2.414214 ≈ 0.414214
        Normalized coefficients:
          FL/FR = 0.414214, FC = 0.292893, BL/BR = 0.292893
          Sum = 0.414214 + 0.292893 + 0.292893 = 1.000000 (0 dBFS peak safe)

    - 7.1 downmix:
        Left  = FL + 0.7071 * FC + 0.7071 * SL + 0.5 * BL
        Right = FR + 0.7071 * FC + 0.7071 * SR + 0.5 * BR
        Sum of weights: 1.0 + sqrt(2) + 0.5 ≈ 2.914214
        When normalize=True, all coefficients are scaled by 1 / 2.914214 ≈ 0.343146
        Normalized coefficients:
          FL/FR = 0.343146, FC = 0.242641, SL/SR = 0.242641, BL/BR = 0.171573
          Sum = 0.343146 + 0.242641 + 0.242641 + 0.171573 = 1.000001 (0 dBFS peak safe)

    Args:
        channels_or_layout: Multi-channel configuration (6, 8, "5.1", "7.1", "5.1(side)").
        normalize: If True, normalizes coefficients so maximum sum equals 1.0 (no clipping).

    Returns:
        str: FFmpeg 'pan' filter expression.
    """
    clean_layout = str(channels_or_layout).strip().lower()

    if clean_layout in ("6", "5.1", "5.1(side)", "surround51"):
        if normalize:
            # Scale = 1 / (1 + sqrt(2)) = sqrt(2) - 1 ≈ 0.414214
            scale = math.sqrt(2) - 1.0
            fl_gain = scale
            fc_gain = scale * (1.0 / math.sqrt(2))
            surround_gain = scale * (1.0 / math.sqrt(2))
            return (
                f"pan=stereo|"
                f"FL={fl_gain:.6f}*FL+{fc_gain:.6f}*FC+{surround_gain:.6f}*BL|"
                f"FR={fl_gain:.6f}*FR+{fc_gain:.6f}*FC+{surround_gain:.6f}*BR"
            )
        else:
            return (
                "pan=stereo|"
                "FL=1.0*FL+0.707107*FC+0.707107*BL|"
                "FR=1.0*FR+0.707107*FC+0.707107*BR"
            )

    elif clean_layout in ("8", "7.1", "surround71"):
        if normalize:
            # Scale = 1 / (1 + sqrt(2) + 0.5) = 1 / (1.5 + sqrt(2)) ≈ 0.343146
            total_sum = 1.5 + math.sqrt(2)
            scale = 1.0 / total_sum
            fl_gain = scale
            fc_gain = scale * (1.0 / math.sqrt(2))
            sl_gain = scale * (1.0 / math.sqrt(2))
            bl_gain = scale * 0.5
            return (
                f"pan=stereo|"
                f"FL={fl_gain:.6f}*FL+{fc_gain:.6f}*FC+{sl_gain:.6f}*SL+{bl_gain:.6f}*BL|"
                f"FR={fl_gain:.6f}*FR+{fc_gain:.6f}*FC+{sl_gain:.6f}*SR+{bl_gain:.6f}*BR"
            )
        else:
            return (
                "pan=stereo|"
                "FL=1.0*FL+0.707107*FC+0.707107*SL+0.5*BL|"
                "FR=1.0*FR+0.707107*FC+0.707107*SR+0.5*BR"
            )

    raise ValueError(
        f"Unsupported channel layout for ITU-R BS.775 downmix: '{channels_or_layout}'. "
        f"Supported layouts: 6, 8, '5.1', '7.1'."
    )


def get_itur_bs775_downmix_filter(channels: int | str = 6, normalize: bool = True) -> str:
    """Convenience alias for generate_itur_bs775_matrix."""
    return generate_itur_bs775_matrix(channels, normalize=normalize)


# ============================================================================
# Audio Filter Configuration
# ============================================================================

@dataclass
class AudioFilterConfig:
    """
    Configuration parameters for professional audio digital signal processing (DSP) filters.

    Attributes:
        volume_db: Volume adjustment in decibels (e.g. +3.0 for boost, -3.0 for reduction).
        eq_preset: Equalizer preset name from EQ_PRESETS ('bass_boost', 'vocal_clarity',
                   'podcast_enhancer', 'treble_boost', 'flat').
        highpass_hz: High-pass cut-off frequency in Hz (e.g. 80 to remove sub-bass rumble).
        lowpass_hz: Low-pass cut-off frequency in Hz (e.g. 15000 to eliminate high-frequency hiss).
        tempo: Audio playback tempo factor without changing pitch (e.g. 1.25 for 25% faster).
        pitch: Pitch scale factor without altering playback speed (e.g. 1.1 for higher pitch).
        dynamic_range_compression: Enables broadcast-quality downward dynamic range compressor.
        limiter: Enables lookahead brickwall limiter (alimiter=limit=-0.5dB) to guarantee
                 no digital clipping or inter-sample peaks. (Automatically active if volume_db > 0).
        loudnorm_target: Target loudness level for EBU R128 normalization (e.g. '-16', '-14', '-23',
                         'podcast', 'streaming').
        downmix: Downmix layout ('5.1', '7.1') using ITU-R BS.775 matrix.
    """
    volume_db: Optional[float] = None
    eq_preset: Optional[str] = None
    highpass_hz: Optional[int] = None
    lowpass_hz: Optional[int] = None
    tempo: Optional[float] = None
    pitch: Optional[float] = None
    dynamic_range_compression: bool = False
    limiter: bool = False
    loudnorm_target: Optional[str] = None
    downmix: Optional[str] = None

    def to_ffmpeg_args(self) -> list[str]:
        """Convert this configuration into FFmpeg -af CLI arguments."""
        return build_audio_filter_chain(self)

    def to_filter_string(self) -> str:
        """Convert this configuration into a single comma-separated filtergraph string."""
        return build_filter_string(self)


# ============================================================================
# Filter Chain Builder Helpers
# ============================================================================

def _parse_loudnorm_target_lufs(target: str) -> float:
    """Extract integrated loudness LUFS target from string."""
    clean = target.strip().lower()
    if clean in ("streaming", "spotify", "youtube"):
        return -14.0
    if clean in ("podcast", "broadcast", "ebu_r128_podcast"):
        return -16.0
    if clean in ("broadcast_tv", "ebu_r128", "ebu"):
        return -23.0

    match = re.search(r"-?\d+(?:\.\d+)?", clean)
    if match:
        val = float(match.group(0))
        # Ensure it's negative LUFS (e.g. if passed as 16, convert to -16.0)
        return -abs(val)
    return -16.0


def _build_tempo_filter_chain(tempo: float) -> list[str]:
    """
    Builds atempo filter chain supporting arbitrary tempo scaling factors.
    FFmpeg's atempo filter natively supports 0.5 to 2.0; values outside this
    range are chained sequentially.
    """
    filters: list[str] = []
    t = float(tempo)
    if t <= 0:
        raise ValueError(f"Tempo scale factor must be positive, got {tempo}")

    # Handle extreme fast speeds
    while t > 2.0:
        filters.append("atempo=2.0")
        t /= 2.0

    # Handle extreme slow speeds
    while t < 0.5:
        filters.append("atempo=0.5")
        t /= 0.5

    # Append remaining scale factor if not identity
    if abs(t - 1.0) > 0.0001:
        filters.append(f"atempo={t:.4g}")
    elif not filters:
        # Exactly 1.0
        pass

    return filters


def _build_pitch_filter(pitch: float) -> list[str]:
    """
    Builds pitch shift filters using asetrate + atempo + aresample.
    Allows pitch shifting without changing duration.
    """
    p = float(pitch)
    if p <= 0:
        raise ValueError(f"Pitch scale factor must be positive, got {pitch}")
    if abs(p - 1.0) < 0.0001:
        return []

    # Shift sample rate by pitch, compensate duration with atempo, and resample back to 44.1kHz
    inv_pitch = 1.0 / p
    tempo_filters = _build_tempo_filter_chain(inv_pitch)
    tempo_str = ",".join(tempo_filters) if tempo_filters else "atempo=1.0"
    return [f"asetrate=44100*{p:.4g}", tempo_str, "aresample=44100"]


# ============================================================================
# Main Filter Chain Assembly
# ============================================================================

def build_filter_string(config: AudioFilterConfig) -> str:
    """
    Combines all configured filters into a single comma-separated FFmpeg filtergraph string.
    Returns empty string if no filters are active.
    """
    filters: list[str] = []

    # 1. Channel Downmix (ITU-R BS.775)
    if config.downmix:
        filters.append(generate_itur_bs775_matrix(config.downmix, normalize=True))

    # 2. High-pass cut-off (anti-rumble)
    if config.highpass_hz is not None and config.highpass_hz > 0:
        filters.append(f"highpass=f={config.highpass_hz}")

    # 3. Low-pass cut-off (hiss removal)
    if config.lowpass_hz is not None and config.lowpass_hz > 0:
        filters.append(f"lowpass=f={config.lowpass_hz}")

    # 4. Equalizer Preset
    if config.eq_preset:
        preset_key = config.eq_preset.strip().lower()
        if preset_key in EQ_PRESETS:
            eq_val = EQ_PRESETS[preset_key]
            if eq_val and eq_val != "anull":
                filters.append(eq_val)
        else:
            # Check without underscores/hyphens
            clean_key = preset_key.replace("-", "_")
            if clean_key in EQ_PRESETS and EQ_PRESETS[clean_key] != "anull":
                filters.append(EQ_PRESETS[clean_key])

    # 5. Dynamic Range Compression (downward compressor)
    if config.dynamic_range_compression:
        filters.append("acompressor=threshold=-18dB:ratio=4:attack=10:release=100")

    # 6. Volume Adjustment
    has_volume_boost = False
    if config.volume_db is not None and abs(config.volume_db) > 0.001:
        filters.append(f"volume={config.volume_db:g}dB")
        if config.volume_db > 0.0:
            has_volume_boost = True

    # 7. Lookahead Limiter (prevents digital clipping from volume boost or explicit request)
    if config.limiter or has_volume_boost:
        filters.append("alimiter=limit=-0.5dB")

    # 8. Pitch Shifting
    if config.pitch is not None and abs(config.pitch - 1.0) > 0.0001:
        filters.extend(_build_pitch_filter(config.pitch))

    # 9. Tempo Scaling (atempo 0.5 to 2.0 without changing pitch)
    if config.tempo is not None and abs(config.tempo - 1.0) > 0.0001:
        filters.extend(_build_tempo_filter_chain(config.tempo))

    # 10. Loudness Normalization (EBU R128 loudnorm target)
    if config.loudnorm_target:
        target_i = _parse_loudnorm_target_lufs(config.loudnorm_target)
        filters.append(f"loudnorm=I={target_i:.1f}:TP=-1.5:LRA=11")

    return ",".join(filters)


def build_audio_filter_chain(config: AudioFilterConfig) -> list[str]:
    """
    Combines EQ, volume boost with alimiter (to prevent digital clipping: alimiter=limit=-0.5dB),
    tempo (atempo filter: 0.5 to 2.0 without changing pitch), and loudness normalization
    into a unified FFmpeg -af filter string argument list.

    Returns:
        list[str]: ['-af', filter_string] if filters are configured, otherwise [].
    """
    filter_string = build_filter_string(config)
    if filter_string:
        return ["-af", filter_string]
    return []
