"""
Unit tests for UI, Input, and Interactive CLI TUI subsystems.
Tests input parsing, clipboard extraction, presets, audio filter chains,
media stream inspector, hotkey command dispatch, and interactive TUI lifecycle.
"""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add workspace root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.probe import MediaProbeResult
from processing.queue_manager import ConversionTask, TaskStatus
from ui.cli_tui import (
    CLI_PRESETS,
    ENTERPRISE_PRESETS,
    AudioFilterChain,
    CliPreset,
    CliTui,
    run_cli_tui,
)
from ui.dashboard_layout import (
    DashboardEvent,
    DashboardState,
    MediaInspectorData,
    SystemTelemetry,
    TerminalDashboardLayout,
    render_dashboard,
)
from ui.gui_app import EnterpriseConverterGui
from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    expand_path,
    filter_video_files,
    get_clipboard_files,
    is_supported_video,
    parse_input_paths,
    parse_tokens,
    strip_quotes,
)


class TestUiInputHandler(unittest.TestCase):
    def test_supported_extensions(self):
        self.assertIn(".mp4", SUPPORTED_VIDEO_EXTENSIONS)
        self.assertIn(".mkv", SUPPORTED_VIDEO_EXTENSIONS)
        self.assertIn(".mov", SUPPORTED_VIDEO_EXTENSIONS)
        self.assertIn(".webm", SUPPORTED_VIDEO_EXTENSIONS)
        self.assertIn(".ts", SUPPORTED_VIDEO_EXTENSIONS)
        self.assertIn(".avi", SUPPORTED_VIDEO_EXTENSIONS)

    def test_strip_quotes(self):
        self.assertEqual(strip_quotes('"F:\\test.mp4"'), "F:\\test.mp4")
        self.assertEqual(strip_quotes("'F:\\test.mp4'"), "F:\\test.mp4")
        self.assertEqual(strip_quotes("  'F:\\test.mp4'  "), "F:\\test.mp4")
        self.assertEqual(strip_quotes("F:\\test.mp4"), "F:\\test.mp4")

    def test_parse_tokens_multiple_files(self):
        raw = '"F:\\video 1.mp4" "F:\\video 2.mkv" F:\\sample.webm'
        tokens = parse_tokens(raw)
        self.assertEqual(len(tokens), 3)
        self.assertEqual(tokens[0], "F:\\video 1.mp4")
        self.assertEqual(tokens[1], "F:\\video 2.mkv")
        self.assertEqual(tokens[2], "F:\\sample.webm")

    def test_parse_tokens_powershell_ampersand(self):
        raw = '& "F:\\test video.mp4"'
        tokens = parse_tokens(raw)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0], "F:\\test video.mp4")

    def test_parse_tokens_multiline(self):
        raw = "F:\\clip1.mp4\r\nF:\\clip2.mkv\nF:\\clip3.avi"
        tokens = parse_tokens(raw)
        self.assertEqual(len(tokens), 3)

    @patch("pyperclip.paste")
    @patch("ui.input_handler._get_windows_hdrop_files")
    def test_get_clipboard_files_text_fallback(self, mock_hdrop, mock_paste):
        mock_hdrop.return_value = []
        tmp_video = Path(__file__).resolve().parent / "dummy.mp4"
        tmp_video.write_bytes(b"dummy")
        try:
            mock_paste.return_value = f'"{tmp_video}"'
            files = get_clipboard_files()
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].resolve(), tmp_video.resolve())
        finally:
            if tmp_video.exists():
                tmp_video.unlink()

    def test_cli_presets(self):
        self.assertIn(1, CLI_PRESETS)
        self.assertEqual(CLI_PRESETS[1].target_format, "mp3")
        self.assertEqual(CLI_PRESETS[2].target_format, "flac")
        self.assertEqual(CLI_PRESETS[3].target_format, "wav")
        self.assertEqual(CLI_PRESETS[4].target_format, "aac")
        self.assertEqual(CLI_PRESETS[5].target_format, "opus")
        self.assertEqual(CLI_PRESETS[6].target_format, "ogg")
        self.assertEqual(CLI_PRESETS[7].target_format, "m4a")

    def test_gui_app_lifecycle(self):
        app = EnterpriseConverterGui()
        app.withdraw()
        self.assertIsNotNone(app.queue_manager)
        options = app._get_current_options()
        self.assertIn("preserve_cover_art", options)
        app.destroy()


