"""
ui/tui_widgets/visualizer_widget.py - Enterprise Real-Time Audio Visualizer Widget.

Part of the Video-to-Audio Transcoder Textual TUI Suite.

Features:
- Three selectable visualization modes:
  * Mode A: Stereo VU Meter + 7-Band Dynamic Spectrum Equalizer
  * Mode B: High-Resolution Smooth Oscilloscope Waveform (Braille 2x4 sub-pixel / line fallback)
  * Mode C: Minimalist Compact Audio Level Monitor
- Sleek minimalist dark styling:
  Monochrome and subtle muted slate / cyan accents (no gaudy or saturated neon colors).
- High performance & responsive:
  * High-framerate animation via set_interval(0.08, ...) when transcoding is active.
  * Low-frequency idle/standby rendering (~0.5s interval) when queue is stopped.
  * Dynamic layout scaling adapting automatically to any width and height in the right panel.
- Interactive mode switcher:
  * Keyboard shortcut: press 'v' (or '1', '2', '3' / 'a', 'b', 'c')
  * Mouse click: click directly on mode labels or widget header.
"""

from __future__ import annotations

import enum
import math
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from textual.binding import Binding
from textual.events import Click, Resize
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget


# =============================================================================
# Helper: Terminal Unicode Support Detection
# =============================================================================

def is_unicode_supported() -> bool:
    """
    Detects whether terminal stdout supports UTF-8 Unicode characters.
    Returns False on legacy Windows cmd.exe / cp1252 consoles without UTF-8.
    """
    try:
        encoding = getattr(sys.stdout, "encoding", "") or ""
        if "utf" in encoding.lower():
            return True
        import os
        if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"):
            return True
        if "utf" in os.environ.get("PYTHONIOENCODING", "").lower():
            return True
    except Exception:
        pass
    return False


# =============================================================================
# Color Palette & Styles (Minimalist Slate / Muted Cyan / Clean Monochrome)
# =============================================================================

STYLE_MUTED_BG = "#1e242c"
STYLE_ACTIVE_BG = "#2a3644"
STYLE_BORDER_DIM = "#374351"
STYLE_TEXT_DIM = "#616f7d"
STYLE_TEXT_MUTED = "#7d8b99"
STYLE_TEXT_NORMAL = "#9eb0bf"
STYLE_TEXT_BRIGHT = "bold #e4ecf3"

# Subtle Slate / Muted Cyan Accents
STYLE_ACCENT_CYAN = "#6c9da8"
STYLE_ACCENT_SLATE = "#58768a"
STYLE_ACCENT_DIM = "#475968"

# Meter Bars - Graduated Slate to Crisp White Transient
STYLE_BAR_EMPTY = "dim #2a3440"
STYLE_BAR_LOW = "#455869"
STYLE_BAR_MID = "#5d788e"
STYLE_BAR_HIGH = "#84aabf"
STYLE_BAR_PEAK = "bold #e6f0f7"
STYLE_OVERLOAD = "#b37b7b"  # Restrained muted rose for overload (no garish red)


# =============================================================================
# Visualization Modes
# =============================================================================

class VisualizerMode(str, enum.Enum):
    """Selectable display modes for the audio visualizer."""
    SPECTRUM = "spectrum"          # Mode A: Stereo VU + 7-Band Spectrum Equalizer
    OSCILLOSCOPE = "oscilloscope"  # Mode B: Smooth Oscilloscope Waveform (Braille)
    COMPACT = "compact"            # Mode C: Minimalist Compact Audio Level Monitor

    @classmethod
    def from_input(cls, val: Any) -> "VisualizerMode":
        """Converts user input (string, int, enum) into a valid VisualizerMode."""
        if isinstance(val, cls):
            return val
        s = str(val).strip().lower()
        if s in ("a", "1", "mode_a", "spectrum", "eq", "equalizer"):
            return cls.SPECTRUM
        if s in ("b", "2", "mode_b", "oscilloscope", "osc", "waveform", "wave"):
            return cls.OSCILLOSCOPE
        if s in ("c", "3", "mode_c", "compact", "mini", "level", "monitor"):
            return cls.COMPACT
        return cls.SPECTRUM


# =============================================================================
# Braille Sub-Pixel Canvas for High-Resolution Oscilloscope
# =============================================================================

BRAILLE_DOT_MAP: Dict[Tuple[int, int], int] = {
    (0, 0): 0x01,
    (0, 1): 0x02,
    (0, 2): 0x04,
    (0, 3): 0x40,
    (1, 0): 0x08,
    (1, 1): 0x10,
    (1, 2): 0x20,
    (1, 3): 0x80,
}


class BrailleCanvas:
    """
    Sub-pixel 2x4 dot matrix canvas backed by Unicode Braille patterns (U+2800..U+28FF).
    Each character cell contains 2 columns and 4 rows of sub-pixels.
    """

    def __init__(self, char_width: int, char_height: int):
        self.char_w = max(1, char_width)
        self.char_h = max(1, char_height)
        self.pixel_w = self.char_w * 2
        self.pixel_h = self.char_h * 4
        self.grid = [[0] * self.char_w for _ in range(self.char_h)]

    def set_pixel(self, px: int, py: int) -> None:
        """Sets an individual sub-pixel if within canvas bounds."""
        if 0 <= px < self.pixel_w and 0 <= py < self.pixel_h:
            cx = px // 2
            cy = py // 4
            sub_x = px % 2
            sub_y = py % 4
            self.grid[cy][cx] |= BRAILLE_DOT_MAP[(sub_x, sub_y)]

    def draw_vertical_span(self, px: int, py0: int, py1: int) -> None:
        """Draws a continuous vertical line of sub-pixels between py0 and py1."""
        low = max(0, min(py0, py1))
        high = min(self.pixel_h - 1, max(py0, py1))
        for y in range(low, high + 1):
            self.set_pixel(px, y)

    def to_lines(self) -> List[str]:
        """Renders the canvas grid into a list of character row strings."""
        lines: List[str] = []
        for cy in range(self.char_h):
            row_chars = []
            for cx in range(self.char_w):
                val = self.grid[cy][cx]
                if val == 0:
                    row_chars.append(" ")
                else:
                    row_chars.append(chr(0x2800 + val))
            lines.append("".join(row_chars))
        return lines


