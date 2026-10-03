"""
test_processing.py - Comprehensive Unit & Integration Tests for Processing Subsystem.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg, find_ffprobe
from processing.progress_tracker import (
    FFmpegProgressTracker,
    ProgressSnapshot,
    format_seconds_to_eta,
    parse_time_string_to_seconds,
)
from processing.queue_manager import (
    ConversionTask,
    QueueManager,
    QueueStats,
    TaskStatus,
)
from processing.reporter import (
    BatchReport,
    TaskReportItem,
    create_report_from_tasks,
    format_bytes_human,
    format_duration_human,
)
from processing.verifier import (
    IntegrityVerifier,
    VerificationResult,
    is_codec_compatible,
    normalize_format_name,
    verify_conversion,
)


class TestProgressTracker(unittest.TestCase):
    """Unit tests for FFmpegProgressTracker and time utilities."""

    def test_parse_time_string_to_seconds(self) -> None:
        self.assertAlmostEqual(parse_time_string_to_seconds("00:00:10.500000"), 10.5)
        self.assertAlmostEqual(parse_time_string_to_seconds("01:02:03.000000"), 3723.0)
        self.assertAlmostEqual(parse_time_string_to_seconds("05:30"), 330.0)
        self.assertIsNone(parse_time_string_to_seconds("N/A"))
        self.assertIsNone(parse_time_string_to_seconds(""))

    def test_format_seconds_to_eta(self) -> None:
        self.assertEqual(format_seconds_to_eta(45), "00:45")
        self.assertEqual(format_seconds_to_eta(125), "02:05")
        self.assertEqual(format_seconds_to_eta(3665), "01:01:05")
        self.assertEqual(format_seconds_to_eta(None), "--:--")
        self.assertEqual(format_seconds_to_eta(-5), "--:--")

    def test_tracker_line_parsing(self) -> None:
        tracker = FFmpegProgressTracker(total_duration=100.0)

        lines = [
            "frame=0",
            "fps=0.00",
            "stream_0_0_q=-1.0",
            "bitrate= 192.0kbits/s",
            "total_size=1048576",
            "out_time_us=50000000",
            "out_time_ms=50000000",
            "out_time=00:00:50.000000",
            "dup_frames=0",
            "drop_frames=0",
            "speed=10.0x",
            "progress=continue",
        ]

        snapshot = None
        for line in lines:
            res = tracker.feed_line(line)
            if res:
                snapshot = res

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertAlmostEqual(snapshot.percent, 50.0, places=1)
        self.assertAlmostEqual(snapshot.out_time_seconds, 50.0, places=1)
        self.assertAlmostEqual(snapshot.speed_factor, 10.0, places=1)
        self.assertEqual(snapshot.speed_str, "10.0x")
        self.assertEqual(snapshot.total_size_bytes, 1048576)
        self.assertEqual(snapshot.bitrate_str, "192.0kbits/s")
        self.assertFalse(snapshot.is_completed)
        self.assertIsNotNone(snapshot.eta_seconds)
        self.assertEqual(snapshot.eta_str, "00:05")

    def test_tracker_completion(self) -> None:
        tracker = FFmpegProgressTracker(total_duration=60.0)
        end_lines = [
            "out_time_us=60000000",
            "speed=20.0x",
            "progress=end",
        ]
        snapshot = None
        for line in end_lines:
            res = tracker.feed_line(line)
            if res:
                snapshot = res

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.percent, 100.0)
        self.assertTrue(snapshot.is_completed)
        self.assertEqual(snapshot.eta_str, "00:00")

    def test_tracker_feed_chunk(self) -> None:
        tracker = FFmpegProgressTracker(total_duration=10.0)
        chunk = "out_time_us=5000000\nspeed=2.0x\nprogress=continue\nout_time_us=10000000\nspeed=2.5x\nprogress=end\n"
        snapshots = tracker.feed_chunk(chunk)
        self.assertEqual(len(snapshots), 2)
        self.assertAlmostEqual(snapshots[0].percent, 50.0)
        self.assertEqual(snapshots[1].percent, 100.0)


class TestVerifier(unittest.TestCase):
    """Unit tests for post-conversion verifier and codec matching."""

    def test_codec_matching(self) -> None:
        self.assertTrue(is_codec_compatible("mp3", "mp3"))
        self.assertTrue(is_codec_compatible("mp3", "mp3float"))
        self.assertTrue(is_codec_compatible("aac", "aac"))
        self.assertTrue(is_codec_compatible("m4a", "aac"))
        self.assertTrue(is_codec_compatible("m4a", "alac"))
        self.assertTrue(is_codec_compatible("flac", "flac"))
        self.assertTrue(is_codec_compatible("wav", "pcm_s16le"))
        self.assertTrue(is_codec_compatible("ogg", "vorbis"))
        self.assertTrue(is_codec_compatible("ogg", "opus"))

        self.assertFalse(is_codec_compatible("mp3", "flac"))
        self.assertFalse(is_codec_compatible("wav", "mp3"))

    def test_nonexistent_file(self) -> None:
        res = verify_conversion(Path("non_existent_audio_file.mp3"), target_format="mp3")
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status_label, "FAIL")
        self.assertTrue(any("does not exist" in e for e in res.errors))

    def test_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf_path = Path(tf.name)
        try:
            res = verify_conversion(tf_path, target_format="mp3")
            self.assertFalse(res.is_valid)
            self.assertTrue(any("empty" in e for e in res.errors))
        finally:
            if tf_path.exists():
                tf_path.unlink()


class TestReporter(unittest.TestCase):
    """Unit tests for reporter, analytics, JSON/CSV exports, and table formatting."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="transcode_rep_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_helpers(self) -> None:
        self.assertEqual(format_bytes_human(500), "500 B")
        self.assertEqual(format_bytes_human(1048576), "1.0 MB")
        self.assertEqual(format_duration_human(75), "01:15")

    def test_batch_report_export(self) -> None:
        # Create mock tasks
        task1 = ConversionTask(
            task_id="task_1",
            source_file=Path("C:/media/video1.mp4"),
            target_format="mp3",
            output_file=Path("C:/media/video1.mp3"),
            status=TaskStatus.COMPLETED,
            duration_seconds=5.2,
            input_size_bytes=10_000_000,
            output_size_bytes=1_000_000,
            speed="12.0x",
        )
        task2 = ConversionTask(
            task_id="task_2",
            source_file=Path("C:/media/corrupt.mp4"),
            target_format="flac",
            output_file=Path("C:/media/corrupt.flac"),
            status=TaskStatus.FAILED,
            error="Corrupted bitstream",
            duration_seconds=1.1,
            input_size_bytes=5_000_000,
            output_size_bytes=0,
        )

        report = create_report_from_tasks([task1, task2], wall_time_seconds=6.3, report_id="test_run_01")
        self.assertEqual(report.total_tasks, 2)
        self.assertEqual(report.successful_tasks, 1)
        self.assertEqual(report.failed_tasks, 1)
        self.assertEqual(report.success_rate_percent, 50.0)
        self.assertEqual(report.total_input_bytes, 15_000_000)
        self.assertEqual(report.total_output_bytes, 1_000_000)
        self.assertEqual(report.total_space_saved_bytes, 14_000_000)

        # JSON Export
        json_path = report.export_json(output_dir=self.temp_dir)
        self.assertTrue(json_path.exists())
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["report_id"], "test_run_01")
        self.assertEqual(len(data["items"]), 2)

        # CSV Export
        csv_path = report.export_csv(output_dir=self.temp_dir)
        self.assertTrue(csv_path.exists())
        with open(csv_path, "r", encoding="utf-8") as f:
            csv_content = f.read()
        self.assertIn("task_1", csv_content)
        self.assertIn("video1.mp4", csv_content)
        self.assertIn("Corrupted bitstream", csv_content)

        # Table formatting
        table_str = report.format_summary_table()
        self.assertIn("ADVANCED VIDEO-TO-AUDIO TRANSCODER BATCH REPORT", table_str)
        self.assertIn("50.0%", table_str)
        self.assertIn("video1.mp4", table_str)


