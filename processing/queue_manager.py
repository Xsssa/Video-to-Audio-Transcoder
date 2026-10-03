"""
queue_manager.py - Enterprise-grade Batch Queue Manager for Video-to-Audio Transcoding.

Features:
- Concurrent job scheduling via thread pool executor with configurable max_workers
- Real-time event notifications (added, started, progress, completed, failed, queue_completed)
- Fine-grained queue control: pause, resume, cancel (with active process termination), clear
- Automatic source probing (ffprobe) and output integrity verification
- Thread-safe state transitions and progress aggregation
- Lossless stream copy optimization when container and codec match
- Cover art preservation and loudness normalization (EBU R128) support
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg, load_config
from core.probe import MediaProbeResult, probe_media
from processing.progress_tracker import FFmpegProgressTracker, ProgressSnapshot
from processing.verifier import VerificationResult, is_codec_compatible, verify_conversion

logger = logging.getLogger("queue_manager")


class TaskStatus(str, Enum):
    """Lifecycle status of a conversion task."""
    PENDING = "PENDING"
    PROBING = "PROBING"
    CONVERTING = "CONVERTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Default audio codec mappings for standard container formats
AUDIO_CODEC_MAP: dict[str, str] = {
    "mp3": "libmp3lame",
    "aac": "aac",
    "m4a": "aac",
    "flac": "flac",
    "ogg": "libvorbis",
    "opus": "libopus",
    "wav": "pcm_s16le",
    "wma": "wmav2",
    "ac3": "ac3",
    "alac": "alac",
}


@dataclass
class ConversionTask:
    """
    Represents an individual video-to-audio conversion task in the batch queue.
    """
    task_id: str
    source_file: Path
    target_format: str
    output_file: Optional[Path] = None
    options: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0          # 0.0 to 100.0
    speed: str = "0.0x"
    eta: str = "--:--"
    error: Optional[str] = None

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_seconds: float = 0.0

    input_size_bytes: int = 0
    output_size_bytes: int = 0

    probe_result: Optional[MediaProbeResult] = None
    verification_result: Optional[VerificationResult] = None
    ffmpeg_cmd: Optional[list[str]] = None
    traceback: Optional[str] = None

    # Internal state flag
    _cancel_requested: bool = field(default=False, repr=False)

    @property
    def is_terminal(self) -> bool:
        """Indicates whether task has reached a final state."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def to_dict(self) -> dict[str, Any]:
        """Serializes task state to dictionary."""
        return {
            "task_id": self.task_id,
            "source_file": str(self.source_file),
            "target_format": self.target_format,
            "output_file": str(self.output_file) if self.output_file else None,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "input_size_bytes": self.input_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "ffmpeg_cmd": self.ffmpeg_cmd,
            "traceback": self.traceback,
        }


@dataclass(frozen=True)
class QueueStats:
    """Aggregated real-time metrics of the queue manager."""
    total_tasks: int
    pending_tasks: int
    probing_tasks: int
    converting_tasks: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    is_paused: bool
    is_running: bool
    progress_percent: float
    elapsed_time_seconds: float
    max_workers: int

    @property
    def active_tasks(self) -> int:
        """Count of tasks currently being processed."""
        return self.probing_tasks + self.converting_tasks


