"""
preset_dialog.py - Enterprise Audio Preset & Format Configuration Modal Screen.

Part of the Enterprise Video to Audio Transcoder TUI interface.
Provides `PresetDialogModal` for interactive selection and live preview of
audio container formats (MP3, FLAC, WAV, AAC, OPUS, OGG, M4A) and quality presets.

Features:
- Sleek minimalist dark styling (charcoal/slate theme, no gaudy colors).
- Dynamic quality/bitrate presets adjusting based on format:
  * Lossy (MP3, AAC, OPUS, OGG, M4A): 320k (High Quality), 256k, 192k, 128k (Fast), VBR High.
  * Lossless (FLAC, WAV): 16-bit / 24-bit PCM / FLAC compression levels.
- Live formatted description displaying format pros/cons, acoustic specs, codec, and use cases.
- Apply button returning formatted configuration dict, and Cancel button returning None.
- Keyboard accessible: Esc to cancel, Enter to apply, Tab/Arrow keys for navigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RadioButton, RadioSet, Static

from core.audio_profiles import AudioFormat, AudioProfile, PRESETS, get_profile


# ============================================================================
# Format & Preset Metadata Models
# ============================================================================

@dataclass(frozen=True)
class FormatMetadata:
    """Metadata specification for supported audio output containers."""
    fmt_id: str
    name: str
    full_name: str
    codec: str
    is_lossless: bool
    default_preset_id: str
    pros: str
    cons: str
    best_for: str


@dataclass(frozen=True)
class QualityPresetMetadata:
    """Metadata specification for encoding quality/bitrate presets."""
    preset_id: str
    label: str
    bitrate: str
    description: str
    profile_key: str
    sample_fmt: Optional[str] = None
    compression_level: Optional[int] = None
    is_vbr: bool = False


# Supported audio formats with engineering trade-offs
FORMAT_REGISTRY: dict[str, FormatMetadata] = {
    "mp3": FormatMetadata(
        fmt_id="mp3",
        name="MP3",
        full_name="MPEG-1 Audio Layer III",
        codec="libmp3lame",
        is_lossless=False,
        default_preset_id="q_lossy_320k",
        pros="Universal compatibility across all players, cars, OS, and mobile devices.",
        cons="Lossy perceptual encoding; discards inaudible high-frequency audio content.",
        best_for="Maximum interoperability, sharing with general audiences, legacy media players.",
    ),
    "flac": FormatMetadata(
        fmt_id="flac",
        name="FLAC",
        full_name="Free Lossless Audio Codec",
        codec="flac",
        is_lossless=True,
        default_preset_id="q_flac_16bit",
        pros="Bit-perfect studio audio quality; compresses raw audio by 40-60% losslessly.",
        cons="Larger file sizes (~20-50 MB/track); not supported on vintage standalone players.",
        best_for="Archiving master tracks, audiophile listening, lossless post-production.",
    ),
    "wav": FormatMetadata(
        fmt_id="wav",
        name="WAV",
        full_name="Waveform Audio File Format (Linear PCM)",
        codec="pcm_s16le",
        is_lossless=True,
        default_preset_id="q_wav_16bit",
        pros="Pure uncompressed linear PCM audio; zero encoding latency; universal DAW support.",
        cons="Very large file size (~10 MB/min); lacks native standardized ID3 metadata tagging.",
        best_for="Professional audio production, DAW editing, zero-latency mixing workflows.",
    ),
    "aac": FormatMetadata(
        fmt_id="aac",
        name="AAC",
        full_name="Advanced Audio Coding (MPEG-4)",
        codec="aac",
        is_lossless=False,
        default_preset_id="q_lossy_256k",
        pros="Superior compression efficiency and high-frequency fidelity compared to MP3.",
        cons="Lossy compression; slight decoding overhead on minimalist microcontrollers.",
        best_for="Apple ecosystem (iPhone, Mac), YouTube streaming, high-fidelity mobile audio.",
    ),
    "opus": FormatMetadata(
        fmt_id="opus",
        name="OPUS",
        full_name="IETF Opus Interactive Audio Codec (RFC 6716)",
        codec="libopus",
        is_lossless=False,
        default_preset_id="q_lossy_192k",
        pros="State-of-the-art compression efficiency; pristine sound quality even at lower bitrates.",
        cons="Limited native playback on vintage standalone car stereos or legacy MP3 players.",
        best_for="Modern streaming, podcasts, discord/voice, high-efficiency audio storage.",
    ),
    "ogg": FormatMetadata(
        fmt_id="ogg",
        name="OGG",
        full_name="Ogg Vorbis Open Container",
        codec="libvorbis",
        is_lossless=False,
        default_preset_id="q_lossy_192k",
        pros="Completely open-source, patent-free container and codec; excellent frequency response.",
        cons="Not natively recognized by default Apple iOS/macOS players without third-party tools.",
        best_for="Game audio development (Godot/Unity), Linux-first distribution, open-source stacks.",
    ),
    "m4a": FormatMetadata(
        fmt_id="m4a",
        name="M4A",
        full_name="MPEG-4 Audio Container (AAC/ALAC)",
        codec="aac",
        is_lossless=False,
        default_preset_id="q_lossy_256k",
        pros="Native standard for Apple devices; rich metadata, album art, and chapter support.",
        cons="Proprietary container heritage; occasional demuxing quirks on minimal Linux players.",
        best_for="Apple Music / iTunes library management, podcasts with chapters and art.",
    ),
}

# Standard lossy bitrate presets (used for MP3, AAC, OPUS, OGG, M4A)
LOSSY_PRESETS: list[QualityPresetMetadata] = [
    QualityPresetMetadata(
        preset_id="q_lossy_320k",
        label="320k (High Quality)",
        bitrate="320k",
        description="320 kbps CBR — Maximum lossy bitrate. Indistinguishable from CD to human ears.",
        profile_key="320k",
    ),
    QualityPresetMetadata(
        preset_id="q_lossy_256k",
        label="256k (Very High)",
        bitrate="256k",
        description="256 kbps CBR — Industry standard for high-res streaming. Pristine acoustic balance.",
        profile_key="256k",
    ),
    QualityPresetMetadata(
        preset_id="q_lossy_192k",
        label="192k (Standard High)",
        bitrate="192k",
        description="192 kbps CBR — Broadcast standard quality. Excellent audio clarity with compact size.",
        profile_key="192k",
    ),
    QualityPresetMetadata(
        preset_id="q_lossy_128k",
        label="128k (Fast)",
        bitrate="128k",
        description="128 kbps CBR — Fast transcode pass, lightweight footprint. Ideal for voice & previews.",
        profile_key="128k",
    ),
    QualityPresetMetadata(
        preset_id="q_lossy_vbr",
        label="VBR High (Dynamic)",
        bitrate="vbr_high",
        description="VBR High (V0/Q8) — Dynamically allocates bits based on acoustic complexity.",
        profile_key="v0",
        is_vbr=True,
    ),
]

# FLAC Lossless presets
FLAC_PRESETS: list[QualityPresetMetadata] = [
    QualityPresetMetadata(
        preset_id="q_flac_16bit",
        label="16-bit Lossless (Level 8)",
        bitrate="lossless",
        description="16-bit / 44.1kHz Lossless — CD-quality standard with maximum standard compression.",
        profile_key="flac_16bit",
        sample_fmt="s16",
        compression_level=8,
    ),
    QualityPresetMetadata(
        preset_id="q_flac_24bit",
        label="24-bit Lossless (Studio Master)",
        bitrate="lossless",
        description="24-bit / 96kHz Studio Master — Ultra-high resolution with 144 dB dynamic range.",
        profile_key="flac_24bit",
        sample_fmt="s32",
        compression_level=8,
    ),
    QualityPresetMetadata(
        preset_id="q_flac_fast",
        label="Lossless Fast (Level 2)",
        bitrate="lossless",
        description="FLAC Level 2 — Fast encoding pass while retaining bit-perfect lossless integrity.",
        profile_key="flac_fast",
        compression_level=2,
    ),
    QualityPresetMetadata(
        preset_id="q_flac_max",
        label="Lossless Maximum (Level 12)",
        bitrate="lossless",
        description="FLAC Level 12 — Exhaustive encoding search for smallest possible lossless file size.",
        profile_key="flac_max",
        compression_level=12,
    ),
]

# WAV Uncompressed PCM presets
WAV_PRESETS: list[QualityPresetMetadata] = [
    QualityPresetMetadata(
        preset_id="q_wav_16bit",
        label="16-bit PCM (Standard)",
        bitrate="lossless",
        description="16-bit Linear PCM — Standard uncompressed studio audio. Universal DAW playback.",
        profile_key="wav_16bit",
        sample_fmt="pcm_s16le",
    ),
    QualityPresetMetadata(
        preset_id="q_wav_24bit",
        label="24-bit PCM (Studio Master)",
        bitrate="lossless",
        description="24-bit Linear PCM — Full 24-bit uncompressed studio master with zero quantization loss.",
        profile_key="wav_24bit",
        sample_fmt="pcm_s24le",
    ),
    QualityPresetMetadata(
        preset_id="q_wav_32float",
        label="32-bit Float PCM (High Dynamic)",
        bitrate="lossless",
        description="32-bit Floating Point PCM — Virtually infinite headroom, immune to clipping distortion.",
        profile_key="wav_32bit_float",
        sample_fmt="pcm_f32le",
    ),
]

# Index lookups for fast retrieval
LOSSY_BY_ID: dict[str, QualityPresetMetadata] = {p.preset_id: p for p in LOSSY_PRESETS}
FLAC_BY_ID: dict[str, QualityPresetMetadata] = {p.preset_id: p for p in FLAC_PRESETS}
WAV_BY_ID: dict[str, QualityPresetMetadata] = {p.preset_id: p for p in WAV_PRESETS}


# ============================================================================
# PresetDialogModal Screen
# ============================================================================

class PresetDialogModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Enterprise Modal Dialog for configuring Audio Format and Bitrate Presets.

    Returns:
        dict: If user clicks 'Apply' or presses Enter, containing:
              - format: str (e.g. 'mp3', 'flac')
              - bitrate: str (e.g. '320k', 'lossless')
              - preset: str (profile key e.g. 'mp3_320k', 'flac_24bit')
              - quality: str (human label e.g. '320k (High Quality)')
              - codec: str (e.g. 'libmp3lame', 'flac')
              - lossless: bool
              - options: dict (direct options dictionary for transcode task)
              - description: str
        None: If user clicks 'Cancel' or presses Escape.
    """

    DEFAULT_CSS = """
    PresetDialogModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.78);
    }

    #preset-dialog-box {
        width: 78;
        max-width: 95%;
        height: auto;
        max-height: 92%;
        background: #131317;
        border: solid #2c2c36;
        padding: 1 2;
    }

    #dialog-header {
        height: auto;
        border-bottom: solid #22222a;
        margin-bottom: 1;
        padding-bottom: 1;
    }

    #dialog-title {
        text-style: bold;
        color: #e4e4eb;
    }

    #dialog-subtitle {
        color: #727282;
    }

    #columns-container {
        height: auto;
        margin-bottom: 1;
    }

    .column-box {
        width: 1fr;
        height: auto;
        border: solid #24242e;
        padding: 0 1;
        background: #17171d;
    }

    .column-header {
        text-style: bold;
        color: #8e8e9e;
        margin-bottom: 1;
    }

    /* RadioSet minimalist styling */
    #format-radios, #lossy-radios, #flac-radios, #wav-radios {
        background: transparent;
        border: none;
        padding: 0;
        margin-bottom: 1;
    }

    #format-radios RadioButton,
    #lossy-radios RadioButton,
    #flac-radios RadioButton,
    #wav-radios RadioButton {
        background: transparent;
        color: #b0b0bc;
        height: 1;
        margin: 0;
        padding: 0;
    }

    #format-radios RadioButton:hover,
    #lossy-radios RadioButton:hover,
    #flac-radios RadioButton:hover,
    #wav-radios RadioButton:hover {
        color: #ffffff;
    }

    #format-radios RadioButton.-selected,
    #lossy-radios RadioButton.-selected,
    #flac-radios RadioButton.-selected,
    #wav-radios RadioButton.-selected {
        color: #f2f2f7;
        text-style: bold;
    }

    /* Initial visibility rules handled via CSS classes */
    #flac-radios {
        display: none;
    }

    #wav-radios {
        display: none;
    }

    /* Live description panel */
    #desc-panel {
        background: #17171d;
        border: solid #24242e;
        padding: 1 1;
        height: 8;
        margin-bottom: 1;
        color: #b8b8c4;
    }

    /* Button actions */
    #button-row {
        height: 3;
        align: right middle;
    }

    #btn-cancel {
        background: #1f1f27;
        color: #9292a0;
        border: none;
        margin-right: 1;
        min-width: 14;
    }

    #btn-cancel:hover {
        background: #2a2a35;
        color: #e4e4eb;
    }

    #btn-apply {
        background: #283647;
        color: #f0f4f8;
        border: none;
        min-width: 20;
    }

    #btn-apply:hover {
        background: #36475d;
        color: #ffffff;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "apply", "Apply Preset", show=True),
        Binding("ctrl+s", "apply", "Save", show=False),
    ]

    def __init__(
        self,
        initial_format: str = "mp3",
        initial_bitrate: Optional[str] = None,
        title: str = "AUDIO PRESET & FORMAT CONFIGURATION",
        subtitle: str = "Configure output container, codec parameters, and compression quality",
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        """
        Initialize the preset dialog modal screen.

        Args:
            initial_format: Pre-selected format (e.g. 'mp3', 'flac', 'wav', 'opus').
            initial_bitrate: Optional pre-selected bitrate or quality string (e.g. '320k', '24-bit').
            title: Header title string.
            subtitle: Header subtitle text.
        """
        super().__init__(name=name, id=id, classes=classes)

        fmt_clean = (initial_format or "mp3").lower().strip().lstrip(".")
        if fmt_clean not in FORMAT_REGISTRY:
            fmt_clean = "mp3"

        self.current_format: str = fmt_clean
        self.initial_bitrate: Optional[str] = initial_bitrate
        self.title_text: str = title
        self.subtitle_text: str = subtitle

    def compose(self) -> ComposeResult:
        """Compose the modal dialog widgets."""
        with Container(id="preset-dialog-box"):
            # Header
            with Vertical(id="dialog-header"):
                yield Label(self.title_text, id="dialog-title")
                yield Label(self.subtitle_text, id="dialog-subtitle")

            # Main Body Columns
            with Horizontal(id="columns-container"):
                # Left Column: Format selection
                with Vertical(classes="column-box"):
                    yield Label("OUTPUT FORMAT", classes="column-header")
                    yield RadioSet(
                        RadioButton(
                            "MP3  (Universal Lossy)",
                            value=(self.current_format == "mp3"),
                            id="fmt_mp3",
                        ),
                        RadioButton(
                            "FLAC (Lossless Master)",
                            value=(self.current_format == "flac"),
                            id="fmt_flac",
                        ),
                        RadioButton(
                            "WAV  (Uncompressed PCM)",
                            value=(self.current_format == "wav"),
                            id="fmt_wav",
                        ),
                        RadioButton(
                            "AAC  (High Efficiency)",
                            value=(self.current_format == "aac"),
                            id="fmt_aac",
                        ),
                        RadioButton(
                            "OPUS (Modern IETF)",
                            value=(self.current_format == "opus"),
                            id="fmt_opus",
                        ),
                        RadioButton(
                            "OGG  (Vorbis Open)",
                            value=(self.current_format == "ogg"),
                            id="fmt_ogg",
                        ),
                        RadioButton(
                            "M4A  (Apple Container)",
                            value=(self.current_format == "m4a"),
                            id="fmt_m4a",
                        ),
                        id="format-radios",
                    )

                # Right Column: Quality / Bitrate presets
                with Vertical(classes="column-box"):
                    yield Label("QUALITY & BITRATE", classes="column-header")

                    # Lossy presets (MP3, AAC, OPUS, OGG, M4A)
                    lossy_val = self._resolve_initial_lossy_preset()
                    yield RadioSet(
                        *(
                            RadioButton(
                                p.label,
                                value=(p.preset_id == lossy_val),
                                id=p.preset_id,
                            )
                            for p in LOSSY_PRESETS
                        ),
                        id="lossy-radios",
                    )

                    # FLAC presets
                    flac_val = self._resolve_initial_flac_preset()
                    yield RadioSet(
                        *(
                            RadioButton(
                                p.label,
                                value=(p.preset_id == flac_val),
                                id=p.preset_id,
                            )
                            for p in FLAC_PRESETS
                        ),
                        id="flac-radios",
                    )

                    # WAV presets
                    wav_val = self._resolve_initial_wav_preset()
                    yield RadioSet(
                        *(
                            RadioButton(
                                p.label,
                                value=(p.preset_id == wav_val),
                                id=p.preset_id,
                            )
                            for p in WAV_PRESETS
                        ),
                        id="wav-radios",
                    )

            # Live Description Panel
            yield Static("", id="desc-panel")

            # Actions Button Row
            with Horizontal(id="button-row"):
                yield Button("Cancel (Esc)", id="btn-cancel")
                yield Button("Apply Preset (Enter)", id="btn-apply")

    def on_mount(self) -> None:
        """Configure widget visibility and trigger initial description update."""
        self._update_quality_set_visibility()
        self._update_description()

        # Place initial focus on format selector for immediate keyboard accessibility
        try:
            self.query_one("#format-radios", RadioSet).focus()
        except Exception:
            pass

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle format or quality radio button changes."""
        radio_set_id = event.radio_set.id

        if radio_set_id == "format-radios":
            pressed_id = event.pressed.id or ""
            new_fmt = pressed_id.replace("fmt_", "")
            if new_fmt in FORMAT_REGISTRY:
                self.current_format = new_fmt
                self._update_quality_set_visibility()
                self._update_description()

        elif radio_set_id in ("lossy-radios", "flac-radios", "wav-radios"):
            self._update_description()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Apply and Cancel button interactions."""
        if event.button.id == "btn-cancel":
            self.action_cancel()
        elif event.button.id == "btn-apply":
            self.action_apply()

    def action_cancel(self) -> None:
        """Cancel selection and dismiss dialog returning None."""
        self.dismiss(None)

    def action_apply(self) -> None:
        """Apply current format and quality configuration and dismiss."""
        result = self.get_current_selection()
        self.dismiss(result)

    def get_current_selection(self) -> Dict[str, Any]:
        """
        Extract the currently active format and quality preset as a result dictionary.

        Returns:
            Dictionary with format, bitrate, codec, lossless, preset, and options.
        """
        fmt_meta = FORMAT_REGISTRY.get(self.current_format, FORMAT_REGISTRY["mp3"])
        quality_meta = self._get_active_quality_metadata()

        # Build standard profile key
        profile_key = self._resolve_audio_profile_key(fmt_meta, quality_meta)

        # Build options dictionary compatible with QueueManager and transcoder
        options: dict[str, Any] = {
            "bitrate": quality_meta.bitrate,
            "preset": profile_key,
        }

        if quality_meta.sample_fmt:
            options["sample_fmt"] = quality_meta.sample_fmt
        if quality_meta.compression_level is not None:
            options["compression_level"] = quality_meta.compression_level

        result: dict[str, Any] = {
            "format": fmt_meta.fmt_id,
            "bitrate": quality_meta.bitrate,
            "quality_id": quality_meta.preset_id,
            "quality_label": quality_meta.label,
            "codec": fmt_meta.codec,
            "lossless": fmt_meta.is_lossless,
            "preset": profile_key,
            "description": quality_meta.description,
            "options": options,
        }

        return result

    # ========================================================================
    # Private Helpers
    # ========================================================================

    def _update_quality_set_visibility(self) -> None:
        """Toggle the active quality RadioSet matching current_format."""
        try:
            lossy_rs = self.query_one("#lossy-radios", RadioSet)
            flac_rs = self.query_one("#flac-radios", RadioSet)
            wav_rs = self.query_one("#wav-radios", RadioSet)

            if self.current_format == "flac":
                lossy_rs.display = False
                flac_rs.display = True
                wav_rs.display = False
            elif self.current_format == "wav":
                lossy_rs.display = False
                flac_rs.display = False
                wav_rs.display = True
            else:
                lossy_rs.display = True
                flac_rs.display = False
                wav_rs.display = False
        except Exception:
            pass

    def _get_active_quality_metadata(self) -> QualityPresetMetadata:
        """Retrieve QualityPresetMetadata for currently selected radio in active set."""
        try:
            if self.current_format == "flac":
                rs = self.query_one("#flac-radios", RadioSet)
                pressed = rs.pressed_button
                if pressed and pressed.id in FLAC_BY_ID:
                    return FLAC_BY_ID[pressed.id]
                return FLAC_PRESETS[0]

            elif self.current_format == "wav":
                rs = self.query_one("#wav-radios", RadioSet)
                pressed = rs.pressed_button
                if pressed and pressed.id in WAV_BY_ID:
                    return WAV_BY_ID[pressed.id]
                return WAV_PRESETS[0]

            else:
                rs = self.query_one("#lossy-radios", RadioSet)
                pressed = rs.pressed_button
                if pressed and pressed.id in LOSSY_BY_ID:
                    return LOSSY_BY_ID[pressed.id]
                return LOSSY_PRESETS[0]
        except Exception:
            if self.current_format == "flac":
                return FLAC_PRESETS[0]
            elif self.current_format == "wav":
                return WAV_PRESETS[0]
            return LOSSY_PRESETS[0]

    def _resolve_audio_profile_key(
        self,
        fmt_meta: FormatMetadata,
        quality_meta: QualityPresetMetadata,
    ) -> str:
        """Derive standard preset key matching core.audio_profiles.PRESETS."""
        fmt_str = fmt_meta.fmt_id

        if fmt_str == "flac":
            return quality_meta.profile_key
        elif fmt_str == "wav":
            return quality_meta.profile_key

        # Lossy formats: MP3, AAC, OPUS, OGG, M4A
        if fmt_str == "mp3":
            if quality_meta.is_vbr:
                return "mp3_v0"
            return f"mp3_{quality_meta.bitrate}"
        elif fmt_str == "aac":
            if quality_meta.is_vbr:
                return "aac_320k"
            return f"aac_{quality_meta.bitrate}"
        elif fmt_str == "opus":
            if quality_meta.bitrate in ("256k", "160k", "128k", "96k"):
                return f"opus_{quality_meta.bitrate}"
            return "opus_256k"
        elif fmt_str == "ogg":
            ogg_map = {
                "320k": "ogg_q10",
                "256k": "ogg_q8",
                "192k": "ogg_q6",
                "128k": "ogg_q4",
                "vbr_high": "ogg_q8",
            }
            return ogg_map.get(quality_meta.bitrate, "ogg_q6")
        elif fmt_str == "m4a":
            if quality_meta.bitrate in ("256k", "320k"):
                return f"m4a_aac_{quality_meta.bitrate}"
            return "m4a_aac_256k"

        return f"{fmt_str}_{quality_meta.bitrate}"

    def _update_description(self) -> None:
        """Render live technical description into the info panel."""
        try:
            panel = self.query_one("#desc-panel", Static)
        except Exception:
            return

        fmt_meta = FORMAT_REGISTRY.get(self.current_format, FORMAT_REGISTRY["mp3"])
        quality_meta = self._get_active_quality_metadata()

        lines: list[str] = [
            f"[bold #e0e0e8]{fmt_meta.name}[/] [dim]({fmt_meta.full_name} • Codec: {fmt_meta.codec})[/]",
            f"[dim #787888]Preset:[/] [bold #c8c8d4]{quality_meta.label}[/] [dim]— {quality_meta.description}[/]",
            f"[dim #7e947e]Pros:[/] [#b4b4c2]{fmt_meta.pros}[/]",
            f"[dim #9e7e7e]Cons:[/] [#888896]{fmt_meta.cons}[/]",
            f"[dim #7e7e94]Best For:[/] [#a4a4b2]{fmt_meta.best_for}[/]",
        ]

        text = Text.from_markup("\n".join(lines))
        panel.update(text)

    def _resolve_initial_lossy_preset(self) -> str:
        """Resolve which lossy preset ID matches initial_bitrate."""
        if not self.initial_bitrate:
            return "q_lossy_320k"
        b_clean = self.initial_bitrate.lower().strip()
        if "320" in b_clean:
            return "q_lossy_320k"
        if "256" in b_clean:
            return "q_lossy_256k"
        if "192" in b_clean:
            return "q_lossy_192k"
        if "128" in b_clean:
            return "q_lossy_128k"
        if "vbr" in b_clean:
            return "q_lossy_vbr"
        return "q_lossy_320k"

    def _resolve_initial_flac_preset(self) -> str:
        """Resolve which FLAC preset ID matches initial_bitrate."""
        if not self.initial_bitrate:
            return "q_flac_16bit"
        b_clean = self.initial_bitrate.lower().strip()
        if "24" in b_clean:
            return "q_flac_24bit"
        if "fast" in b_clean:
            return "q_flac_fast"
        if "max" in b_clean:
            return "q_flac_max"
        return "q_flac_16bit"

    def _resolve_initial_wav_preset(self) -> str:
        """Resolve which WAV preset ID matches initial_bitrate."""
        if not self.initial_bitrate:
            return "q_wav_16bit"
        b_clean = self.initial_bitrate.lower().strip()
        if "24" in b_clean:
            return "q_wav_24bit"
        if "32" in b_clean or "float" in b_clean:
            return "q_wav_32float"
        return "q_wav_16bit"


# ============================================================================
# Standalone Module Test / Demo
# ============================================================================

if __name__ == "__main__":
    from textual.app import App

    class DemoApp(App):
        def on_mount(self) -> None:
            def on_dialog_dismiss(result: Optional[dict[str, Any]]) -> None:
                print("Dialog Dismissed Result:", result)
                self.exit()

            self.push_screen(PresetDialogModal(), on_dialog_dismiss)

    app = DemoApp()
    app.run()