class TestQueueManager(unittest.TestCase):
    """Unit and integration tests for QueueManager controls and callbacks."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="queue_test_"))
        self.qm = QueueManager(
            max_workers=2,
            default_output_dir=self.temp_dir,
        )

    def tearDown(self) -> None:
        self.qm.stop(cancel_running=True)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_queue_add_and_stats(self) -> None:
        t1 = self.qm.add_task(Path("test1.mp4"), target_format="mp3")
        t2 = self.qm.add_task(Path("test2.mkv"), target_format="flac")

        stats = self.qm.get_stats()
        self.assertEqual(stats.total_tasks, 2)
        self.assertEqual(t1.target_format, "mp3")
        self.assertEqual(t2.target_format, "flac")

    def test_pause_resume_queue(self) -> None:
        self.assertFalse(self.qm.get_stats().is_paused)
        self.qm.pause_queue()
        self.assertTrue(self.qm.get_stats().is_paused)
        self.qm.resume_queue()
        self.assertFalse(self.qm.get_stats().is_paused)

    def test_cancel_task(self) -> None:
        t = self.qm.add_task(Path("dummy.mp4"), target_format="mp3")
        cancelled = self.qm.cancel_task(t.task_id)
        self.assertTrue(cancelled)
        self.assertEqual(t.status, TaskStatus.CANCELLED)

    def test_clear_queue(self) -> None:
        self.qm.pause_queue()
        t1 = self.qm.add_task(Path("clear1.mp4"))
        t2 = self.qm.add_task(Path("clear2.mp4"))
        cancelled = self.qm.clear_queue()
        self.assertIn(t1, cancelled)
        self.assertIn(t2, cancelled)
        self.assertEqual(t1.status, TaskStatus.CANCELLED)
        self.assertEqual(t2.status, TaskStatus.CANCELLED)


class TestLiveFFmpegConversion(unittest.TestCase):
    """Live end-to-end integration test creating a synthetic audio/video file and transcoding."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = find_ffmpeg()
        cls.ffprobe = find_ffprobe()
        cls.test_dir = Path(tempfile.mkdtemp(prefix="live_transcode_"))

        # Generate a 2.0-second synthetic test video with audio (sine wave 440Hz)
        cls.sample_video = cls.test_dir / "synthetic_test.mp4"
        cmd = [
            str(cls.ffmpeg),
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(cls.sample_video),
        ]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_end_to_end_transcode(self) -> None:
        events: list[str] = []
        progress_snapshots: list[ProgressSnapshot] = []

        output_mp3 = self.test_dir / "output.mp3"

        def on_started(task: ConversionTask) -> None:
            events.append("started")

        def on_progress(task: ConversionTask, snap: ProgressSnapshot) -> None:
            progress_snapshots.append(snap)

        def on_completed(task: ConversionTask, ver: VerificationResult) -> None:
            events.append("completed")

        def on_queue_done(stats: QueueStats) -> None:
            events.append("queue_done")

        qm = QueueManager(
            max_workers=1,
            default_output_dir=self.test_dir,
            on_task_started=on_started,
            on_task_progress=on_progress,
            on_task_completed=on_completed,
            on_queue_completed=on_queue_done,
        )

        try:
            task = qm.add_task(
                source_file=self.sample_video,
                target_format="mp3",
                output_file=output_mp3,
                options={"bitrate": "192k"},
            )

            done = qm.wait_completion(timeout=20.0)
            self.assertTrue(done, "Queue did not complete in time")

            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertIn("started", events)
            self.assertIn("completed", events)
            self.assertIn("queue_done", events)
            self.assertTrue(output_mp3.exists())
            self.assertGreater(output_mp3.stat().st_size, 0)

            # Verification assertions
            ver = task.verification_result
            self.assertIsNotNone(ver)
            assert ver is not None
            self.assertTrue(ver.is_valid)
            self.assertEqual(ver.codec, "mp3")
            self.assertAlmostEqual(ver.duration, 2.0, delta=0.5)

            # Batch report assertions
            report = create_report_from_tasks([task], wall_time_seconds=task.duration_seconds)
            self.assertEqual(report.total_tasks, 1)
            self.assertEqual(report.successful_tasks, 1)
            self.assertEqual(report.success_rate_percent, 100.0)
            json_file = report.export_json(output_dir=self.test_dir)
            self.assertTrue(json_file.exists())

        finally:
            qm.stop()


if __name__ == "__main__":
    unittest.main()