# =============================================================================
# Audio Ballistics & Synthesis Model
# =============================================================================

class AudioPhysicsModel:
    """
    Simulates real-world broadcast VU ballistics, pink-noise 7-band spectrum,
    and CRT oscilloscope harmonics for the transcoder visualizer.
    """

    SPECTRUM_FREQS = ["60Hz", "150Hz", "400Hz", "1kHz", "2.5kHz", "6kHz", "15kHz"]
    COMPACT_FREQS = ["60", "150", "400", "1k", "2.5k", "6k", "15k"]

    BLOCK_STEPS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    ASCII_BLOCK_STEPS = [" ", ".", ":", "-", "=", "+", "#", "#", "@"]

    def __init__(self, safe_ascii: Optional[bool] = None):
        self.safe_ascii = not is_unicode_supported() if safe_ascii is None else safe_ascii
        self.cur_l_db = -60.0
        self.cur_r_db = -60.0
        self.peak_l_db = -60.0
        self.peak_r_db = -60.0
        self.peak_l_hold = 0.0
        self.peak_r_hold = 0.0

        # 7-Band Equalizer levels and falling peak caps
        self.band_levels = [0.0] * 7
        self.band_peaks = [0.0] * 7
        self.band_peak_holds = [0.0] * 7

        # Oscilloscope phase accumulator
        self.phase_acc = 0.0

    def update(
        self,
        dt: float,
        is_active: bool,
        progress: float,
        speed: float,
        target_l_db: Optional[float] = None,
        target_r_db: Optional[float] = None,
    ) -> None:
        """Advances ballistics simulation by elapsed time dt seconds."""
        if not is_active:
            # Idle / Standby: smoothly decay levels to noise floor
            self.cur_l_db = max(-60.0, self.cur_l_db - 30.0 * dt)
            self.cur_r_db = max(-60.0, self.cur_r_db - 30.0 * dt)
            self.peak_l_db = max(-60.0, self.peak_l_db - 15.0 * dt)
            self.peak_r_db = max(-60.0, self.peak_r_db - 15.0 * dt)

            for i in range(7):
                self.band_levels[i] = max(0.0, self.band_levels[i] - 1.5 * dt)
                self.band_peaks[i] = max(0.0, self.band_peaks[i] - 0.8 * dt)

            self.phase_acc += dt * 0.8  # slow idle drift
            return

        # Active processing
        spd = max(0.2, min(8.0, speed))
        self.phase_acc += dt * (2.4 * spd)
        t = self.phase_acc

        # 1. Determine instantaneous target dB levels
        if target_l_db is not None and target_r_db is not None:
            t_l = target_l_db
            t_r = target_r_db
        else:
            # Multi-oscillator dynamic audio level synthesis
            base = 0.68 + 0.18 * math.sin(t * 1.7) + 0.12 * math.cos(t * 3.9)
            pan = 0.14 * math.sin(t * 2.1 + 0.4)
            transient = 0.15 if math.sin(t * 0.9) > 0.82 else 0.0

            norm_l = max(0.06, min(1.0, base + pan + transient + 0.04 * math.sin(t * 6.5)))
            norm_r = max(0.06, min(1.0, base - pan + transient + 0.04 * math.cos(t * 5.8)))

            t_l = -48.0 * (1.0 - norm_l)
            t_r = -48.0 * (1.0 - norm_r)

        # Fast attack, smooth studio decay
        attack_rate = 120.0
        decay_rate = 24.0
        self.cur_l_db = self._ballistic_step(self.cur_l_db, t_l, dt, attack_rate, decay_rate)
        self.cur_r_db = self._ballistic_step(self.cur_r_db, t_r, dt, attack_rate, decay_rate)

        # Peak hold logic for L/R
        self.peak_l_db, self.peak_l_hold = self._update_peak_hold(
            self.cur_l_db, self.peak_l_db, self.peak_l_hold, dt
        )
        self.peak_r_db, self.peak_r_hold = self._update_peak_hold(
            self.cur_r_db, self.peak_r_db, self.peak_r_hold, dt
        )

        # 2. 7-Band Equalizer Simulation (Pink noise spectrum curve + jitter)
        band_weights = [1.1, 1.8, 2.7, 3.9, 5.4, 7.1, 8.9]
        band_phases = [0.0, 1.3, 2.5, 3.2, 4.6, 5.1, 0.9]
        band_base = [0.72, 0.78, 0.68, 0.62, 0.54, 0.46, 0.36]

        for i in range(7):
            w = band_weights[i]
            ph = band_phases[i]
            b_val = band_base[i]

            o1 = 0.28 * math.sin(t * w + ph)
            o2 = 0.14 * math.cos(t * w * 1.7 + ph * 1.4)
            target_band = max(0.08, min(1.0, b_val + o1 + o2))

            # Fast attack, smooth decay
            if target_band > self.band_levels[i]:
                self.band_levels[i] = min(1.0, self.band_levels[i] + 4.0 * dt)
            else:
                self.band_levels[i] = max(0.0, self.band_levels[i] - 1.8 * dt)

            # Band peak cap hold
            cur_b = self.band_levels[i]
            if cur_b >= self.band_peaks[i]:
                self.band_peaks[i] = cur_b
                self.band_peak_holds[i] = 0.65
            else:
                if self.band_peak_holds[i] > 0.0:
                    self.band_peak_holds[i] -= dt
                else:
                    self.band_peaks[i] = max(cur_b, self.band_peaks[i] - 1.2 * dt)

    @staticmethod
    def _ballistic_step(cur: float, target: float, dt: float, attack: float, decay: float) -> float:
        if target >= cur:
            return min(target, cur + attack * dt)
        return max(target, cur - decay * dt)

    @staticmethod
    def _update_peak_hold(cur: float, peak: float, hold_time: float, dt: float) -> Tuple[float, float]:
        if cur >= peak:
            return cur, 0.75
        if hold_time > 0.0:
            return peak, hold_time - dt
        return max(cur, peak - 16.0 * dt), 0.0


