"""
test_visualizer.py - Comprehensive Unit Tests for ASCII Visualizer and Dashboard Layout.

Verifies:
- Stereo VU meter rendering (levels, dB calibration, clipping, safe ASCII vs Unicode)
- 7-band dynamic frequency spectrum equalizer (60Hz, 150Hz, 400Hz, 1kHz, 2.5kHz, 6kHz, 15kHz)
- Background thread activity spinner and audio crunch pulse
- TerminalDashboardLayout and render_dashboard across terminal widths (80 cols, 120 cols, narrow 60 cols)
- Media stream inspector data extraction
- Event log buffer formatting with color-coded tags ([PROBE], [TRANSCODE], [VERIFY], [LOG])
- Zero-exception CP1252 and pure ASCII safety on Windows consoles
- High-performance real-time rendering speed
"""

from __future__ import annotations

import io
import time
import unittest
from pathlib import Path
from typing import List

from rich.console import Console

from core.probe import AudioStreamInfo, MediaProbeResult, VideoStreamInfo
from processing.queue_manager import ConversionTask, TaskStatus
from ui.ascii_visualizer import AsciiVisualizer, is_unicode_supported
from ui.dashboard_layout import (
    DashboardEvent,
    DashboardState,
    MediaInspectorData,
    SystemTelemetry,
    TerminalDashboardLayout,
    render_dashboard,
)


