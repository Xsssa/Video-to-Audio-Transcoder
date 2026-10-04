"""
file_picker.py - Enterprise Video File Picker Modal Screen for Textual TUI.

Provides an interactive filesystem browser modal dialog designed for selecting
video files and directories. Adheres to sleek, minimalist, dark styling.

Features:
- Visual directory navigation using an enhanced VideoDirectoryTree widget.
- Video format filtering and highlighting (.mp4, .mkv, .avi, .mov, etc.)
  leveraging SUPPORTED_VIDEO_EXTENSIONS from ui.input_handler.
- Windows drive switching toolbar (e.g. C:, D:, F:) and root storage switching.
- Quick access toolbar (Up, Home, Refresh, Video Filter toggle).
- Split view: DirectoryTree on the left, file preview / inspection panel on the right.
- Action buttons at the bottom: Cancel, Select, Select All, Clear with consistent height (3).
- Shortcut hints cleanly positioned on the left side of the footer.
- Responsive container fitting terminal bounds (width: 78, max-height: 90%) without clipping.
- Multi-select support (Space key / Select All) or single file/directory selection.
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
            prefix.append("[✓] ", style="bold #5cbcdb")
        else:
            prefix.append("[ ] ", style="dim #484f60")

        if is_dir:
            # Folder icon
            folder_icon = "📂 " if node.is_expanded else "📁 "
            prefix.append(folder_icon, style="bold #e1e4ec")
            node_label.stylize("bold #e1e4ec")
        elif is_video:
            # Supported video file: sleek highlight
            prefix.append("🎬 ", style="#5cbcdb")
            node_label.stylize("bold #ffffff")
            # Stylize extension subtly
            ext = path.suffix
            if ext:
                node_label.highlight_regex(
                    re_pattern=r"\.[a-zA-Z0-9]+$",
                    style="#5cbcdb",
                )
        else:
            # Non-video file: muted dim style
            prefix.append("📄 ", style="dim #5e6678")
            node_label.stylize("dim #788296")

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
    Adheres to sleek minimalist dark styling fitting within terminal bounds (width: 78, max-height: 90%).
    Returns List[Path] when dismissed.
    """

    DEFAULT_CSS = """
    FilePickerModal {
        align: center middle;
        background: rgba(8, 10, 14, 0.85);
    }

    #file-picker-container, #picker-dialog {
        width: 78;
        max-width: 100%;
        height: 90%;
        max-height: 90%;
        background: #141722;
        border: round #242938;
        padding: 0 1;
        layout: vertical;
    }

    #file-picker-container:focus-within, #picker-dialog:focus-within {
        border: round #3d5470;
    }

    /* Header Bar */
    #file-picker-header, #picker-header {
        width: 100%;
        height: 2;
        border-bottom: solid #1b1f2b;
        align: left middle;
        padding: 0;
        margin-bottom: 0;
    }

    #dialog-title {
        text-style: bold;
        color: #e1e4ec;
        width: auto;
    }

    #current-path-label {
        color: #9aa2b4;
        text-align: right;
        width: 1fr;
        text-overflow: ellipsis;
    }

    /* Drive & Quick Access Toolbar */
    #nav-toolbar {
        width: 100%;
        height: 3;
        margin-top: 0;
        margin-bottom: 0;
        align: left middle;
    }

    #drive-toolbar {
        width: auto;
        height: 3;
        align: left middle;
    }

    #quick-access-toolbar {
        width: 1fr;
        height: 3;
        align: right middle;
    }

    .drive-btn {
        height: 3;
        min-width: 5;
        width: 5;
        margin-right: 1;
        background: #181c28;
        color: #9aa2b4;
        border: none;
        padding: 0 1;
    }

    .drive-btn:hover {
        background: #1d2332;
        color: #ffffff;
    }

    .drive-btn:focus {
        background: #222a3d;
        color: #ffffff;
    }

    .drive-btn.-active {
        background: #1e3745;
        color: #5cbcdb;
        text-style: bold;
    }

    .nav-btn {
        height: 3;
        margin-left: 1;
        background: #181c28;
        color: #9aa2b4;
        border: none;
        padding: 0 1;
    }

    .nav-btn:hover {
        background: #1d2332;
        color: #ffffff;
    }

    .nav-btn:focus {
        background: #222a3d;
        color: #ffffff;
    }

    #btn-up {
        min-width: 6;
    }

    #btn-home {
        min-width: 8;
    }

    #btn-refresh {
        min-width: 10;
    }

    #btn-toggle-filter {
        min-width: 12;
    }

    /* Main Split Body */
    #main-body {
        width: 100%;
        height: 1fr;
        margin-top: 0;
        margin-bottom: 0;
    }

    #tree-pane {
        width: 56%;
        height: 100%;
        background: #10121a;
        border: solid #1f2330;
        padding: 0;
    }

    VideoDirectoryTree {
        background: #10121a;
        color: #e1e4ec;
        height: 100%;
        border: none;
        scrollbar-size-vertical: 1;
    }

    VideoDirectoryTree:focus {
        border: none;
    }

    #preview-pane, #side-pane {
        width: 44%;
        height: 100%;
        margin-left: 1;
        background: transparent;
        padding: 0;
    }

    #preview-header, #details-header {
        height: 1;
        text-style: bold;
        color: #7d879e;
        background: #141722;
        padding: 0 1;
        margin-bottom: 0;
    }

    #item-details-box {
        height: 5;
        background: #10121a;
        border: solid #1f2330;
        padding: 0 1;
        color: #e1e4ec;
        margin-bottom: 0;
    }

    #selected-header {
        height: 1;
        text-style: bold;
        color: #7d879e;
        background: #141722;
        padding: 0 1;
        margin-bottom: 0;
    }

    #selected-table {
        height: 1fr;
        background: #10121a;
        border: solid #1f2330;
        scrollbar-size-vertical: 1;
    }

    /* Footer Bar & Action Buttons */
    #footer-bar {
        width: 100%;
        height: 4;
        border-top: solid #1b1f2b;
        margin-top: 0;
        align: left middle;
    }

    #key-hints {
        color: #788296;
        width: 1fr;
        content-align: left middle;
        height: 3;
    }

    #dialog-buttons {
        width: auto;
        height: 3;
        align: right middle;
    }

    .footer-btn {
        height: 3;
        margin-left: 1;
        background: #181c28;
        color: #9aa2b4;
        border: none;
        padding: 0 1;
    }

    .footer-btn:hover {
        background: #1d2332;
        color: #ffffff;
    }

    .footer-btn:focus {
        background: #222a3d;
        color: #ffffff;
    }

    #btn-clear {
        min-width: 7;
        width: 7;
    }

    #btn-clear:hover {
        background: #28171a;
        color: #b36262;
    }

    #btn-clear:focus {
        background: #3e2227;
        color: #ffffff;
    }

    #btn-select-all {
        min-width: 12;
        width: 12;
    }

    #btn-cancel {
        min-width: 8;
        width: 8;
    }

    #btn-select {
        min-width: 8;
        width: 8;
        background: #1c3340;
        color: #5cbcdb;
        text-style: bold;
    }

    #btn-select:hover {
        background: #244456;
        color: #ffffff;
    }

    #btn-select:focus {
        background: #2a4c63;
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

        # Detected system storage drives
        self.drives: List[Path] = get_system_drives()

    def compose(self) -> ComposeResult:
        """Compose child widgets for the modal file picker."""
        with Container(id="file-picker-container"):
            # Header with clean title and path display
            with Horizontal(id="file-picker-header"):
                yield Label(self.dialog_title.upper(), id="dialog-title")
                yield Label(f"📁 {self.start_path}", id="current-path-label")

            # Drive toolbar (C:, D:, F:) and quick access buttons cleanly aligned
            with Horizontal(id="nav-toolbar"):
                with Horizontal(id="drive-toolbar"):
                    for drive in self.drives:
                        d_name = drive.drive or str(drive)
                        btn_id = f"drive-{d_name.replace(':', '').replace('/', 'root')}"
                        is_active = str(self.start_path).upper().startswith(d_name.upper())
                        classes = "drive-btn -active" if is_active else "drive-btn"
                        yield Button(d_name, id=btn_id, classes=classes)

                with Horizontal(id="quick-access-toolbar"):
                    yield Button("▲ Up", id="btn-up", classes="nav-btn")
                    yield Button("⌂ Home", id="btn-home", classes="nav-btn")
                    yield Button("⟳ Refresh", id="btn-refresh", classes="nav-btn")
                    filter_label = "🎬 Videos" if self.show_only_videos else "📄 All Files"
                    yield Button(filter_label, id="btn-toggle-filter", classes="nav-btn")

            # Split view: DirectoryTree on the left, file preview / inspection panel on the right
            with Horizontal(id="main-body"):
                with Vertical(id="tree-pane"):
                    self.file_tree = VideoDirectoryTree(
                        path=self.start_path,
                        selected_paths=self.selected_paths,
                        show_only_videos=self.show_only_videos,
                        id="video-dir-tree",
                    )
                    yield self.file_tree

                with Vertical(id="preview-pane"):
                    yield Label("FILE INSPECTION", id="preview-header")
                    yield Static(
                        "[dim #5e6678](Highlight a file to view details)[/]",
                        id="item-details-box",
                    )
                    yield Label("SELECTED QUEUE (0)", id="selected-header")
                    yield DataTable(id="selected-table")

            # Footer: Shortcut hints on the left, action buttons on the right
            with Horizontal(id="footer-bar"):
                yield Label(
                    "[dim #5e6678]Space[/] [dim #9aa2b4]Toggle[/]  [dim #5e6678]Bksp[/] [dim #9aa2b4]Up[/]\n"
                    "[dim #5e6678]Enter[/] [dim #9aa2b4]Select[/]  [dim #5e6678]Esc[/] [dim #9aa2b4]Cancel[/]",
                    id="key-hints",
                )
                with Horizontal(id="dialog-buttons"):
                    yield Button("Clear", id="btn-clear", classes="footer-btn")
                    yield Button("Select All", id="btn-select-all", classes="footer-btn")
                    yield Button("Cancel", id="btn-cancel", classes="footer-btn")
                    yield Button("Select", id="btn-select", classes="footer-btn")

    def on_mount(self) -> None:
        """Initialize table, views, and active drive state once mounted."""
        table = self.query_one("#selected-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("File", "Size")
        self._update_selected_ui()
        self._update_drive_active_state()

    def _update_header_path(self) -> None:
        """Update current path label in header."""
        try:
            curr = Path(self.file_tree.path).resolve()
            lbl = self.query_one("#current-path-label", Label)
            lbl.update(f"📁 {curr}")
        except Exception:
            pass

    def _update_drive_active_state(self) -> None:
        """Highlight the drive button corresponding to the active tree path."""
        try:
            curr_str = str(Path(self.file_tree.path).resolve()).upper()
            for drive in self.drives:
                d_name = drive.drive or str(drive)
                btn_id = f"drive-{d_name.replace(':', '').replace('/', 'root')}"
                btn = self.query_one(f"#{btn_id}", Button)
                if curr_str.startswith(d_name.upper()):
                    btn.add_class("-active")
                else:
                    btn.remove_class("-active")
        except Exception:
            pass

    def _update_selected_ui(self) -> None:
        """Synchronize the Selected Files table and count label."""
        try:
            count = len(self.selected_paths)
            header = self.query_one("#selected-header", Label)
            header.update(f"SELECTED QUEUE ({count})")

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
                box.update(f"[dim #5e6678]{path.name} (Not found)[/]")
                return

            if path.is_file():
                stat = path.stat()
                size = format_file_size(stat.st_size)
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                is_vid = path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                type_name = f"Video ({path.suffix.upper()})" if is_vid else f"File ({path.suffix})"
                badge = "[bold #5cbcdb]✓ Video[/]" if is_vid else "[dim #5e6678]Other[/]"
                sel_status = "[bold #72a37d]● Selected[/]" if path in self.selected_paths else "[dim #5e6678]○ Unselected[/]"

                details = (
                    f"[bold #ffffff]{path.name}[/]\n"
                    f"[dim #788296]Type:[/] {type_name}  {badge}\n"
                    f"[dim #788296]Size:[/] [#e1e4ec]{size}[/]   [dim #788296]State:[/] {sel_status}\n"
                    f"[dim #788296]Date:[/] [#9aa2b4]{mtime}[/]"
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
                        f"[bold #5cbcdb]📁 {path.name}/[/]\n"
                        f"[dim #788296]Directory | [/][#e1e4ec]{len(entries)} items[/] [dim #5e6678]({vid_count} videos)[/]\n"
                        f"[dim #788296]Path:[/] [dim #5e6678]{str(path)}[/]"
                    )
                except (PermissionError, OSError):
                    details = f"[bold #5cbcdb]📁 {path.name}/[/]\n[dim #b36262](Access Restricted)[/]"
                box.update(details)
        except Exception:
            pass

    def _toggle_path_selection(self, path: Path) -> None:
        """Toggle selection state for a specific path."""
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
        self._update_details(path)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[DirEntry]) -> None:
        """Handle cursor movement in tree to update details box."""
        if event.node and event.node.data:
            self._update_details(event.node.data.path)

    def on_video_directory_tree_selection_toggled(
        self, event: VideoDirectoryTree.SelectionToggled
    ) -> None:
        """Handle space-toggle event emitted from VideoDirectoryTree."""
        self._toggle_path_selection(event.path)

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
        """Directory node selected; update path info and preview."""
        self._update_details(event.path)
        self._update_header_path()
        self._update_drive_active_state()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Allow clicking/selecting a row in selected-table to inspect details."""
        row_key = event.row_key.value
        if row_key:
            target = Path(row_key)
            self._update_details(target)

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
        elif btn_id == "btn-home":
            self.action_go_home()
        elif btn_id == "btn-refresh":
            self.action_refresh()
        elif btn_id == "btn-toggle-filter":
            self.action_toggle_filter()
        elif btn_id == "btn-select-all":
            self.action_select_all_videos()
        elif btn_id == "btn-clear":
            self.action_clear_selection()
        elif btn_id.startswith("drive-"):
            drive_key = btn_id.replace("drive-", "")
            target_drive: Optional[Path] = None
            if drive_key == "root":
                target_drive = Path("/")
            else:
                target_drive = Path(f"{drive_key}:\\")
            if target_drive and target_drive.exists():
                self.file_tree.path = target_drive
                self._update_header_path()
                self._update_drive_active_state()

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
            self._update_drive_active_state()
        else:
            self.notify("Already at filesystem root", timeout=1.5)

    def action_go_home(self) -> None:
        """Navigate to user home directory."""
        home = Path.home().resolve()
        if home.exists():
            self.file_tree.path = home
            self._update_header_path()
            self._update_drive_active_state()
            self.notify(f"Navigated to {home.name or home}", timeout=1.5)

    def action_refresh(self) -> None:
        """Reload directory tree contents."""
        self.file_tree.reload()
        self._update_header_path()
        self._update_drive_active_state()
        self.notify("Refreshed file list", timeout=1.5)

    def action_toggle_filter(self) -> None:
        """Toggle video-only vs all files filter."""
        self.show_only_videos = not self.show_only_videos
        self.file_tree.show_only_videos = self.show_only_videos
        btn = self.query_one("#btn-toggle-filter", Button)
        btn.label = "🎬 Videos" if self.show_only_videos else "📄 All Files"
        self.file_tree.reload()
        self.notify(
            "Showing video files only" if self.show_only_videos else "Showing all files",
            timeout=1.5,
        )

    def action_toggle_selection(self) -> None:
        """Toggle selection on currently focused item."""
        if self.file_tree.cursor_node and self.file_tree.cursor_node.data:
            self._toggle_path_selection(self.file_tree.cursor_node.data.path)

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
