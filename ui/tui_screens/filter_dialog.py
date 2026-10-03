"""
filter_dialog.py - DSP Audio Processing & Filters Modal Dialog.

Provides the `FilterDialogModal` Textual ModalScreen, enabling users to
interactively configure advanced audio processing options for the Video-to-Audio
Transcoder TUI:
1. EBU R128 Loudness Normalization (-16 LUFS broadcast standard).
2. Lossless Stream Copy (-c:a copy bypass when codecs match).
3. Target Sample Rate (Source, 44.1 kHz CD, 48.0 kHz Studio, 96.0 kHz Hi-Res).
4. Channel Geometry (Source, Mono 1.0, Stereo 2.0, 5.1 Surround).
5. Cover Art Preservation (embed video poster into container metadata).
6. Save Filters and Cancel actions returning structured configuration dictionaries upon dismiss.

Design Aesthetic:
Sleek, minimalist dark styling utilizing muted zinc/slate shades (#121214,
#18181b, #27272a, #3f3f46, #52525b) with high-contrast neutral text and zero gaudy colors.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Select, Static, Switch


# =============================================================================
# Select Choices & Configuration Constants
# =============================================================================

SAMPLE_RATE_CHOICES: List[Tuple[str, Union[str, int]]] = [
    ("Source (Keep original)", "source"),
    ("44,100 Hz (CD Audio)", 44100),
    ("48,000 Hz (Standard Video/Studio)", 48000),
    ("96,000 Hz (Hi-Res Audio)", 96000),
]

CHANNEL_CHOICES: List[Tuple[str, Union[str, int]]] = [
    ("Source (Keep original)", "source"),
    ("Mono (1.0)", 1),
    ("Stereo (2.0)", 2),
    ("5.1 Surround (6 ch)", 6),
]


# =============================================================================
# Modal Screen Implementation
# =============================================================================

class FilterDialogModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Sleek, minimalist dark modal dialog for configuring advanced audio DSP filters,
    sample rate resampling, channel geometries, stream copy, and metadata flags.
    """

    DEFAULT_CSS = """
    FilterDialogModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.78);
    }

    #filter-dialog-container {
        width: 74;
        max-width: 95%;
        height: auto;
        max-height: 94%;
        overflow-y: auto;
        background: #121214;
        border: solid #27272a;
        padding: 0 1;
    }

    #dialog-header {
        width: 100%;
        height: auto;
        border-bottom: solid #27272a;
        padding-bottom: 0;
    }

    #dialog-title {
        color: #f4f4f5;
        text-style: bold;
        width: 100%;
    }

    #dialog-subtitle {
        color: #71717a;
        width: 100%;
    }

    #dialog-body {
        width: 100%;
        height: auto;
    }

    /* Option Rows */
    .option-row {
        width: 100%;
        height: auto;
        align: left middle;
        padding: 0 1;
        background: #18181b;
        border-top: solid #27272a;
    }

    .option-label {
        width: 1fr;
        color: #f4f4f5;
        text-style: bold;
    }

    .select-row {
        width: 100%;
        height: auto;
        align: left middle;
        padding: 0 1;
        background: #18181b;
        border-top: solid #27272a;
    }

    .field-label {
        width: 18;
        color: #a1a1aa;
        text-style: bold;
    }

    .select-row Select {
        width: 1fr;
        background: #121214;
        border: none;
        color: #f4f4f5;
    }

    .select-row Select:focus {
        color: #ffffff;
    }

    /* Checkbox Styling */
    Checkbox {
        background: transparent;
        color: #f4f4f5;
        border: none;
        padding: 0;
        margin: 0;
    }

    Checkbox:focus {
        text-style: bold;
    }

    /* Minimalist Dark Switches */
    Switch {
        background: #27272a;
        border: none;
    }

    Switch.-on {
        background: #3f3f46;
    }

    Switch > .switch--slider {
        background: #18181b;
        color: #71717a;
    }

    Switch.-on > .switch--slider {
        background: #e4e4e7;
        color: #121214;
    }

    /* Action Footer */
    #dialog-footer {
        width: 100%;
        height: auto;
        align: right middle;
        margin-top: 1;
        border-top: solid #27272a;
        padding-top: 0;
    }

    #dialog-hotkey-hint {
        width: 1fr;
        color: #52525b;
    }

    #btn-cancel {
        background: #18181b;
        color: #a1a1aa;
        border: solid #3f3f46;
        min-width: 12;
        margin-right: 1;
    }

    #btn-cancel:hover {
        background: #27272a;
        color: #f4f4f5;
        border: solid #52525b;
    }

    #btn-save {
        background: #27272a;
        color: #ffffff;
        border: solid #52525b;
        text-style: bold;
        min-width: 16;
    }

    #btn-save:hover {
        background: #3f3f46;
        border: solid #71717a;
        color: #ffffff;
    }

    #btn-save:focus {
        background: #3f3f46;
        border: double #a1a1aa;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save Filters", show=True),
    ]

    def __init__(
        self,
        initial_options: Optional[Dict[str, Any]] = None,
        *,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        """
        Initialize the DSP Audio Filters Modal Screen.

        Args:
            initial_options: Dictionary containing optional initial values for:
                - 'loudness_normalization' or 'ebu_r128': bool (default False)
                - 'lossless_stream_copy' or 'lossless_copy_if_match': bool (default True)
                - 'sample_rate': None, 'source', 44100, 48000, 96000 (default 'source')
                - 'channels': None, 'source', 1, 2, 6 (default 'source')
                - 'preserve_cover_art' or 'extract_cover_art': bool (default True)
        """
        super().__init__(name=name, id=id, classes=classes)
        init = initial_options or {}

        # 1. Loudness Normalization (EBU R128)
        self._init_loudnorm: bool = bool(
            init.get("loudness_normalization", init.get("ebu_r128", False))
        )

        # 2. Lossless Stream Copy
        self._init_lossless: bool = bool(
            init.get("lossless_stream_copy", init.get("lossless_copy_if_match", True))
        )

        # 3. Sample Rate
        raw_sr = init.get("sample_rate", "source")
        if raw_sr in (None, 0, "0", "source", "original"):
            self._init_sample_rate: Union[str, int] = "source"
        elif raw_sr in (44100, "44100"):
            self._init_sample_rate = 44100
        elif raw_sr in (48000, "48000"):
            self._init_sample_rate = 48000
        elif raw_sr in (96000, "96000"):
            self._init_sample_rate = 96000
        else:
            self._init_sample_rate = "source"

        # 4. Channels
        raw_ch = init.get("channels", "source")
        if raw_ch in (None, 0, "0", "source", "original"):
            self._init_channels: Union[str, int] = "source"
        elif raw_ch in (1, "1", "mono"):
            self._init_channels = 1
        elif raw_ch in (2, "2", "stereo"):
            self._init_channels = 2
        elif raw_ch in (6, "6", "5.1", "surround"):
            self._init_channels = 6
        else:
            self._init_channels = "source"

        # 5. Cover Art Preservation
        self._init_cover_art: bool = bool(
            init.get("preserve_cover_art", init.get("extract_cover_art", True))
        )

    def compose(self) -> ComposeResult:
        """Compose modal widgets with clean, minimalist dark styling."""
        with Vertical(id="filter-dialog-container"):
            # Header
            with Vertical(id="dialog-header"):
                yield Label("DSP AUDIO PROCESSING & FILTERS", id="dialog-title")
                yield Label(
                    "Configure loudness normalization, stream copy, sampling, and metadata.",
                    id="dialog-subtitle",
                )

            # Body Settings
            with Vertical(id="dialog-body"):
                # 1. EBU R128 Loudness Normalization
                with Horizontal(classes="option-row"):
                    yield Label("EBU R128 Loudness (-16 LUFS)", classes="option-label")
                    yield Switch(value=self._init_loudnorm, id="switch-loudnorm")

                # 2. Lossless Stream Copy
                with Horizontal(classes="option-row"):
                    yield Checkbox(
                        "Lossless Stream Copy (no re-encode if codec matches)",
                        value=self._init_lossless,
                        id="check-lossless",
                    )

                # 3. Sample Rate
                with Horizontal(classes="select-row"):
                    yield Label("Sample Rate:", classes="field-label")
                    yield Select[Union[str, int]](
                        SAMPLE_RATE_CHOICES,
                        value=self._init_sample_rate,
                        id="select-sample-rate",
                        allow_blank=False,
                        compact=True,
                    )

                # 4. Channels
                with Horizontal(classes="select-row"):
                    yield Label("Channels:", classes="field-label")
                    yield Select[Union[str, int]](
                        CHANNEL_CHOICES,
                        value=self._init_channels,
                        id="select-channels",
                        allow_blank=False,
                        compact=True,
                    )

                # 5. Cover Art Preservation
                with Horizontal(classes="option-row"):
                    yield Label("Preserve Cover Art (embed thumbnail/poster)", classes="option-label")
                    yield Switch(value=self._init_cover_art, id="switch-cover-art")

            # Actions / Footer
            with Horizontal(id="dialog-footer"):
                yield Label("[Esc] Cancel  |  [Ctrl+S] Save", id="dialog-hotkey-hint")
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save Filters", id="btn-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks for Save Filters and Cancel."""
        if event.button.id == "btn-save":
            self.action_save()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        """Dismiss screen and return configured options dictionary."""
        options = self.get_options()
        self.dismiss(options)

    def action_cancel(self) -> None:
        """Dismiss screen without applying changes."""
        self.dismiss(None)

    def get_options(self) -> Dict[str, Any]:
        """
        Extract the current user-configured filter options dictionary.

        Returns:
            Dictionary containing both standard options and engine-compatible aliases:
            - 'loudness_normalization': bool
            - 'lossless_stream_copy': bool
            - 'sample_rate': Optional[int] (None if 'source')
            - 'channels': Optional[int] (None if 'source')
            - 'preserve_cover_art': bool
            - 'ebu_r128': bool
            - 'lossless_copy_if_match': bool
            - 'extract_cover_art': bool
        """
        # 1. Loudness Normalization
        try:
            loudnorm_val = self.query_one("#switch-loudnorm", Switch).value
        except Exception:
            loudnorm_val = self._init_loudnorm

        # 2. Lossless Stream Copy
        try:
            lossless_val = self.query_one("#check-lossless", Checkbox).value
        except Exception:
            lossless_val = self._init_lossless

        # 3. Sample Rate
        try:
            sr_widget = self.query_one("#select-sample-rate", Select)
            sr_raw = sr_widget.value
            if sr_raw is Select.NULL:
                sr_raw = getattr(sr_widget, "_value", self._init_sample_rate)
        except Exception:
            sr_raw = self._init_sample_rate

        # 4. Channels
        try:
            ch_widget = self.query_one("#select-channels", Select)
            ch_raw = ch_widget.value
            if ch_raw is Select.NULL:
                ch_raw = getattr(ch_widget, "_value", self._init_channels)
        except Exception:
            ch_raw = self._init_channels

        # 5. Cover Art Preservation
        try:
            cover_art_val = self.query_one("#switch-cover-art", Switch).value
        except Exception:
            cover_art_val = self._init_cover_art

        # Process sample rate integer
        if sr_raw in ("source", None, Select.NULL, 0, "0"):
            sr_int: Optional[int] = None
        else:
            try:
                sr_int = int(sr_raw)
            except (ValueError, TypeError):
                sr_int = None

        # Process channels integer
        if ch_raw in ("source", None, Select.NULL, 0, "0"):
            ch_int: Optional[int] = None
        else:
            try:
                ch_int = int(ch_raw)
            except (ValueError, TypeError):
                ch_int = None

        return {
            # Canonical requested keys
            "loudness_normalization": bool(loudnorm_val),
            "lossless_stream_copy": bool(lossless_val),
            "sample_rate": sr_int,
            "channels": ch_int,
            "preserve_cover_art": bool(cover_art_val),
            # Engine and TranscodeOptions compatibility aliases
            "ebu_r128": bool(loudnorm_val),
            "lossless_copy_if_match": bool(lossless_val),
            "extract_cover_art": bool(cover_art_val),
        }

    def to_audio_filter_config(self) -> Optional[Any]:
        """
        Convert current options to core.audio_filters.AudioFilterConfig if available.
        """
        try:
            from core.audio_filters import AudioFilterConfig
            opts = self.get_options()
            config = AudioFilterConfig()
            if opts["loudness_normalization"]:
                config.loudnorm_target = "-16"
            if opts["channels"] == 2:
                config.downmix = "5.1"
            return config
        except ImportError:
            return None


# =============================================================================
# Standalone Interactive Test Harness
# =============================================================================

class _FilterDialogDemoApp(App[None]):
    """Demonstration app to preview FilterDialogModal interactively."""

    CSS = """
    Screen {
        background: #09090b;
        align: center middle;
    }
    #btn-open {
        background: #27272a;
        color: #f4f4f5;
        border: solid #3f3f46;
        min-width: 24;
    }
    #lbl-result {
        color: #a1a1aa;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Button("Open Filter Dialog", id="btn-open")
        yield Label("Result: None", id="lbl-result")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-open":
            def on_dialog_dismiss(result: Optional[Dict[str, Any]]) -> None:
                lbl = self.query_one("#lbl-result", Label)
                lbl.update(f"Result: {result}")

            self.push_screen(FilterDialogModal(), callback=on_dialog_dismiss)


if __name__ == "__main__":
    app = _FilterDialogDemoApp()
    app.run()