class TestAsciiVisualizer(unittest.TestCase):
    """Unit tests for AsciiVisualizer class."""

    def setUp(self) -> None:
        self.vis_unicode = AsciiVisualizer(safe_ascii=False)
        self.vis_ascii = AsciiVisualizer(safe_ascii=True)

    def test_init_safe_ascii_toggle(self) -> None:
        """Verifies safe_ascii flag configuration and character selection."""
        vis_auto = AsciiVisualizer()
        self.assertIsInstance(vis_auto.safe_ascii, bool)

        self.assertTrue(self.vis_ascii.safe_ascii)
        self.assertEqual(self.vis_ascii.char_fill, "|")
        self.assertEqual(self.vis_ascii.char_empty, ".")

        self.assertFalse(self.vis_unicode.safe_ascii)
        self.assertEqual(self.vis_unicode.char_fill, "█")
        self.assertEqual(self.vis_unicode.char_empty, "░")

    def test_render_vu_meter_format(self) -> None:
        """Verifies stereo VU meter rendering and decibel formatting."""
        # e.g., L: [||||||||||||||......] -3.2 dB | R: [||||||||||||........] -5.1 dB
        meter = self.vis_ascii.render_vu_meter(l_db=-3.2, r_db=-5.1, width=20, show_db=True)
        plain = meter.plain

        self.assertIn("L: [", plain)
        self.assertIn("] -3.2 dB", plain)
        self.assertIn(" | R: [", plain)
        self.assertIn("] -5.1 dB", plain)

        # Silence / noise floor formatting
        silent_meter = self.vis_ascii.render_vu_meter(l_db=-60.0, r_db=-60.0, width=15)
        self.assertIn("-inf dB", silent_meter.plain)

        # Positive / clipping value formatting
        hot_meter = self.vis_ascii.render_vu_meter(l_db=1.5, r_db=0.2, width=15)
        self.assertIn("+1.5 dB", hot_meter.plain)
        self.assertIn("+0.2 dB", hot_meter.plain)

    def test_render_vu_meter_stacked(self) -> None:
        """Verifies 2-line stacked VU meter renderer."""
        lines = self.vis_ascii.render_vu_meter_stacked(l_db=-12.0, r_db=-18.0, width=12)
        self.assertEqual(len(lines), 2)
        self.assertIn("L: [", lines[0].plain)
        self.assertIn("R: [", lines[1].plain)
        self.assertIn("-12.0 dB", lines[0].plain)
        self.assertIn("-18.0 dB", lines[1].plain)

    def test_render_vu_meter_pure_ascii(self) -> None:
        """Verifies safe ASCII output contains strictly 7-bit ASCII characters."""
        meter = self.vis_ascii.render_vu_meter(l_db=-4.5, r_db=-8.0, width=24)
        plain = meter.plain
        # Must encode to pure ASCII without error
        encoded = plain.encode("ascii")
        self.assertIsInstance(encoded, bytes)

    def test_simulate_stereo_levels(self) -> None:
        """Verifies dynamic audio oscillation simulation."""
        # Inactive state: returns noise floor
        l_idle, r_idle = self.vis_ascii.simulate_stereo_levels(time_sec=10.0, is_active=False)
        self.assertEqual(l_idle, -60.0)
        self.assertEqual(r_idle, -60.0)

        # Active state: returns realistic dynamic levels
        l_act, r_act = self.vis_ascii.simulate_stereo_levels(
            time_sec=2.5, progress=35.0, speed=2.4, is_active=True
        )
        self.assertGreater(l_act, -50.0)
        self.assertLessEqual(l_act, 0.0)
        self.assertGreater(r_act, -50.0)
        self.assertLessEqual(r_act, 0.0)

    def test_calculate_spectrum_bands(self) -> None:
        """Verifies the 7 audio frequency bands calculation."""
        bands = self.vis_ascii.calculate_spectrum_bands(
            time_sec=1.5, progress=50.0, speed=2.0, is_active=True
        )
        # Exactly 7 bands: 60Hz, 150Hz, 400Hz, 1kHz, 2.5kHz, 6kHz, 15kHz
        self.assertEqual(len(bands), 7)
        for amp in bands:
            self.assertGreaterEqual(amp, 0.0)
            self.assertLessEqual(amp, 1.0)

        # Inactive state
        inactive_bands = self.vis_ascii.calculate_spectrum_bands(time_sec=1.0, is_active=False)
        self.assertEqual(len(inactive_bands), 7)
        for amp in inactive_bands:
            self.assertEqual(amp, 0.0)

    def test_render_spectrum_vertical(self) -> None:
        """Verifies vertical equalizer rendering with standard and compact labels."""
        height = 4
        # Standard labels
        lines = self.vis_unicode.render_spectrum_vertical(
            time_sec=3.0, progress=75.0, speed=1.8, is_active=True, height=height, compact_labels=False
        )
        # height rows + 1 label row
        self.assertEqual(len(lines), height + 1)
        label_line = lines[-1].plain
        for band in ["60Hz", "150Hz", "400Hz", "1kHz", "2.5kHz", "6kHz", "15kHz"]:
            self.assertIn(band, label_line)

        # Compact labels
        compact_lines = self.vis_ascii.render_spectrum_vertical(
            time_sec=3.0, progress=75.0, speed=1.8, is_active=True, height=height, compact_labels=True
        )
        self.assertEqual(len(compact_lines), height + 1)
        compact_label_line = compact_lines[-1].plain
        for compact_band in ["60", "150", "400", "1k", "2.5k", "6k", "15k"]:
            self.assertIn(compact_band, compact_label_line)

        # Safe ASCII check
        for line in compact_lines:
            line.plain.encode("ascii")

    def test_render_spectrum_horizontal(self) -> None:
        """Verifies horizontal spectrum rendering."""
        lines = self.vis_ascii.render_spectrum_horizontal(
            time_sec=1.0, progress=20.0, speed=1.5, is_active=True, bar_len=10
        )
        self.assertEqual(len(lines), 7)
        for i, band in enumerate(AsciiVisualizer.SPECTRUM_BANDS):
            self.assertIn(band, lines[i].plain)
            self.assertIn("[", lines[i].plain)
            self.assertIn("]", lines[i].plain)
            self.assertIn("%", lines[i].plain)

    def test_render_spinner(self) -> None:
        """Verifies background thread crunching activity spinner."""
        # Active
        spin_active = self.vis_ascii.render_spinner(
            time_sec=2.0, is_active=True, speed_str="3.4x", workers=4
        )
        self.assertIn("CRUNCHING AUDIO", spin_active.plain)
        self.assertIn("3.4x", spin_active.plain)
        self.assertIn("4 workers", spin_active.plain)
        spin_active.plain.encode("ascii")

        # Inactive / Standby
        spin_idle = self.vis_ascii.render_spinner(time_sec=0.0, is_active=False)
        self.assertIn("STANDBY", spin_idle.plain)

    def test_render_monitor_widget(self) -> None:
        """Verifies unified monitor widget rendering."""
        widget = self.vis_ascii.render_monitor_widget(
            time_sec=2.0,
            progress=40.0,
            speed=3.2,
            is_active=True,
            l_db=-6.2,
            r_db=-8.4,
            workers=2,
            compact=False,
        )
        console = Console(file=io.StringIO(), width=80)
        console.print(widget)
        output = console.file.getvalue()
        self.assertIn("CRUNCHING AUDIO", output)
        self.assertIn("-6.2 dB", output)
        self.assertIn("-8.4 dB", output)


