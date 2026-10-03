"""
test_audio_filters.py - Comprehensive Unit & Integration Tests for Audio DSP Filters,
ITU-R BS.775 Downmixing, Enterprise Audio Presets, and Multi-track Audio Discovery.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.audio_filters import (
    EQ_PRESETS,
    AudioFilterConfig,
    build_audio_filter_chain,
    build_filter_string,
    generate_itur_bs775_matrix,
    get_itur_bs775_downmix_filter,
)
from core.audio_profiles import (
    PRESETS,
    AudioFormat,
    AudioProfile,
    BitrateMode,
    can_lossless_copy,
    get_profile,
)
from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg
from core.probe import AudioStreamInfo, MediaProbeResult, probe_media
from core.transcoder import TranscodeOptions, Transcoder


class TestAudioFilterConfig(unittest.TestCase):
    """Test AudioFilterConfig dataclass initialization and default behaviors."""

    def test_default_initialization(self) -> None:
        cfg = AudioFilterConfig()
        self.assertIsNone(cfg.volume_db)
        self.assertIsNone(cfg.eq_preset)
        self.assertIsNone(cfg.highpass_hz)
        self.assertIsNone(cfg.lowpass_hz)
        self.assertIsNone(cfg.tempo)
        self.assertIsNone(cfg.pitch)
        self.assertFalse(cfg.dynamic_range_compression)
        self.assertFalse(cfg.limiter)
        self.assertIsNone(cfg.loudnorm_target)
        self.assertIsNone(cfg.downmix)
        # Empty config yields empty chain
        self.assertEqual(build_audio_filter_chain(cfg), [])
        self.assertEqual(build_filter_string(cfg), "")
        self.assertEqual(cfg.to_ffmpeg_args(), [])
        self.assertEqual(cfg.to_filter_string(), "")

    def test_custom_initialization(self) -> None:
        cfg = AudioFilterConfig(
            volume_db=3.5,
            eq_preset="podcast_enhancer",
            highpass_hz=80,
            lowpass_hz=16000,
            tempo=1.2,
            pitch=1.05,
            dynamic_range_compression=True,
            limiter=True,
            loudnorm_target="-16",
            downmix="5.1",
        )
        self.assertEqual(cfg.volume_db, 3.5)
        self.assertEqual(cfg.eq_preset, "podcast_enhancer")
        self.assertEqual(cfg.highpass_hz, 80)
        self.assertEqual(cfg.lowpass_hz, 16000)
        self.assertEqual(cfg.tempo, 1.2)
        self.assertEqual(cfg.pitch, 1.05)
        self.assertTrue(cfg.dynamic_range_compression)
        self.assertTrue(cfg.limiter)
        self.assertEqual(cfg.loudnorm_target, "-16")
        self.assertEqual(cfg.downmix, "5.1")


class TestEqualizerPresets(unittest.TestCase):
    """Verify standard EQ curves and broadcast profiles."""

    def test_eq_presets_presence(self) -> None:
        expected_presets = [
            "bass_boost",
            "vocal_clarity",
            "podcast_enhancer",
            "treble_boost",
            "flat",
        ]
        for p in expected_presets:
            self.assertIn(p, EQ_PRESETS, f"Preset '{p}' must be in EQ_PRESETS")

    def test_bass_boost_spec(self) -> None:
        filt = EQ_PRESETS["bass_boost"]
        self.assertIn("lowshelf", filt)
        self.assertIn("100", filt)
        self.assertIn("6", filt)

    def test_vocal_clarity_spec(self) -> None:
        filt = EQ_PRESETS["vocal_clarity"]
        self.assertIn("equalizer", filt)
        self.assertIn("3000", filt)
        self.assertIn("3.5", filt)
        self.assertIn("highpass", filt)
        self.assertIn("120", filt)

    def test_podcast_enhancer_spec(self) -> None:
        filt = EQ_PRESETS["podcast_enhancer"]
        self.assertIn("highpass", filt)
        self.assertIn("80", filt)
        self.assertIn("acompressor", filt)
        self.assertTrue("highshelf" in filt or "equalizer" in filt)

    def test_treble_boost_spec(self) -> None:
        filt = EQ_PRESETS["treble_boost"]
        self.assertIn("highshelf", filt)
        self.assertIn("8000", filt)
        self.assertIn("4", filt)

    def test_flat_spec(self) -> None:
        filt = EQ_PRESETS["flat"]
        self.assertEqual(filt, "anull")


class TestFilterchainBuilder(unittest.TestCase):
    """Test build_audio_filter_chain logic, limiting, tempo chaining, and loudnorm."""

    def test_volume_boost_adds_limiter(self) -> None:
        cfg = AudioFilterConfig(volume_db=4.0)
        args = build_audio_filter_chain(cfg)
        self.assertEqual(args[0], "-af")
        filter_str = args[1]
        self.assertIn("volume=4dB", filter_str)
        self.assertIn("alimiter=limit=-0.5dB", filter_str)

    def test_volume_reduction_no_limiter_by_default(self) -> None:
        cfg = AudioFilterConfig(volume_db=-3.0)
        args = build_audio_filter_chain(cfg)
        self.assertEqual(args[0], "-af")
        filter_str = args[1]
        self.assertIn("volume=-3dB", filter_str)
        self.assertNotIn("alimiter", filter_str)

    def test_explicit_limiter_enabled(self) -> None:
        cfg = AudioFilterConfig(limiter=True)
        args = build_audio_filter_chain(cfg)
        self.assertEqual(args[0], "-af")
        self.assertEqual(args[1], "alimiter=limit=-0.5dB")

    def test_volume_boost_with_explicit_limiter_no_duplicate(self) -> None:
        cfg = AudioFilterConfig(volume_db=3.0, limiter=True)
        filter_str = build_filter_string(cfg)
        self.assertEqual(filter_str.count("alimiter=limit=-0.5dB"), 1)

    def test_highpass_and_lowpass(self) -> None:
        cfg = AudioFilterConfig(highpass_hz=80, lowpass_hz=15000)
        filter_str = build_filter_string(cfg)
        self.assertIn("highpass=f=80", filter_str)
        self.assertIn("lowpass=f=15000", filter_str)

    def test_dynamic_range_compression(self) -> None:
        cfg = AudioFilterConfig(dynamic_range_compression=True)
        filter_str = build_filter_string(cfg)
        self.assertIn("acompressor=", filter_str)

    def test_tempo_standard_range(self) -> None:
        cfg = AudioFilterConfig(tempo=1.5)
        filter_str = build_filter_string(cfg)
        self.assertIn("atempo=1.5", filter_str)

    def test_tempo_fast_chaining(self) -> None:
        # 3.0x requires chaining atempo=2.0,atempo=1.5
        cfg = AudioFilterConfig(tempo=3.0)
        filter_str = build_filter_string(cfg)
        self.assertIn("atempo=2.0", filter_str)
        self.assertIn("atempo=1.5", filter_str)

    def test_tempo_slow_chaining(self) -> None:
        # 0.25x requires chaining atempo=0.5,atempo=0.5
        cfg = AudioFilterConfig(tempo=0.25)
        filter_str = build_filter_string(cfg)
        self.assertEqual(filter_str.count("atempo=0.5"), 2)

    def test_tempo_identity_no_filter(self) -> None:
        cfg = AudioFilterConfig(tempo=1.0)
        filter_str = build_filter_string(cfg)
        self.assertEqual(filter_str, "")

    def test_tempo_invalid_raises(self) -> None:
        cfg = AudioFilterConfig(tempo=-1.0)
        with self.assertRaises(ValueError):
            build_audio_filter_chain(cfg)

    def test_pitch_shifting(self) -> None:
        cfg = AudioFilterConfig(pitch=1.2)
        filter_str = build_filter_string(cfg)
        self.assertIn("asetrate=44100*1.2", filter_str)
        self.assertIn("atempo=", filter_str)
        self.assertIn("aresample=44100", filter_str)

    def test_loudnorm_targets(self) -> None:
        # "-16"
        cfg16 = AudioFilterConfig(loudnorm_target="-16")
        self.assertIn("loudnorm=I=-16.0:TP=-1.5:LRA=11", build_filter_string(cfg16))

        # "-14 LUFS"
        cfg14 = AudioFilterConfig(loudnorm_target="-14 LUFS")
        self.assertIn("loudnorm=I=-14.0:TP=-1.5:LRA=11", build_filter_string(cfg14))

        # "streaming"
        cfg_stream = AudioFilterConfig(loudnorm_target="streaming")
        self.assertIn("loudnorm=I=-14.0:TP=-1.5:LRA=11", build_filter_string(cfg_stream))

        # "podcast"
        cfg_pod = AudioFilterConfig(loudnorm_target="podcast")
        self.assertIn("loudnorm=I=-16.0:TP=-1.5:LRA=11", build_filter_string(cfg_pod))

    def test_combined_unified_filterchain(self) -> None:
        cfg = AudioFilterConfig(
            highpass_hz=80,
            eq_preset="vocal_clarity",
            volume_db=2.5,
            tempo=1.1,
            loudnorm_target="-16",
        )
        args = build_audio_filter_chain(cfg)
        self.assertEqual(args[0], "-af")
        fstr = args[1]
        # Verify ordering: highpass -> EQ -> volume -> limiter -> tempo -> loudnorm
        idx_hp = fstr.find("highpass=f=80")
        idx_eq = fstr.find("equalizer=f=3000")
        idx_vol = fstr.find("volume=2.5dB")
        idx_lim = fstr.find("alimiter=limit=-0.5dB")
        idx_tempo = fstr.find("atempo=1.1")
        idx_norm = fstr.find("loudnorm=I=-16.0")

        self.assertTrue(
            -1 < idx_hp < idx_eq < idx_vol < idx_lim < idx_tempo < idx_norm,
            f"Filter ordering mismatch: {fstr}",
        )


class TestDownmixITUBS775(unittest.TestCase):
    """Test ITU-R BS.775 multichannel downmixing to stereo."""

    def test_downmix_51_normalized(self) -> None:
        filt = generate_itur_bs775_matrix(6, normalize=True)
        self.assertTrue(filt.startswith("pan=stereo|"))
        # Expected scale: sqrt(2) - 1 ≈ 0.414214
        self.assertIn("FL=0.414214*FL+0.292893*FC+0.292893*BL", filt)
        self.assertIn("FR=0.414214*FR+0.292893*FC+0.292893*BR", filt)

        # Sum of coefficients must equal 1.0 (0 dBFS peak protection)
        coeff_sum = 0.414214 + 0.292893 + 0.292893
        self.assertAlmostEqual(coeff_sum, 1.0, places=4)

    def test_downmix_51_string_inputs(self) -> None:
        filt1 = generate_itur_bs775_matrix("5.1")
        filt2 = get_itur_bs775_downmix_filter("5.1(side)")
        self.assertEqual(filt1, filt2)
        self.assertIn("FL=0.414214", filt1)

    def test_downmix_71_normalized(self) -> None:
        filt = generate_itur_bs775_matrix(8, normalize=True)
        self.assertTrue(filt.startswith("pan=stereo|"))
        self.assertIn("FL=0.343146*FL+0.242641*FC+0.242641*SL+0.171573*BL", filt)
        self.assertIn("FR=0.343146*FR+0.242641*FC+0.242641*SR+0.171573*BR", filt)

        # Sum of coefficients must equal ~1.0
        coeff_sum = 0.343146 + 0.242641 + 0.242641 + 0.171573
        self.assertAlmostEqual(coeff_sum, 1.0, places=4)

    def test_downmix_71_string_input(self) -> None:
        filt = get_itur_bs775_downmix_filter("7.1")
        self.assertIn("0.343146", filt)

    def test_downmix_unnormalized(self) -> None:
        filt_51_raw = generate_itur_bs775_matrix(6, normalize=False)
        self.assertIn("1.0*FL+0.707107*FC+0.707107*BL", filt_51_raw)

        filt_71_raw = generate_itur_bs775_matrix(8, normalize=False)
        self.assertIn("1.0*FL+0.707107*FC+0.707107*SL+0.5*BL", filt_71_raw)

    def test_downmix_invalid_layout_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_itur_bs775_matrix(4)
        with self.assertRaises(ValueError):
            generate_itur_bs775_matrix("mono")


class TestEnterprisePresets(unittest.TestCase):
    """Test newly added enterprise and broadcast presets in core/audio_profiles.py."""

    def test_studio_master_preset(self) -> None:
        p = get_profile("studio_master")
        self.assertEqual(p.format, AudioFormat.FLAC)
        self.assertEqual(p.codec, "flac")
        self.assertEqual(p.bitrate_mode, BitrateMode.LOSSLESS)
        self.assertEqual(p.sample_rate, 96000)
        self.assertEqual(p.sample_fmt, "s32")
        args = p.to_ffmpeg_args()
        self.assertIn("-ar", args)
        self.assertIn("96000", args)
        self.assertIn("-sample_fmt", args)
        self.assertIn("s32", args)

    def test_podcast_broadcast_preset(self) -> None:
        p = get_profile("podcast_broadcast")
        self.assertEqual(p.format, AudioFormat.MP3)
        self.assertEqual(p.codec, "libmp3lame")
        self.assertEqual(p.bitrate_kbps, 192)
        args = p.to_ffmpeg_args()
        self.assertIn("-b:a", args)
        self.assertIn("192k", args)
        self.assertIn("-af", args)
        af_idx = args.index("-af")
        filt = args[af_idx + 1]
        self.assertIn("highpass=f=80", filt)
        self.assertIn("acompressor=", filt)
        self.assertIn("loudnorm=I=-16", filt)

    def test_streaming_optimized_preset(self) -> None:
        p = get_profile("streaming_optimized")
        self.assertEqual(p.format, AudioFormat.AAC)
        self.assertEqual(p.codec, "aac")
        self.assertEqual(p.bitrate_kbps, 256)
        args = p.to_ffmpeg_args()
        self.assertIn("-b:a", args)
        self.assertIn("256k", args)
        self.assertIn("-af", args)
        af_idx = args.index("-af")
        filt = args[af_idx + 1]
        self.assertIn("loudnorm=I=-14", filt)

    def test_audiophile_lossless_preset(self) -> None:
        p = get_profile("audiophile_lossless")
        self.assertEqual(p.format, AudioFormat.FLAC)
        self.assertEqual(p.codec, "flac")
        self.assertEqual(p.bitrate_mode, BitrateMode.LOSSLESS)
        self.assertEqual(p.compression_level, 12)
        self.assertEqual(p.sample_fmt, "s32")
        args = p.to_ffmpeg_args()
        self.assertIn("-compression_level", args)
        self.assertIn("12", args)
        self.assertIn("-sample_fmt", args)
        self.assertIn("s32", args)

    def test_ultra_compression_preset(self) -> None:
        p = get_profile("ultra_compression")
        self.assertEqual(p.format, AudioFormat.OPUS)
        self.assertEqual(p.codec, "libopus")
        self.assertEqual(p.bitrate_kbps, 64)
        self.assertEqual(p.application, "audio")
        args = p.to_ffmpeg_args()
        self.assertIn("-b:a", args)
        self.assertIn("64k", args)
        self.assertIn("-application", args)
        self.assertIn("audio", args)
        # Ensure -b:a is not duplicated
        self.assertEqual(args.count("-b:a"), 1)

    def test_direct_lossless_copy_preset(self) -> None:
        p = get_profile("direct_lossless_copy")
        self.assertEqual(p.codec, "copy")
        args = p.to_ffmpeg_args()
        self.assertEqual(args, ["-c:a", "copy"])
        self.assertTrue(can_lossless_copy("aac", "copy"))
        self.assertTrue(can_lossless_copy("mp3", "copy"))
        self.assertTrue(can_lossless_copy("flac", AudioFormat.COPY))


class TestMultiAudioStreamDiscovery(unittest.TestCase):
    """Test get_audio_streams_summary in MediaProbeResult."""

    def test_get_audio_streams_summary_multitrack(self) -> None:
        stream1 = AudioStreamInfo(
            index=1,
            codec_name="aac",
            codec_long_name="Advanced Audio Coding",
            channels=2,
            channel_layout="stereo",
            sample_rate=44100,
            bit_rate=192000,
            duration=120.0,
            tags={"language": "eng", "title": "English Stereo"},
            is_default=True,
        )
        stream2 = AudioStreamInfo(
            index=2,
            codec_name="ac3",
            codec_long_name="Dolby Digital",
            channels=6,
            channel_layout="5.1",
            sample_rate=48000,
            bit_rate=384000,
            duration=120.0,
            tags={"language": "jpn", "title": "Japanese 5.1"},
            is_default=False,
        )
        probe = MediaProbeResult(
            file_path=Path("synthetic.mkv"),
            format_name="matroska",
            format_long_name="Matroska",
            duration=120.0,
            size_bytes=5000000,
            bit_rate=4000000,
            audio_streams=[stream1, stream2],
            video_streams=[],
            has_video=False,
            has_cover_art=False,
            cover_art_stream_index=None,
        )

        summary = probe.get_audio_streams_summary()
        self.assertEqual(len(summary), 2)

        # Track 1
        self.assertEqual(summary[0]["index"], 1)
        self.assertEqual(summary[0]["codec"], "aac")
        self.assertEqual(summary[0]["channels"], 2)
        self.assertEqual(summary[0]["language"], "eng")
        self.assertEqual(summary[0]["title"], "English Stereo")
        self.assertTrue(summary[0]["is_default"])

        # Track 2
        self.assertEqual(summary[1]["index"], 2)
        self.assertEqual(summary[1]["codec"], "ac3")
        self.assertEqual(summary[1]["channels"], 6)
        self.assertEqual(summary[1]["language"], "jpn")
        self.assertEqual(summary[1]["title"], "Japanese 5.1")
        self.assertFalse(summary[1]["is_default"])

    def test_get_audio_streams_summary_empty(self) -> None:
        probe = MediaProbeResult(
            file_path=Path("silent.mp4"),
            format_name="mov,mp4",
            format_long_name="QuickTime",
            duration=10.0,
            size_bytes=1000,
            bit_rate=10000,
            audio_streams=[],
            video_streams=[],
            has_video=False,
            has_cover_art=False,
            cover_art_stream_index=None,
        )
        self.assertEqual(probe.get_audio_streams_summary(), [])


class TestFFmpegDSPIntegration(unittest.TestCase):
    """Integration test verifying FFmpeg executes the filters and presets successfully."""

    tmp_dir: tempfile.TemporaryDirectory[str]
    work_dir: Path
    test_video: Path
    ffmpeg_bin: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.work_dir = Path(cls.tmp_dir.name)
        cls.test_video = cls.work_dir / "input_multitrack.mp4"
        cls.ffmpeg_bin = find_ffmpeg()

        # Generate synthetic 2-second test video with stereo AAC audio
        cmd = [
            str(cls.ffmpeg_bin),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
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

    def test_filtergraph_execution_with_ffmpeg(self) -> None:
        """Execute all filters directly via FFmpeg null sink to confirm zero filter errors."""
        cfg = AudioFilterConfig(
            volume_db=3.0,
            eq_preset="podcast_enhancer",
            tempo=1.2,
            loudnorm_target="-16",
        )
        filter_args = build_audio_filter_chain(cfg)
        self.assertEqual(filter_args[0], "-af")

        cmd = [
            str(self.ffmpeg_bin),
            "-nostats",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-af",
            filter_args[1],
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
        )
        self.assertEqual(
            res.returncode,
            0,
            f"FFmpeg filter failed: {res.stderr.decode('utf-8', errors='replace')}",
        )

    def test_downmix_51_execution_with_ffmpeg(self) -> None:
        pan_filt = generate_itur_bs775_matrix("5.1", normalize=True)
        cmd = [
            str(self.ffmpeg_bin),
            "-nostats",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-ac",
            "6",
            "-af",
            pan_filt,
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
        )
        self.assertEqual(
            res.returncode,
            0,
            f"FFmpeg 5.1 downmix failed: {res.stderr.decode('utf-8', errors='replace')}",
        )

    def test_downmix_71_execution_with_ffmpeg(self) -> None:
        pan_filt = generate_itur_bs775_matrix("7.1", normalize=True)
        cmd = [
            str(self.ffmpeg_bin),
            "-nostats",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-ac",
            "8",
            "-af",
            pan_filt,
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
        )
        self.assertEqual(
            res.returncode,
            0,
            f"FFmpeg 7.1 downmix failed: {res.stderr.decode('utf-8', errors='replace')}",
        )

    def test_transcode_with_audio_filter_config(self) -> None:
        out_file = self.work_dir / "transcode_filter_test.mp3"
        transcoder = Transcoder()
        cfg = AudioFilterConfig(
            volume_db=2.0,
            eq_preset="bass_boost",
            tempo=1.1,
        )
        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_file,
            format="mp3",
            preset="mp3_192k",
            audio_filter_config=cfg,
        )
        res = transcoder.transcode(opts)
        self.assertTrue(res.success)
        self.assertTrue(out_file.is_file())
        self.assertGreater(out_file.stat().st_size, 0)

        probe = probe_media(out_file)
        self.assertTrue(probe.has_audio)
        self.assertEqual(probe.primary_audio_stream.codec_name, "mp3")

    def test_transcode_with_podcast_broadcast_preset(self) -> None:
        out_file = self.work_dir / "podcast_broadcast_test.mp3"
        transcoder = Transcoder()
        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_file,
            preset="podcast_broadcast",
        )
        res = transcoder.transcode(opts)
        self.assertTrue(res.success)
        self.assertTrue(out_file.is_file())
        self.assertGreater(out_file.stat().st_size, 0)

    def test_transcode_with_streaming_optimized_preset(self) -> None:
        out_file = self.work_dir / "streaming_optimized_test.aac"
        transcoder = Transcoder()
        opts = TranscodeOptions(
            input_path=self.test_video,
            output_path=out_file,
            preset="streaming_optimized",
        )
        res = transcoder.transcode(opts)
        self.assertTrue(res.success)
        self.assertTrue(out_file.is_file())
        self.assertGreater(out_file.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