# =============================================================================
# AudioVisualizerWidget Implementation
# =============================================================================

class AudioVisualizerWidget(Widget):
    """
    Enterprise Real-Time Audio Visualizer Widget.
    Renders 3 switchable visualization modes with sleek dark minimalist styling.
    """

    DEFAULT_CSS = """
    AudioVisualizerWidget {
        height: 100%;
        width: 100%;
        background: transparent;
        padding: 0;
        margin: 0;
    }
    AudioVisualizerWidget:focus {
        border: none;
    }
    """

    BINDINGS = [
        Binding("v", "cycle_mode", "Next Visualizer Mode", priority=True),
        Binding("1", "set_mode_a", "Mode A (Spectrum)", show=False),
        Binding("2", "set_mode_b", "Mode B (Oscilloscope)", show=False),
        Binding("3", "set_mode_c", "Mode C (Compact)", show=False),
        Binding("a", "set_mode_a", "Mode A", show=False),
        Binding("b", "set_mode_b", "Mode B", show=False),
        Binding("c", "set_mode_c", "Mode C", show=False),
    ]

    # Reactive attributes
    mode: reactive[VisualizerMode] = reactive(VisualizerMode.SPECTRUM)
    is_active: reactive[bool] = reactive(False)
    current_progress: reactive[float] = reactive(0.0)
    current_speed: reactive[float] = reactive(1.0)
    worker_count: reactive[int] = reactive(1)
    safe_ascii: reactive[bool] = reactive(False)

    class ModeChanged(Message):
        """Message emitted when visualizer mode changes."""
        def __init__(self, mode: VisualizerMode) -> None:
            super().__init__()
            self.mode = mode

    def __init__(
        self,
        mode: Union[VisualizerMode, str] = VisualizerMode.SPECTRUM,
        safe_ascii: Optional[bool] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.can_focus = True

        auto_safe = not is_unicode_supported()
        initial_safe = auto_safe if safe_ascii is None else safe_ascii
        self.physics = AudioPhysicsModel(safe_ascii=initial_safe)
        self._last_tick_time = time.time()
        self._anim_timer: Optional[Timer] = None

        # Mode button click coordinate hit zones (updated during render)
        self._tab_hitboxes: List[Tuple[int, int, VisualizerMode]] = []

        # Initialize reactives
        self.safe_ascii = initial_safe
        self.mode = VisualizerMode.from_input(mode)

    def on_mount(self) -> None:
        """Called when widget is added to screen tree."""
        self._setup_animation_timer()

    def _setup_animation_timer(self) -> None:
        """Configures timer interval: 0.08s when active, 0.5s when idle/standby."""
        if self._anim_timer is not None:
            self._anim_timer.stop()
            self._anim_timer = None

        interval = 0.08 if self.is_active else 0.50
        self._last_tick_time = time.time()
        self._anim_timer = self.set_interval(interval, self._on_tick)

    def watch_is_active(self, old_val: bool, new_val: bool) -> None:
        """Reactively switches timer interval when transcode starts or pauses."""
        if getattr(self, "is_mounted", False):
            self._setup_animation_timer()

    def watch_safe_ascii(self, old_val: bool, new_val: bool) -> None:
        """Toggles safe ASCII support on the physics model."""
        if hasattr(self, "physics"):
            self.physics.safe_ascii = new_val
            self.refresh()

    def _on_tick(self) -> None:
        """Advances physics and refreshes widget surface."""
        now = time.time()
        dt = max(0.001, min(0.2, now - self._last_tick_time))
        self._last_tick_time = now

        self.physics.update(
            dt=dt,
            is_active=self.is_active,
            progress=self.current_progress,
            speed=self.current_speed,
        )
        self.refresh()

    # =========================================================================
    # User Actions & Event Handlers
    # =========================================================================

    def action_cycle_mode(self) -> None:
        """Cycles to the next visualization mode."""
        self.cycle_mode()

    def action_set_mode_a(self) -> None:
        """Switches to Mode A: Stereo VU + 7-Band Spectrum."""
        self.set_mode(VisualizerMode.SPECTRUM)

    def action_set_mode_b(self) -> None:
        """Switches to Mode B: Smooth Oscilloscope Waveform."""
        self.set_mode(VisualizerMode.OSCILLOSCOPE)

    def action_set_mode_c(self) -> None:
        """Switches to Mode C: Minimalist Compact Audio Level Monitor."""
        self.set_mode(VisualizerMode.COMPACT)

    def cycle_mode(self) -> None:
        """Advances to the next mode in sequence: A -> B -> C -> A."""
        cycle = [
            VisualizerMode.SPECTRUM,
            VisualizerMode.OSCILLOSCOPE,
            VisualizerMode.COMPACT,
        ]
        cur_idx = cycle.index(self.mode) if self.mode in cycle else 0
        next_mode = cycle[(cur_idx + 1) % len(cycle)]
        self.set_mode(next_mode)

    def set_mode(self, mode: Union[VisualizerMode, str, int]) -> None:
        """Sets the active visualization mode."""
        new_mode = VisualizerMode.from_input(mode)
        if self.mode != new_mode:
            self.mode = new_mode
            if getattr(self, "is_mounted", False):
                self.post_message(self.ModeChanged(self.mode))
            self.refresh()

    def set_active(self, active: bool) -> None:
        """Sets active state to start/pause high-rate visualizer animation."""
        self.is_active = active

    def set_telemetry(self, progress: float = 0.0, speed: float = 1.0, workers: int = 1) -> None:
        """Updates transcode telemetry for dynamic modulation."""
        self.current_progress = progress
        self.current_speed = speed
        self.worker_count = workers

    def update_levels(self, l_db: float, r_db: float) -> None:
        """Feeds external audio decibel measurement data."""
        self.physics.update(
            dt=0.04,
            is_active=self.is_active,
            progress=self.current_progress,
            speed=self.current_speed,
            target_l_db=l_db,
            target_r_db=r_db,
        )
        self.refresh()

    def on_click(self, event: Click) -> None:
        """
        Handles mouse clicks.
        Clicking on top header tab switches directly to that mode.
        Clicking anywhere else cycles modes.
        """
        if event.y <= 1 and self._tab_hitboxes:
            for start_x, end_x, tab_mode in self._tab_hitboxes:
                if start_x <= event.x <= end_x:
                    self.set_mode(tab_mode)
                    return
        # Fallback click anywhere to cycle
        self.cycle_mode()

    def on_resize(self, event: Resize) -> None:
        """Forces refresh on size changes to guarantee perfect responsive layout."""
        self.refresh()

    # =========================================================================
    # Rendering Orchestration
    # =========================================================================

    def render(self) -> RenderableType:
        """Renders the active visualization mode based on widget dimensions."""
        width = self.size.width if self.size.width > 0 else 46
        height = self.size.height if self.size.height > 0 else 12

        # Reset hitboxes for this render pass
        self._tab_hitboxes = []

        if width < 22 or height < 3:
            return self._render_minimal_fallback(width, height)

        if self.mode == VisualizerMode.SPECTRUM:
            return self._render_mode_spectrum(width, height)
        elif self.mode == VisualizerMode.OSCILLOSCOPE:
            return self._render_mode_oscilloscope(width, height)
        else:
            return self._render_mode_compact(width, height)

    # -------------------------------------------------------------------------
    # Shared Header Row with Clickable Mode Tabs
    # -------------------------------------------------------------------------

    def _render_header_row(self, width: int, mode_hint: str = "") -> Text:
        """
        Renders sleek top tab bar:
        [A: SPECTRUM]  [B: OSCILLO]  [C: COMPACT]      ● ACTIVE  3.2x
        """
        out = Text()
        col_pos = 0

        # Tabs configuration adapted to available width
        if width < 32:
            tabs = [
                (VisualizerMode.SPECTRUM, "A", "A"),
                (VisualizerMode.OSCILLOSCOPE, "B", "B"),
                (VisualizerMode.COMPACT, "C", "C"),
            ]
        elif width < 48:
            tabs = [
                (VisualizerMode.SPECTRUM, "A: EQ", "A: EQ"),
                (VisualizerMode.OSCILLOSCOPE, "B: OSC", "B: OSC"),
                (VisualizerMode.COMPACT, "C: LVL", "C: LVL"),
            ]
        else:
            tabs = [
                (VisualizerMode.SPECTRUM, "A: SPECTRUM", "A: SPECTRUM"),
                (VisualizerMode.OSCILLOSCOPE, "B: OSCILLO", "B: OSCILLO"),
                (VisualizerMode.COMPACT, "C: COMPACT", "C: COMPACT"),
            ]

        for v_mode, full_label, short_label in tabs:
            lbl = full_label
            is_current = (self.mode == v_mode)

            start_col = col_pos
            if is_current:
                out.append(f" {lbl} ", style=f"bold {STYLE_TEXT_BRIGHT} on {STYLE_ACTIVE_BG}")
                col_pos += len(lbl) + 2
            else:
                out.append(f" {lbl} ", style=f"{STYLE_TEXT_DIM} on {STYLE_MUTED_BG}")
                col_pos += len(lbl) + 2

            end_col = col_pos - 1
            self._tab_hitboxes.append((start_col, end_col, v_mode))
            out.append(" ", style="default")
            col_pos += 1

        # Status badge aligned right if space permits
        status_text = self._build_status_badge()
        status_len = len(status_text.plain)
        if col_pos + status_len < width:
            padding_len = max(1, width - col_pos - status_len)
            out.append(" " * padding_len)
            out.append_text(status_text)
        elif col_pos + 3 <= width:
            bullet = "*" if self.safe_ascii else "●"
            out.append(" " * (width - col_pos - 2))
            out.append(f"{bullet} ", style=f"bold {STYLE_ACCENT_CYAN}" if self.is_active else f"{STYLE_TEXT_DIM}")

        return out

    def _build_status_badge(self) -> Text:
        """Constructs minimalist state badge (ACTIVE / STANDBY)."""
        badge = Text()
        if self.is_active:
            bullet = "*" if self.safe_ascii else "●"
            badge.append(f"{bullet} ", style=f"bold {STYLE_ACCENT_CYAN}")
            badge.append("LIVE", style=f"bold {STYLE_TEXT_BRIGHT}")
            if self.current_speed > 0:
                badge.append(f" {self.current_speed:.1f}x", style=f"{STYLE_TEXT_MUTED}")
        else:
            bullet = "-" if self.safe_ascii else "○"
            badge.append(f"{bullet} ", style=f"{STYLE_TEXT_DIM}")
            badge.append("STANDBY", style=f"{STYLE_TEXT_DIM}")
        return badge

    # =========================================================================
    # Mode A: Stereo VU Meter + 7-Band Spectrum Equalizer
    # =========================================================================

    def _render_mode_spectrum(self, width: int, height: int) -> RenderableType:
        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column("Display", justify="left")

        # 1. Header with Mode Tabs (1 row)
        table.add_row(self._render_header_row(width))

        available = max(1, height - 1)
        if available <= 3:
            # Inline VU (1 row) + spectrum (available - 1 rows)
            table.add_row(self._render_inline_vu(width))
            spec_rows = max(1, available - 1)
            for s_line in self._render_spectrum_bars(width, spec_rows, compact_labels=True):
                table.add_row(s_line)
        elif available <= 5:
            # Stacked VU (2 rows) + spectrum (available - 2 rows)
            for v_line in self._render_stacked_vu(width, show_scale=False):
                table.add_row(v_line)
            spec_rows = max(1, available - 2)
            for s_line in self._render_spectrum_bars(width, spec_rows, compact_labels=(width < 50)):
                table.add_row(s_line)
        else:
            # Stacked VU (2 rows, or 3 if scale ticks fit) + full spectrum
            show_ticks = (available >= 9 and width >= 40)
            vu_lines = self._render_stacked_vu(width, show_scale=show_ticks)
            for v_line in vu_lines:
                table.add_row(v_line)
            spec_rows = max(1, available - len(vu_lines))
            for s_line in self._render_spectrum_bars(width, spec_rows, compact_labels=(width < 50)):
                table.add_row(s_line)

        return table

    def _render_stacked_vu(self, width: int, show_scale: bool = False) -> List[Text]:
        """Renders stacked L and R VU meter bars with dB readouts and peak markers."""
        lines: List[Text] = []
        show_peak = (width >= 44)
        db_l = self._format_db(self.physics.cur_l_db)
        db_r = self._format_db(self.physics.cur_r_db)
        pk_l = self._format_db(self.physics.peak_l_db)
        pk_r = self._format_db(self.physics.peak_r_db)

        prefix = " L: [" if width >= 30 else "L["
        suffix_l = f"] {db_l}" + (f"  PK {pk_l}" if show_peak else "")
        suffix_r = f"] {db_r}" + (f"  PK {pk_r}" if show_peak else "")

        overhead = len(prefix) + max(len(suffix_l), len(suffix_r)) + 1
        bar_w = max(4, min(36, width - overhead))

        line_l = Text()
        line_l.append(prefix, style=f"bold {STYLE_ACCENT_SLATE}")
        self._append_meter_bar(line_l, self.physics.cur_l_db, self.physics.peak_l_db, bar_w)
        line_l.append(f"] {db_l}", style=f"{STYLE_TEXT_NORMAL}")
        if show_peak:
            line_l.append(f"  PK {pk_l}", style=f"{STYLE_TEXT_DIM}")
        lines.append(line_l)

        line_r = Text()
        line_r.append(prefix.replace("L", "R"), style=f"bold {STYLE_ACCENT_SLATE}")
        self._append_meter_bar(line_r, self.physics.cur_r_db, self.physics.peak_r_db, bar_w)
        line_r.append(f"] {db_r}", style=f"{STYLE_TEXT_NORMAL}")
        if show_peak:
            line_r.append(f"  PK {pk_r}", style=f"{STYLE_TEXT_DIM}")
        lines.append(line_r)

        if show_scale and width >= 40:
            scale_line = Text()
            scale_line.append(" " * len(prefix))
            scale_line.append("-48", style=f"{STYLE_TEXT_DIM}")
            mid_pad = max(1, bar_w // 2 - 4)
            scale_line.append(" " * mid_pad)
            scale_line.append("-12", style=f"{STYLE_TEXT_DIM}")
            right_pad = max(1, bar_w - mid_pad - 8)
            scale_line.append(" " * right_pad)
            scale_line.append("0 dB", style=f"{STYLE_TEXT_DIM}")
            lines.append(scale_line)

        return lines

    def _render_inline_vu(self, width: int) -> Text:
        """Renders single-line inline L/R meter for constrained heights."""
        db_l = self._format_db(self.physics.cur_l_db)
        db_r = self._format_db(self.physics.cur_r_db)
        prefix_l = "L["
        suffix_l = f"] {db_l} | "
        prefix_r = "R["
        suffix_r = f"] {db_r}"

        overhead = len(prefix_l) + len(suffix_l) + len(prefix_r) + len(suffix_r) + 2
        bar_w = max(3, (width - overhead) // 2)

        out = Text()
        out.append(prefix_l, style=f"{STYLE_BORDER_DIM}")
        self._append_meter_bar(out, self.physics.cur_l_db, self.physics.peak_l_db, bar_w)
        out.append(suffix_l, style=f"{STYLE_BORDER_DIM}")

        out.append(prefix_r, style=f"{STYLE_BORDER_DIM}")
        self._append_meter_bar(out, self.physics.cur_r_db, self.physics.peak_r_db, bar_w)
        out.append(suffix_r, style=f"{STYLE_BORDER_DIM}")
        return out

    def _append_meter_bar(self, text_obj: Text, db_val: float, peak_db: float, width: int) -> None:
        """Appends monochrome/slate graduated VU meter blocks with peak pip."""
        min_db = -48.0
        max_db = 0.0
        range_db = 48.0

        clamped = max(min_db, min(max_db + 2.0, db_val))
        frac = max(0.0, min(1.0, (clamped - min_db) / range_db))
        filled_cnt = int(round(frac * width))

        clamped_peak = max(min_db, min(max_db + 2.0, peak_db))
        frac_peak = max(0.0, min(1.0, (clamped_peak - min_db) / range_db))
        peak_idx = min(width - 1, int(round(frac_peak * width)))

        fill_char = "|" if self.safe_ascii else "█"
        empty_char = "." if self.safe_ascii else "░"
        peak_char = "#" if self.safe_ascii else "|"

        for i in range(width):
            if i < filled_cnt:
                # Graduated subtle slate shading
                if i < width * 0.50:
                    style = STYLE_BAR_LOW
                elif i < width * 0.80:
                    style = STYLE_BAR_MID
                elif i < width * 0.95:
                    style = STYLE_BAR_HIGH
                else:
                    style = STYLE_BAR_PEAK
                text_obj.append(fill_char, style=style)
            elif i == peak_idx and peak_idx > filled_cnt:
                # Floating peak transient indicator
                text_obj.append(peak_char, style=f"bold {STYLE_BAR_PEAK}")
            else:
                text_obj.append(empty_char, style=f"{STYLE_BAR_EMPTY}")

    def _render_spectrum_bars(self, width: int, height: int, compact_labels: bool = False) -> List[Text]:
        """
        Renders 7 dynamic frequency columns with falling peak caps and bottom labels.
        """
        lines: List[Text] = []
        if height <= 1:
            bar_rows = 1
            has_labels = False
        else:
            bar_rows = height - 1
            has_labels = True

        col_w = max(1, (width - 2) // 7)
        fill_char = "|" if self.safe_ascii else "█"
        peak_cap_char = "-" if self.safe_ascii else "▔"
        char_steps = self.physics.ASCII_BLOCK_STEPS if self.safe_ascii else self.physics.BLOCK_STEPS

        for r in range(bar_rows - 1, -1, -1):
            line = Text()
            line.append(" ")
            thresh_low = r / float(bar_rows)
            thresh_high = (r + 1) / float(bar_rows)

            if r >= bar_rows - 1:
                row_style = f"bold {STYLE_BAR_PEAK}"
            elif r >= bar_rows * 0.65:
                row_style = STYLE_BAR_HIGH
            elif r >= bar_rows * 0.35:
                row_style = STYLE_BAR_MID
            else:
                row_style = STYLE_BAR_LOW

            for i in range(7):
                cur_lvl = self.physics.band_levels[i]
                pk_lvl = self.physics.band_peaks[i]

                has_peak_cap = (thresh_low <= pk_lvl < thresh_high) and (pk_lvl > cur_lvl + 0.05)
                block_w = max(1, col_w if col_w == 1 else col_w - 1)

                if cur_lvl >= thresh_high:
                    cell = (fill_char * block_w).center(col_w)
                    line.append(cell, style=row_style)
                elif cur_lvl > thresh_low:
                    frac = (cur_lvl - thresh_low) / max(0.001, (thresh_high - thresh_low))
                    step_idx = max(1, min(len(char_steps) - 1, int(frac * (len(char_steps) - 1))))
                    step_char = char_steps[step_idx]
                    cell = (step_char * block_w).center(col_w)
                    line.append(cell, style=row_style)
                elif has_peak_cap:
                    cell = (peak_cap_char * block_w).center(col_w)
                    line.append(cell, style=f"bold {STYLE_TEXT_BRIGHT}")
                else:
                    line.append(" " * col_w)

            lines.append(line)

        if has_labels:
            if col_w >= 6:
                labels = self.physics.SPECTRUM_FREQS
            elif col_w >= 3:
                labels = self.physics.COMPACT_FREQS
            else:
                labels = ["6", "1", "4", "1", "2", "6", "1"]

            label_line = Text()
            label_line.append(" ")
            for lbl in labels:
                label_line.append(lbl.center(col_w), style=f"{STYLE_TEXT_DIM}")
            lines.append(label_line)

        return lines

    # =========================================================================
    # Mode B: Smooth Oscilloscope Waveform (Braille 2x4 Sub-Pixel Matrix)
    # =========================================================================

    def _render_mode_oscilloscope(self, width: int, height: int) -> RenderableType:
        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column("Oscilloscope", justify="left")

        # 1. Header with Mode Tabs (1 row)
        table.add_row(self._render_header_row(width))

        # 2. Oscilloscope Canvas Height
        remaining = max(1, height - 1)
        footer_needed = (remaining >= 5)
        canvas_h = max(1, remaining - (1 if footer_needed else 0))
        canvas_w = max(8, width - 2)

        if self.safe_ascii:
            wave_lines = self._render_ascii_oscilloscope(canvas_w, canvas_h)
            for w_line in wave_lines:
                table.add_row(w_line)
        else:
            canvas = BrailleCanvas(canvas_w, canvas_h)
            self._plot_braille_waveform(canvas)

            braille_rows = canvas.to_lines()
            mid_y = canvas_h // 2
            for r_idx, b_row in enumerate(braille_rows):
                line = Text()
                line.append(" ")  # left margin
                dist_from_center = abs(r_idx - mid_y)
                row_style = f"bold {STYLE_ACCENT_CYAN}" if dist_from_center <= 1 else f"{STYLE_ACCENT_SLATE}"
                line.append(b_row[:canvas_w], style=row_style)
                table.add_row(line)

        # 3. Footer Telemetry
        if footer_needed:
            footer = Text()
            db_l = self._format_db(self.physics.cur_l_db)
            db_r = self._format_db(self.physics.cur_r_db)
            if width >= 50:
                footer.append(" [TRIG: AUTO] ", style=f"{STYLE_TEXT_DIM}")
                footer.append("TIMEBASE: 2.5ms ", style=f"{STYLE_TEXT_MUTED}")
                footer.append(f"| L: {db_l} | R: {db_r}", style=f"{STYLE_TEXT_DIM}")
            elif width >= 30:
                footer.append(" [AUTO] ", style=f"{STYLE_TEXT_DIM}")
                footer.append(f"L: {db_l} | R: {db_r}", style=f"{STYLE_TEXT_DIM}")
            else:
                footer.append(f" {db_l} / {db_r}", style=f"{STYLE_TEXT_DIM}")
            table.add_row(footer)

        return table

    def _plot_braille_waveform(self, canvas: BrailleCanvas) -> None:
        """
        Plots an unbroken, smooth waveform across the Braille sub-pixel grid.
        Connects consecutive sample points vertically so transient leaps do not create gaps.
        """
        pw = canvas.pixel_w
        ph = canvas.pixel_h
        center_y = ph // 2

        # 1. Plot faint center reference axis dots
        for px in range(0, pw, 8):
            canvas.set_pixel(px, center_y)

        # 2. Compute audio waveform sample points
        t = self.physics.phase_acc
        is_active = self.is_active

        # Modulate amplitude by current audio decibels
        max_amplitude = (ph // 2) - 2
        linear_gain = 0.0 if not is_active else math.pow(10.0, max(-48.0, self.physics.cur_l_db) / 20.0)
        gain_scale = max(0.15, min(0.92, linear_gain * 1.8))

        prev_py = center_y

        for px in range(pw):
            norm_x = (px / float(pw)) * 4.0 * math.pi

            if is_active:
                # Rich harmonic audio waveform
                w1 = math.sin(norm_x - t * 2.0)
                w2 = 0.35 * math.sin(norm_x * 2.2 + t * 3.1)
                w3 = 0.15 * math.cos(norm_x * 4.1 - t * 1.5)
                # Transient ripple
                burst = 0.12 * math.sin(norm_x * 8.0) if math.sin(t * 1.2) > 0.7 else 0.0
                wave_val = (w1 + w2 + w3 + burst) / 1.6
                py = int(round(center_y + (wave_val * max_amplitude * gain_scale)))
            else:
                # Standby: Gentle resting wave
                gentle = 0.12 * math.sin(norm_x * 0.8 - t * 0.6) * math.sin(norm_x * 0.3)
                py = int(round(center_y + (gentle * max_amplitude)))

            py = max(0, min(ph - 1, py))

            # Draw vertical span from previous point to ensure an unbroken cathode ray trace
            if px > 0:
                canvas.draw_vertical_span(px, prev_py, py)
            else:
                canvas.set_pixel(px, py)

            prev_py = py

    def _render_ascii_oscilloscope(self, width: int, height: int) -> List[Text]:
        """Sleek line symbol fallback for legacy consoles without UTF-8 Braille."""
        lines: List[Text] = []
        center_y = height // 2
        t = self.physics.phase_acc

        for y in range(height):
            line = Text()
            line.append(" ")
            row_y_norm = 1.0 - (y / float(height - 1)) if height > 1 else 0.5

            for x in range(width):
                norm_x = (x / float(width)) * 3.5 * math.pi
                if self.is_active:
                    w = 0.65 * math.sin(norm_x - t * 2.0) + 0.35 * math.cos(norm_x * 2.4 + t * 1.5)
                else:
                    w = 0.10 * math.sin(norm_x - t * 0.5)

                val_norm = (w + 1.0) / 2.0
                diff = abs(val_norm - row_y_norm)

                if diff < (0.8 / float(height)):
                    sym = "=" if diff < 0.2 / float(height) else "~"
                    line.append(sym, style=f"bold {STYLE_ACCENT_CYAN}")
                elif y == center_y and x % 4 == 0:
                    line.append(".", style=f"{STYLE_BORDER_DIM}")
                else:
                    line.append(" ")

            lines.append(line)
        return lines

    # =========================================================================
    # Mode C: Minimalist Compact Audio Level Monitor
    # =========================================================================

    def _render_mode_compact(self, width: int, height: int) -> RenderableType:
        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column("Compact", justify="left")

        # 1. Header with Mode Tabs
        table.add_row(self._render_header_row(width))

        # 2. Dual Horizontal Precision Meters
        remaining = max(1, height - 1)
        bar_len = max(4, width - (26 if width >= 38 else 16))
        table.add_row(self._render_compact_meter_line("CH1", self.physics.cur_l_db, self.physics.peak_l_db, bar_len, width))
        if remaining >= 2:
            table.add_row(self._render_compact_meter_line("CH2", self.physics.cur_r_db, self.physics.peak_r_db, bar_len, width))

        # 3. Stream Telemetry Metrics
        if remaining >= 3:
            metrics_line = Text()
            rms_l = max(-60.0, self.physics.cur_l_db - 3.2)
            metrics_line.append(" RMS: ", style=f"{STYLE_TEXT_DIM}")
            metrics_line.append(self._format_db(rms_l), style=f"{STYLE_TEXT_NORMAL}")
            if width >= 36:
                metrics_line.append(" | PEAK: ", style=f"{STYLE_TEXT_DIM}")
                metrics_line.append(self._format_db(self.physics.peak_l_db), style=f"{STYLE_TEXT_BRIGHT}")
            if width >= 48:
                metrics_line.append(" | CREST: ", style=f"{STYLE_TEXT_DIM}")
                crest = max(0.0, self.physics.peak_l_db - rms_l)
                metrics_line.append(f"{crest:.1f} dB", style=f"{STYLE_TEXT_NORMAL}")
            table.add_row(metrics_line)

        # 4. Inline 7-Band Energy Indicators
        if remaining >= 4 and width >= 30:
            energy_line = Text()
            energy_line.append(" EQ:  ", style=f"{STYLE_TEXT_DIM}")
            labels = ["60", "150", "400", "1k", "2.5k", "6k", "15k"]
            steps = self.physics.ASCII_BLOCK_STEPS if self.safe_ascii else self.physics.BLOCK_STEPS

            for i in range(7):
                lvl = self.physics.band_levels[i]
                s_idx = max(0, min(len(steps) - 1, int(lvl * (len(steps) - 1))))
                ch = steps[s_idx]
                energy_line.append(f"{labels[i]}:", style=f"{STYLE_TEXT_DIM}")
                energy_line.append(f"{ch} ", style=f"{STYLE_BAR_HIGH if lvl > 0.6 else STYLE_BAR_LOW}")

            table.add_row(energy_line)

        # 5. Pipeline Telemetry
        if remaining >= 5:
            pipe_line = Text()
            pipe_line.append(" THREADS: ", style=f"{STYLE_TEXT_DIM}")
            pipe_line.append(f"{self.worker_count}w", style=f"{STYLE_TEXT_NORMAL}")
            pipe_line.append(" | ", style=f"{STYLE_TEXT_DIM}")
            pipe_line.append("ENCODING" if self.is_active else "IDLE", style=f"{STYLE_TEXT_BRIGHT if self.is_active else STYLE_TEXT_DIM}")
            table.add_row(pipe_line)

        return table

    def _render_compact_meter_line(
        self,
        channel_label: str,
        cur_db: float,
        peak_db: float,
        bar_width: int,
        total_width: int = 40,
    ) -> Text:
        """Renders single channel row for Mode C."""
        line = Text()
        show_peak = (total_width >= 36)
        prefix = f" {channel_label} [" if total_width >= 28 else f"{channel_label}["
        line.append(prefix, style=f"{STYLE_BORDER_DIM}")
        self._append_meter_bar(line, cur_db, peak_db, bar_width)
        line.append("] ", style=f"{STYLE_BORDER_DIM}")
        line.append(self._format_db(cur_db), style=f"{STYLE_TEXT_NORMAL}")
        if show_peak:
            line.append(f" MAX {self._format_db(peak_db)}", style=f"{STYLE_TEXT_DIM}")
        return line

    # -------------------------------------------------------------------------
    # Minimal Fallback for Extremely Small Rectangles (< 22 cols or < 3 rows)
    # -------------------------------------------------------------------------

    def _render_minimal_fallback(self, width: int, height: int) -> RenderableType:
        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column("Mini", justify="left")

        line = Text()
        bullet = "*" if self.safe_ascii else "●"
        status_style = STYLE_ACCENT_CYAN if self.is_active else STYLE_TEXT_DIM
        line.append(f"{bullet} ", style=status_style)
        line.append("AUDIO: ", style=f"{STYLE_TEXT_DIM}")
        line.append(self._format_db(self.physics.cur_l_db), style=f"{STYLE_TEXT_NORMAL}")
        table.add_row(line)

        if height > 1:
            line2 = Text()
            bar_len = max(4, width - 4)
            fill_cnt = int(round(max(0.0, min(1.0, (self.physics.cur_l_db + 48.0) / 48.0)) * bar_len))
            line2.append("[" + ("=" * fill_cnt) + (" " * (bar_len - fill_cnt)) + "]", style=f"{STYLE_TEXT_DIM}")
            table.add_row(line2)

        return table

    # =========================================================================
    # Numeric Formatting Helpers
    # =========================================================================

    @staticmethod
    def _format_db(val: float) -> str:
        """Formats decibel level float into studio string e.g. -6.2 dB."""
        if val <= -54.0:
            return "-inf dB"
        sign = "+" if val > 0.0 else ""
        return f"{sign}{val:.1f} dB"


# Aliases for package exports
VisualizerWidget = AudioVisualizerWidget
VisualizerModeChanged = AudioVisualizerWidget.ModeChanged