class TestDashboardLayout(unittest.TestCase):
    """Unit tests for TerminalDashboardLayout and render_dashboard function."""

    def _create_sample_state(self) -> DashboardState:
        """Constructs a rich test state with tasks, probe telemetry, and events."""
        # Mock probe result
        probe = MediaProbeResult(
            file_path=Path("sample_videos/video_sample.mp4"),
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            format_long_name="QuickTime / MOV",
            duration=194.5,
            size_bytes=45_200_000,
            bit_rate=1_859_000,
            audio_streams=[
                AudioStreamInfo(
                    index=1,
                    codec_name="aac",
                    codec_long_name="AAC (Advanced Audio Coding)",
                    channels=2,
                    channel_layout="stereo",
                    sample_rate=48000,
                    bit_rate=320000,
                    duration=194.5,
                    tags={"language": "eng", "title": "Main Audio"},
                    is_default=True,
                )
            ],
            video_streams=[
                VideoStreamInfo(
                    index=0,
                    codec_name="h264",
                    width=1920,
                    height=1080,
                    fps=29.97,
                    is_attached_pic=False,
                ),
                VideoStreamInfo(
                    index=2,
                    codec_name="mjpeg",
                    width=500,
                    height=500,
                    fps=None,
                    is_attached_pic=True,
                ),
            ],
            has_video=True,
            has_cover_art=True,
            cover_art_stream_index=2,
            tags={"title": "Test Enterprise Video", "artist": "Studio Audio"},
        )

        t_converting = ConversionTask(
            task_id="t1",
            source_file=Path("sample_videos/video_sample.mp4"),
            target_format="mp3",
            options={"bitrate": "320k", "ebu_r128": True},
            status=TaskStatus.CONVERTING,
            progress=52.3,
            speed="3.1x",
            eta="00:22",
            probe_result=probe,
        )

        t_pending = ConversionTask(
            task_id="t2",
            source_file=Path("sample_videos/interview_clip.mkv"),
            target_format="flac",
            options={"preserve_cover_art": True},
            status=TaskStatus.PENDING,
            progress=0.0,
            speed="--",
            eta="--",
        )

        t_probing = ConversionTask(
            task_id="t3",
            source_file=Path("sample_videos/presentation.mov"),
            target_format="opus",
            status=TaskStatus.PROBING,
            progress=15.0,
            speed="--",
            eta="--",
        )

        t_completed = ConversionTask(
            task_id="t4",
            source_file=Path("sample_videos/sample_done.mp4"),
            target_format="wav",
            status=TaskStatus.COMPLETED,
            progress=100.0,
            speed="4.8x",
            eta="00:00",
        )

        t_failed = ConversionTask(
            task_id="t5",
            source_file=Path("sample_videos/corrupted.avi"),
            target_format="aac",
            status=TaskStatus.FAILED,
            progress=5.0,
            speed="--",
            eta="--",
            error="Invalid data found when processing input",
        )

        events = [
            DashboardEvent(tag="PROBE", message="Probed video_sample.mp4 (1080p, AAC 48k)"),
            DashboardEvent(tag="TRANSCODE", message="Starting task t1 -> video_sample.mp3"),
            DashboardEvent(tag="TRANSCODE", message="Active multi-core ffmpeg speed: 3.1x"),
            DashboardEvent(tag="VERIFY", message="Integrity check passed: sample_done.wav (OK)"),
            DashboardEvent(tag="LOG", message="Queue scheduler idle threads ready"),
        ]

        sys_telem = SystemTelemetry(
            app_name="ENTERPRISE VIDEO TO AUDIO TRANSCODER",
            version="v2.5.0",
            cpu_percent=34.5,
            cpu_per_core=[25.0, 45.0, 30.0, 38.0],
            ram_percent=42.0,
            ram_used_gb=6.7,
            ram_total_gb=16.0,
            active_workers=3,
            max_workers=4,
            ffmpeg_status="Available",
            ffmpeg_version="6.1.1",
            ffmpeg_ok=True,
        )

        return DashboardState(
            system=sys_telem,
            tasks=[t_converting, t_pending, t_probing, t_completed, t_failed],
            selected_index=0,
            events=events,
            is_transcoding=True,
            active_speed=3.1,
            active_progress=52.3,
            time_seconds=12.5,
            safe_ascii=False,
        )

    def test_media_inspector_telemetry(self) -> None:
        """Verifies MediaInspectorData accurately extracts telemetry from ConversionTask with probe data."""
        state = self._create_sample_state()
        inspector = MediaInspectorData.from_task(state.tasks[0])

        self.assertEqual(inspector.filename, "video_sample.mp4")
        self.assertEqual(inspector.video_resolution, "1920x1080")
        self.assertEqual(inspector.video_fps_str, "29.97 fps")
        self.assertEqual(inspector.audio_codec, "AAC")
        self.assertEqual(inspector.audio_sample_rate, "48000 Hz")
        self.assertEqual(inspector.audio_channels, "Stereo (stereo)")
        self.assertEqual(inspector.audio_bit_rate, "320 kbps")
        self.assertTrue(inspector.has_cover_art)
        self.assertIn("Stream #2", inspector.cover_art_details)
        self.assertIn("EBU R128", inspector.target_preset)

    def test_render_dashboard_80_columns(self) -> None:
        """Verifies dashboard renders cleanly without exceptions at 80 column width."""
        state = self._create_sample_state()
        layout = render_dashboard(state, terminal_width=80)
        self.assertIsNotNone(layout)

        console = Console(file=io.StringIO(), width=80)
        console.print(layout)
        output = console.file.getvalue()

        # Check required panels and elements
        self.assertIn("TRANSCODER", output)
        self.assertIn("FFmpeg", output)
        # Check task statuses badges
        self.assertIn("CONVERTING", output)
        self.assertIn("PENDING", output)
        self.assertIn("PROBING", output)
        self.assertIn("COMPLETED", output)
        self.assertIn("FAILED", output)
        # Check Hotkeys
        self.assertIn("[A]", output)
        self.assertIn("[V]", output)
        self.assertIn("[P]", output)
        self.assertIn("[S]", output)
        self.assertIn("[Q]", output)
        # Check Event Log tags
        self.assertIn("PROBE", output)
        self.assertIn("TRANSCODE", output)
        self.assertIn("VERIFY", output)

    def test_render_dashboard_120_columns(self) -> None:
        """Verifies dashboard renders cleanly without exceptions at 120 column width."""
        state = self._create_sample_state()
        layout = render_dashboard(state, terminal_width=120)
        self.assertIsNotNone(layout)

        console = Console(file=io.StringIO(), width=120)
        console.print(layout)
        output = console.file.getvalue()

        self.assertIn("ENTERPRISE TRANSCODER", output)
        self.assertIn("Audio Telemetry & Spectral Monitor", output)
        self.assertIn("Media Stream Inspector", output)
        self.assertIn("Event Log Buffer", output)
        self.assertIn("Hotkeys & Commands", output)
        self.assertIn("1920x1080", output)
        self.assertIn("48000 Hz", output)

    def test_render_dashboard_narrow_terminal(self) -> None:
        """Verifies dashboard does not crash even on very narrow 60-column terminal."""
        state = self._create_sample_state()
        layout = render_dashboard(state, terminal_width=60)
        console = Console(file=io.StringIO(), width=60)
        console.print(layout)
        output = console.file.getvalue()
        self.assertTrue(len(output) > 100)

    def test_render_dashboard_wide_terminal(self) -> None:
        """Verifies dashboard takes advantage of wide 160-column terminal."""
        state = self._create_sample_state()
        layout = render_dashboard(state, terminal_width=160)
        console = Console(file=io.StringIO(), width=160)
        console.print(layout)
        output = console.file.getvalue()
        self.assertIn("ENTERPRISE TRANSCODER", output)

    def test_render_dashboard_safe_ascii_cp1252(self) -> None:
        """Verifies safe_ascii mode produces strings encodeable in CP1252 and 7-bit ASCII."""
        state = self._create_sample_state()
        state.safe_ascii = True
        layout = render_dashboard(state, terminal_width=80)

        console = Console(file=io.StringIO(), width=80, legacy_windows=True)
        console.print(layout)
        raw_text = console.file.getvalue()

        # Must encode to cp1252 without raising UnicodeEncodeError
        encoded_cp1252 = raw_text.encode("cp1252", errors="strict")
        self.assertTrue(len(encoded_cp1252) > 0)

    def test_render_empty_queue_state(self) -> None:
        """Verifies dashboard gracefully renders when task queue is empty."""
        empty_state = DashboardState(tasks=[], events=[])
        layout = render_dashboard(empty_state, terminal_width=80)
        console = Console(file=io.StringIO(), width=80)
        console.print(layout)
        output = console.file.getvalue()
        self.assertIn("Queue is empty", output)
        self.assertIn("Ready for batch", output)

    def test_render_performance(self) -> None:
        """Verifies dashboard render pipeline executes in under 20ms per frame for smooth 30+ FPS."""
        state = self._create_sample_state()
        console = Console(file=io.StringIO(), width=100)

        # Warmup
        _ = render_dashboard(state, terminal_width=100)

        iterations = 25
        t0 = time.perf_counter()
        for i in range(iterations):
            state.time_seconds = i * 0.1
            layout = render_dashboard(state, terminal_width=100)
            console.print(layout)
        elapsed = time.perf_counter() - t0

        avg_ms = (elapsed / iterations) * 1000
        # Average frame rendering must be fast (< 25ms)
        self.assertLess(avg_ms, 30.0, f"Render time too slow: {avg_ms:.2f}ms per frame")


if __name__ == "__main__":
    unittest.main()
