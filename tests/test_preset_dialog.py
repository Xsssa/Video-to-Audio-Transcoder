"""
Tests for ui.tui_screens.preset_dialog (PresetDialogModal).
"""

import pytest
from textual.app import App

from core.audio_profiles import get_profile
from ui.tui_screens.preset_dialog import (
    FLAC_PRESETS,
    FORMAT_REGISTRY,
    LOSSY_PRESETS,
    WAV_PRESETS,
    PresetDialogModal,
)


def test_registry_contains_required_formats():
    """Verify that all required output formats are defined in the registry."""
    required = ["mp3", "flac", "wav", "aac", "opus", "ogg", "m4a"]
    for fmt in required:
        assert fmt in FORMAT_REGISTRY, f"Missing format {fmt} in FORMAT_REGISTRY"
        meta = FORMAT_REGISTRY[fmt]
        assert meta.name
        assert meta.full_name
        assert meta.codec
        assert meta.pros
        assert meta.cons
        assert meta.best_for


def test_presets_coverage():
    """Verify lossy and lossless presets match requirements."""
    lossy_bitrates = [p.bitrate for p in LOSSY_PRESETS]
    assert "320k" in lossy_bitrates
    assert "256k" in lossy_bitrates
    assert "192k" in lossy_bitrates
    assert "128k" in lossy_bitrates
    assert "vbr_high" in lossy_bitrates

    flac_ids = [p.preset_id for p in FLAC_PRESETS]
    assert "q_flac_16bit" in flac_ids
    assert "q_flac_24bit" in flac_ids
    assert "q_flac_fast" in flac_ids
    assert "q_flac_max" in flac_ids

    wav_ids = [p.preset_id for p in WAV_PRESETS]
    assert "q_wav_16bit" in wav_ids
    assert "q_wav_24bit" in wav_ids
    assert "q_wav_32float" in wav_ids


@pytest.mark.asyncio
async def test_modal_initial_format_and_selection():
    """Test modal initialization with default and custom initial formats."""
    modal_mp3 = PresetDialogModal(initial_format="mp3", initial_bitrate="256k")
    assert modal_mp3.current_format == "mp3"
    assert modal_mp3._resolve_initial_lossy_preset() == "q_lossy_256k"

    modal_flac = PresetDialogModal(initial_format="flac", initial_bitrate="24-bit")
    assert modal_flac.current_format == "flac"
    assert modal_flac._resolve_initial_flac_preset() == "q_flac_24bit"

    modal_wav = PresetDialogModal(initial_format="wav", initial_bitrate="32-bit float")
    assert modal_wav.current_format == "wav"
    assert modal_wav._resolve_initial_wav_preset() == "q_wav_32float"


@pytest.mark.asyncio
async def test_modal_app_lifecycle_and_apply():
    """Test running the modal in a Textual app harness, switching formats and applying."""
    applied_result = []

    class TestApp(App):
        def on_mount(self):
            def on_dismiss(result):
                applied_result.append(result)
                self.exit()

            self.push_screen(PresetDialogModal(initial_format="mp3"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, PresetDialogModal)

        # Trigger apply
        modal.action_apply()
        await pilot.pause()

    assert len(applied_result) == 1
    res = applied_result[0]
    assert res is not None
    assert res["format"] == "mp3"
    assert res["bitrate"] in ("320k", "256k", "192k", "128k", "vbr_high")
    assert res["codec"] == "libmp3lame"
    assert res["lossless"] is False
    assert "options" in res
    assert "preset" in res

    # Verify profile resolution from core
    prof = get_profile(res["format"], res["preset"])
    assert prof is not None


@pytest.mark.asyncio
async def test_modal_app_cancel():
    """Test cancelling the dialog returns None."""
    dismiss_result = []

    class TestApp(App):
        def on_mount(self):
            def on_dismiss(result):
                dismiss_result.append(result)
                self.exit()

            self.push_screen(PresetDialogModal(initial_format="opus"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_cancel()
        await pilot.pause()

    assert len(dismiss_result) == 1
    assert dismiss_result[0] is None


@pytest.mark.asyncio
async def test_modal_format_switching_and_description():
    """Test dynamically switching between lossy, FLAC, and WAV and checking visibility."""
    class TestApp(App):
        def on_mount(self):
            self.push_screen(PresetDialogModal(initial_format="mp3"))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, PresetDialogModal)

        lossy_rs = modal.query_one("#lossy-radios")
        flac_rs = modal.query_one("#flac-radios")
        wav_rs = modal.query_one("#wav-radios")
        desc_panel = modal.query_one("#desc-panel")

        # Initially MP3 (lossy)
        assert lossy_rs.display is True
        assert flac_rs.display is False
        assert wav_rs.display is False

        # Switch to FLAC
        modal.current_format = "flac"
        modal._update_quality_set_visibility()
        modal._update_description()
        await pilot.pause()

        assert lossy_rs.display is False
        assert flac_rs.display is True
        assert wav_rs.display is False
        assert "FLAC" in str(desc_panel.render())

        # Switch to WAV
        modal.current_format = "wav"
        modal._update_quality_set_visibility()
        modal._update_description()
        await pilot.pause()

        assert lossy_rs.display is False
        assert flac_rs.display is False
        assert wav_rs.display is True
        assert "WAV" in str(desc_panel.render())

        # Switch to OPUS
        modal.current_format = "opus"
        modal._update_quality_set_visibility()
        modal._update_description()
        await pilot.pause()

        assert lossy_rs.display is True
        assert flac_rs.display is False
        assert wav_rs.display is False
        assert "OPUS" in str(desc_panel.render())

        # Test selection output for OPUS
        sel = modal.get_current_selection()
        assert sel["format"] == "opus"
        assert sel["codec"] == "libopus"
        assert sel["lossless"] is False
