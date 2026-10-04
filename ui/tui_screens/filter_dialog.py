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
Sleek, minimalist dark styling utilizing muted zinc/slate shades with clean,
aligned cards, ultra-clean custom toggle switches, and balanced buttons.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, Select, Static


# =============================================================================
# Custom Minimalist Toggle Switch Widget
# =============================================================================

class CleanToggle(Widget):
    """
    A sleek, minimalist toggle switch with clean text indicators and zero bulky borders.
    Fully keyboard (Space/Enter) and mouse clickable.
    """

    DEFAULT_CSS = """
    CleanToggle {
        width: 9;
        height: 1;
        background: #141722;
        color: #5e6678;
        border: none;
        text-align: center;
        content-align: center middle;
        margin: 0;
        padding: 0;
    }

    CleanToggle:hover {
        background: #1b2030;
        color: #8b95ad;
    }

    CleanToggle:focus {
        background: #20273c;
        color: #ffffff;
        text-style: bold;
    }

    CleanToggle.-on {
        background: #162b3b;
        color: #5cbcdb;
    }

    CleanToggle.-on:hover {
        background: #1b3549;
        color: #72cced;
    }

    CleanToggle.-on:focus {
        background: #1e3f57;
        color: #ffffff;
        text-style: bold;
    }
    """

    value: reactive[bool] = reactive(False)

    def __init__(self, value: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.value = value
        self.can_focus = True

    def watch_value(self, val: bool) -> None:
        self.set_class(val, "-on")

    def render(self) -> Text:
        if self.value:
            return Text("  [ON]   ", style="bold #5cbcdb")
        else:
            return Text("  [OFF]  ", style="dim #5e6678")

    def on_click(self) -> None:
        self.value = not self.value

    def key_space(self) -> None:
        self.value = not self.value

    def key_enter(self) -> None:
        self.value = not self.value


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
        max-height: 90%;
        background: #10121a;
        border: round #242938;
        padding: 0 1;
    }

    #dialog-header {
        width: 100%;
        height: auto;
        border-bottom: solid #1b1f2b;
        padding-bottom: 0;
        margin-bottom: 0;
    }

    #dialog-title {
        color: #e1e4ec;
        text-style: bold;
        width: 100%;
    }

    #dialog-subtitle {
        color: #5e6678;
        width: 100%;
    }

    #dialog-body {
        width: 100%;
        height: auto;
    }

    /* Option Cards */
    .option-row, .select-row {
        width: 100%;
        height: 2;
        align: left middle;
        padding: 0 1;
        background: #141722;
        border-bottom: solid #1b1f2b;
    }

    .option-row:hover, .select-row:hover {
        background: #161a27;
    }

    .option-label, .field-label {
        width: 1fr;
        height: 1;
        color: #e1e4ec;
        content-align: left middle;
    }

    .option-row Select, .select-row Select {
        width: 32;
        height: 1;
        background: #10121a;
        border: none;
        color: #e1e4ec;
    }

    .option-row Select:hover, .select-row Select:hover {
        background: #171b26;
        color: #ffffff;
    }

    .option-row Select:focus, .select-row Select:focus {
        background: #1b2434;
        color: #ffffff;
    }

    /* Action Footer & Buttons */
    #dialog-footer {
        width: 100%;
        height: auto;
        align: right middle;
        margin-top: 1;
        padding-top: 0;
    }

    #dialog-hotkey-hint {
        width: 1fr;
        height: 3;
        color: #5e6678;
        content-align: left middle;
    }

    #btn-cancel {
        height: 3;
        min-width: 14;
        background: #141722;
        color: #9aa2b4;
        border: round #242938;
        margin-right: 1;
        text-align: center;
    }

    #btn-cancel:hover {
        background: #1d2332;
        color: #e1e4ec;
        border: round #3d5470;
    }

    #btn-cancel:focus {
        background: #1d2332;
        color: #ffffff;
        border: round #4ba3be;
    }

    #btn-save {
        height: 3;
        min-width: 18;
        background: #1c3547;
        color: #e1e4ec;
        border: round #2e5570;
        text-style: bold;
        text-align: center;
    }

    #btn-save:hover {
        background: #254659;
        color: #ffffff;
        border: round #5cbcdb;
    }

    #btn-save:focus {
        background: #254659;
        color: #ffffff;
        border: round #5cbcdb;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save Filters", show=True),
        Binding("enter", "save", "Save Filters", show=False),
    ]

    def __init__(
        self,
        initial_options: Optional[Dict[str, Any]] = None,
        *,
        current_options: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        """
        Initialize the DSP Audio Filters Modal Screen.
        Accepts either initial_options or current_options.
        """
        super().__init__(name=name, id=id, classes=classes)
        init = initial_options or current_options or {}

        # 1. Loudness Normalization (EBU R128)
        self._init_loudnorm: bool = bool(
            init.get("loudness_normalization", init.get("ebu_r128", False))
        )

        # 2. Lossless Stream Copy
        self._init_lossless: bool = bool(
            init.get("lossless_stream_copy", init.get("lossless_copy_if_match", False))
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
                    yield CleanToggle(value=self._init_loudnorm, id="switch-loudnorm")

                # 2. Lossless Stream Copy
                with Horizontal(classes="option-row"):
                    yield Label("Lossless Stream Copy (bypass re-encode if codec matches)", classes="option-label")
                    yield CleanToggle(value=self._init_lossless, id="switch-lossless")

                # 3. Sample Rate
                with Horizontal(classes="option-row select-row"):
                    yield Label("Sample Rate:", classes="option-label field-label")
                    yield Select[Union[str, int]](
                        SAMPLE_RATE_CHOICES,
                        value=self._init_sample_rate,
                        id="select-sample-rate",
                        allow_blank=False,
                        compact=True,
                    )

                # 4. Channels
                with Horizontal(classes="option-row select-row"):
                    yield Label("Channels:", classes="option-label field-label")
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
                    yield CleanToggle(value=self._init_cover_art, id="switch-cover-art")

            # Actions / Footer
            with Horizontal(id="dialog-footer"):
                yield Label("Esc: Cancel  •  Ctrl+S / Enter: Save", id="dialog-hotkey-hint")
                yield Button("Cancel", id="btn-cancel")
                yield Button("Save Filters", id="btn-save")

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
        """
        # 1. Loudness Normalization
        try:
            loudnorm_val = self.query_one("#switch-loudnorm", CleanToggle).value
        except Exception:
            loudnorm_val = self._init_loudnorm

        # 2. Lossless Stream Copy
        try:
            lossless_val = self.query_one("#switch-lossless", CleanToggle).value
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
            cover_art_val = self.query_one("#switch-cover-art", CleanToggle).value
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
        background: #181d26;
        color: #e1e4ec;
        border: solid #2a4c63;
        min-width: 24;
    }
    #lbl-result {
        color: #9aa2b4;
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
