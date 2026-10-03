"""
ascii_visualizer.py - High-Performance Terminal Audio Visualizer & Spectral Monitor.

Provides:
- Stereo VU Meter renderer (L/R channels, dB calibration, peak indicators)
- 7-Band Dynamic Spectrum / Equalizer animation (60Hz, 150Hz, 400Hz, 1kHz, 2.5kHz, 6kHz, 15kHz)
- Audio Crunch Pulse & Activity Spinner for background threads
- Safe ASCII fallback mode for legacy Windows consoles (CP1252 / Non-UTF8)
"""

from __future__ import annotations

import math
import sys
import time
import random
from typing import List, Optional, Tuple, Union

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text


def is_unicode_supported() -> bool:
    """
    Detects whether stdout supports UTF-8 / Unicode block characters.
    Returns False on legacy Windows consoles configured with CP1252 or non-UTF8.
    """
    try:
        encoding = getattr(sys.stdout, "encoding", "") or ""
        if "utf" in encoding.lower():
            return True
        # On Windows, check if PYTHONIOENCODING or modern Windows Terminal is present
        if sys.platform == "win32":
            if "WT_SESSION" in sys.modules.get("os", {}).get("environ", {}):  # type: ignore
                return True
            import os
            if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"):
                return True
            if "utf" in os.environ.get("PYTHONIOENCODING", "").lower():
                return True
    except Exception:
        pass
    return False


