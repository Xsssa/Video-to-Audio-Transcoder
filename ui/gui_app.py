"""
Modern Desktop GUI for Enterprise Video to Audio Transcoder.
Built with CustomTkinter and TkinterDnD2.
Features a visual drag & drop zone, multi-worker queue manager,
comprehensive audio encoding panel, live log drawer, and exportable reports.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk
import psutil

from core.ffmpeg_finder import find_ffmpeg, get_ffmpeg_version
from processing.progress_tracker import ProgressSnapshot
from processing.queue_manager import ConversionTask, QueueManager, QueueStats, TaskStatus
from processing.reporter import create_report_from_tasks
from processing.verifier import VerificationResult
from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    expand_path,
    get_clipboard_files,
    parse_input_paths,
)
from processing.watch_folder import WatchFolderDaemon
from ui.tray_manager import SystemTrayManager

# Optional TkinterDnD integration
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    class _BaseApp(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore
        def __init__(self, *args, **kwargs):
            ctk.CTk.__init__(self, *args, **kwargs)
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_available = True
            except Exception:
                self.dnd_available = False

    HAS_TKDND = True
except Exception:

    class _BaseApp(ctk.CTk):  # type: ignore
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.dnd_available = False

    HAS_TKDND = False
    DND_FILES = None


class QueueRowWidget(ctk.CTkFrame):
    """Visual row component representing a queued task."""

    def __init__(
        self,
        master,
        task: ConversionTask,
        on_remove: callable,
        **kwargs,
    ):
        super().__init__(master, fg_color=("gray85", "#242424"), corner_radius=6, **kwargs)
        self.task = task
        self.on_remove = on_remove

        # Grid configuration
        self.grid_columnconfigure(0, weight=3)  # Filename
        self.grid_columnconfigure(1, weight=1)  # Source format
        self.grid_columnconfigure(2, weight=1)  # Target format
        self.grid_columnconfigure(3, weight=1)  # Status
        self.grid_columnconfigure(4, weight=3)  # Progress bar
        self.grid_columnconfigure(5, weight=2)  # Speed / ETA
        self.grid_columnconfigure(6, weight=0)  # Remove button

        # 0. Filename
        fname = self.task.source_file.name
        short_name = fname if len(fname) <= 32 else fname[:29] + "..."
        self.lbl_name = ctk.CTkLabel(
            self,
            text=short_name,
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_name.grid(row=0, column=0, padx=(10, 5), pady=8, sticky="w")

        # 1. Source format badge
        src_ext = self.task.source_file.suffix.lstrip(".").upper() or "VID"
        self.lbl_src = ctk.CTkLabel(
            self,
            text=src_ext,
            text_color="#3B8ED0",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=45,
        )
        self.lbl_src.grid(row=0, column=1, padx=4, pady=8)

        # 2. Target format badge
        self.lbl_target = ctk.CTkLabel(
            self,
            text=self.task.target_format.upper(),
            text_color="#E5A50A",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=45,
        )
        self.lbl_target.grid(row=0, column=2, padx=4, pady=8)

        # 3. Status
        self.lbl_status = ctk.CTkLabel(
            self,
            text=self.task.status.value,
            font=ctk.CTkFont(size=11),
            width=75,
        )
        self.lbl_status.grid(row=0, column=3, padx=4, pady=8)

        # 4. Progress bar
        self.progress_bar = ctk.CTkProgressBar(self, height=10)
        self.progress_bar.set(self.task.progress / 100.0)
        self.progress_bar.grid(row=0, column=4, padx=8, pady=8, sticky="ew")

        # 5. Speed / ETA
        self.lbl_speed = ctk.CTkLabel(
            self,
            text="-",
            font=ctk.CTkFont(size=11),
            anchor="e",
        )
        self.lbl_speed.grid(row=0, column=5, padx=8, pady=8, sticky="e")

        # 6. Remove button
        self.btn_remove = ctk.CTkButton(
            self,
            text="✕",
            width=28,
            height=26,
            fg_color="transparent",
            hover_color=("#ff4d4d", "#b30000"),
            text_color=("gray30", "gray80"),
            command=lambda: self.on_remove(self.task.task_id),
        )
        self.btn_remove.grid(row=0, column=6, padx=(4, 8), pady=8)

    def update_state(self) -> None:
        """Refreshes visual components with latest task state."""
        self.lbl_status.configure(text=self.task.status.value)

        # Status text colors
        if self.task.status == TaskStatus.COMPLETED:
            self.lbl_status.configure(text_color="#2ecc71")
            self.progress_bar.set(1.0)
            self.progress_bar.configure(progress_color="#2ecc71")
            self.btn_remove.configure(state="normal")
            speed_txt = self.task.speed if self.task.speed != "0.0x" else "Done"
            self.lbl_speed.configure(text=speed_txt)
        elif self.task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.lbl_status.configure(text_color="#e74c3c")
            self.progress_bar.configure(progress_color="#e74c3c")
            self.btn_remove.configure(state="normal")
            self.lbl_speed.configure(text="Failed" if self.task.status == TaskStatus.FAILED else "Cancelled")
        elif self.task.status in (TaskStatus.CONVERTING, TaskStatus.PROBING):
            self.lbl_status.configure(text_color="#3498db")
            pct = max(0.0, min(100.0, self.task.progress))
            self.progress_bar.set(pct / 100.0)
            self.progress_bar.configure(progress_color="#3B8ED0")
            self.btn_remove.configure(state="disabled")

            eta_txt = f" • {self.task.eta}" if self.task.eta and self.task.eta != "--:--" else ""
            self.lbl_speed.configure(text=f"{self.task.speed}{eta_txt}")
        else:
            self.lbl_status.configure(text_color=("gray20", "gray80"))
            self.progress_bar.set(0.0)
            self.progress_bar.configure(progress_color="#3B8ED0")
            self.btn_remove.configure(state="normal")
            self.lbl_speed.configure(text="-")


class EnterpriseConverterGui(_BaseApp):
    """Main Application Window for Enterprise Video to Audio Transcoder."""

    def __init__(self, initial_paths: Optional[List[Path]] = None):
        super().__init__()

        # Appearance configuration
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("Enterprise Video to Audio Converter")
        self.geometry("1150x840")
        self.minsize(980, 720)

        # Core Queue coordinator (starts paused so user can stage files and change settings)
        self.queue_manager = QueueManager()
        self.queue_manager.pause_queue()

        self.row_widgets: Dict[str, QueueRowWidget] = {}
        self.update_event_queue: queue.Queue = queue.Queue()
        self._is_active_run = False

        # Connect QueueManager callbacks safely via thread queue
        self._wire_queue_callbacks()

        # Build UI Layout
        self._create_layout()

        # Daemons
        self.watch_daemon = None
        self.tray_manager = SystemTrayManager(
            app_name="Video to Audio Transcoder",
            on_show_window=self._show_window_from_tray,
            on_exit=self._exit_app
        )
        self.tray_manager.start()
        
        # Override close button to minimize to tray
        self.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        # Load initial files if provided
        if initial_paths:
            self.after(200, lambda: self._add_paths(initial_paths))

        # Start periodic GUI updater
        self.after(50, self._process_update_queue)

    def _wire_queue_callbacks(self) -> None:
        """Wires QueueManager callbacks to push events into thread-safe queue."""
        def on_started(task: ConversionTask):
            self.update_event_queue.put(("task_started", task.task_id))

        def on_progress(task: ConversionTask, snap: ProgressSnapshot):
            self.update_event_queue.put(("task_progress", task.task_id))

        def on_completed(task: ConversionTask, ver: VerificationResult):
            self.update_event_queue.put(("task_completed", task.task_id))

        def on_failed(task: ConversionTask, err: str):
            self.update_event_queue.put(("task_failed", task.task_id))

        def on_finished(stats: QueueStats):
            self.update_event_queue.put(("queue_finished", stats))

        self.queue_manager.on_task_started = on_started
        self.queue_manager.on_task_progress = on_progress
        self.queue_manager.on_task_completed = on_completed
        self.queue_manager.on_task_failed = on_failed
        self.queue_manager.on_queue_completed = on_finished

    def _create_layout(self) -> None:
        """Constructs two-panel layout: sidebar controls and main content area."""
        self._log_buffer: List[str] = []
        self.grid_columnconfigure(0, weight=0, minsize=320)  # Settings Sidebar
        self.grid_columnconfigure(1, weight=1)               # Main Content Area
        self.grid_rowconfigure(0, weight=1)                  # Top Main Row
        self.grid_rowconfigure(1, weight=0)                  # Bottom Log Drawer

        self._create_sidebar()
        self._create_log_drawer()
        self._create_main_content()

    def _create_sidebar(self) -> None:
        """Creates the audio configuration sidebar."""
        self.sidebar = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray90", "#1e1e1e"))
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.sidebar.grid_rowconfigure(20, weight=1)

        # Title / Branding
        lbl_brand = ctk.CTkLabel(
            self.sidebar,
            text="⚡ TRANSCODER",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#3B8ED0",
        )
        lbl_brand.grid(row=0, column=0, padx=20, pady=(20, 2), sticky="w")

        lbl_sub = ctk.CTkLabel(
            self.sidebar,
            text="Enterprise Audio Extraction",
            font=ctk.CTkFont(size=12),
            text_color="gray60",
        )
        lbl_sub.grid(row=1, column=0, padx=20, pady=(0, 15), sticky="w")

        # Separator
        sep1 = ctk.CTkFrame(self.sidebar, height=2, fg_color=("gray80", "#2d2d2d"))
        sep1.grid(row=2, column=0, sticky="ew", padx=15, pady=(0, 15))

        # Audio Settings Header
        lbl_settings = ctk.CTkLabel(
            self.sidebar,
            text="AUDIO ENCODING SETTINGS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray70",
        )
        lbl_settings.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="w")

        # 1. Target Format
        lbl_fmt = ctk.CTkLabel(self.sidebar, text="Target Format:", font=ctk.CTkFont(size=12))
        lbl_fmt.grid(row=4, column=0, padx=20, pady=(2, 2), sticky="w")
        self.opt_format = ctk.CTkOptionMenu(
            self.sidebar,
            values=["MP3", "FLAC", "WAV", "AAC", "OPUS", "OGG", "M4A"],
            command=self._on_format_changed,
        )
        self.opt_format.set("MP3")
        self.opt_format.grid(row=5, column=0, padx=20, pady=(0, 10), sticky="ew")

        # 2. Bitrate Selector
        self.lbl_bitrate = ctk.CTkLabel(self.sidebar, text="Bitrate:", font=ctk.CTkFont(size=12))
        self.lbl_bitrate.grid(row=6, column=0, padx=20, pady=(2, 2), sticky="w")
        self.opt_bitrate = ctk.CTkOptionMenu(
            self.sidebar,
            values=["320k", "256k", "192k", "128k", "VBR"],
        )
        self.opt_bitrate.set("320k")
        self.opt_bitrate.grid(row=7, column=0, padx=20, pady=(0, 10), sticky="ew")

        # 3. Sample Rate Selector
        lbl_sr = ctk.CTkLabel(self.sidebar, text="Sample Rate:", font=ctk.CTkFont(size=12))
        lbl_sr.grid(row=8, column=0, padx=20, pady=(2, 2), sticky="w")
        self.opt_sample_rate = ctk.CTkOptionMenu(
            self.sidebar,
            values=["Original", "44.1kHz", "48kHz", "96kHz"],
        )
        self.opt_sample_rate.set("Original")
        self.opt_sample_rate.grid(row=9, column=0, padx=20, pady=(0, 10), sticky="ew")

        # 4. Channels Selector
        lbl_ch = ctk.CTkLabel(self.sidebar, text="Audio Channels:", font=ctk.CTkFont(size=12))
        lbl_ch.grid(row=10, column=0, padx=20, pady=(2, 2), sticky="w")
        self.opt_channels = ctk.CTkOptionMenu(
            self.sidebar,
            values=["Original", "Stereo", "Mono", "5.1"],
        )
        self.opt_channels.set("Original")
        self.opt_channels.grid(row=11, column=0, padx=20, pady=(0, 15), sticky="ew")

        # Toggles
        self.sw_lossless_copy = ctk.CTkSwitch(
            self.sidebar,
            text="Lossless Copy (if compatible)",
            font=ctk.CTkFont(size=12),
        )
        self.sw_lossless_copy.grid(row=12, column=0, padx=20, pady=(0, 8), sticky="w")

        self.sw_loudnorm = ctk.CTkSwitch(
            self.sidebar,
            text="EBU R128 Loudness Normalization",
            font=ctk.CTkFont(size=12),
        )
        self.sw_loudnorm.grid(row=13, column=0, padx=20, pady=(0, 8), sticky="w")

        self.sw_metadata = ctk.CTkSwitch(
            self.sidebar,
            text="Preserve Metadata / Artwork",
            font=ctk.CTkFont(size=12),
        )
        self.sw_metadata.select()
        self.sw_metadata.grid(row=14, column=0, padx=20, pady=(0, 8), sticky="w")
        
        self.sw_watch_folder = ctk.CTkSwitch(
            self.sidebar,
            text="Watch Folder Daemon",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e67e22",
            command=self._toggle_watch_folder
        )
        self.sw_watch_folder.grid(row=15, column=0, padx=20, pady=(0, 15), sticky="w")

        # Separator
        sep2 = ctk.CTkFrame(self.sidebar, height=2, fg_color=("gray80", "#2d2d2d"))
        sep2.grid(row=16, column=0, sticky="ew", padx=15, pady=(0, 15))

        # Output Destination Section
        lbl_dest = ctk.CTkLabel(
            self.sidebar,
            text="OUTPUT DIRECTORY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray70",
        )
        lbl_dest.grid(row=17, column=0, padx=20, pady=(0, 6), sticky="w")

        self.entry_output = ctk.CTkEntry(
            self.sidebar,
            placeholder_text="Same directory as source video",
            font=ctk.CTkFont(size=11),
        )
        self.entry_output.grid(row=18, column=0, padx=20, pady=(0, 6), sticky="ew")

        dest_btn_box = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        dest_btn_box.grid(row=19, column=0, padx=20, pady=(0, 15), sticky="ew")
        dest_btn_box.grid_columnconfigure(0, weight=1)
        dest_btn_box.grid_columnconfigure(1, weight=1)

        btn_browse_dest = ctk.CTkButton(
            dest_btn_box,
            text="Browse...",
            height=28,
            font=ctk.CTkFont(size=11),
            command=self._browse_output_dir,
        )
        btn_browse_dest.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        btn_reset_dest = ctk.CTkButton(
            dest_btn_box,
            text="Reset",
            height=28,
            fg_color="transparent",
            border_width=1,
            font=ctk.CTkFont(size=11),
            command=lambda: self.entry_output.delete(0, "end"),
        )
        btn_reset_dest.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # System Status Card at bottom of sidebar
        card_sys = ctk.CTkFrame(self.sidebar, fg_color=("gray85", "#262626"), corner_radius=6)
        card_sys.grid(row=21, column=0, padx=15, pady=15, sticky="ew")

        try:
            ffmpeg_bin = find_ffmpeg()
            status_text = "FFmpeg: Detected ✔"
            status_color = "#2ecc71"
        except Exception:
            status_text = "FFmpeg: Missing ✖"
            status_color = "#e74c3c"

        lbl_sys_stat = ctk.CTkLabel(
            card_sys,
            text=status_text,
            text_color=status_color,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        lbl_sys_stat.pack(padx=10, pady=(6, 2), anchor="w")

        cpu_cnt = psutil.cpu_count(logical=True) or 4
        lbl_cpu = ctk.CTkLabel(
            card_sys,
            text=f"CPU Threads: {cpu_cnt} | Workers: {self.queue_manager.max_workers}",
            text_color="gray60",
            font=ctk.CTkFont(size=10),
        )
        lbl_cpu.pack(padx=10, pady=(0, 6), anchor="w")

    def _create_main_content(self) -> None:
        """Creates the main content area with Drop Box, Toolbar, and Queue Table."""
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=15)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(3, weight=1)  # Queue Table expands

        # 1. Visual Drag & Drop Box
        self.drop_box = ctk.CTkFrame(
            self.main_frame,
            height=130,
            corner_radius=10,
            border_width=2,
            border_color=("#3B8ED0", "#2980b9"),
            fg_color=("gray95", "#1f242b"),
        )
        self.drop_box.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.drop_box.pack_propagate(False)

        # Drop Box text & icons
        lbl_drop_title = ctk.CTkLabel(
            self.drop_box,
            text="📥 Drag & Drop Video Files or Folders Here",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        lbl_drop_title.pack(pady=(15, 4))

        lbl_drop_sub = ctk.CTkLabel(
            self.drop_box,
            text="Supports MP4, MKV, AVI, MOV, WMV, FLV, WEBM, TS, M4V, MTS, and more",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        lbl_drop_sub.pack(pady=(0, 8))

        # Quick action buttons inside Drop Box
        box_actions = ctk.CTkFrame(self.drop_box, fg_color="transparent")
        box_actions.pack(pady=(0, 10))

        btn_add_files = ctk.CTkButton(
            box_actions,
            text="📁 Add Files",
            width=110,
            height=28,
            font=ctk.CTkFont(size=11),
            command=self._browse_input_files,
        )
        btn_add_files.pack(side="left", padx=6)

        btn_add_folder = ctk.CTkButton(
            box_actions,
            text="📂 Add Folder",
            width=110,
            height=28,
            font=ctk.CTkFont(size=11),
            command=self._browse_input_folder,
        )
        btn_add_folder.pack(side="left", padx=6)

        btn_paste = ctk.CTkButton(
            box_actions,
            text="📋 Paste Clipboard",
            width=130,
            height=28,
            fg_color=("gray75", "#3a3a3a"),
            hover_color=("gray65", "#4a4a4a"),
            font=ctk.CTkFont(size=11),
            command=self._paste_clipboard,
        )
        btn_paste.pack(side="left", padx=6)

        # Setup Drag and Drop events if TkinterDnD is available
        self._setup_dnd()

        # 2. Queue Header and Control Toolbar
        toolbar = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        toolbar.grid_columnconfigure(0, weight=1)

        # Queue count label
        self.lbl_queue_count = ctk.CTkLabel(
            toolbar,
            text="Queue: 0 items",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.lbl_queue_count.grid(row=0, column=0, sticky="w")

        # Action Buttons
        btn_box = ctk.CTkFrame(toolbar, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_start = ctk.CTkButton(
            btn_box,
            text="▶ Start Conversion",
            width=140,
            height=32,
            fg_color="#2ecc71",
            hover_color="#27ae60",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_start_conversion,
        )
        self.btn_start.pack(side="left", padx=4)

        self.btn_pause = ctk.CTkButton(
            btn_box,
            text="⏸ Pause",
            width=80,
            height=32,
            fg_color="#f39c12",
            hover_color="#d68910",
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
            command=self._toggle_pause,
        )
        self.btn_pause.pack(side="left", padx=4)

        self.btn_clear_done = ctk.CTkButton(
            btn_box,
            text="🧹 Clear Completed",
            width=120,
            height=32,
            fg_color=("gray75", "#3a3a3a"),
            hover_color=("gray65", "#4a4a4a"),
            font=ctk.CTkFont(size=11),
            command=self._clear_completed,
        )
        self.btn_clear_done.pack(side="left", padx=4)

        self.btn_export = ctk.CTkButton(
            btn_box,
            text="📊 Export Report",
            width=110,
            height=32,
            fg_color=("gray75", "#3a3a3a"),
            hover_color=("gray65", "#4a4a4a"),
            font=ctk.CTkFont(size=11),
            command=self._export_report,
        )
        self.btn_export.pack(side="left", padx=4)

        self.btn_open_out = ctk.CTkButton(
            btn_box,
            text="📂 Open Output",
            width=100,
            height=32,
            fg_color=("gray75", "#3a3a3a"),
            hover_color=("gray65", "#4a4a4a"),
            font=ctk.CTkFont(size=11),
            command=self._open_output_folder,
        )
        self.btn_open_out.pack(side="left", padx=4)

        # 3. Table Column Header
        tbl_header = ctk.CTkFrame(self.main_frame, height=28, fg_color=("gray80", "#181818"), corner_radius=4)
        tbl_header.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        tbl_header.grid_columnconfigure(0, weight=3)
        tbl_header.grid_columnconfigure(1, weight=1)
        tbl_header.grid_columnconfigure(2, weight=1)
        tbl_header.grid_columnconfigure(3, weight=1)
        tbl_header.grid_columnconfigure(4, weight=3)
        tbl_header.grid_columnconfigure(5, weight=2)
        tbl_header.grid_columnconfigure(6, weight=0)

        ctk.CTkLabel(tbl_header, text="File Name", font=ctk.CTkFont(size=11, weight="bold"), anchor="w").grid(row=0, column=0, padx=(10, 5), pady=4, sticky="w")
        ctk.CTkLabel(tbl_header, text="Source", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=1, padx=4, pady=4)
        ctk.CTkLabel(tbl_header, text="Target", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=2, padx=4, pady=4)
        ctk.CTkLabel(tbl_header, text="Status", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=3, padx=4, pady=4)
        ctk.CTkLabel(tbl_header, text="Progress", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=4, padx=8, pady=4)
        ctk.CTkLabel(tbl_header, text="Speed / ETA", font=ctk.CTkFont(size=11, weight="bold"), anchor="e").grid(row=0, column=5, padx=8, pady=4, sticky="e")
        ctk.CTkLabel(tbl_header, text="Act", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=6, padx=(4, 12), pady=4)

        # 4. Scrollable Queue Listview
        self.scroll_queue = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.scroll_queue.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        self.scroll_queue.grid_columnconfigure(0, weight=1)

        # Empty state placeholder
        self.lbl_empty_queue = ctk.CTkLabel(
            self.scroll_queue,
            text="No files in queue. Drag video files above or click 'Add Files'.",
            font=ctk.CTkFont(size=13),
            text_color="gray50",
        )
        self.lbl_empty_queue.pack(pady=40)

    def _create_log_drawer(self) -> None:
        """Creates the bottom expandable live log drawer."""
        self.drawer_frame = ctk.CTkFrame(self, height=130, fg_color=("gray90", "#181818"), corner_radius=0)
        self.drawer_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.drawer_frame.grid_columnconfigure(0, weight=1)
        self.drawer_frame.pack_propagate(False)

        header_box = ctk.CTkFrame(self.drawer_frame, height=26, fg_color=("gray85", "#202020"), corner_radius=0)
        header_box.pack(fill="x")

        lbl_log = ctk.CTkLabel(
            header_box,
            text="📋 Activity Log",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        lbl_log.pack(side="left", padx=15, pady=3)

        btn_clear_log = ctk.CTkButton(
            header_box,
            text="Clear Log",
            width=65,
            height=20,
            fg_color="transparent",
            font=ctk.CTkFont(size=10),
            command=self._clear_log,
        )
        btn_clear_log.pack(side="right", padx=10, pady=3)

        self.txt_log = ctk.CTkTextbox(
            self.drawer_frame,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=("gray95", "#121212"),
            text_color=("gray20", "#cccccc"),
            activate_scrollbars=True,
        )
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(4, 6))

        self.append_log("Transcoder initialized. Ready.")

    def _setup_dnd(self) -> None:
        """Initializes Drag and Drop on drop_box widget if supported."""
        if hasattr(self, "dnd_available") and self.dnd_available and DND_FILES:
            try:
                self.drop_box.drop_target_register(DND_FILES)
                self.drop_box.dnd_bind("<<Drop>>", self._on_dnd_drop)
                self.drop_box.dnd_bind("<<DragEnter>>", self._on_dnd_enter)
                self.drop_box.dnd_bind("<<DragLeave>>", self._on_dnd_leave)
                self.append_log("Visual Drag & Drop support active.")
            except Exception as e:
                self.append_log(f"Drag & Drop hook notice: {e}")
        else:
            self.append_log("Drag & Drop ready via file picker and clipboard.")

    def _on_dnd_enter(self, event) -> None:
        self.drop_box.configure(border_color="#2ecc71")

    def _on_dnd_leave(self, event) -> None:
        self.drop_box.configure(border_color=("#3B8ED0", "#2980b9"))

    def _on_dnd_drop(self, event) -> None:
        self.drop_box.configure(border_color=("#3B8ED0", "#2980b9"))
        raw_data = event.data
        if not raw_data:
            return
        paths = parse_input_paths(raw_data, recursive=True)
        if paths:
            self._add_paths(paths)
        else:
            self.append_log("No supported video files recognized in dropped items.")

    def _on_format_changed(self, choice: str) -> None:
        """Adapts settings controls based on target format selection."""
        is_lossless = choice.upper() in ("FLAC", "WAV")
        if is_lossless:
            self.opt_bitrate.configure(state="disabled")
            self.lbl_bitrate.configure(text_color="gray50")
        else:
            self.opt_bitrate.configure(state="normal")
            self.lbl_bitrate.configure(text_color=("gray10", "gray90"))

    def _get_current_options(self) -> Dict[str, Any]:
        """Builds options dictionary for QueueManager task."""
        fmt = self.opt_format.get().lower()
        opts: Dict[str, Any] = {
            "preserve_cover_art": bool(self.sw_metadata.get()),
            "ebu_r128": bool(self.sw_loudnorm.get()),
            "lossless_copy_if_match": bool(self.sw_lossless_copy.get()),
        }

        # Bitrate
        if fmt not in ("flac", "wav"):
            br = self.opt_bitrate.get()
            if br != "VBR":
                opts["bitrate"] = br

        # Sample rate
        sr_val = self.opt_sample_rate.get()
        if sr_val == "44.1kHz":
            opts["sample_rate"] = 44100
        elif sr_val == "48kHz":
            opts["sample_rate"] = 48000
        elif sr_val == "96kHz":
            opts["sample_rate"] = 96000

        # Channels
        ch_val = self.opt_channels.get()
        if ch_val == "Mono":
            opts["channels"] = 1
        elif ch_val == "Stereo":
            opts["channels"] = 2
        elif ch_val == "5.1":
            opts["channels"] = 6

        return opts

    def _add_paths(self, paths: List[Path]) -> None:
        """Adds paths to QueueManager and instantiates row widgets."""
        target_fmt = self.opt_format.get().lower()
        options = self._get_current_options()

        out_txt = self.entry_output.get().strip()
        custom_out_dir = Path(out_txt).resolve() if out_txt else None

        added_tasks: List[ConversionTask] = []
        for p in paths:
            # Check duplicate in queue
            all_existing = self.queue_manager.get_all_tasks()
            if any(t.source_file == p and not t.is_terminal for t in all_existing):
                continue

            dest_file = None
            if custom_out_dir:
                dest_file = custom_out_dir / f"{p.stem}.{target_fmt}"

            task = self.queue_manager.add_task(
                source_file=p,
                target_format=target_fmt,
                output_file=dest_file,
                options=options,
            )
            added_tasks.append(task)

        if not added_tasks:
            return

        if self.lbl_empty_queue.winfo_ismapped():
            self.lbl_empty_queue.pack_forget()

        for task in added_tasks:
            row = QueueRowWidget(
                self.scroll_queue,
                task=task,
                on_remove=self._remove_task_row,
            )
            row.pack(fill="x", pady=2)
            self.row_widgets[task.task_id] = row

        self._refresh_queue_count()
        self.append_log(f"Added {len(added_tasks)} file(s) to queue.")

    def _remove_task_row(self, task_id: str) -> None:
        """Removes a task from queue and destroys its row widget."""
        self.queue_manager.cancel_task(task_id)
        with self.queue_manager._lock:
            self.queue_manager._tasks.pop(task_id, None)

        row = self.row_widgets.pop(task_id, None)
        if row:
            row.destroy()
        self._refresh_queue_count()
        self.append_log(f"Removed task {task_id}")

    def _refresh_queue_count(self) -> None:
        tasks = self.queue_manager.get_all_tasks()
        count = len(tasks)
        self.lbl_queue_count.configure(text=f"Queue: {count} items")
        if count == 0 and not self.lbl_empty_queue.winfo_ismapped():
            self.lbl_empty_queue.pack(pady=40)

    def _browse_input_files(self) -> None:
        ext_patterns = ";*".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        filetypes = [
            ("Video Files", f"*{ext_patterns}"),
            ("All Files", "*.*"),
        ]
        files = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=filetypes,
        )
        if files:
            paths = [Path(f) for f in files]
            self._add_paths(paths)

    def _browse_input_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Folder Containing Videos")
        if folder:
            paths = expand_path(folder, recursive=True)
            if paths:
                self._add_paths(paths)
            else:
                messagebox.showinfo("No Videos Found", f"No supported video files were found in:\n{folder}")

    def _paste_clipboard(self) -> None:
        paths = get_clipboard_files(recursive=True)
        if paths:
            self._add_paths(paths)
        else:
            messagebox.showinfo("Clipboard", "No video files or valid file paths were detected on the clipboard.")

    def _browse_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select Destination Directory")
        if folder:
            self.entry_output.delete(0, "end")
            self.entry_output.insert(0, folder)

    def _toggle_start_conversion(self) -> None:
        if self._is_active_run:
            # Stop / Cancel flow
            if messagebox.askyesno("Cancel Conversion", "Do you wish to cancel active conversions?"):
                self.queue_manager.clear_queue(cancel_active=True)
                self._is_active_run = False
                self.btn_start.configure(text="▶ Start Conversion", fg_color="#2ecc71", hover_color="#27ae60")
                self.btn_pause.configure(state="disabled", text="⏸ Pause", fg_color="#f39c12")
                self.append_log("Conversion cancelled by user.")
            return

        all_tasks = self.queue_manager.get_all_tasks()
        pending = [t for t in all_tasks if not t.is_terminal]
        if not pending:
            messagebox.showinfo("Queue Empty", "No queued files to convert. Please add video files first.")
            return

        # Start execution
        self._is_active_run = True
        self.btn_start.configure(text="⏹ Stop Conversion", fg_color="#e74c3c", hover_color="#c0392b")
        self.btn_pause.configure(state="normal", text="⏸ Pause", fg_color="#f39c12")

        self.queue_manager.resume_queue()
        self.append_log(f"Started conversion batch of {len(pending)} files...")

    def _toggle_pause(self) -> None:
        if not self._is_active_run:
            return
        if self.queue_manager._is_paused:
            self.queue_manager.resume_queue()
            self.btn_pause.configure(text="⏸ Pause", fg_color="#f39c12", hover_color="#d68910")
            self.append_log("Queue RESUMED.")
        else:
            self.queue_manager.pause_queue()
            self.btn_pause.configure(text="▶ Resume", fg_color="#3498db", hover_color="#2980b9")
            self.append_log("Queue PAUSED.")

    def _clear_completed(self) -> None:
        with self.queue_manager._lock:
            terminal_ids = [tid for tid, t in self.queue_manager._tasks.items() if t.is_terminal]
            for tid in terminal_ids:
                del self.queue_manager._tasks[tid]

        # Destroy row widgets for terminal tasks
        for tid in terminal_ids:
            row = self.row_widgets.pop(tid, None)
            if row:
                row.destroy()

        self._refresh_queue_count()
        self.append_log(f"Cleared {len(terminal_ids)} completed/failed tasks.")

    def _export_report(self) -> None:
        tasks = self.queue_manager.get_all_tasks()
        if not tasks:
            messagebox.showinfo("Report", "No conversion tasks have been processed yet.")
            return

        dest_file = filedialog.asksaveasfilename(
            title="Save Conversion Report",
            defaultextension=".txt",
            filetypes=[
                ("Text Report (*.txt)", "*.txt"),
                ("JSON File (*.json)", "*.json"),
                ("CSV Spreadsheet (*.csv)", "*.csv"),
            ],
        )
        if not dest_file:
            return

        p = Path(dest_file)
        report = create_report_from_tasks(tasks)

        if p.suffix.lower() == ".json":
            report.export_json(p)
        elif p.suffix.lower() == ".csv":
            report.export_csv(p)
        else:
            p.write_text(report.format_summary_table(), encoding="utf-8")

        self.append_log(f"Exported conversion report to: {p}")
        messagebox.showinfo("Report Exported", f"Successfully exported report to:\n{p}")

    def _open_output_folder(self) -> None:
        out_txt = self.entry_output.get().strip()
        folder = Path(out_txt) if out_txt else None

        if not folder or not folder.is_dir():
            tasks = self.queue_manager.get_all_tasks()
            for t in tasks:
                if t.output_file and t.output_file.parent.is_dir():
                    folder = t.output_file.parent
                    break
                elif t.source_file.parent.is_dir():
                    folder = t.source_file.parent
                    break

        if folder and folder.is_dir():
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        else:
            messagebox.showinfo("Open Output Folder", "No valid output folder available to open.")

    def append_log(self, message: str) -> None:
        now_str = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{now_str}] {message}\n"
        if hasattr(self, "txt_log") and self.txt_log:
            if hasattr(self, "_log_buffer") and self._log_buffer:
                for b in self._log_buffer:
                    self.txt_log.insert("end", b)
                self._log_buffer.clear()
            self.txt_log.insert("end", formatted)
            self.txt_log.see("end")
        else:
            if not hasattr(self, "_log_buffer"):
                self._log_buffer = []
            self._log_buffer.append(formatted)

    def _clear_log(self) -> None:
        if hasattr(self, "txt_log") and self.txt_log:
            self.txt_log.delete("1.0", "end")

    def _process_update_queue(self) -> None:
        """Processes pending events from background conversion threads."""
        try:
            while True:
                event_type, payload = self.update_event_queue.get_nowait()
                if event_type in ("task_started", "task_progress", "task_completed", "task_failed"):
                    row = self.row_widgets.get(payload)
                    if row:
                        row.update_state()

                elif event_type == "queue_finished":
                    self._is_active_run = False
                    self.btn_start.configure(text="▶ Start Conversion", fg_color="#2ecc71", hover_color="#27ae60")
                    self.btn_pause.configure(state="disabled", text="⏸ Pause", fg_color="#f39c12")
                    stats: QueueStats = payload
                    self.append_log(f"Batch completed: {stats.completed_tasks} succeeded, {stats.failed_tasks} failed.")

        except queue.Empty:
            pass

        self.after(50, self._process_update_queue)

    def _toggle_watch_folder(self) -> None:
        """Toggles the Watch Folder daemon."""
        if self.sw_watch_folder.get():
            folder_path = filedialog.askdirectory(title="Select Folder to Watch")
            if not folder_path:
                self.sw_watch_folder.deselect()
                return
            
            def _on_new_watched_file(file_path: Path):
                self.after(0, lambda: self._add_paths([file_path]))
                if self._is_active_run:
                    pass # Will be picked up automatically
                else:
                    # Auto-start conversion if paused
                    self.after(500, self._toggle_start_conversion)
                    
            self.watch_daemon = WatchFolderDaemon(folder_path, _on_new_watched_file)
            self.watch_daemon.start()
            self.append_log(f"Started watching folder: {folder_path}")
            self.tray_manager.send_notification("Watch Folder Active", f"Monitoring {folder_path} for new videos.")
        else:
            if self.watch_daemon:
                self.watch_daemon.stop()
                self.watch_daemon = None
                self.append_log("Stopped watching folder.")

    def _minimize_to_tray(self) -> None:
        """Minimizes the window, keeping background tasks running."""
        self.withdraw()
        self.tray_manager.send_notification("Transcoder Minimized", "App is running in the background. Check system tray.")

    def _show_window_from_tray(self) -> None:
        """Restores the window from tray."""
        self.after(0, self.deiconify)

    def _exit_app(self) -> None:
        """Fully exits the application."""
        if self.watch_daemon:
            self.watch_daemon.stop()
        self.tray_manager.stop()
        self._on_close()

    def _on_close(self) -> None:
        """Handles window close button cleanly."""
        try:
            self.queue_manager.clear_queue(cancel_active=True)
            self.queue_manager.shutdown(wait=False)
        except Exception:
            pass
        self.destroy()

    def destroy(self) -> None:
        try:
            self.queue_manager.shutdown(wait=False)
        except Exception:
            pass
        super().destroy()


def run_gui(initial_paths: Optional[List[Path]] = None) -> None:
    """Entry point for GUI mode."""
    app = EnterpriseConverterGui(initial_paths=initial_paths)
    app.mainloop()