class QueueManager:
    """
    Enterprise Batch Queue Manager for concurrent media transcoding.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        default_output_dir: Optional[str | Path] = None,
        config_path: Optional[str | Path] = None,
        ffmpeg_path: Optional[str | Path] = None,
        ffprobe_path: Optional[str | Path] = None,
        on_task_added: Optional[Callable[[ConversionTask], None]] = None,
        on_task_started: Optional[Callable[[ConversionTask], None]] = None,
        on_task_progress: Optional[Callable[[ConversionTask, ProgressSnapshot], None]] = None,
        on_task_completed: Optional[Callable[[ConversionTask, VerificationResult], None]] = None,
        on_task_failed: Optional[Callable[[ConversionTask, str], None]] = None,
        on_queue_completed: Optional[Callable[[QueueStats], None]] = None,
    ) -> None:
        """
        Initializes the batch queue manager.

        Args:
            max_workers: Number of concurrent worker threads. Defaults to config or CPU count.
            default_output_dir: Default destination directory for audio outputs.
            config_path: Optional custom path to config.json.
            ffmpeg_path: Optional custom path to ffmpeg binary.
            ffprobe_path: Optional custom path to ffprobe binary.
            on_task_added: Callback when a task is registered.
            on_task_started: Callback when a task starts processing.
            on_task_progress: Callback on real-time task progress updates.
            on_task_completed: Callback when a task finishes and passes integrity verification.
            on_task_failed: Callback when a task encounters an unrecoverable failure.
            on_queue_completed: Callback when all tasks in the queue have reached terminal state.
        """
        self.config_path = config_path
        self._cfg = load_config(config_path)

        # Worker count resolution
        if max_workers is not None and max_workers > 0:
            self.max_workers = max_workers
        elif "threads" in self._cfg and isinstance(self._cfg["threads"], int):
            self.max_workers = max(1, self._cfg["threads"])
        else:
            self.max_workers = max(1, min(16, (os.cpu_count() or 4)))

        # Output directory resolution
        cfg_out = self._cfg.get("default_output_dir")
        if default_output_dir:
            self.default_output_dir = Path(default_output_dir).resolve()
        elif cfg_out:
            self.default_output_dir = Path(cfg_out).resolve()
        else:
            self.default_output_dir = Path.cwd() / "output"

        self.ffmpeg_path = ffmpeg_path or self._cfg.get("ffmpeg_path")
        self.ffprobe_path = ffprobe_path or self._cfg.get("ffprobe_path")

        # Callbacks
        self.on_task_added = on_task_added
        self.on_task_started = on_task_started
        self.on_task_progress = on_task_progress
        self.on_task_completed = on_task_completed
        self.on_task_failed = on_task_failed
        self.on_queue_completed = on_queue_completed

        # Multicast callback lists for advanced subscribers
        self._listeners: dict[str, list[Callable[..., None]]] = {
            "task_added": [],
            "task_started": [],
            "task_progress": [],
            "task_completed": [],
            "task_failed": [],
            "queue_completed": [],
        }

        # Internal state & synchronization
        self._lock = threading.RLock()
        self._tasks: dict[str, ConversionTask] = {}
        self._pending_queue: deque[str] = deque()
        self._active_tasks: set[str] = set()
        self._active_processes: dict[str, subprocess.Popen[Any]] = {}

        self._pause_event = threading.Event()
        self._pause_event.set()  # Initially unpaused
        self._shutdown_event = threading.Event()

        self._is_paused = False
        self._start_time = time.monotonic()
        self._queue_completed_fired = False

        # Executor & Dispatcher thread
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="TranscodeWorker",
        )
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            name="QueueDispatcher",
            daemon=True,
        )
        self._dispatcher_thread.start()

    # -------------------------------------------------------------------------
    # Callback Subscription
    # -------------------------------------------------------------------------
    def register_callback(self, event_name: str, callback: Callable[..., None]) -> None:
        """Subscribes an additional listener to a specific queue event."""
        with self._lock:
            if event_name in self._listeners:
                self._listeners[event_name].append(callback)
            else:
                raise ValueError(f"Unknown event name: {event_name}. Allowed: {list(self._listeners.keys())}")

    def _safe_call(self, direct_cb: Optional[Callable[..., None]], event_name: str, *args: Any) -> None:
        """Safely invokes user callbacks without risking worker thread crashes."""
        if direct_cb:
            try:
                direct_cb(*args)
            except Exception as exc:
                logger.error("Error in direct callback %s: %s", event_name, exc, exc_info=True)

        with self._lock:
            listeners = list(self._listeners.get(event_name, []))

        for listener in listeners:
            try:
                listener(*args)
            except Exception as exc:
                logger.error("Error in listener callback %s: %s", event_name, exc, exc_info=True)

    # -------------------------------------------------------------------------
    # Task Management & Controls
    # -------------------------------------------------------------------------
    def add_task(
        self,
        source_file: str | Path,
        target_format: str = "mp3",
        output_file: Optional[str | Path] = None,
        options: Optional[dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> ConversionTask:
        """
        Adds a new conversion task to the batch queue.

        Args:
            source_file: Path to source media file.
            target_format: Audio format (e.g. 'mp3', 'aac', 'flac', 'wav').
            output_file: Destination path (auto-generated if None).
            options: Conversion tuning options (bitrate, sample_rate, channels, ebu_r128, etc.).
            task_id: Optional unique task ID (UUID generated if None).

        Returns:
            ConversionTask instance.
        """
        src = Path(source_file).resolve()
        fmt = target_format.strip().lstrip(".").lower()
        tid = task_id or f"task_{uuid.uuid4().hex[:8]}"

        opts = dict(options) if options else {}

        # Resolve output destination if provided
        dest_path: Optional[Path] = None
        if output_file:
            dest_path = Path(output_file).resolve()

        task = ConversionTask(
            task_id=tid,
            source_file=src,
            target_format=fmt,
            output_file=dest_path,
            options=opts,
            status=TaskStatus.PENDING,
        )

        with self._lock:
            self._tasks[tid] = task
            self._pending_queue.append(tid)
            self._queue_completed_fired = False

        self._safe_call(self.on_task_added, "task_added", task)
        return task

    def add_tasks(self, task_definitions: Sequence[dict[str, Any] | ConversionTask]) -> list[ConversionTask]:
        """Bulk-registers multiple conversion tasks."""
        added: list[ConversionTask] = []
        for item in task_definitions:
            if isinstance(item, ConversionTask):
                with self._lock:
                    self._tasks[item.task_id] = item
                    self._pending_queue.append(item.task_id)
                    self._queue_completed_fired = False
                self._safe_call(self.on_task_added, "task_added", item)
                added.append(item)
            elif isinstance(item, dict):
                t = self.add_task(**item)
                added.append(t)
        return added

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancels a task if pending or actively running.

        Returns:
            True if task was found and marked cancelled, False otherwise.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.is_terminal:
                return False

            task._cancel_requested = True
            task.status = TaskStatus.CANCELLED
            task.error = "Conversion cancelled by user"
            task.completed_at = time.time()
            if task.started_at:
                task.duration_seconds = task.completed_at - task.started_at

            # If pending in queue, remove
            try:
                self._pending_queue.remove(task_id)
            except ValueError:
                pass

            # If actively executing in a subprocess, terminate it
            proc = self._active_processes.get(task_id)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._safe_call(self.on_task_failed, "task_failed", task, "Cancelled")
        self._check_queue_completion()
        return True

    def remove_task(self, task_id: str) -> bool:
        """
        Removes a task from the queue entirely.
        If the task is active or pending, cancels it first.

        Returns:
            True if task was found and removed, False otherwise.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if not task.is_terminal:
                self.cancel_task(task_id)

            try:
                self._pending_queue.remove(task_id)
            except ValueError:
                pass

            self._tasks.pop(task_id, None)

        self._check_queue_completion()
        return True

    def move_task_up(self, task_id: str) -> bool:
        """
        Moves a task up by one position in the queue.

        Returns:
            True if moved, False if already at top or not found.
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

            # Reorder in pending queue if present
            try:
                idx = self._pending_queue.index(task_id)
                if idx > 0:
                    self._pending_queue[idx], self._pending_queue[idx - 1] = (
                        self._pending_queue[idx - 1],
                        self._pending_queue[idx],
                    )
            except ValueError:
                pass

            # Reorder in _tasks dict to preserve display order
            keys = list(self._tasks.keys())
            try:
                k_idx = keys.index(task_id)
                if k_idx > 0:
                    keys[k_idx], keys[k_idx - 1] = keys[k_idx - 1], keys[k_idx]
                    self._tasks = {k: self._tasks[k] for k in keys}
                    return True
            except ValueError:
                pass

        return False

    def move_task_down(self, task_id: str) -> bool:
        """
        Moves a task down by one position in the queue.

        Returns:
            True if moved, False if already at bottom or not found.
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

            # Reorder in pending queue if present
            try:
                idx = self._pending_queue.index(task_id)
                if idx < len(self._pending_queue) - 1:
                    self._pending_queue[idx], self._pending_queue[idx + 1] = (
                        self._pending_queue[idx + 1],
                        self._pending_queue[idx],
                    )
            except ValueError:
                pass

            # Reorder in _tasks dict to preserve display order
            keys = list(self._tasks.keys())
            try:
                k_idx = keys.index(task_id)
                if k_idx < len(keys) - 1:
                    keys[k_idx], keys[k_idx + 1] = keys[k_idx + 1], keys[k_idx]
                    self._tasks = {k: self._tasks[k] for k in keys}
                    return True
            except ValueError:
                pass

        return False

    def pause_queue(self) -> None:
        """Pauses dispatching new tasks to workers. Currently active tasks finish."""
        with self._lock:
            self._is_paused = True
            self._pause_event.clear()

    def resume_queue(self) -> None:
        """Resumes dispatching tasks to worker threads."""
        with self._lock:
            self._is_paused = False
            self._pause_event.set()

    def clear_queue(self, cancel_active: bool = False) -> list[ConversionTask]:
        """
        Clears all pending tasks from the queue.
        If cancel_active is True, also terminates currently running tasks.

        Returns:
            List of cancelled tasks.
        """
        cancelled_tasks: list[ConversionTask] = []

        with self._lock:
            # Cancel all pending tasks
            while self._pending_queue:
                tid = self._pending_queue.popleft()
                t = self._tasks.get(tid)
                if t and not t.is_terminal:
                    t.status = TaskStatus.CANCELLED
                    t.error = "Cancelled due to queue clear"
                    cancelled_tasks.append(t)

            # Optionally cancel running tasks
            if cancel_active:
                for tid in list(self._active_tasks):
                    t = self._tasks.get(tid)
                    if t and not t.is_terminal:
                        t._cancel_requested = True
                        t.status = TaskStatus.CANCELLED
                        t.error = "Cancelled due to queue clear"
                        cancelled_tasks.append(t)
                    proc = self._active_processes.get(tid)
                    if proc:
                        try:
                            proc.kill()
                        except Exception:
                            pass

        for c_task in cancelled_tasks:
            self._safe_call(self.on_task_failed, "task_failed", c_task, "Cancelled (Queue Cleared)")

        self._check_queue_completion()
        return cancelled_tasks

    def get_task(self, task_id: str) -> Optional[ConversionTask]:
        """Retrieves a task by its ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[ConversionTask]:
        """Returns snapshot of all tracked tasks."""
        with self._lock:
            return list(self._tasks.values())

    def get_stats(self) -> QueueStats:
        """Computes real-time queue metrics and progress."""
        with self._lock:
            total = len(self._tasks)
            pending = sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)
            probing = sum(1 for t in self._tasks.values() if t.status == TaskStatus.PROBING)
            converting = sum(1 for t in self._tasks.values() if t.status == TaskStatus.CONVERTING)
            completed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)
            failed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED)
            cancelled = sum(1 for t in self._tasks.values() if t.status == TaskStatus.CANCELLED)

            is_running = (probing + converting) > 0 or (pending > 0 and not self._is_paused)

            if total > 0:
                progress_sum = sum(t.progress for t in self._tasks.values())
                progress_percent = progress_sum / total
            else:
                progress_percent = 0.0

            elapsed = time.monotonic() - self._start_time

            return QueueStats(
                total_tasks=total,
                pending_tasks=pending,
                probing_tasks=probing,
                converting_tasks=converting,
                completed_tasks=completed,
                failed_tasks=failed,
                cancelled_tasks=cancelled,
                is_paused=self._is_paused,
                is_running=is_running,
                progress_percent=round(progress_percent, 1),
                elapsed_time_seconds=round(elapsed, 2),
                max_workers=self.max_workers,
            )

    # -------------------------------------------------------------------------
    # Dispatcher & Worker Execution
    # -------------------------------------------------------------------------
    def _dispatcher_loop(self) -> None:
        """Background dispatcher that assigns pending tasks to the thread pool."""
        while not self._shutdown_event.is_set():
            # Wait if paused
            self._pause_event.wait(timeout=0.1)
            if self._shutdown_event.is_set():
                break

            task_to_launch: Optional[str] = None

            with self._lock:
                if (
                    not self._is_paused
                    and self._pending_queue
                    and len(self._active_tasks) < self.max_workers
                ):
                    task_to_launch = self._pending_queue.popleft()
                    self._active_tasks.add(task_to_launch)

            if task_to_launch:
                self._executor.submit(self._run_task, task_to_launch)
            else:
                time.sleep(0.05)

    def _run_task(self, task_id: str) -> None:
        """Executes a single conversion task end-to-end."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task or task._cancel_requested:
            with self._lock:
                self._active_tasks.discard(task_id)
            self._check_queue_completion()
            return

        task.started_at = time.time()

        try:
            # 1. Probing Phase
            with self._lock:
                task.status = TaskStatus.PROBING
            self._safe_call(self.on_task_started, "task_started", task)

            if not task.source_file.exists():
                raise FileNotFoundError(f"Source file not found: {task.source_file}")

            task.input_size_bytes = task.source_file.stat().st_size
            probe_result = probe_media(task.source_file, ffprobe_path=self.ffprobe_path)
            task.probe_result = probe_result

            if not probe_result.has_audio:
                raise ValueError(f"No audio stream detected in source file: {task.source_file}")

            # Resolve output file if not pre-set
            if not task.output_file:
                target_dir = self.default_output_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                ext = task.target_format.lstrip(".").lower()
                task.output_file = target_dir / f"{task.source_file.stem}.{ext}"
            else:
                task.output_file.parent.mkdir(parents=True, exist_ok=True)

            if task._cancel_requested:
                raise InterruptedError("Task cancelled before conversion started.")

            # 2. Conversion Phase
            with self._lock:
                task.status = TaskStatus.CONVERTING

            ffmpeg_bin = find_ffmpeg(custom_path=self.ffmpeg_path, config_path=self.config_path)
            cmd = self._build_ffmpeg_command(task, ffmpeg_bin)
            task.ffmpeg_cmd = list(cmd)

            tracker = FFmpegProgressTracker(
                total_duration=probe_result.duration,
                start_time=time.monotonic(),
            )

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_get_creation_flags(),
            )

            with self._lock:
                self._active_processes[task_id] = proc

            # Real-time stdout progress monitoring
            last_notify_time = 0.0
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    if task._cancel_requested:
                        proc.kill()
                        break

                    snapshot = tracker.feed_line(line)
                    if snapshot:
                        task.progress = snapshot.percent
                        task.speed = snapshot.speed_str
                        task.eta = snapshot.eta_str

                        # Throttle callbacks slightly to avoid saturation (max 20 per sec)
                        now = time.monotonic()
                        if now - last_notify_time >= 0.05 or snapshot.is_completed:
                            last_notify_time = now
                            self._safe_call(self.on_task_progress, "task_progress", task, snapshot)

            stderr_out, _ = proc.communicate()

            with self._lock:
                self._active_processes.pop(task_id, None)

            if task._cancel_requested:
                raise InterruptedError("Task cancelled during conversion.")

            if proc.returncode != 0:
                err_msg = stderr_out.strip() or f"FFmpeg exited with error code {proc.returncode}"
                raise RuntimeError(err_msg)

            # 3. Post-conversion Integrity Verification Phase
            expected_dur = probe_result.duration
            ver_result = verify_conversion(
                output_file=task.output_file,
                target_format=task.target_format,
                expected_duration=expected_dur,
                tolerance_seconds=1.5,
                ffprobe_path=self.ffprobe_path,
            )
            task.verification_result = ver_result

            if not ver_result.is_valid:
                raise ValueError("; ".join(ver_result.errors))

            # Mark task completion
            with self._lock:
                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
                task.speed = tracker.current_snapshot.speed_str
                task.eta = "00:00"
                task.completed_at = time.time()
                task.duration_seconds = task.completed_at - task.started_at
                task.output_size_bytes = (
                    task.output_file.stat().st_size
                    if task.output_file.exists()
                    else 0
                )

            self._safe_call(self.on_task_completed, "task_completed", task, ver_result)

        except InterruptedError:
            with self._lock:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                if task.started_at:
                    task.duration_seconds = task.completed_at - task.started_at
            # Clean up partial output if cancelled
            if task.output_file and task.output_file.exists():
                try:
                    task.output_file.unlink()
                except OSError:
                    pass
            self._safe_call(self.on_task_failed, "task_failed", task, "Cancelled")

        except Exception as exc:
            import traceback as _tb
            err_str = str(exc)
            tb_str = _tb.format_exc()
            with self._lock:
                task.status = TaskStatus.FAILED
                task.error = err_str
                task.traceback = tb_str
                task.completed_at = time.time()
                if task.started_at:
                    task.duration_seconds = task.completed_at - task.started_at
            # Clean up corrupted output
            if task.output_file and task.output_file.exists():
                try:
                    task.output_file.unlink()
                except OSError:
                    pass
            self._safe_call(self.on_task_failed, "task_failed", task, err_str)

        finally:
            with self._lock:
                self._active_tasks.discard(task_id)
                self._active_processes.pop(task_id, None)
            self._check_queue_completion()

    def _build_ffmpeg_command(self, task: ConversionTask, ffmpeg_bin: Path) -> list[str]:
        """
        Constructs the FFmpeg command line according to task options.
        """
        opts = task.options
        fmt = task.target_format.lower()
        probe = task.probe_result

        cmd: list[str] = [
            str(ffmpeg_bin),
            "-y",                   # Overwrite output
            "-nostdin",             # Disable interactive stdin
            "-progress", "pipe:1",  # Output progress metrics to stdout
            "-nostats",             # Avoid stderr progress spam
            "-i", str(task.source_file),
        ]

        # Audio stream selection
        audio_stream_idx = opts.get("audio_stream_index")
        if audio_stream_idx is not None:
            cmd.extend(["-map", f"0:{audio_stream_idx}"])
        else:
            cmd.extend(["-map", "0:a:0"])

        # Check for lossless direct stream copy if requested and format matches
        lossless_copy = opts.get("lossless_copy_if_match", self._cfg.get("lossless_copy_if_match", False))
        can_copy = False
        if lossless_copy and probe and probe.primary_audio_stream:
            src_codec = probe.primary_audio_stream.codec_name
            if is_codec_compatible(fmt, src_codec) and "bitrate" not in opts and "sample_rate" not in opts:
                can_copy = True

        if can_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            # Codec selection
            custom_codec = opts.get("codec") or opts.get("audio_codec")
            codec = custom_codec or AUDIO_CODEC_MAP.get(fmt, "aac")
            cmd.extend(["-c:a", str(codec)])

            # Bitrate
            bitrate = opts.get("bitrate")
            if bitrate:
                b_str = str(bitrate)
                if b_str.isdigit():
                    b_int = int(b_str)
                    if fmt == "opus" and b_int > 256000:
                        b_int = 256000
                    b_str = f"{b_int // 1000}k" if b_int > 1000 else f"{b_int}k"
                elif fmt == "opus" and ("320" in b_str or "512" in b_str):
                    b_str = "256k"
                cmd.extend(["-b:a", b_str])

            # Sample Rate
            sample_rate = opts.get("sample_rate")
            if sample_rate:
                cmd.extend(["-ar", str(sample_rate)])

            # Channels
            channels = opts.get("channels")
            if channels:
                cmd.extend(["-ac", str(channels)])

            # Audio filters (Loudness normalization / EBU R128)
            audio_filters: list[str] = []
            ebu_r128 = opts.get("ebu_r128", self._cfg.get("ebu_r128", False))
            if ebu_r128:
                audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

            custom_filter = opts.get("audio_filter") or opts.get("filter:a")
            if custom_filter:
                audio_filters.append(str(custom_filter))

            if audio_filters:
                cmd.extend(["-af", ",".join(audio_filters)])

        # Metadata preservation
        cmd.extend(["-map_metadata", "0"])

        # Cover art handling: preserve attached picture if supported by format
        preserve_cover = opts.get("preserve_cover_art", True)
        if (
            preserve_cover
            and probe
            and probe.has_cover_art
            and fmt in ("mp3", "m4a", "flac")
            and probe.cover_art_stream_index is not None
        ):
            cmd.extend(["-map", f"0:{probe.cover_art_stream_index}"])
            cmd.extend(["-c:v", "copy"])
            if fmt == "mp3":
                cmd.extend(["-id3v2_version", "3"])
                cmd.extend(["-metadata:s:v", 'title="Album cover"'])
                cmd.extend(["-metadata:s:v", 'comment="Cover (front)"'])
        else:
            # Strip video streams completely
            cmd.append("-vn")

        # Custom arguments if provided
        custom_args = opts.get("custom_args")
        if custom_args:
            if isinstance(custom_args, list):
                cmd.extend(custom_args)
            elif isinstance(custom_args, str):
                cmd.extend(custom_args.split())

        cmd.append(str(task.output_file))
        return cmd

    def _check_queue_completion(self) -> None:
        """Evaluates whether all tasks in the batch have reached a terminal state."""
        fire_completion = False
        stats: Optional[QueueStats] = None

        with self._lock:
            if not self._queue_completed_fired and len(self._tasks) > 0:
                all_done = all(t.is_terminal for t in self._tasks.values())
                if all_done and not self._pending_queue and not self._active_tasks:
                    self._queue_completed_fired = True
                    fire_completion = True
                    stats = self.get_stats()

        if fire_completion and stats:
            self._safe_call(self.on_queue_completed, "queue_completed", stats)

    def wait_completion(self, timeout: Optional[float] = None) -> bool:
        """
        Blocks until all submitted tasks have completed, failed, or been cancelled.

        Args:
            timeout: Maximum wait time in seconds (None for indefinite).

        Returns:
            True if all tasks finished, False if timeout occurred.
        """
        start_wait = time.monotonic()
        while True:
            with self._lock:
                if len(self._tasks) > 0 and all(t.is_terminal for t in self._tasks.values()):
                    return True
                if len(self._tasks) == 0:
                    return True

            if timeout is not None and (time.monotonic() - start_wait) >= timeout:
                return False

            time.sleep(0.1)

    def stop(self, cancel_running: bool = False, timeout: Optional[float] = 5.0) -> None:
        """
        Gracefully terminates the queue manager and executor pool.
        """
        self._shutdown_event.set()
        self._pause_event.set()

        if cancel_running:
            self.clear_queue(cancel_active=True)

        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=timeout)

    def shutdown(self, wait: bool = True, cancel_running: bool = False, timeout: Optional[float] = 5.0) -> None:
        """Alias for stop() accepting wait for standard executor compatibility."""
        self.stop(cancel_running=cancel_running, timeout=timeout)