class TestAudioFilterChain(unittest.TestCase):
    def test_default_empty_chain(self):
        chain = AudioFilterChain()
        self.assertFalse(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "")
        self.assertEqual(chain.get_summary(), "Bypass (No Filters)")

    def test_volume_filter(self):
        chain = AudioFilterChain(volume_db=3.5)
        self.assertTrue(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "volume=+3.5dB")
        self.assertIn("Vol: +3.5dB", chain.get_summary())

        chain_neg = AudioFilterChain(volume_db=-2.0)
        self.assertEqual(chain_neg.to_ffmpeg_filter(), "volume=-2.0dB")

    def test_eq_modes(self):
        # Bass boost
        chain_bass = AudioFilterChain(eq_mode="bass_boost", bass_gain_db=5.0)
        self.assertIn("bass=g=5.0:f=100", chain_bass.to_ffmpeg_filter())
        self.assertIn("EQ: Bass Boost", chain_bass.get_summary())

        # Vocal clarity
        chain_vocal = AudioFilterChain(eq_mode="vocal_clarity", vocal_gain_db=4.0)
        self.assertIn("equalizer=f=3000:t=q:w=1.5:g=4.0", chain_vocal.to_ffmpeg_filter())
        self.assertIn("EQ: Vocal Clarity", chain_vocal.get_summary())

        # Both
        chain_both = AudioFilterChain(eq_mode="both")
        filter_str = chain_both.to_ffmpeg_filter()
        self.assertIn("bass=g=4.0:f=100", filter_str)
        self.assertIn("equalizer=f=3000:t=q:w=1.5:g=3.5", filter_str)

    def test_highpass_rumble_cut(self):
        chain = AudioFilterChain(highpass_hz=80)
        self.assertTrue(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "highpass=f=80")
        self.assertIn("Cut: <80Hz", chain.get_summary())

    def test_tempo_speed(self):
        chain = AudioFilterChain(tempo=1.25)
        self.assertTrue(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "atempo=1.25")
        self.assertIn("Speed: 1.25x", chain.get_summary())

    def test_loudness_target(self):
        chain = AudioFilterChain(loudness_target_lufs=-14.0)
        self.assertTrue(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "loudnorm=I=-14.0:TP=-1.0:LRA=11")
        self.assertIn("Loudness: -14LUFS", chain.get_summary())

    def test_combined_full_filter_chain(self):
        chain = AudioFilterChain(
            volume_db=2.0,
            eq_mode="both",
            bass_gain_db=4.5,
            vocal_gain_db=3.0,
            highpass_hz=80,
            tempo=1.1,
            loudness_target_lufs=-16.0,
        )
        filter_str = chain.to_ffmpeg_filter()
        self.assertTrue(chain.is_active())
        # Verify all filters are present in chain
        self.assertIn("highpass=f=80", filter_str)
        self.assertIn("bass=g=4.5:f=100", filter_str)
        self.assertIn("equalizer=f=3000:t=q:w=1.5:g=3.0", filter_str)
        self.assertIn("volume=+2.0dB", filter_str)
        self.assertIn("atempo=1.10", filter_str)
        self.assertIn("loudnorm=I=-16.0:TP=-1.0:LRA=11", filter_str)

    def test_filter_chain_reset(self):
        chain = AudioFilterChain(volume_db=3.0, highpass_hz=100)
        self.assertTrue(chain.is_active())
        chain.reset()
        self.assertFalse(chain.is_active())
        self.assertEqual(chain.to_ffmpeg_filter(), "")


