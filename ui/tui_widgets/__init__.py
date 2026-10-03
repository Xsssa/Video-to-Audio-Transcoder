"""
ui/tui_widgets package - Modular Textual TUI widgets for Video to Audio Transcoder.
"""

from ui.tui_widgets.queue_table import (
    COLUMN_HEADERS,
    COLUMN_KEYS,
    QueueDataTable,
    QueueStatsHeader,
    QueueSummaryFooter,
    QueueTableWidget,
    TaskDeleted,
    TaskDetailModal,
    TaskDetailsRequested,
    TaskMoved,
    TaskSelected,
    format_duration_hms,
    format_file_size,
    format_status_indicator,
)
from ui.tui_widgets.resource_monitor import (
    ResourceMonitorWidget,
    ResourceTelemetrySnapshot,
)
from ui.tui_widgets.status_bar import TUIStatusBar
from ui.tui_widgets.visualizer_widget import (
    AudioVisualizerWidget,
    VisualizerMode,
)

VisualizerWidget = AudioVisualizerWidget

__all__ = [
    # Status Bar
    "TUIStatusBar",
    # Queue Table
    "COLUMN_HEADERS",
    "COLUMN_KEYS",
    "QueueDataTable",
    "QueueStatsHeader",
    "QueueSummaryFooter",
    "QueueTableWidget",
    "TaskDeleted",
    "TaskDetailModal",
    "TaskDetailsRequested",
    "TaskMoved",
    "TaskSelected",
    "format_duration_hms",
    "format_file_size",
    "format_status_indicator",
    # Resource Monitor
    "ResourceMonitorWidget",
    "ResourceTelemetrySnapshot",
    # Visualizer
    "AudioVisualizerWidget",
    "VisualizerMode",
    "VisualizerWidget",
]
