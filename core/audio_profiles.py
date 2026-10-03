"""
audio_profiles.py - Comprehensive audio encoding profile and format specification system.

Supports:
- MP3 (libmp3lame, CBR 64k-320k, VBR 0-9)
- FLAC (flac, lossless, compression 0-12, 16/24-bit)
- WAV (pcm_s16le, pcm_s24le, pcm_f32le)
- AAC (aac, CBR 64k-320k)
- OGG (libvorbis, quality -1 to 10)
- OPUS (libopus, 64k-256k, music/voice application)
- M4A (aac / alac)
- AIFF (pcm_s16be, pcm_s24be)
- WMA (wmav2)
- AC3 (ac3)

Provides `can_lossless_copy(source_codec, target_format)` to dynamically determine if
stream copy (-c:a copy) can be performed without lossy re-encoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AudioFormat(str, Enum):
    """Supported target audio container formats."""
    MP3 = "mp3"
    FLAC = "flac"
    WAV = "wav"
    AAC = "aac"
    OGG = "ogg"
    OPUS = "opus"
    M4A = "m4a"
    AIFF = "aiff"
    WMA = "wma"
    AC3 = "ac3"
    COPY = "copy"

    @classmethod
    def from_string(cls, val: str | AudioFormat) -> AudioFormat:
        if isinstance(val, AudioFormat):
            return val
        clean = val.strip().lstrip(".").lower()
        mapping = {
            "mp3": cls.MP3,
            "flac": cls.FLAC,
            "wav": cls.WAV,
            "wave": cls.WAV,
            "aac": cls.AAC,
            "ogg": cls.OGG,
            "oga": cls.OGG,
            "opus": cls.OPUS,
            "m4a": cls.M4A,
            "alac": cls.M4A,
            "aiff": cls.AIFF,
            "aif": cls.AIFF,
            "wma": cls.WMA,
            "ac3": cls.AC3,
            "copy": cls.COPY,
        }
        if clean in mapping:
            return mapping[clean]
        raise ValueError(f"Unsupported audio format: '{val}'. Supported: {[f.value for f in cls]}")


class BitrateMode(str, Enum):
    """Bitrate rate control modes."""
    CBR = "cbr"
    VBR = "vbr"
    LOSSLESS = "lossless"


@dataclass
class AudioProfile:
    """
    Complete configuration for an audio transcode target.
    Generates exact FFmpeg CLI arguments for encoding.
    """
    format: AudioFormat
    codec: str
    bitrate_mode: BitrateMode = BitrateMode.CBR
    bitrate_kbps: Optional[int] = None
    vbr_quality: Optional[int | float] = None
    compression_level: Optional[int] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    sample_fmt: Optional[str] = None
    application: Optional[str] = None  # For Opus: audio, voip, lowdelay
    extra_ffmpeg_args: list[str] = field(default_factory=list)

    def to_ffmpeg_args(self) -> list[str]:
        """Convert this profile into ffmpeg command line arguments."""
        if self.codec == "copy":
            args: list[str] = ["-c:a", "copy"]
            if self.extra_ffmpeg_args:
                args.extend(self.extra_ffmpeg_args)
            return args

        args: list[str] = ["-c:a", self.codec]

        # Rate control / Bitrate
        if self.bitrate_mode == BitrateMode.CBR and self.bitrate_kbps is not None:
            args.extend(["-b:a", f"{self.bitrate_kbps}k"])
        elif self.bitrate_mode == BitrateMode.VBR:
            if self.codec == "libmp3lame" and self.vbr_quality is not None:
                args.extend(["-q:a", str(int(self.vbr_quality))])
            elif self.codec == "libvorbis" and self.vbr_quality is not None:
                args.extend(["-qscale:a", str(self.vbr_quality)])
            elif self.bitrate_kbps is not None:
                args.extend(["-b:a", f"{self.bitrate_kbps}k"])

        # FLAC Compression level
        if self.codec == "flac" and self.compression_level is not None:
            args.extend(["-compression_level", str(self.compression_level)])

        # Opus specific tuning
        if self.codec == "libopus":
            if self.application:
                args.extend(["-application", self.application])
            if self.bitrate_kbps and "-b:a" not in args:
                args.extend(["-b:a", f"{self.bitrate_kbps}k"])

        # Sample format (bit depth)
        if self.sample_fmt:
            args.extend(["-sample_fmt", self.sample_fmt])

        # Sample rate
        if self.sample_rate:
            args.extend(["-ar", str(self.sample_rate)])

        # Channels
        if self.channels:
            args.extend(["-ac", str(self.channels)])

        # Extra custom arguments
        if self.extra_ffmpeg_args:
            args.extend(self.extra_ffmpeg_args)

        return args


# ============================================================================
# Lossless Copy Decision Matrix
# ============================================================================

def can_lossless_copy(source_codec: str, target_format: str | AudioFormat) -> bool:
    """
    Determines whether a source audio stream can be copied into the destination
    container without re-encoding (stream copy via '-c:a copy').

    Args:
        source_codec: Codec string reported by ffprobe (e.g. 'aac', 'mp3', 'flac').
        target_format: Target container format (e.g. 'mp3', 'm4a', 'flac').

    Returns:
        bool: True if lossless copy is natively supported and recommended.
    """
    if not source_codec:
        return False

    tgt = AudioFormat.from_string(target_format)
    src = source_codec.lower().strip()

    # MP3 container can hold mp3 / mp3float
    if tgt == AudioFormat.MP3:
        return src in ("mp3", "mp3float")

    # AAC raw container (.aac) can hold ADTS AAC
    if tgt == AudioFormat.AAC:
        return src in ("aac", "mp4a-latm")

    # M4A container (.m4a) natively supports AAC and ALAC
    if tgt == AudioFormat.M4A:
        return src in ("aac", "alac", "mp4a-latm")

    # FLAC container supports FLAC streams
    if tgt == AudioFormat.FLAC:
        return src in ("flac",)

    # OGG container can hold Vorbis, FLAC, Opus
    if tgt == AudioFormat.OGG:
        return src in ("vorbis", "libvorbis", "flac", "opus", "speex")

    # OPUS container supports Opus streams
    if tgt == AudioFormat.OPUS:
        return src in ("opus", "libopus")

    # WAV container supports standard uncompressed PCM formats
    if tgt == AudioFormat.WAV:
        return src in (
            "pcm_s16le",
            "pcm_s24le",
            "pcm_s32le",
            "pcm_f32le",
            "pcm_f64le",
            "pcm_u8",
        )

    # AIFF container supports big-endian PCM formats
    if tgt == AudioFormat.AIFF:
        return src in (
            "pcm_s16be",
            "pcm_s24be",
            "pcm_s32be",
            "pcm_f32be",
            "pcm_f64be",
        )

    # WMA container supports wmav1, wmav2, wmapro
    if tgt == AudioFormat.WMA:
        return src in ("wmav1", "wmav2", "wmapro")

    # AC3 container supports ac3 and eac3
    if tgt == AudioFormat.AC3:
        return src in ("ac3", "eac3")

    # Direct stream copy target format
    if tgt == AudioFormat.COPY or src == "copy":
        return True

    return False


# ============================================================================
# Standard Presets Library
# ============================================================================

PRESETS: dict[str, AudioProfile] = {
    # MP3 Presets (libmp3lame)
    "mp3_320k": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=320,
    ),
    "mp3_256k": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
    ),
    "mp3_192k": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=192,
    ),
    "mp3_128k": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=128,
    ),
    "mp3_64k": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=64,
    ),
    "mp3_v0": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=0,  # ~245 kbps, highest VBR quality
    ),
    "mp3_v2": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=2,  # ~190 kbps, standard high quality
    ),

    # FLAC Presets (flac)
    "flac_16bit": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=8,
        sample_fmt="s16",
    ),
    "flac_24bit": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=8,
        sample_fmt="s32",
    ),
    "flac_fast": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=2,
    ),
    "flac_max": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=12,
    ),

    # WAV Presets (pcm_s16le, pcm_s24le, pcm_f32le)
    "wav_16bit": AudioProfile(
        format=AudioFormat.WAV,
        codec="pcm_s16le",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),
    "wav_24bit": AudioProfile(
        format=AudioFormat.WAV,
        codec="pcm_s24le",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),
    "wav_32bit_float": AudioProfile(
        format=AudioFormat.WAV,
        codec="pcm_f32le",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),

    # AAC Presets (aac)
    "aac_320k": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=320,
    ),
    "aac_256k": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
    ),
    "aac_192k": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=192,
    ),
    "aac_128k": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=128,
    ),

    # OGG Presets (libvorbis)
    "ogg_q10": AudioProfile(
        format=AudioFormat.OGG,
        codec="libvorbis",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=10,  # Max vorbis quality (~500 kbps)
    ),
    "ogg_q8": AudioProfile(
        format=AudioFormat.OGG,
        codec="libvorbis",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=8,  # ~256 kbps
    ),
    "ogg_q6": AudioProfile(
        format=AudioFormat.OGG,
        codec="libvorbis",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=6,  # ~192 kbps
    ),
    "ogg_q4": AudioProfile(
        format=AudioFormat.OGG,
        codec="libvorbis",
        bitrate_mode=BitrateMode.VBR,
        vbr_quality=4,  # ~128 kbps
    ),

    # OPUS Presets (libopus)
    "opus_256k": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
        application="audio",
    ),
    "opus_160k": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=160,
        application="audio",
    ),
    "opus_128k": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=128,
        application="audio",
    ),
    "opus_96k": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=96,
        application="audio",
    ),
    "opus_64k_voice": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=64,
        application="voip",
    ),

    # M4A Presets (aac / alac)
    "m4a_aac_256k": AudioProfile(
        format=AudioFormat.M4A,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
    ),
    "m4a_aac_320k": AudioProfile(
        format=AudioFormat.M4A,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=320,
    ),
    "m4a_alac": AudioProfile(
        format=AudioFormat.M4A,
        codec="alac",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),

    # AIFF Presets (pcm_s16be, pcm_s24be)
    "aiff_16bit": AudioProfile(
        format=AudioFormat.AIFF,
        codec="pcm_s16be",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),
    "aiff_24bit": AudioProfile(
        format=AudioFormat.AIFF,
        codec="pcm_s24be",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),

    # WMA Presets (wmav2)
    "wma_192k": AudioProfile(
        format=AudioFormat.WMA,
        codec="wmav2",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=192,
    ),
    "wma_128k": AudioProfile(
        format=AudioFormat.WMA,
        codec="wmav2",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=128,
    ),

    # AC3 Presets (ac3)
    "ac3_640k": AudioProfile(
        format=AudioFormat.AC3,
        codec="ac3",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=640,
    ),
    "ac3_448k": AudioProfile(
        format=AudioFormat.AC3,
        codec="ac3",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=448,
    ),
    "ac3_384k": AudioProfile(
        format=AudioFormat.AC3,
        codec="ac3",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=384,
    ),

    # ========================================================================
    # Enterprise & Broadcast Presets
    # ========================================================================

    # Studio Master: 24-bit 96kHz FLAC
    "studio_master": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=8,
        sample_rate=96000,
        sample_fmt="s32",
    ),
    "flac_studio_master": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=8,
        sample_rate=96000,
        sample_fmt="s32",
    ),

    # Podcast Broadcast: MP3 192k with Podcast Enhancer filter + EBU R128 (-16 LUFS)
    "podcast_broadcast": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=192,
        sample_rate=44100,
        extra_ffmpeg_args=[
            "-af",
            "highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50,highshelf=f=6000:g=-3,loudnorm=I=-16:TP=-1.5:LRA=11",
        ],
    ),
    "mp3_podcast_broadcast": AudioProfile(
        format=AudioFormat.MP3,
        codec="libmp3lame",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=192,
        sample_rate=44100,
        extra_ffmpeg_args=[
            "-af",
            "highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50,highshelf=f=6000:g=-3,loudnorm=I=-16:TP=-1.5:LRA=11",
        ],
    ),

    # Streaming Optimized: AAC 256k with EBU R128 (-14 LUFS Spotify/YouTube standard)
    "streaming_optimized": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
        sample_rate=44100,
        extra_ffmpeg_args=[
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
        ],
    ),
    "aac_streaming_optimized": AudioProfile(
        format=AudioFormat.AAC,
        codec="aac",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=256,
        sample_rate=44100,
        extra_ffmpeg_args=[
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
        ],
    ),

    # Audiophile Lossless: FLAC Compression Level 12, 24-bit
    "audiophile_lossless": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=12,
        sample_fmt="s32",
    ),
    "flac_audiophile_lossless": AudioProfile(
        format=AudioFormat.FLAC,
        codec="flac",
        bitrate_mode=BitrateMode.LOSSLESS,
        compression_level=12,
        sample_fmt="s32",
    ),

    # Ultra Compression: OPUS 64kbps Speech/Music hybrid
    "ultra_compression": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=64,
        application="audio",
    ),
    "opus_ultra_compression": AudioProfile(
        format=AudioFormat.OPUS,
        codec="libopus",
        bitrate_mode=BitrateMode.CBR,
        bitrate_kbps=64,
        application="audio",
    ),

    # Direct Lossless Copy: Stream copy passthrough
    "direct_lossless_copy": AudioProfile(
        format=AudioFormat.COPY,
        codec="copy",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),
    "copy": AudioProfile(
        format=AudioFormat.COPY,
        codec="copy",
        bitrate_mode=BitrateMode.LOSSLESS,
    ),
}

# Default preset mapping for each format
DEFAULT_PRESET_MAP: dict[AudioFormat, str] = {
    AudioFormat.MP3: "mp3_320k",
    AudioFormat.FLAC: "flac_16bit",
    AudioFormat.WAV: "wav_16bit",
    AudioFormat.AAC: "aac_256k",
    AudioFormat.OGG: "ogg_q6",
    AudioFormat.OPUS: "opus_160k",
    AudioFormat.M4A: "m4a_aac_256k",
    AudioFormat.AIFF: "aiff_16bit",
    AudioFormat.WMA: "wma_192k",
    AudioFormat.AC3: "ac3_448k",
    AudioFormat.COPY: "direct_lossless_copy",
}


def get_profile(
    format_name: str | AudioFormat,
    preset: Optional[str] = None,
    **overrides: Any,
) -> AudioProfile:
    """
    Resolve and construct an AudioProfile for the given format, preset, and overrides.

    Args:
        format_name: AudioFormat or string representation (e.g. 'mp3', 'flac', or preset key 'studio_master').
        preset: Optional preset key (e.g. 'mp3_320k', 'flac_24bit', 'v0', 'podcast_broadcast').
        **overrides: Any fields to override on AudioProfile (e.g. sample_rate=48000, bitrate_kbps=256).

    Returns:
        AudioProfile configured instance.
    """
    base_preset_key: Optional[str] = None

    # Check if format_name is itself a preset key when preset is not specified
    if isinstance(format_name, str) and preset is None:
        cand_key = format_name.lower().strip().replace(" ", "_").replace("-", "_")
        if cand_key in PRESETS:
            preset = cand_key
            fmt = PRESETS[cand_key].format
        else:
            fmt = AudioFormat.from_string(format_name)
    elif isinstance(format_name, AudioFormat):
        fmt = format_name
    else:
        fmt = AudioFormat.from_string(format_name)

    if preset:
        # Check direct preset or prefixed preset
        preset_clean = preset.lower().strip().replace(" ", "_").replace("-", "_")
        if preset_clean in PRESETS:
            base_preset_key = preset_clean
        else:
            fmt_prefix = f"{fmt.value}_{preset_clean}"
            if fmt_prefix in PRESETS:
                base_preset_key = fmt_prefix

    if not base_preset_key:
        base_preset_key = DEFAULT_PRESET_MAP.get(fmt, "mp3_320k")

    template = PRESETS[base_preset_key]

    # Create new profile clone with applied overrides
    profile = AudioProfile(
        format=fmt,
        codec=overrides.get("codec", template.codec),
        bitrate_mode=overrides.get("bitrate_mode", template.bitrate_mode),
        bitrate_kbps=overrides.get("bitrate_kbps", template.bitrate_kbps),
        vbr_quality=overrides.get("vbr_quality", template.vbr_quality),
        compression_level=overrides.get("compression_level", template.compression_level),
        sample_rate=overrides.get("sample_rate", template.sample_rate),
        channels=overrides.get("channels", template.channels),
        sample_fmt=overrides.get("sample_fmt", template.sample_fmt),
        application=overrides.get("application", template.application),
        extra_ffmpeg_args=list(overrides.get("extra_ffmpeg_args", template.extra_ffmpeg_args)),
    )
    return profile