class TestEnterprisePresets(unittest.TestCase):
    def test_named_presets_catalog(self):
        self.assertIn("studio_master", ENTERPRISE_PRESETS)
        self.assertIn("audiophile", ENTERPRISE_PRESETS)
        self.assertIn("podcast", ENTERPRISE_PRESETS)
        self.assertIn("streaming", ENTERPRISE_PRESETS)
        self.assertIn("flac_lossless", ENTERPRISE_PRESETS)
        self.assertIn("opus", ENTERPRISE_PRESETS)
        self.assertIn("mp3_hq", ENTERPRISE_PRESETS)

        # Studio Master: 24-bit 96kHz PCM WAV
        sm = ENTERPRISE_PRESETS["studio_master"]
        self.assertEqual(sm.target_format, "wav")
        self.assertEqual(sm.options.get("codec"), "pcm_s24le")
        self.assertEqual(sm.options.get("sample_rate"), 96000)

        # Audiophile: FLAC 24-bit Level 8
        aud = ENTERPRISE_PRESETS["audiophile"]
        self.assertEqual(aud.target_format, "flac")
        self.assertEqual(aud.options.get("compression_level"), 8)

        # Podcast: MP3 with speech filters
        pod = ENTERPRISE_PRESETS["podcast"]
        self.assertEqual(pod.target_format, "mp3")
        self.assertTrue(pod.options.get("ebu_r128"))
        self.assertIsNotNone(pod.default_filter)
        self.assertEqual(pod.default_filter.highpass_hz, 80)
        self.assertEqual(pod.default_filter.loudness_target_lufs, -16.0)

        # Streaming: AAC with -14LUFS
        stream = ENTERPRISE_PRESETS["streaming"]
        self.assertEqual(stream.target_format, "aac")
        self.assertIn("loudnorm=I=-14", stream.options.get("audio_filter", ""))


