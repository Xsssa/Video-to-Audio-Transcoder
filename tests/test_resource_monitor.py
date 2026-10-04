"""
test_resource_monitor.py - Comprehensive Unit Tests for ResourceMonitorWidget.

Verifies:
1. Per-core CPU utilization sampling and sparkline rendering.
2. RAM utilization (used/total GB and percentages).
3. Transcoder worker concurrency (active threads vs max workers).
4. GPU hardware acceleration status detection (NVENC / QSV / AMF / Auto-Accelerated).
5. FFmpeg binary status detection and version formatting.
6. Automatic background timer and smooth flicker-free refresh.
7. Sleek minimalist monochrome styling and safe ASCII fallback.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psutil
from textual.app import App, ComposeResult
from rich.table import Table
from rich.panel import Panel

from ui.tui_widgets.resource_monitor import (
    ResourceMonitorWidget,
    ResourceTelemetrySnapshot,
    GpuInfo,
    FFmpegInfo,
    detect_gpu_acceleration,
    detect_ffmpeg_status,
    sample_resource_telemetry,
    render_sparkline,
    render_mini_bar,
    render_resource_monitor_table,
    render_resource_monitor_panel,
)


class TestResourceMonitorTelemetry(unittest.TestCase):
    """Tests for raw telemetry sampling and hardware detection engines."""

    def test_sample_telemetry_live(self):
        """Verify real system telemetry sampling."""
        snapshot = sample_resource_telemetry(active_workers=2, max_workers=6)
        self.assertIsInstance(snapshot, ResourceTelemetrySnapshot)
        self.assertIsInstance(snapshot.cpu_percent_overall, float)
        self.assertIsInstance(snapshot.cpu_per_core, list)
        self.assertGreater(len(snapshot.cpu_per_core), 0)
        self.assertGreaterEqual(snapshot.ram_total_gb, 1.0)
        self.assertGreaterEqual(snapshot.ram_percent, 0.0)
        self.assertEqual(snapshot.active_workers, 2)
        self.assertEqual(snapshot.max_workers, 6)
        self.assertIsInstance(snapshot.gpu_info, GpuInfo)
        self.assertIsInstance(snapshot.ffmpeg_info, FFmpegInfo)

    @patch("psutil.cpu_percent")
    @patch("psutil.virtual_memory")
    def test_sample_telemetry_mocked(self, mock_vm, mock_cpu):
        """Verify metric calculation accuracy with mocked values."""
        mock_cpu.side_effect = [45.5, [20.0, 40.0, 60.0, 80.0]]
        mock_vm.return_value = MagicMock(
            total=16 * (1024**3),
            available=8 * (1024**3),
            percent=50.0,
        )

        snapshot = sample_resource_telemetry(active_workers=1, max_workers=4)
        self.assertEqual(snapshot.cpu_percent_overall, 45.5)
        self.assertEqual(snapshot.cpu_per_core, [20.0, 40.0, 60.0, 80.0])
        self.assertAlmostEqual(snapshot.ram_total_gb, 16.0, places=1)
        self.assertAlmostEqual(snapshot.ram_used_gb, 8.0, places=1)
        self.assertEqual(snapshot.ram_percent, 50.0)
        self.assertEqual(snapshot.active_workers, 1)
        self.assertEqual(snapshot.max_workers, 4)

    def test_queue_manager_integration(self):
        """Verify active workers and max_workers extraction from QueueManager."""
        mock_qm = MagicMock()
        mock_qm.get_stats.return_value = MagicMock(
            converting_tasks=3,
            probing_tasks=1,
            max_workers=8,
        )

        snapshot = sample_resource_telemetry(queue_manager=mock_qm)
        self.assertEqual(snapshot.active_workers, 4)  # 3 converting + 1 probing
        self.assertEqual(snapshot.max_workers, 8)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_gpu_acceleration_nvidia(self, mock_sub, mock_which):
        """Verify detection of NVIDIA NVENC hardware acceleration."""
        mock_which.return_value = r"C:\Windows\System32\nvidia-smi.exe"
        mock_sub.return_value = MagicMock(
            stdout="cuda\ndxva2\nqsv\nd3d11va\n",
            stderr="",
        )

        gpu_info = detect_gpu_acceleration(force_refresh=True)
        self.assertTrue(gpu_info.available)
        self.assertIn("Auto-Accelerated", gpu_info.status_text)

    def test_detect_ffmpeg_status(self):
        """Verify FFmpeg detection and binary path resolution."""
        ffmpeg_info = detect_ffmpeg_status(force_refresh=True)
        self.assertIsInstance(ffmpeg_info, FFmpegInfo)
        if ffmpeg_info.is_ok:
            self.assertTrue(ffmpeg_info.path.endswith("ffmpeg.exe") or "ffmpeg" in ffmpeg_info.path.lower())
            self.assertTrue(ffmpeg_info.status_text.startswith("[OK]"))


class TestMonochromeRenderers(unittest.TestCase):
    """Tests for sleek monochrome dark UI rendering."""

    def test_render_sparkline_unicode(self):
        spark = render_sparkline([10.0, 50.0, 90.0], safe_ascii=False)
        self.assertIn("[", spark.plain)
        self.assertIn("]", spark.plain)
        self.assertEqual(len(spark.plain), 5)  # '[', 3 chars, ']'

    def test_render_sparkline_safe_ascii(self):
        spark = render_sparkline([10.0, 50.0, 90.0], safe_ascii=True)
        self.assertIn("[", spark.plain)
        self.assertIn("]", spark.plain)
        for ch in spark.plain:
            self.assertLess(ord(ch), 128)

    def test_render_mini_bar_unicode_and_ascii(self):
        bar_uni = render_mini_bar(50.0, width=10, safe_ascii=False)
        self.assertIn("█", bar_uni.plain)

        bar_asc = render_mini_bar(50.0, width=10, safe_ascii=True)
        self.assertIn("#", bar_asc.plain)
        self.assertIn("-", bar_asc.plain)

    def test_render_table_and_panel(self):
        snapshot = ResourceTelemetrySnapshot(
            cpu_percent_overall=28.4,
            cpu_per_core=[20.0, 30.0, 40.0, 50.0],
            ram_used_gb=6.2,
            ram_total_gb=16.0,
            ram_percent=38.8,
            active_workers=2,
            max_workers=4,
            gpu_info=GpuInfo(available=True, status_text="GPU: Auto-Accelerated (NVIDIA NVENC)"),
            ffmpeg_info=FFmpegInfo(is_ok=True, path=r"F:\discord bot\bin\ffmpeg.exe", version="6.1"),
        )

        table = render_resource_monitor_table(snapshot, safe_ascii=False)
        self.assertIsInstance(table, Table)

        panel = render_resource_monitor_panel(snapshot, safe_ascii=False)
        self.assertIsInstance(panel, Panel)

    def test_telemetry_table_rows_and_labels_consistency(self):
        """
        Verify the 5 required telemetry rows:
        - Row 1: CPU core usage with sparkline and percentage.
        - Row 2: RAM usage with progress bar and GB ratio.
        - Row 3: Active workers / concurrency bar.
        - Row 4: GPU acceleration status.
        - Row 5: FFmpeg binary status.
        Ensure consistent column widths and no_wrap on both columns.
        """
        snapshot = ResourceTelemetrySnapshot(
            cpu_percent_overall=42.5,
            cpu_per_core=[10.0, 30.0, 60.0, 90.0],
            ram_used_gb=8.5,
            ram_total_gb=32.0,
            ram_percent=26.5,
            active_workers=3,
            max_workers=6,
            gpu_info=GpuInfo(available=True, engine="NVIDIA NVENC", status_text="GPU: Auto-Accelerated (NVIDIA NVENC)"),
            ffmpeg_info=FFmpegInfo(is_ok=True, path="C:\\ffmpeg\\ffmpeg.exe", version="6.1.1"),
        )

        table_wide = render_resource_monitor_table(snapshot, compact=False, width=80)
        self.assertEqual(len(table_wide.rows), 5)

        # Check column properties
        col_metric, col_value = table_wide.columns
        self.assertTrue(col_metric.no_wrap)
        self.assertEqual(col_metric.width, 11)
        self.assertTrue(col_value.no_wrap)
        self.assertEqual(col_value.overflow, "ellipsis")

        # Check compact mode column properties
        table_compact = render_resource_monitor_table(snapshot, compact=True, width=35)
        self.assertEqual(len(table_compact.rows), 5)
        c_metric, c_value = table_compact.columns
        self.assertTrue(c_metric.no_wrap)
        self.assertEqual(c_metric.width, 8)
        self.assertTrue(c_value.no_wrap)
        self.assertEqual(c_value.overflow, "ellipsis")

    def test_narrow_panel_rendering(self):
        """Verify rendering at narrow widths (30, 35, 40) executes without errors."""
        snapshot = ResourceTelemetrySnapshot(
            cpu_percent_overall=35.0,
            cpu_per_core=[20.0, 40.0, 60.0, 80.0, 30.0, 50.0, 70.0, 90.0],
            ram_used_gb=12.0,
            ram_total_gb=32.0,
            ram_percent=37.5,
            active_workers=2,
            max_workers=4,
            gpu_info=GpuInfo(available=True, engine="NVIDIA NVENC", status_text="NVIDIA NVENC"),
            ffmpeg_info=FFmpegInfo(is_ok=True, path="ffmpeg.exe", version="6.1"),
        )

        for w in [30, 35, 40]:
            panel = render_resource_monitor_panel(snapshot, width=w)
            self.assertIsInstance(panel, Panel)
            # Narrow panels should use compact title
            if w < 40:
                self.assertIn("SYSTEM TELEMETRY", str(panel.title))

    def test_panel_border_styling_slate_dark_theme(self):
        """Verify panel border matches global dark slate theme (#242938)."""
        snapshot = ResourceTelemetrySnapshot()
        panel = render_resource_monitor_panel(snapshot)
        self.assertEqual(panel.border_style, "#242938")


class TestTextualResourceMonitorWidget(unittest.TestCase):
    """Tests for Textual lifecycle, background timer, and widget methods."""

    def test_headless_textual_app(self):
        class TelemetryApp(App):
            def compose(self) -> ComposeResult:
                yield ResourceMonitorWidget(id="monitor_widget")

        async def run_async():
            app = TelemetryApp()
            async with app.run_test() as pilot:
                widget = app.query_one("#monitor_widget", ResourceMonitorWidget)
                self.assertIsNotNone(widget)
                self.assertIsNotNone(widget._timer)
                
                # Check initial refresh
                self.assertGreaterEqual(widget.cpu_percent, 0.0)
                self.assertGreaterEqual(widget.ram_percent, 0.0)

                # Test manual worker adjustment
                widget.set_workers(active=3, max_workers=8)
                self.assertEqual(widget.active_workers, 3)
                self.assertEqual(widget.max_workers, 8)

                # Test get_snapshot
                snap = widget.get_snapshot()
                self.assertIsInstance(snap, ResourceTelemetrySnapshot)
                self.assertEqual(snap.active_workers, 3)
                self.assertEqual(snap.max_workers, 8)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