class AsciiVisualizer:
    """
    Terminal visualizer for audio levels, frequency spectrum, and thread crunch activity.
    Supports Unicode block/braille rendering with zero-exception fallback to safe ASCII.
    """

    SPECTRUM_BANDS: List[str] = [
        "60Hz",
        "150Hz",
        "400Hz",
        "1kHz",
        "2.5kHz",
        "6kHz",
        "15kHz",
    ]

    # Compact band labels for narrower terminal widths
    COMPACT_BANDS: List[str] = [
        "60",
        "150",
        "400",
        "1k",
        "2.5k",
        "6k",
        "15k",
    ]

    # Spinner animation sequences
    BRAILLE_SPINNER: List[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    ASCII_SPINNER: List[str] = ["|", "/", "-", "\\"]

    # Block height characters for Unicode (0/8 to 8/8)
    UNICODE_BLOCK_STEPS: List[str] = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    ASCII_BLOCK_STEPS: List[str] = [" ", ".", ":", "-", "=", "+", "#", "#", "@"]

    def __init__(self, safe_ascii: Optional[bool] = None, bar_width: int = 18):
        """
        Initialize the visualizer.

        :param safe_ascii: True to enforce pure 7-bit ASCII characters (no CP1252 crash risk).
                           If None, auto-detects based on system/terminal capabilities.
        :param bar_width: Default width of VU meter bars in character columns.
        """
        if safe_ascii is None:
            self._safe_ascii: bool = not is_unicode_supported()
        else:
            self._safe_ascii = safe_ascii

        self.bar_width = max(8, bar_width)
        self._apply_ascii_mode()

    @property
    def safe_ascii(self) -> bool:
        """Indicates whether safe ASCII mode is enabled."""
        return self._safe_ascii

    @safe_ascii.setter
    def safe_ascii(self, value: bool) -> None:
        """Dynamically toggles safe ASCII mode and updates all glyphs."""
        self._safe_ascii = value
        self._apply_ascii_mode()

    def _apply_ascii_mode(self) -> None:
        """Applies character sets according to safe_ascii mode."""
        if self._safe_ascii:
            self.char_fill = "|"
            self.char_empty = "."
            self.char_peak = "#"
            self.char_bolt = "[*]"
            self.spinner_frames = self.ASCII_SPINNER
            self.spectrum_chars = self.ASCII_BLOCK_STEPS
        else:
            self.char_fill = "█"
            self.char_empty = "░"
            self.char_peak = "█"
            self.char_bolt = "⚡"
            self.spinner_frames = self.BRAILLE_SPINNER
            self.spectrum_chars = self.UNICODE_BLOCK_STEPS

    # =========================================================================
    # 1. Stereo VU Meter Renderer
    # =========================================================================

    def render_vu_meter(
        self,
        l_db: float,
        r_db: float,
        width: Optional[int] = None,
        show_db: bool = True,
        min_db: float = -48.0,
        max_db: float = 0.0,
    ) -> Text:
        """
        Renders a single-line stereo VU meter with calibrated dB readouts:
        e.g., L: [||||||||||||||......] -3.2 dB | R: [||||||||||||........] -5.1 dB

        :param l_db: Left channel volume in decibels (e.g. -3.2, -60.0 to 0.0).
        :param r_db: Right channel volume in decibels.
        :param width: Character width of the progress bar portion.
        :param show_db: Whether to append numeric dB labels.
        :param min_db: Noise floor dB level (mapped to 0% bar).
        :param max_db: Ceiling dB level (mapped to 100% bar).
        :return: Rich Text object styled with green, yellow, red gradients.
        """
        bar_len = width if width is not None else self.bar_width
        out = Text()

        # Left Channel
        out.append("L: ", style="bold cyan")
        out.append("[", style="dim white")
        self._append_meter_bar(out, l_db, bar_len, min_db, max_db)
        out.append("]", style="dim white")
        if show_db:
            out.append(f" {self._format_db(l_db)}", style=self._get_db_style(l_db))

        out.append(" | ", style="dim cyan")

        # Right Channel
        out.append("R: ", style="bold cyan")
        out.append("[", style="dim white")
        self._append_meter_bar(out, r_db, bar_len, min_db, max_db)
        out.append("]", style="dim white")
        if show_db:
            out.append(f" {self._format_db(r_db)}", style=self._get_db_style(r_db))

        return out

    def render_vu_meter_stacked(
        self,
        l_db: float,
        r_db: float,
        width: Optional[int] = None,
        show_db: bool = True,
        min_db: float = -48.0,
        max_db: float = 0.0,
    ) -> List[Text]:
        """
        Renders a stacked 2-line stereo VU meter:
        Line 1: L: [████████████░░░░░░] -3.2 dB
        Line 2: R: [██████████░░░░░░░░] -5.1 dB
        """
        bar_len = width if width is not None else self.bar_width
        line_l = Text()
        line_l.append("L: ", style="bold cyan")
        line_l.append("[", style="dim white")
        self._append_meter_bar(line_l, l_db, bar_len, min_db, max_db)
        line_l.append("]", style="dim white")
        if show_db:
            line_l.append(f" {self._format_db(l_db)}", style=self._get_db_style(l_db))

        line_r = Text()
        line_r.append("R: ", style="bold cyan")
        line_r.append("[", style="dim white")
        self._append_meter_bar(line_r, r_db, bar_len, min_db, max_db)
        line_r.append("]", style="dim white")
        if show_db:
            line_r.append(f" {self._format_db(r_db)}", style=self._get_db_style(r_db))

        return [line_l, line_r]

    def _append_meter_bar(
        self,
        text_obj: Text,
        db_val: float,
        width: int,
        min_db: float,
        max_db: float,
    ) -> None:
        """Appends colored VU meter blocks according to volume percentage."""
        # Clamp db_val
        db_clamped = max(min_db, min(max_db + 3.0, db_val))
        range_db = max(0.1, max_db - min_db)
        fraction = max(0.0, min(1.0, (db_clamped - min_db) / range_db))
        filled_count = int(round(fraction * width))

        # Zone thresholds (e.g. green up to 60%, yellow 60-85%, red >85%)
        green_zone = int(width * 0.60)
        yellow_zone = int(width * 0.85)

        for i in range(width):
            if i < filled_count:
                if i < green_zone:
                    style = "white"
                elif i < yellow_zone:
                    style = "dim white"
                else:
                    style = "dim"
                text_obj.append(self.char_fill, style=style)
            else:
                text_obj.append(self.char_empty, style="dim white")

    @staticmethod
    def _format_db(db_val: float) -> str:
        """Formats decibel float into standard studio string."""
        if db_val <= -60.0:
            return "-inf dB"
        sign = "+" if db_val > 0.0 else ""
        return f"{sign}{db_val:.1f} dB"

    @staticmethod
    def _get_db_style(db_val: float) -> str:
        """Determines text styling for dB numeric indicator."""
        if db_val > -1.0:
            return "bold bright_red"
        if db_val > -6.0:
            return "bright_yellow"
        if db_val > -18.0:
            return "green"
        return "dim cyan"

    def simulate_stereo_levels(
        self,
        time_sec: float,
        progress: float = 0.0,
        speed: float = 1.0,
        is_active: bool = True,
    ) -> Tuple[float, float]:
        """
        Generates realistic dynamic stereo dB fluctuations based on transcode state.
        When inactive: drops down to noise floor (-60 dB).
        When active: bounces dynamically between -24.0 dB and -1.0 dB with stereo panning.
        """
        if not is_active:
            return -60.0, -60.0

        spd = max(0.2, min(8.0, speed))
        t = time_sec * spd

        # Dynamic harmonic wave synthesis for volume modulation
        base_energy = 0.65 + 0.20 * math.sin(t * 1.5) + 0.15 * math.cos(t * 3.7)
        # Stereo divergence (panning / independent channel dynamics)
        pan = 0.15 * math.sin(t * 2.3 + 0.5)

        l_norm = max(0.05, min(0.98, base_energy + pan + 0.05 * math.sin(t * 7.1)))
        r_norm = max(0.05, min(0.98, base_energy - pan + 0.05 * math.cos(t * 6.3)))

        # Add occasional dynamic peak/accent
        if math.sin(t * 0.8) > 0.85:
            l_norm = min(1.0, l_norm + 0.12)
            r_norm = min(1.0, r_norm + 0.10)

        # Convert 0.0..1.0 norm to dB (-45.0 to 0.0)
        l_db = -45.0 * (1.0 - l_norm)
        r_db = -45.0 * (1.0 - r_norm)

        return round(l_db, 1), round(r_db, 1)

    # =========================================================================
    # 2. Dynamic Waveform / Spectrum Animation (7 Bands)
    # =========================================================================

    def calculate_spectrum_bands(
        self,
        time_sec: float,
        progress: float = 0.0,
        speed: float = 1.0,
        is_active: bool = True,
    ) -> List[float]:
        """
        Calculates normalized amplitude (0.0 to 1.0) for each of the 7 audio bands:
        [60Hz, 150Hz, 400Hz, 1kHz, 2.5kHz, 6kHz, 15kHz]
        using multi-oscillator acoustic modeling modulated by speed and progress.
        """
        if not is_active:
            return [0.0] * 7

        spd = max(0.1, min(10.0, speed))
        t = time_sec * (1.0 + 0.4 * spd)
        prog_bias = (progress % 100.0) / 100.0

        # Frequency coefficients and phase offsets for realistic musical spectrum
        freq_weights = [1.2, 1.9, 2.8, 4.1, 5.7, 7.3, 9.1]
        phases = [0.0, 1.2, 2.4, 3.1, 4.5, 5.3, 0.7]
        base_levels = [0.70, 0.75, 0.65, 0.60, 0.55, 0.45, 0.35]  # Natural pink noise tilt

        amplitudes = []
        for i in range(7):
            w = freq_weights[i]
            ph = phases[i]
            base = base_levels[i]

            # Primary oscillation + secondary harmonic jitter + macro swell
            osc1 = 0.30 * math.sin(t * w + ph)
            osc2 = 0.15 * math.cos(t * w * 1.8 + ph * 1.5)
            macro = 0.15 * math.sin(t * 0.4 + prog_bias * 2.0 * math.pi)

            val = base + osc1 + osc2 + macro

            # Micro-jitter for high frequencies (presence and brilliance)
            if i >= 4:
                val += 0.08 * math.sin(t * 14.0 + i)

            # Clamp between 0.05 and 1.0
            clamped = max(0.06, min(1.0, val))
            amplitudes.append(clamped)

        return amplitudes

    def render_spectrum_vertical(
        self,
        time_sec: float,
        progress: float = 0.0,
        speed: float = 1.0,
        is_active: bool = True,
        height: int = 5,
        compact_labels: bool = False,
    ) -> List[Text]:
        """
        Renders a multi-line vertical dancing frequency equalizer.
        Produces `height` rows of spectrum bars followed by a frequency label row.

        Example:
                █     █     █
          █     █     █     █     █
          █     █     █     █     █     █
         60Hz 150Hz 400Hz  1kHz 2.5kHz 6kHz 15kHz
        """
        amps = self.calculate_spectrum_bands(time_sec, progress, speed, is_active)
        lines: List[Text] = []
        col_width = 4 if compact_labels else 6

        # Color scale based on vertical row height (from bottom to top)
        # Top rows get yellow/red, lower rows get bright green / cyan
        for row in range(height - 1, -1, -1):
            line = Text()
            row_threshold_low = row / float(height)
            row_threshold_high = (row + 1) / float(height)

            # Determine color for this row
            if row >= height - 1:
                row_style = "dim"
            elif row >= height - 2:
                row_style = "dim white"
            elif row >= 1:
                row_style = "white"
            else:
                row_style = "bold white"

            # Render 7 columns
            for i, amp in enumerate(amps):
                if amp >= row_threshold_high:
                    # Fully filled cell
                    char = self.char_fill if self.safe_ascii else "█"
                    content = char.center(col_width)
                    line.append(content, style=row_style)
                elif amp > row_threshold_low:
                    # Partially filled cell
                    frac = (amp - row_threshold_low) / (row_threshold_high - row_threshold_low)
                    step_idx = int(frac * (len(self.spectrum_chars) - 1))
                    step_idx = max(1, min(len(self.spectrum_chars) - 1, step_idx))
                    char = self.spectrum_chars[step_idx]
                    content = char.center(col_width)
                    line.append(content, style=row_style)
                else:
                    # Empty space
                    line.append(" " * col_width)

            lines.append(line)

        # Append frequency band label row
        labels = self.COMPACT_BANDS if compact_labels else self.SPECTRUM_BANDS
        label_line = Text()
        for i, lbl in enumerate(labels):
            label_line.append(lbl.center(col_width), style="dim")
        lines.append(label_line)

        return lines

    def render_spectrum_horizontal(
        self,
        time_sec: float,
        progress: float = 0.0,
        speed: float = 1.0,
        is_active: bool = True,
        bar_len: int = 8,
    ) -> List[Text]:
        """
        Renders horizontal spectrum lines for each frequency band.
        Useful when vertical space is constrained but horizontal space is available.
        """
        amps = self.calculate_spectrum_bands(time_sec, progress, speed, is_active)
        lines: List[Text] = []

        for band_name, amp in zip(self.SPECTRUM_BANDS, amps):
            line = Text()
            line.append(f"{band_name:>6}: [", style="dim cyan")
            filled = int(round(amp * bar_len))
            for b in range(bar_len):
                if b < filled:
                    style = "bright_green" if b < bar_len * 0.6 else ("bright_yellow" if b < bar_len * 0.85 else "bold red")
                    line.append(self.char_fill, style=style)
                else:
                    line.append(self.char_empty, style="dim white")
            line.append("]", style="dim cyan")
            pct = int(amp * 100)
            line.append(f" {pct:>3}%", style="dim white")
            lines.append(line)

        return lines

    def render_oscilloscope(
        self,
        time_sec: float,
        is_active: bool = True,
        width: int = 42,
        height: int = 5,
    ) -> List[Text]:
        """
        Renders a Matrix-style raw oscilloscope waveform.
        """
        lines: List[Text] = []
        if not is_active:
            for _ in range(height):
                lines.append(Text("-" * width, style="dim"))
            return lines

        t = time_sec * 3.0
        wave_chars = ["_", ".", "-", "~", "*", "+", "=", "#", "█"] if not self.safe_ascii else ["_", ".", "-", "~", "*", "+", "=", "#", "@"]
        
        for y in range(height):
            line = Text()
            for x in range(width):
                val = math.sin(t + x * 0.2) + 0.5 * math.cos(t * 1.5 - x * 0.4) + 0.3 * math.sin(t * 3.1 + x * 0.1)
                val = max(0.0, min(1.0, (val + 1.8) / 3.6))
                
                if random.random() > 0.95:
                    val = random.random()
                
                row_threshold = 1.0 - (y / float(height - 1)) if height > 1 else 0.5
                
                if abs(val - row_threshold) < (1.0 / height):
                    idx = int(val * (len(wave_chars) - 1))
                    style = "white" if val > 0.8 else ("dim white" if val > 0.4 else "dim")
                    line.append(wave_chars[idx], style=style)
                else:
                    if random.random() > 0.98:
                        line.append(random.choice(["0", "1"]), style="dim")
                    else:
                        line.append(" ")
            lines.append(line)
        return lines

    # =========================================================================
    # 3. Pulse & Activity Spinner
    # =========================================================================

    def render_spinner(
        self,
        time_sec: float,
        is_active: bool = True,
        speed_str: str = "",
        workers: int = 1,
    ) -> Text:
        """
        Renders an animated pulse & activity spinner indicating background crunching.
        """
        out = Text()
        if not is_active:
            out.append("[STANDBY] ", style="dim")
            out.append("Threads idle", style="dim")
            return out

        frame_idx = int(time_sec * 8.0) % len(self.spinner_frames)
        frame_char = self.spinner_frames[frame_idx]

        # Activity pulse wave
        pulse_steps = 6
        pulse_pos = int(time_sec * 6.0) % (pulse_steps * 2)
        if pulse_pos >= pulse_steps:
            pulse_pos = (pulse_steps * 2 - 1) - pulse_pos

        pulse_bar = ["."] * pulse_steps
        if 0 <= pulse_pos < pulse_steps:
            pulse_bar[pulse_pos] = "=" if self.safe_ascii else "●"
        pulse_str = "".join(pulse_bar)

        out.append(f"{self.char_bolt} ", style="bold white")
        out.append(f"{frame_char} ", style="dim white")
        out.append("CRUNCHING ", style="white")
        out.append(f"[{pulse_str}] ", style="dim")

        if speed_str:
            out.append(f"{speed_str} ", style="white")

        thread_lbl = "worker" if workers == 1 else "workers"
        out.append(f"({workers} {thread_lbl})", style="dim")

        return out

    # =========================================================================
    # 4. Combined Audio Monitor Widget
    # =========================================================================

    def render_monitor_widget(
        self,
        time_sec: float,
        progress: float = 0.0,
        speed: float = 1.0,
        is_active: bool = True,
        l_db: Optional[float] = None,
        r_db: Optional[float] = None,
        workers: int = 1,
        spectrum_height: int = 4,
        compact: bool = False,
    ) -> Table:
        """
        Builds a unified visual table containing:
        1. Pulse & thread activity indicator
        2. Stereo VU Meters
        3. 7-Band Dancing Spectrum Equalizer
        """
        # Determine dB values (either provided or simulated)
        if l_db is None or r_db is None:
            sim_l, sim_r = self.simulate_stereo_levels(time_sec, progress, speed, is_active)
            cur_l = sim_l if l_db is None else l_db
            cur_r = sim_r if r_db is None else r_db
        else:
            cur_l, cur_r = l_db, r_db

        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column("Monitor", justify="left")

        # 1. Activity Spinner Header
        speed_display = f"{speed:.1f}x" if is_active and speed > 0 else ""
        table.add_row(self.render_spinner(time_sec, is_active, speed_display, workers))

        # 2. Stereo VU Meter
        meter_width = 12 if compact else 18
        if compact:
            # Stacked L / R on separate lines
            for row in self.render_vu_meter_stacked(cur_l, cur_r, width=meter_width):
                table.add_row(row)
        else:
            # Inline L / R
            table.add_row(self.render_vu_meter(cur_l, cur_r, width=meter_width))

        # 3. Visualizations (Spectrum + Oscilloscope)
        if compact:
            spec_lines = self.render_spectrum_vertical(
                time_sec=time_sec,
                progress=progress,
                speed=speed,
                is_active=is_active,
                height=spectrum_height,
                compact_labels=True,
            )
            for s_line in spec_lines:
                table.add_row(s_line)
        else:
            vis_grid = Table.grid(padding=(0, 1), expand=True)
            vis_grid.add_column("Spectrum", ratio=1)
            vis_grid.add_column("Oscilloscope", ratio=2)
            
            spec_lines = self.render_spectrum_vertical(
                time_sec=time_sec,
                progress=progress,
                speed=speed,
                is_active=is_active,
                height=spectrum_height,
                compact_labels=False,
            )
            osc_lines = self.render_oscilloscope(
                time_sec=time_sec,
                is_active=is_active,
                width=35,
                height=spectrum_height + 1,
            )
            
            for i in range(max(len(spec_lines), len(osc_lines))):
                s_part = spec_lines[i] if i < len(spec_lines) else Text()
                o_part = osc_lines[i] if i < len(osc_lines) else Text()
                vis_grid.add_row(s_part, o_part)
                
            table.add_row(vis_grid)

        return table