class TestCliTuiController(unittest.TestCase):
    def setUp(self):
        self.tui = CliTui()

    def tearDown(self):
        self.tui._stop_clipboard_watcher()

    def test_initial_state(self):
        state = self.tui.get_dashboard_state()
        self.assertIsNotNone(state)
        self.assertEqual(len(state.tasks), 0)
        self.assertFalse(state.is_transcoding)
        self.assertFalse(state.is_watching)
        self.assertIn("MP3", state.active_preset_name)

    def test_stage_paths_and_clear(self):
        sample_file = Path("sample_videos/video_sample.mp4").resolve()
        if sample_file.exists():
            added = self.tui._stage_paths([sample_file], verbose=False)
            self.assertEqual(len(added), 1)
            self.assertEqual(len(self.tui.staged_files), 1)

            # Dashboard state reflects staged files
            state = self.tui.get_dashboard_state()
            self.assertEqual(len(state.tasks), 1)
            self.assertEqual(state.tasks[0].source_file, sample_file)

            # Clear queue
            self.tui.handle_clear_queue()
            self.assertEqual(len(self.tui.staged_files), 0)
            state_cleared = self.tui.get_dashboard_state()
            self.assertEqual(len(state_cleared.tasks), 0)

    @patch("ui.cli_tui.get_clipboard_files")
    def test_handle_paste_clipboard(self, mock_clip):
        sample_file = Path("sample_videos/video_sample.mp4").resolve()
        mock_clip.return_value = [sample_file] if sample_file.exists() else []

        if sample_file.exists():
            added = self.tui.handle_paste_clipboard()
            self.assertEqual(len(added), 1)
            self.assertIn(sample_file, self.tui.staged_files)

    def test_handle_preset_menu(self):
        # Select Studio Master (Option 1)
        p1 = self.tui.handle_preset_menu("1")
        self.assertEqual(p1.target_format, "wav")
        self.assertEqual(self.tui.current_preset.target_format, "wav")

        # Select Audiophile Hi-Fi (Option 2)
        p2 = self.tui.handle_preset_menu("2")
        self.assertEqual(p2.target_format, "flac")

        # Select Podcast Enhancer (Option 3)
        p3 = self.tui.handle_preset_menu("3")
        self.assertEqual(p3.target_format, "mp3")
        self.assertTrue(self.tui.filter_chain.is_active())
        self.assertEqual(self.tui.filter_chain.highpass_hz, 80)

        # Select Streaming -14LUFS (Option 4)
        p4 = self.tui.handle_preset_menu("4")
        self.assertEqual(p4.target_format, "aac")
        self.assertEqual(self.tui.filter_chain.loudness_target_lufs, -14.0)

        # Select OPUS (Option 6)
        p6 = self.tui.handle_preset_menu("6")
        self.assertEqual(p6.target_format, "opus")

    def test_handle_set_output_dir(self):
        tmp_dir = Path("output/test_tui_output").resolve()
        self.tui.handle_set_output_dir(str(tmp_dir))
        self.assertEqual(self.tui.output_dir, tmp_dir)
        self.assertTrue(tmp_dir.exists())

        # Reset to source directory
        self.tui.handle_set_output_dir("")
        self.assertIsNone(self.tui.output_dir)

    def test_handle_toggle_clipboard_watcher(self):
        # Toggle ON
        is_active = self.tui.handle_toggle_clipboard_watcher()
        self.assertTrue(is_active)
        self.assertTrue(self.tui.clipboard_watcher_active)

        # Toggle OFF
        is_active = self.tui.handle_toggle_clipboard_watcher()
        self.assertFalse(is_active)
        self.assertFalse(self.tui.clipboard_watcher_active)

    def test_handle_inspect_media(self):
        sample_file = Path("sample_videos/video_sample.mp4").resolve()
        if sample_file.exists():
            self.tui._stage_paths([sample_file], verbose=False)
            probe = self.tui.handle_inspect_media(file_idx=0)
            self.assertIsNotNone(probe)
            self.assertTrue(isinstance(probe, MediaProbeResult))
            self.assertIsNotNone(self.tui.inspector_data)
            self.assertEqual(self.tui.inspector_data.filename, sample_file.name)

    def test_handle_report_modal_export(self):
        # Create a mock completed task
        sample_file = Path("sample_videos/video_sample.mp4").resolve()
        mock_task = ConversionTask(
            task_id="t_test",
            source_file=sample_file,
            target_format="mp3",
            output_file=Path("output/video_sample.mp3").resolve(),
            status=TaskStatus.COMPLETED,
            duration_seconds=2.5,
            output_size_bytes=1024 * 1024,
            speed="3.2x",
        )
        self.tui.session_tasks.append(mock_task)

        # Export JSON
        out_json = self.tui.handle_report_modal("json")
        self.assertIsNotNone(out_json)
        self.assertTrue(out_json.exists())
        self.assertTrue(out_json.stat().st_size > 0)
        try:
            out_json.unlink()
        except OSError:
            pass

        # Export CSV
        out_csv = self.tui.handle_report_modal("csv")
        self.assertIsNotNone(out_csv)
        self.assertTrue(out_csv.exists())
        try:
            out_csv.unlink()
        except OSError:
            pass

    def test_dispatch_command(self):
        self.assertFalse(self.tui.dispatch_command("status"))
        self.assertFalse(self.tui.dispatch_command("help"))
        self.assertFalse(self.tui.dispatch_command("c"))
        self.assertTrue(self.tui.dispatch_command("q"))

    def test_single_iteration_run(self):
        # Non-blocking single pass for tests/pipes
        self.tui.run(single_iteration=True)
        # Should complete cleanly without hanging


if __name__ == "__main__":
    unittest.main()
