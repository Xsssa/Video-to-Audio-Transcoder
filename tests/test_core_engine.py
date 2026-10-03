"""
test_core_engine.py - Comprehensive Unit & Integration Tests for the Core Transcoding Engine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.audio_profiles import (
    PRESETS,
    AudioFormat,
    AudioProfile,
    BitrateMode,
    can_lossless_copy,
    get_profile,
)
from core.ffmpeg_finder import (
    _get_creation_flags,
    find_config_file,
    find_ffmpeg,
    find_ffprobe,
    get_ffmpeg_version,
    get_ffprobe_version,
    load_config,
    verify_ffmpeg_installation,
)
from core.metadata import (
    MediaMetadata,
    apply_metadata,
    build_ffmpeg_metadata_args,
    detect_image_mime,
    extract_metadata,
)
from core.normalizer import (
    LoudnessConfig,
    build_loudnorm_filter_string,
    build_normalizer_filter,
    measure_loudness,
)
from core.probe import AudioStreamInfo, MediaProbeResult, probe_media
from core.transcoder import (
    TranscodeOptions,
    TranscodeProgress,
    TranscodeResult,
    Transcoder,
)


class TestFFmpegFinder(unittest.TestCase):
    """Test locating and checking FFmpeg and FFprobe binaries."""

    def test_find_binaries(self) -> None:
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        self.assertTrue(ffmpeg.is_file(), f"ffmpeg not found at {ffmpeg}")
        self.assertTrue(ffprobe.is_file(), f"ffprobe not found at {ffprobe}")

    def test_get_versions(self) -> None:
        ffmpeg_ver = get_ffmpeg_version()
        ffprobe_ver = get_ffprobe_version()
        self.assertTrue(len(ffmpeg_ver) > 0)
        self.assertTrue(len(ffprobe_ver) > 0)

    def test_verify_installation(self) -> None:
        ver = verify_ffmpeg_installation()
        self.assertTrue(ver.is_valid)
        self.assertIsNotNone(ver.ffmpeg_path)
        self.assertIsNotNone(ver.ffprobe_path)
        self.assertIsNotNone(ver.ffmpeg_version)
        self.assertIsNotNone(ver.ffprobe_version)

    def test_load_config(self) -> None:
        cfg = load_config()
        self.assertIn("ffmpeg_path", cfg)
        self.assertIn("default_format", cfg)
        self.assertEqual(cfg.get("default_format"), "mp3")


class TestAudioProfiles(unittest.TestCase):
    """Test audio formats, profiles, and lossless copy matrix."""

    def test_format_parsing(self) -> None:
        self.assertEqual(AudioFormat.from_string("mp3"), AudioFormat.MP3)
        self.assertEqual(AudioFormat.from_string(".FLAC"), AudioFormat.FLAC)
        self.assertEqual(AudioFormat.from_string("wave"), AudioFormat.WAV)
        self.assertEqual(AudioFormat.from_string("alac"), AudioFormat.M4A)

    def test_get_profile_and_args(self) -> None:
        p_mp3 = get_profile("mp3", "320k")
        args_mp3 = p_mp3.to_ffmpeg_args()
        self.assertIn("-c:a", args_mp3)
        self.assertIn("libmp3lame", args_mp3)
        self.assertIn("-b:a", args_mp3)
        self.assertIn("320k", args_mp3)

        p_flac = get_profile("flac", "flac_16bit")
        args_flac = p_flac.to_ffmpeg_args()
        self.assertIn("flac", args_flac)
        self.assertIn("-compression_level", args_flac)

        p_opus = get_profile("opus", "opus_128k")
        args_opus = p_opus.to_ffmpeg_args()
        self.assertIn("libopus", args_opus)
        self.assertIn("-application", args_opus)

        p_wav = get_profile("wav", "wav_24bit")
        args_wav = p_wav.to_ffmpeg_args()
        self.assertIn("pcm_s24le", args_wav)

    def test_lossless_copy_matrix(self) -> None:
        self.assertTrue(can_lossless_copy("aac", "m4a"))
        self.assertTrue(can_lossless_copy("aac", "aac"))
        self.assertTrue(can_lossless_copy("mp3", "mp3"))
        self.assertTrue(can_lossless_copy("flac", "flac"))
        self.assertTrue(can_lossless_copy("opus", "opus"))
        self.assertTrue(can_lossless_copy("vorbis", "ogg"))
        self.assertTrue(can_lossless_copy("pcm_s16le", "wav"))
        self.assertTrue(can_lossless_copy("pcm_s16be", "aiff"))
        self.assertTrue(can_lossless_copy("wmav2", "wma"))
        self.assertTrue(can_lossless_copy("ac3", "ac3"))

        # Incompatible pairs
        self.assertFalse(can_lossless_copy("aac", "mp3"))
        self.assertFalse(can_lossless_copy("mp3", "flac"))
        self.assertFalse(can_lossless_copy("opus", "wav"))


class TestNormalizer(unittest.TestCase):
    """Test EBU R128 loudness normalization filter construction."""

    def test_filter_string_generation(self) -> None:
        cfg = LoudnessConfig(target_i=-16.0, target_tp=-1.5, target_lra=11.0)
        single_pass = build_loudnorm_filter_string(cfg, None)
        self.assertEqual(single_pass, "loudnorm=I=-16.0:TP=-1.5:LRA=11.0")


class TestCoreIntegration(unittest.TestCase):
    """End-to-end integration tests using synthetic media generation."""

    tmp_dir: tempfile.TemporaryDirectory
    work_dir: Path
    test_video: Path
    ffmpeg_bin: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.work_dir = Path(cls.tmp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()

        # Generate a 2-second synthetic video with 440Hz sine wave audio and an attached test frame
        cls.test_video = cls.work_dir / "sample_video.mp4"
        cmd = [
            str(cls.ffmpeg_bin),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-metadata",
            "title=SynthTitle",
            "-metadata",
            "artist=SynthArtist",
            "-metadata",
            "album=SynthAlbum",
            "-metadata",
            "date=2026",
            str(cls.test_video),
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"Failed to generate synthetic test video: {res.stderr.decode('utf-8', errors='replace')}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp_dir.cleanup()

    def test_probe_synthetic_video(self) -> None:
        probe = probe_media(self.test_video)
        self.assertTrue(probe.has_audio)
        self.assertTrue(probe.has_video)
        self.assertAlmostEqual(probe.duration, 2.0, delta=0.5)
        self.assertIsNotNone(probe.primary_audio_stream)
        if probe.primary_audio_stream:
            self.assertEqual(probe.primary_audio_stream.codec_name, "aac")
            self.assertEqual(probe.primary_audio_stream.sample_rate, 44100)

    def test_metadata_extraction(self) -> None:
        meta = extract_metadata(self.test_video, extract_cover=False)
        self.assertEqual(meta.title, "SynthTitle")
        self.assertEqual(meta.artist, "SynthArtist")
        self.assertEqual(meta.album, "SynthAlbum")
        self.assertEqual(meta.year, "2026")

    def test_transcode_to_mp3(self) -> None:
        out_mp3 = self.work_dir / "output.mp3"
        transcoder = Transcoder()
        progress_snapshots: list[TranscodeProgress] = []

        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_mp3,
            format="mp3",
            preset="mp3_192k",
            embed_metadata=True,
        )

        res = transcoder.transcode(opts, progress_callback=lambda p: progress_snapshots.append(p))
        self.assertTrue(res.success)
        self.assertFalse(res.cancelled)
        self.assertTrue(out_mp3.is_file())
        self.assertGreater(out_mp3.stat().st_size, 0)
        self.assertTrue(len(progress_snapshots) > 0)
        self.assertEqual(progress_snapshots[-1].status, "completed")

        # Probe output MP3 to verify
        probe_out = probe_media(out_mp3)
        self.assertTrue(probe_out.has_audio)
        self.assertEqual(probe_out.primary_audio_stream.codec_name, "mp3")

    def test_transcode_lossless_copy_aac_to_m4a(self) -> None:
        out_m4a = self.work_dir / "output_lossless.m4a"
        transcoder = Transcoder()

        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_m4a,
            format="m4a",
            lossless_copy_if_match=True,
        )

        res = transcoder.transcode(opts)
        self.assertTrue(res.success)
        self.assertTrue(res.was_lossless_copy)
        self.assertTrue(out_m4a.is_file())

        probe_out = probe_media(out_m4a)
        self.assertEqual(probe_out.primary_audio_stream.codec_name, "aac")

    def test_transcode_with_ebu_r128(self) -> None:
        out_loudnorm = self.work_dir / "output_loudnorm.mp3"
        transcoder = Transcoder()

        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_loudnorm,
            format="mp3",
            ebu_r128=True,
            two_pass_loudnorm=False,  # Single pass test for speed
        )

        res = transcoder.transcode(opts)
        self.assertTrue(res.success)
        self.assertFalse(res.was_lossless_copy)
        self.assertTrue(out_loudnorm.is_file())

    def test_two_pass_loudnorm_measurement(self) -> None:
        stats = measure_loudness(self.test_video)
        self.assertIsNotNone(stats)
        self.assertIsInstance(stats.input_i, float)
        self.assertIsInstance(stats.input_tp, float)
        filter_str = build_loudnorm_filter_string(LoudnessConfig(), stats)
        self.assertIn("measured_I=", filter_str)
        self.assertIn("linear=true", filter_str)

    def test_mutagen_cover_art_embedding(self) -> None:
        out_cover = self.work_dir / "output_cover.mp3"
        transcoder = Transcoder()
        res = transcoder.transcode(
            TranscodeOptions(
                input_path=self.test_video,
                output_path=out_cover,
                format="mp3",
            )
        )
        self.assertTrue(res.success)

        # 1x1 test PNG
        dummy_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        meta = MediaMetadata(
            title="CoverArtTrack",
            artist="CoverArtArtist",
            cover_bytes=dummy_png,
            cover_mime="image/png",
        )
        applied = apply_metadata(out_cover, meta)
        self.assertTrue(applied)

        from mutagen.mp3 import MP3
        audio = MP3(str(out_cover))
        self.assertEqual(str(audio.tags.get("TIT2")), "CoverArtTrack")
        has_apic = any(k.startswith("APIC") for k in audio.tags.keys())
        self.assertTrue(has_apic)

    def test_transcode_cancellation(self) -> None:
        cancel_evt = threading.Event()
        out_cancel = self.work_dir / "output_cancelled.mp3"
        transcoder = Transcoder()

        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_cancel,
            format="mp3",
            cancellation_event=cancel_evt,
        )

        # Trigger immediate cancellation
        cancel_evt.set()
        res = transcoder.transcode(opts)
        self.assertTrue(res.cancelled)
        self.assertFalse(res.success)
        self.assertFalse(out_cancel.exists())


if __name__ == "__main__":
    unittest.main()
