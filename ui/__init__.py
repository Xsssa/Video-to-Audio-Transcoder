"""
Enterprise Video to Audio Converter - UI & Input Package.
Provides smart CLI input parsing, rich terminal UI (TUI), and modern desktop GUI.
"""

from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    InputParser,
    expand_path,
    filter_video_files,
    get_clipboard_files,
    is_supported_video,
    parse_input_paths,
    parse_tokens,
    strip_quotes,
)
from ui.cli_tui import (
    AudioFilterChain,
    CLI_PRESETS,
    CliPreset,
    CliTui,
    ENTERPRISE_PRESETS,
    run_cli_tui,
)
from ui.gui_app import EnterpriseConverterGui, run_gui
from ui.ascii_visualizer import AsciiVisualizer, is_unicode_supported
from ui.dashboard_layout import (
    DashboardEvent,
    DashboardLayout,
    DashboardState,
    EventLogStream,
    HardwareTelemetry,
    MediaInspectorData,
    SystemTelemetry,
    TerminalDashboardLayout,
    VUMeter,
    render_dashboard,
)

from ui.tui_theme import (
    THEME_COLORS,
    TUI_CSS,
    SLATE_DARK_THEME,
    apply_tui_theme,
    get_status_badge_markup,
    get_status_style,
    get_spectrum_palette,
    get_vu_meter_color,
)

__version__ = "2.0.0"

__all__ = [
    "THEME_COLORS",
    "TUI_CSS",
    "SLATE_DARK_THEME",
    "apply_tui_theme",
    "get_status_badge_markup",
    "get_status_style",
    "get_spectrum_palette",
    "get_vu_meter_color",
    "SUPPORTED_VIDEO_EXTENSIONS",
    "InputParser",
    "expand_path",
    "filter_video_files",
    "get_clipboard_files",
    "is_supported_video",
    "parse_input_paths",
    "parse_tokens",
    "strip_quotes",
    "AudioFilterChain",
    "CLI_PRESETS",
    "CliPreset",
    "CliTui",
    "ENTERPRISE_PRESETS",
    "run_cli_tui",
    "EnterpriseConverterGui",
    "run_gui",
    "AsciiVisualizer",
    "is_unicode_supported",
    "DashboardEvent",
    "DashboardLayout",
    "DashboardState",
    "EventLogStream",
    "HardwareTelemetry",
    "MediaInspectorData",
    "SystemTelemetry",
    "TerminalDashboardLayout",
    "VUMeter",
    "render_dashboard",
    "__version__",
]
