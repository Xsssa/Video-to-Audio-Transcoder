"""
Unit and integration tests for HistoryModalScreen and batch history inspector.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Label, Static

from core.probe import AudioStreamInfo, MediaProbeResult
from processing.queue_manager import ConversionTask, TaskStatus
from processing.verifier import VerificationResult
from ui.tui_screens.history_screen import (
    HistoryModalScreen,
    HistoryTabPane,
    HistoryView,
    extract_compression_details,
    extract_loudness_info,
    extract_task_format_specs,
    extract_task_traceback,
    format_command_line,
    format_compression_ratio,
    reconstruct_ffmpeg_command,
)


def create_dummy_completed_task() -> ConversionTask:
    task = ConversionTask(
        task_id="task_completed_001",
        source_file=Path("test_video.mp4"),
        target_format="mp3",
        output_file=Path("test_audio.mp3"),
        options={
            "bitrate": "320k",
            "sample_rate": 48000,
            "channels": 2,
            "ebu_r128": True,
            "target_i": -16.0,
            "target_tp": -1.5,
            "target_lra": 11.0,
        },
        status=TaskStatus.COMPLETED,
        progress=100.0,
        speed="4.5x",
        duration_seconds=5.2,
        input_size_bytes=100_000_000,  # 100 MB
        output_size_bytes=12_000_000,  # 12 MB (-88%)
        probe_result=MediaProbeResult(
            file_path=Path("test_video.mp4"),
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            format_long_name="QuickTime / MOV",
            duration=120.0,
            size_bytes=100_000_000,
            bit_rate=6666666,
            audio_streams=[
                AudioStreamInfo(
                    index=1,
                    codec_name="aac",
                    codec_long_name="AAC (Advanced Audio Coding)",
                    channels=2,
                    channel_layout="stereo",
                    sample_rate=48000,
                    bit_rate=192000,
                    duration=120.0,
                )
            ],
            video_streams=[],
            has_video=True,
            has_cover_art=False,
            cover_art_stream_index=None,
        ),
        verification_result=VerificationResult(
            is_valid=True,
            file_path=Path("test_audio.mp3"),
            size_bytes=12_000_000,
            duration=120.0,
            codec="mp3",
            bitrate=320000,
            sample_rate=48000,
            channels=2,
            warnings=[],
            errors=[],
        ),
        ffmpeg_cmd=[
            "ffmpeg", "-y", "-nostdin", "-progress", "pipe:1",
            "-i", "test_video.mp4", "-map", "0:a:0",
            "-c:a", "libmp3lame", "-b:a", "320k",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "test_audio.mp3"
        ],
    )
    return task


def create_dummy_failed_task() -> ConversionTask:
    task = ConversionTask(
        task_id="task_failed_002",
        source_file=Path("corrupt_video.avi"),
        target_format="flac",
        output_file=Path("corrupt_audio.flac"),
        options={"bitrate": "1411k"},
        status=TaskStatus.FAILED,
        progress=15.0,
        speed="0.8x",
        duration_seconds=1.1,
        input_size_bytes=50_000_000,
        output_size_bytes=0,
        error="FFmpeg exit code 1: Invalid data found when processing input",
        traceback=(
            "Traceback (most recent call last):\n"
            "  File 'queue_manager.py', line 601, in _run_task\n"
            "    raise RuntimeError(err_msg)\n"
            "RuntimeError: FFmpeg exit code 1: Invalid data found when processing input"
        ),
        ffmpeg_cmd=[
            "ffmpeg", "-y", "-nostdin", "-progress", "pipe:1",
            "-i", "corrupt_video.avi", "-map", "0:a:0",
            "-c:a", "flac", "corrupt_audio.flac"
        ],
    )
    return task


# =============================================================================
# Unit Tests for Helper Functions
# =============================================================================

def test_format_compression_ratio():
    ratio_str, val = format_compression_ratio(100_000_000, 12_000_000)
    assert ratio_str == "-88.0%"
    assert pytest.approx(val, 0.1) == -88.0

    ratio_str, val = format_compression_ratio(0, 100)
    assert ratio_str == "--"


def test_reconstruct_ffmpeg_command_and_formatting():
    task = create_dummy_completed_task()
    cmd = reconstruct_ffmpeg_command(task)
    assert "ffmpeg" in cmd
    assert "test_video.mp4" in cmd
    assert "test_audio.mp3" in cmd

    cmd_line = format_command_line(cmd)
    assert "ffmpeg" in cmd_line
    assert "test_video.mp4" in cmd_line


def test_extract_loudness_info():
    task = create_dummy_completed_task()
    sum_str, det_str = extract_loudness_info(task)
    assert "-16.0 LUFS" in sum_str
    assert "EBU R128" in det_str

    task_plain = ConversionTask(
        task_id="t3",
        source_file=Path("clip.mp4"),
        target_format="wav",
    )
    sum_str2, det_str2 = extract_loudness_info(task_plain)
    assert sum_str2 == "N/A"


def test_extract_compression_details():
    task = create_dummy_completed_task()
    info = extract_compression_details(task)
    assert info["ratio_str"] == "-88.0%"
    assert info["input_human"] == "95.4 MB" or "MB" in info["input_human"]
    assert info["space_saved_bytes"] == 88_000_000


def test_extract_task_format_specs():
    task = create_dummy_completed_task()
    specs = extract_task_format_specs(task)
    assert "MOV" in specs["source_format"]
    assert specs["target_format"] == "MP3"
    assert "320 kbps" in specs["bitrate"]
    assert "02:00" in specs["duration_str"]


def test_extract_task_traceback():
    task = create_dummy_failed_task()
    tb = extract_task_traceback(task)
    assert "Traceback (most recent call last)" in tb
    assert "RuntimeError" in tb


# =============================================================================
# Interactive Textual App Tests
# =============================================================================

class MockHistoryHostApp(App):
    """Host App for testing HistoryModalScreen."""
    def __init__(self, tasks=None):
        super().__init__()
        self.test_tasks = tasks or []

    def on_mount(self):
        self.push_screen(HistoryModalScreen(tasks=self.test_tasks))


@pytest.mark.asyncio
async def test_history_modal_screen_renders_completed_task():
    t_comp = create_dummy_completed_task()
    app = MockHistoryHostApp(tasks=[t_comp])

    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, HistoryModalScreen)

        # Verify summary stats
        stat_completed = screen.query_one("#stat-completed", Label)
        assert "1" in str(stat_completed.render())

        # Verify data table has row
        table = screen.query_one("#history-data-table", DataTable)
        assert table.row_count == 1

        # Verify inspector content contains completed specs
        inspector_content = screen.query_one("#inspector-content", Static)
        rendered_text = str(inspector_content.render())

        assert "COMPLETED" in rendered_text
        assert "MP3" in rendered_text
        assert "320 kbps" in rendered_text
        assert "-88.0%" in rendered_text
        assert "-16.0 LUFS" in rendered_text


@pytest.mark.asyncio
async def test_history_modal_screen_renders_failed_task_and_cmd():
    t_fail = create_dummy_failed_task()
    app = MockHistoryHostApp(tasks=[t_fail])

    async with app.run_test() as pilot:
        screen = app.screen
        inspector_content = screen.query_one("#inspector-content", Static)
        rendered_text = str(inspector_content.render())

        assert "FAILED" in rendered_text
        assert "corrupt_video.avi" in rendered_text
        # Exact FFmpeg command line
        assert "ffmpeg" in rendered_text
        assert "corrupt_audio.flac" in rendered_text
        # Traceback
        assert "Traceback" in rendered_text
        assert "RuntimeError" in rendered_text


@pytest.mark.asyncio
async def test_history_modal_screen_export_reports(tmp_path):
    t_comp = create_dummy_completed_task()
    t_fail = create_dummy_failed_task()
    app = MockHistoryHostApp(tasks=[t_comp, t_fail])

    async with app.run_test() as pilot:
        screen = app.screen
        view = screen.query_one("#history-view-inner", HistoryView)

        # Test JSON export
        json_path = view.export_report("json")
        assert json_path is not None
        assert json_path.exists()
        assert json_path.suffix == ".json"

        # Test CSV export
        csv_path = view.export_report("csv")
        assert csv_path is not None
        assert csv_path.exists()
        assert csv_path.suffix == ".csv"

        # Test TXT export
        txt_path = view.export_report("txt")
        assert txt_path is not None
        assert txt_path.exists()
        assert txt_path.suffix == ".txt"


@pytest.mark.asyncio
async def test_history_modal_screen_dismiss_with_close_button():
    t_comp = create_dummy_completed_task()
    app = MockHistoryHostApp(tasks=[t_comp])

    async with app.run_test() as pilot:
        assert isinstance(app.screen, HistoryModalScreen)
        btn_close = app.screen.query_one("#btn-close-modal", Button)
        await pilot.click(btn_close)
        await pilot.pause()

        # Modal should be dismissed
        assert not isinstance(app.screen, HistoryModalScreen)


@pytest.mark.asyncio
async def test_history_modal_screen_dismiss_with_escape_key():
    t_comp = create_dummy_completed_task()
    app = MockHistoryHostApp(tasks=[t_comp])

    async with app.run_test() as pilot:
        assert isinstance(app.screen, HistoryModalScreen)
        await pilot.press("escape")
        await pilot.pause()

        # Modal should be dismissed
        assert not isinstance(app.screen, HistoryModalScreen)


@pytest.mark.asyncio
async def test_history_modal_screen_filtering():
    t_comp = create_dummy_completed_task()
    t_fail = create_dummy_failed_task()
    app = MockHistoryHostApp(tasks=[t_comp, t_fail])

    async with app.run_test() as pilot:
        screen = app.screen
        table = screen.query_one("#history-data-table", DataTable)
        assert table.row_count == 2

        # Filter: Completed only
        btn_completed = screen.query_one("#btn-filter-completed", Button)
        await pilot.click(btn_completed)
        await pilot.pause()
        assert table.row_count == 1

        # Filter: Failed only
        btn_failed = screen.query_one("#btn-filter-failed", Button)
        await pilot.click(btn_failed)
        await pilot.pause()
        assert table.row_count == 1

        # Filter: All
        btn_all = screen.query_one("#btn-filter-all", Button)
        await pilot.click(btn_all)
        await pilot.pause()
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_history_modal_screen_empty_state():
    app = MockHistoryHostApp(tasks=[])

    async with app.run_test() as pilot:
        screen = app.screen
        table = screen.query_one("#history-data-table", DataTable)
        assert table.row_count == 0

        inspector = screen.query_one("#inspector-content", Static)
        assert "No all tasks" in str(inspector.render()) or "No" in str(inspector.render())


@pytest.mark.asyncio
async def test_history_tab_pane():
    from textual.widgets import TabbedContent

    class TabApp(App):
        def compose(self) -> ComposeResult:
            t = create_dummy_completed_task()
            with TabbedContent():
                yield HistoryTabPane("History", id="tab-hist", tasks=[t])

    app = TabApp()
    async with app.run_test() as pilot:
        tab_pane = app.query_one("#tab-hist", HistoryTabPane)
        assert tab_pane is not None
        tab_pane.refresh_history()

