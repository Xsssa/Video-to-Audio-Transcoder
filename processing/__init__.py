"""
processing package - Enterprise Video-to-Audio Transcoder Processing Subsystem.

Provides:
- progress_tracker: Real-time FFmpeg pipe:1 progress parser, ETA, and speed factor calculator.
- queue_manager: Concurrent multithreaded batch transcoding queue manager.
- verifier: Post-conversion audio stream integrity, duration, and codec compliance verifier.
- reporter: JSON/CSV batch analytics export and formatted ASCII summary reporter.
"""

from processing.progress_tracker import (
    FFmpegProgressTracker,
    ProgressSnapshot,
    format_seconds_to_eta,
    parse_progress_stream,
    parse_time_string_to_seconds,
)
from processing.queue_manager import (
    AUDIO_CODEC_MAP,
    ConversionTask,
    QueueManager,
    QueueStats,
    TaskStatus,
)
from processing.reporter import (
    BatchReport,
    TaskReportItem,
    create_report_from_tasks,
    format_bytes_human,
    format_duration_human,
)
from processing.verifier import (
    FORMAT_CODEC_MAP,
    IntegrityVerifier,
    VerificationResult,
    is_codec_compatible,
    verify_conversion,
)

__all__ = [
    # Progress Tracker
    "FFmpegProgressTracker",
    "ProgressSnapshot",
    "format_seconds_to_eta",
    "parse_progress_stream",
    "parse_time_string_to_seconds",
    # Queue Manager
    "QueueManager",
    "ConversionTask",
    "TaskStatus",
    "QueueStats",
    "AUDIO_CODEC_MAP",
    # Verifier
    "IntegrityVerifier",
    "VerificationResult",
    "verify_conversion",
    "is_codec_compatible",
    "FORMAT_CODEC_MAP",
    # Reporter
    "BatchReport",
    "TaskReportItem",
    "create_report_from_tasks",
    "format_bytes_human",
    "format_duration_human",
]
