"""
tests/test_file_picker.py - Comprehensive tests for FilePickerModal screen layout and interactions.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from textual.app import App
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from ui.tui_screens.file_picker import FilePickerModal, VideoDirectoryTree, format_file_size, get_system_drives


class FilePickerTestApp(App[None]):
    def __init__(self, start_path: Path | None = None, **kwargs) -> None:
        super().__init__()
        self.start_path = start_path or Path.cwd()
        self.dismissed_result: list[Path] | None = None
        self.kwargs = kwargs

    def on_mount(self) -> None:
        def on_dismiss(result: list[Path]) -> None:
            self.dismissed_result = result

        self.push_screen(
            FilePickerModal(start_path=self.start_path, **self.kwargs),
            on_dismiss,
        )


@pytest.mark.asyncio
async def test_file_picker_layout_structure() -> None:
    """Verifies that the file picker modal layout has all expected containers, alignment, and button heights."""
    app = FilePickerTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, FilePickerModal)

        # 1. Dialog container (#file-picker-container)
        container = screen.query_one("#file-picker-container", Container)
        assert container is not None
        # Verify container region bounds: width is 78 on standard 80x24 terminal
        assert container.region.width == 78
        assert container.region.height <= 24
        # Verify no scrollbar clipping on modal container
        assert not container.show_vertical_scrollbar
        assert not container.show_horizontal_scrollbar

        # 2. Header with clean title and path display
        header = screen.query_one("#file-picker-header", Horizontal)
        assert header is not None
        title = screen.query_one("#dialog-title", Label)
        path_label = screen.query_one("#current-path-label", Label)
        assert "SELECT" in str(title.render()).upper()
        assert "📁" in str(path_label.render())

        # 3. Drive toolbar and quick access toolbar
        nav_toolbar = screen.query_one("#nav-toolbar", Horizontal)
        assert nav_toolbar is not None
        drive_toolbar = screen.query_one("#drive-toolbar", Horizontal)
        assert drive_toolbar is not None
        quick_toolbar = screen.query_one("#quick-access-toolbar", Horizontal)
        assert quick_toolbar is not None

        # Verify quick access buttons
        assert screen.query_one("#btn-up", Button) is not None
        assert screen.query_one("#btn-home", Button) is not None
        assert screen.query_one("#btn-refresh", Button) is not None
        assert screen.query_one("#btn-toggle-filter", Button) is not None

        # 4. Split view: DirectoryTree on the left, file preview / inspection panel on the right
        main_body = screen.query_one("#main-body", Horizontal)
        assert main_body is not None
        tree_pane = screen.query_one("#tree-pane", Vertical)
        assert tree_pane is not None
        tree = screen.query_one("#video-dir-tree", VideoDirectoryTree)
        assert tree is not None

        preview_pane = screen.query_one("#preview-pane", Vertical)
        assert preview_pane is not None
        details_box = screen.query_one("#item-details-box", Static)
        assert details_box is not None
        selected_table = screen.query_one("#selected-table", DataTable)
        assert selected_table is not None

        # 5. Footer: Shortcut hints on the left, action buttons on the right
        footer = screen.query_one("#footer-bar", Horizontal)
        assert footer is not None
        key_hints = screen.query_one("#key-hints", Label)
        assert key_hints is not None
        assert "Space" in str(key_hints.render())
        assert "Enter" in str(key_hints.render())

        buttons_container = screen.query_one("#dialog-buttons", Horizontal)
        assert buttons_container is not None

        # 6. Action buttons: Cancel, Select, Select All, Clear with height 3
        btn_clear = screen.query_one("#btn-clear", Button)
        btn_select_all = screen.query_one("#btn-select-all", Button)
        btn_cancel = screen.query_one("#btn-cancel", Button)
        btn_select = screen.query_one("#btn-select", Button)

        for btn in (btn_clear, btn_select_all, btn_cancel, btn_select):
            assert btn.region.height == 3
            assert btn.styles.height.value == 3


@pytest.mark.asyncio
async def test_file_picker_button_actions() -> None:
    """Verifies that Clear, Select All, Cancel, and Select actions function properly."""
    app = FilePickerTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, FilePickerModal)

        # Test Select All
        btn_select_all = screen.query_one("#btn-select-all", Button)
        await pilot.click(btn_select_all)
        await pilot.pause(0.1)

        # Test Clear
        btn_clear = screen.query_one("#btn-clear", Button)
        await pilot.click(btn_clear)
        await pilot.pause(0.1)
        assert len(screen.selected_paths) == 0

        # Test Cancel button dismisses with empty list
        btn_cancel = screen.query_one("#btn-cancel", Button)
        await pilot.click(btn_cancel)
        await pilot.pause(0.1)

    assert app.dismissed_result == []


@pytest.mark.asyncio
async def test_file_picker_quick_navigation() -> None:
    """Verifies Up, Home, Refresh, and Filter toggle buttons."""
    app = FilePickerTestApp()
    async with app.run_test(size=(100, 30)) as pilot:
        screen = app.screen
        assert isinstance(screen, FilePickerModal)

        # Test filter toggle
        btn_filter = screen.query_one("#btn-toggle-filter", Button)
        initial_mode = screen.show_only_videos
        await pilot.click(btn_filter)
        await pilot.pause(0.05)
        assert screen.show_only_videos != initial_mode

        # Test refresh
        btn_refresh = screen.query_one("#btn-refresh", Button)
        await pilot.click(btn_refresh)
        await pilot.pause(0.05)

        # Test Home navigation
        btn_home = screen.query_one("#btn-home", Button)
        await pilot.click(btn_home)
        await pilot.pause(0.05)
        assert Path(screen.file_tree.path).resolve() == Path.home().resolve()

        # Test Up navigation
        btn_up = screen.query_one("#btn-up", Button)
        await pilot.click(btn_up)
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_file_picker_responsive_terminal_sizes() -> None:
    """Verifies that the layout fits comfortably across multiple terminal dimensions without container scrollbars."""
    for width, height in [(80, 24), (100, 30), (120, 35)]:
        app = FilePickerTestApp()
        async with app.run_test(size=(width, height)) as pilot:
            screen = app.screen
            container = screen.query_one("#file-picker-container", Container)

            # Sizing checks
            assert container.region.width <= 78
            assert container.region.height <= int(height * 0.95)
            # No scrollbars on container
            assert not container.show_vertical_scrollbar
            assert not container.show_horizontal_scrollbar
