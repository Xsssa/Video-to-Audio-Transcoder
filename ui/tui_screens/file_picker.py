"""
file_picker.py - Enterprise Video File Picker Modal Screen for Textual TUI.

Provides an interactive filesystem browser modal dialog designed for selecting
video files and directories. Adheres to sleek, minimalist, dark styling.

Features:
- Visual directory navigation using an enhanced VideoDirectoryTree widget.
- Video format filtering and highlighting (.mp4, .mkv, .avi, .mov, etc.)
  leveraging SUPPORTED_VIDEO_EXTENSIONS from ui.input_handler.
- Multi-select support (via Space key / Select All) or single file/directory selection.
- Keyboard navigation: Enter to select, Escape to cancel, Backspace to navigate up.
- Windows drive switching toolbar (e.g. C:, D:, F:).
- Live item inspection preview pane (file size, format, last modified).
- Selected files queue management.
- Returns List[Path] when dismissed.
"""

from __future__ import annotations

import os
import string
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Set, Union

# Ensure root of project is in sys.path when executed directly
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Label,
    Static,
    Tree,
)
from textual.widgets._directory_tree import DirEntry

from ui.input_handler import SUPPORTED_VIDEO_EXTENSIONS, is_supported_video


def format_file_size(num_bytes: int) -> str:
    """Format byte size into human-readable string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} PB"


def get_system_drives() -> List[Path]:
    """Detect accessible root storage drives (Windows drives or / on POSIX)."""
    if sys.platform != "win32":
        return [Path("/")]
    drives: List[Path] = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\")
        if p.exists():
            drives.append(p)
    return drives


class VideoDirectoryTree(DirectoryTree):
    """
    Subclass of DirectoryTree specialized for multimedia workflows:
    - Filters and highlights video media files.
    - Displays custom icons and selection checkboxes.
    - Customizes Space key to toggle selection instead of node expansion.
    """

    class SelectionToggled(Message):
        """Posted when a user requests toggling selection for the current node."""

        def __init__(self, node: Tree.Node[DirEntry], path: Path) -> None:
            super().__init__()
            self.node = node
            self.path = path

    BINDINGS = [
        *[b for b in DirectoryTree.BINDINGS if b.key != "space"],
        Binding("space", "toggle_selection", "Toggle Selection", show=True),
    ]

    def __init__(
        self,
        path: Union[str, Path],
        selected_paths: Set[Path],
        show_only_videos: bool = True,
        hide_hidden: bool = True,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            path=path,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.selected_paths = selected_paths
        self.show_only_videos = show_only_videos
        self.hide_hidden = hide_hidden

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """Filter paths displayed in the tree."""
        for p in paths:
            # Skip hidden files or directories starting with '.'
            if self.hide_hidden and p.name.startswith("."):
                continue
            try:
                if p.is_dir():
                    yield p
                elif not self.show_only_videos or p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                    yield p
            except (PermissionError, OSError):
                continue

    def render_label(
        self,
        node: Tree.Node[DirEntry],
        base_style: Style,
        style: Style,
    ) -> Text:
        """Render visually distinctive labels with sleek dark styling."""
        node_label = node._label.copy()
        node_label.stylize(style)

        if not self.is_mounted or node.data is None:
            return node_label

        path = node.data.path
        is_dir = node._allow_expand
        is_selected = path in self.selected_paths
        is_video = (not is_dir) and (path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS)

        # Selection indicator badge
        prefix = Text()
        if is_selected:
            prefix.append("[✓] ", style="bold #89b4fa")
        else:
            prefix.append("[ ] ", style="dim #45475a")

        if is_dir:
            # Folder icon
            folder_icon = "📂 " if node.is_expanded else "📁 "
            prefix.append(folder_icon, style="bold #cdd6f4")
            node_label.stylize("bold #cdd6f4")
        elif is_video:
            # Supported video file: sleek highlight
            prefix.append("🎬 ", style="#89b4fa")
            node_label.stylize("bold #ffffff")
            # Stylize extension subtly
            ext = path.suffix
            if ext:
                node_label.highlight_regex(
                    re_pattern=r"\.[a-zA-Z0-9]+$",
                    style="#89b4fa",
                )
        else:
            # Non-video file: muted dim style
            prefix.append("📄 ", style="dim #6c7086")
            node_label.stylize("dim #7f849c")

        return Text.assemble(prefix, node_label)

    def action_toggle_selection(self) -> None:
        """Trigger selection toggling on current cursor node."""
        if self.cursor_node and self.cursor_node.data:
            self.post_message(
                self.SelectionToggled(self.cursor_node, self.cursor_node.data.path)
            )


class FilePickerModal(ModalScreen[List[Path]]):
    """
    Enterprise-grade Textual ModalScreen for selecting video files or directories.
    Returns List[Path] when dismissed.
    """

    DEFAULT_CSS = """
    FilePickerModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.82);
    }

    #picker-dialog {
        width: 94%;
        max-width: 135;
        height: 90%;
        max-height: 44;
        background: #181825;
        border: round #313244;
        padding: 1;
    }

    #picker-header {
        height: 3;
        dock: top;
        border-bottom: solid #313244;
        margin-bottom: 1;
    }

    #dialog-title {
        text-style: bold;
        color: #cdd6f4;
        width: 32;
    }

    #current-path-label {
        color: #a6adc8;
        text-align: right;
        width: 1fr;
        text-overflow: ellipsis;
    }

    #nav-toolbar {
        height: 3;
        margin-bottom: 1;
    }

    .nav-btn {
        margin-right: 1;
        height: 3;
        background: #313244;
        color: #cdd6f4;
        border: none;
    }

    .nav-btn:hover {
        background: #45475a;
        color: #ffffff;
    }

    .drive-btn {
        min-width: 5;
        width: 6;
        height: 3;
        margin-right: 1;
        background: #1e1e2e;
        color: #89b4fa;
        border: solid #313244;
    }

    .drive-btn:hover {
        background: #313244;
        color: #ffffff;
    }

    #main-body {
        height: 1fr;
    }

    #tree-pane {
        width: 62%;
        height: 100%;
        border: round #313244;
        background: #11111b;
    }

    VideoDirectoryTree {
        background: #11111b;
        color: #cdd6f4;
        height: 100%;
    }

    #side-pane {
        width: 38%;
        height: 100%;
        margin-left: 1;
    }

    #selected-header {
        text-style: bold;
        color: #cdd6f4;
        margin-bottom: 0;
    }

    #selected-table {
        height: 1fr;
        background: #11111b;
        border: round #313244;
        margin-bottom: 1;
    }

    #side-actions {
        height: 3;
        margin-bottom: 1;
    }

    .side-btn {
        margin-right: 1;
        height: 3;
        background: #313244;
        color: #cdd6f4;
        border: none;
    }

    .side-btn:hover {
        background: #45475a;
        color: #ffffff;
    }

    #details-header {
        text-style: bold;
        color: #a6adc8;
        margin-bottom: 0;
    }

    #item-details-box {
        height: 6;
        background: #11111b;
        border: round #313244;
        padding: 0 1;
        color: #cdd6f4;
    }

    #footer-bar {
        height: 3;
        dock: bottom;
        border-top: solid #313244;
        padding-top: 1;
    }

    #key-hints {
        color: #6c7086;
        width: 1fr;
    }

    #dialog-buttons {
        width: auto;
    }

    #dialog-buttons Button {
        margin-left: 1;
        height: 3;
        border: none;
    }

    #btn-select {
        background: #3b4261;
        color: #c0caf5;
        text-style: bold;
    }

    #btn-select:hover {
        background: #7aa2f7;
        color: #1a1b26;
    }

    #btn-select-dir {
        background: #2a2e3f;
        color: #a9b1d6;
    }

    #btn-select-dir:hover {
        background: #3b4261;
        color: #ffffff;
    }

    #btn-cancel {
        background: #2a2b36;
        color: #a6adc8;
    }

    #btn-cancel:hover {
        background: #45475a;
        color: #ffffff;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("backspace", "go_up", "Up a folder", priority=True),
        Binding("enter", "confirm_selection", "Select", priority=False),
        Binding("space", "toggle_selection", "Toggle Selection", priority=False),
        Binding("ctrl+a", "select_all_videos", "Select All in Dir", priority=False),
        Binding("c", "clear_selection", "Clear Selection", priority=False),
    ]

    def __init__(
        self,
        start_path: Optional[Union[str, Path]] = None,
        allow_multiple: bool = True,
        select_directories: bool = True,
        show_only_videos: bool = True,
        initial_selection: Optional[Iterable[Union[str, Path]]] = None,
        title: str = "Select Video Source(s)",
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        # Resolve starting root
        resolved_start = Path(start_path or Path.cwd()).resolve()
        initial_preselect: Optional[Path] = None

        if resolved_start.is_file():
            initial_preselect = resolved_start
            resolved_start = resolved_start.parent

        self.start_path: Path = resolved_start
        self.allow_multiple: bool = allow_multiple
        self.select_directories: bool = select_directories
        self.show_only_videos: bool = show_only_videos
        self.dialog_title: str = title

        # Selected paths state
        self.selected_paths: Set[Path] = set()
        if initial_preselect:
            self.selected_paths.add(initial_preselect)
        if initial_selection:
            for item in initial_selection:
                self.selected_paths.add(Path(item).resolve())

        # Detected system drives
        self.drives: List[Path] = get_system_drives()

    def compose(self) -> ComposeResult:
        """Compose child widgets."""
        with Container(id="picker-dialog"):
            # Header
            with Horizontal(id="picker-header"):
                yield Label(self.dialog_title.upper(), id="dialog-title")
                yield Label(f"📁 {self.start_path}", id="current-path-label")

            # Navigation Toolbar
            with Horizontal(id="nav-toolbar"):
                yield Button("⬆ Up (Backspace)", id="btn-up", classes="nav-btn")
                yield Button("⟳ Refresh", id="btn-refresh", classes="nav-btn")
                filter_text = (
                    "Filter: Videos Only" if self.show_only_videos else "Filter: All Files"
                )
                yield Button(filter_text, id="btn-toggle-filter", classes="nav-btn")

                # Drive buttons on Windows
                if len(self.drives) > 1:
                    for drive in self.drives:
                        d_name = drive.drive or str(drive)
                        yield Button(d_name, id=f"drive-{d_name.replace(':', '')}", classes="drive-btn")

            # Main Body: File Tree + Selected List & Details
            with Horizontal(id="main-body"):
                with Vertical(id="tree-pane"):
                    self.file_tree = VideoDirectoryTree(
                        path=self.start_path,
                        selected_paths=self.selected_paths,
                        show_only_videos=self.show_only_videos,
                        id="video-dir-tree",
                    )
                    yield self.file_tree

                with Vertical(id="side-pane"):
                    yield Label("SELECTED FILES (0)", id="selected-header")
                    yield DataTable(id="selected-table")
                    with Horizontal(id="side-actions"):
                        yield Button(
                            "Select All in Folder",
                            id="btn-select-all",
                            classes="side-btn",
                        )
                        yield Button("Clear", id="btn-clear", classes="side-btn")
                    yield Label("ITEM DETAILS", id="details-header")
                    yield Static(
                        "[dim](Highlight a file to view details)[/]",
                        id="item-details-box",
                    )

            # Footer
            with Horizontal(id="footer-bar"):
                yield Label(
                    "[Enter] Select   [Space] Toggle   [Bksp] Up   [Esc] Cancel",
                    id="key-hints",
                )
                with Horizontal(id="dialog-buttons"):
                    yield Button("Select", id="btn-select", variant="primary")
                    if self.select_directories:
                        yield Button("Select Folder", id="btn-select-dir")
                    yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        """Initialize table and views once mounted."""
        table = self.query_one("#selected-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("File", "Size")
        self._update_selected_ui()

    def _update_header_path(self) -> None:
        """Update current path label in header."""
        try:
            curr = Path(self.file_tree.path).resolve()
            lbl = self.query_one("#current-path-label", Label)
            lbl.update(f"📁 {curr}")
        except Exception:
            pass

    def _update_selected_ui(self) -> None:
        """Synchronize the Selected Files table and count label."""
        try:
            count = len(self.selected_paths)
            header = self.query_one("#selected-header", Label)
            header.update(f"SELECTED ITEMS ({count})")

            table = self.query_one("#selected-table", DataTable)
            table.clear()

            for p in sorted(self.selected_paths, key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    if p.is_dir():
                        size_str = "DIR"
                    elif p.is_file():
                        size_str = format_file_size(p.stat().st_size)
                    else:
                        size_str = "-"
                except Exception:
                    size_str = "?"
                table.add_row(p.name, size_str, key=str(p))
        except Exception:
            pass

    def _update_details(self, path: Path) -> None:
        """Render detailed inspection of highlighted item."""
        try:
            box = self.query_one("#item-details-box", Static)
            if not path.exists():
                box.update(f"[dim]{path.name} (Not found)[/]")
                return

            if path.is_file():
                stat = path.stat()
                size = format_file_size(stat.st_size)
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                is_vid = path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                type_name = f"Video ({path.suffix.upper()})" if is_vid else f"File ({path.suffix})"
                badge = "[bold #89b4fa]✓ Supported[/]" if is_vid else "[dim #6c7086]Non-video[/]"

                details = (
                    f"[bold #ffffff]{path.name}[/]\n"
                    f"[dim]Type:[/] {type_name}  {badge}\n"
                    f"[dim]Size:[/] {size}   [dim]Modified:[/] {mtime}"
                )
                box.update(details)
            elif path.is_dir():
                try:
                    entries = list(path.iterdir())
                    vid_count = sum(
                        1
                        for entry in entries
                        if entry.is_file()
                        and entry.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                    )
                    details = (
                        f"[bold #89b4fa]📁 {path.name}/[/]\n"
                        f"[dim]Directory | {len(entries)} items ({vid_count} videos)[/]\n"
                        f"[dim]{str(path)}[/]"
                    )
                except (PermissionError, OSError):
                    details = f"[bold #89b4fa]📁 {path.name}/[/]\n[dim](Restricted Access)[/]"
                box.update(details)
        except Exception:
            pass

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[DirEntry]) -> None:
        """Handle cursor movement in tree to update details box."""
        if event.node and event.node.data:
            self._update_details(event.node.data.path)

    def on_video_directory_tree_selection_toggled(
        self, event: VideoDirectoryTree.SelectionToggled
    ) -> None:
        """Handle space-toggle event emitted from VideoDirectoryTree."""
        path = event.path
        if path.is_dir() and not self.select_directories:
            self.notify("Directories cannot be selected in this mode", severity="warning")
            return

        if path in self.selected_paths:
            self.selected_paths.remove(path)
        else:
            if not self.allow_multiple:
                self.selected_paths.clear()
            self.selected_paths.add(path)

        self.file_tree.refresh()
        self._update_selected_ui()

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        """Handle user hitting Enter or clicking directly on a file."""
        event.stop()
        path = event.path

        # If only video files allowed
        if self.show_only_videos and path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            self.notify(f"'{path.name}' is not a supported video file", severity="warning")
            return

        # If items were already selected in batch mode
        if self.allow_multiple and self.selected_paths:
            if path not in self.selected_paths:
                self.selected_paths.add(path)
            self.dismiss(sorted(list(self.selected_paths)))
        else:
            # Single select or direct Enter
            self.dismiss([path])

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        """Directory node selected; update path info."""
        self._update_details(event.path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Process toolbar and dialog button clicks."""
        btn_id = event.button.id
        if not btn_id:
            return

        if btn_id == "btn-cancel":
            self.action_cancel()
        elif btn_id == "btn-select":
            self.action_confirm_selection()
        elif btn_id == "btn-select-dir":
            self._select_directory_target()
        elif btn_id == "btn-up":
            self.action_go_up()
        elif btn_id == "btn-refresh":
            self.action_refresh()
        elif btn_id == "btn-toggle-filter":
            self.action_toggle_filter()
        elif btn_id == "btn-select-all":
            self.action_select_all_videos()
        elif btn_id == "btn-clear":
            self.action_clear_selection()
        elif btn_id.startswith("drive-"):
            drive_letter = btn_id.replace("drive-", "")
            target_drive = Path(f"{drive_letter}:\\")
            if target_drive.exists():
                self.file_tree.path = target_drive
                self._update_header_path()

    def _select_directory_target(self) -> None:
        """Explicitly selects the highlighted directory or the current root."""
        if not self.select_directories:
            self.notify("Directory selection is disabled", severity="warning")
            return

        cursor = self.file_tree.cursor_node
        if cursor and cursor.data and cursor.data.path.is_dir():
            self.dismiss([cursor.data.path])
        else:
            current_root = Path(self.file_tree.path).resolve()
            self.dismiss([current_root])

    def action_cancel(self) -> None:
        """Escape / cancel action; dismisses with empty list."""
        self.dismiss([])

    def action_go_up(self) -> None:
        """Navigate one folder up the hierarchy."""
        curr = Path(self.file_tree.path).resolve()
        parent = curr.parent
        if parent != curr and parent.exists():
            self.file_tree.path = parent
            self._update_header_path()
        else:
            self.notify("Already at filesystem root", timeout=1.5)

    def action_refresh(self) -> None:
        """Reload directory tree contents."""
        self.file_tree.reload()
        self._update_header_path()

    def action_toggle_filter(self) -> None:
        """Toggle video-only vs all files filter."""
        self.show_only_videos = not self.show_only_videos
        self.file_tree.show_only_videos = self.show_only_videos
        btn = self.query_one("#btn-toggle-filter", Button)
        btn.label = (
            "Filter: Videos Only" if self.show_only_videos else "Filter: All Files"
        )
        self.file_tree.reload()
        self.notify(
            "Showing video files only" if self.show_only_videos else "Showing all files",
            timeout=1.5,
        )

    def action_toggle_selection(self) -> None:
        """Toggle selection on currently focused item."""
        if self.file_tree.cursor_node and self.file_tree.cursor_node.data:
            path = self.file_tree.cursor_node.data.path
            if path in self.selected_paths:
                self.selected_paths.remove(path)
            else:
                if not self.allow_multiple:
                    self.selected_paths.clear()
                self.selected_paths.add(path)
            self.file_tree.refresh()
            self._update_selected_ui()

    def action_select_all_videos(self) -> None:
        """Select all supported video files in the current folder."""
        curr = Path(self.file_tree.path).resolve()
        count_added = 0
        try:
            for item in curr.iterdir():
                if item.is_file() and item.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                    if item not in self.selected_paths:
                        self.selected_paths.add(item)
                        count_added += 1
            self.file_tree.refresh()
            self._update_selected_ui()
            self.notify(f"Added {count_added} video file(s)", timeout=2)
        except Exception as e:
            self.notify(f"Could not scan directory: {e}", severity="error")

    def action_clear_selection(self) -> None:
        """Clear all selected paths."""
        self.selected_paths.clear()
        self.file_tree.refresh()
        self._update_selected_ui()
        self.notify("Cleared selection", timeout=1.5)

    def action_confirm_selection(self) -> None:
        """Confirm selection and dismiss modal."""
        if self.selected_paths:
            self.dismiss(sorted(list(self.selected_paths)))
            return

        # Fallback to cursor node if no checkbox was toggled
        cursor = self.file_tree.cursor_node
        if cursor and cursor.data:
            target_path = cursor.data.path
            if target_path.is_file():
                if (
                    not self.show_only_videos
                    or target_path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                ):
                    self.dismiss([target_path])
                else:
                    self.notify(
                        "Selected file is not a supported video format",
                        severity="warning",
                    )
            elif target_path.is_dir():
                if self.select_directories:
                    self.dismiss([target_path])
                else:
                    self.notify("Please select a video file", severity="warning")
        else:
            if self.select_directories:
                self.dismiss([Path(self.file_tree.path).resolve()])
            else:
                self.notify("No video files selected", severity="warning")


def main() -> None:
    """Standalone test runner for FilePickerModal."""
    class StandaloneFilePickerApp(App):
        CSS = """
        Screen {
            background: #11111b;
        }
        """

        def on_mount(self) -> None:
            def on_picker_dismissed(result: List[Path]) -> None:
                print(f"\n[Picker Result] Selected {len(result)} path(s):")
                for p in result:
                    print(f"  - {p}")
                self.exit()

            self.push_screen(
                FilePickerModal(
                    start_path=Path.cwd(),
                    allow_multiple=True,
                    select_directories=True,
                ),
                on_picker_dismissed,
            )

    app = StandaloneFilePickerApp()
    app.run()


if __name__ == "__main__":
    main()
