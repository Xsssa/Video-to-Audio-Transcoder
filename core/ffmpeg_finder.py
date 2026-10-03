"""
ffmpeg_finder.py - Robust locator and validator for FFmpeg and FFprobe binaries.

Prioritizes:
1. Custom path explicitly passed in function calls.
2. config.json in the current working directory, workspace root, or specified path.
3. Specific binary path at 'F:\\discord bot\\bin\\'.
4. Environment variables (FFMPEG_PATH, FFPROBE_PATH, FFMPEG_DIR, FFMPEG_BIN).
5. System PATH environment variable.
6. Common default installation directories on Windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class FFmpegNotFoundError(FileNotFoundError):
    """Raised when FFmpeg binary cannot be located."""
    pass


class FFprobeNotFoundError(FileNotFoundError):
    """Raised when FFprobe binary cannot be located."""
    pass


class FFmpegExecutionError(RuntimeError):
    """Raised when FFmpeg or FFprobe fails during execution or version check."""
    pass


@dataclass(frozen=True)
class FFmpegVerification:
    """Represents the verification result of FFmpeg and FFprobe binaries."""
    is_valid: bool
    ffmpeg_path: Optional[str]
    ffprobe_path: Optional[str]
    ffmpeg_version: Optional[str]
    ffprobe_version: Optional[str]
    error_message: Optional[str] = None

    def __bool__(self) -> bool:
        return self.is_valid


def _get_creation_flags() -> int:
    """Returns CREATE_NO_WINDOW flag on Windows to prevent console windows popping up."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def find_config_file(explicit_path: Optional[str | Path] = None) -> Optional[Path]:
    """
    Search for config.json starting from explicit path, current working directory,
    or parent directories.
    """
    if explicit_path:
        p = Path(explicit_path)
        if p.is_file():
            return p.resolve()

    candidates = [
        Path.cwd() / "config.json",
        Path(__file__).resolve().parent.parent / "config.json",
    ]

    for cand in candidates:
        if cand.is_file():
            return cand.resolve()

    return None


def load_config(config_path: Optional[str | Path] = None) -> dict[str, Any]:
    """
    Load configuration from config.json, returning an empty dict if not found or invalid.
    """
    cfg_file = find_config_file(config_path)
    if not cfg_file:
        return {}

    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_candidate(candidate: Optional[str | Path]) -> Optional[Path]:
    """Check if candidate path exists and is an executable file."""
    if not candidate:
        return None
    p = Path(candidate).expanduser()
    if p.is_file():
        return p.resolve()
    return None


def find_ffmpeg(
    custom_path: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
) -> Path:
    """
    Locates the ffmpeg executable using a strict multi-tier fallback mechanism.

    Priority order:
    1. Explicit custom_path argument.
    2. 'ffmpeg_path' defined in config.json.
    3. F:\\discord bot\\bin\\ffmpeg.exe.
    4. Environment variable FFMPEG_PATH or FFMPEG_DIR/ffmpeg.exe.
    5. System PATH via shutil.which("ffmpeg").
    6. Common Windows paths (e.g. C:\\ffmpeg\\bin\\ffmpeg.exe).

    Returns:
        Path: Absolute resolved path to ffmpeg.

    Raises:
        FFmpegNotFoundError: If no valid ffmpeg executable is found.
    """
    # 1. Custom path
    resolved = _resolve_candidate(custom_path)
    if resolved:
        return resolved

    # 2. Config.json
    cfg = load_config(config_path)
    cfg_ffmpeg = cfg.get("ffmpeg_path")
    resolved = _resolve_candidate(cfg_ffmpeg)
    if resolved:
        return resolved

    # 3. Known preferred directory
    discord_bot_ffmpeg = Path(r"F:\discord bot\bin\ffmpeg.exe")
    resolved = _resolve_candidate(discord_bot_ffmpeg)
    if resolved:
        return resolved

    # 4. Environment variables
    env_ffmpeg = os.environ.get("FFMPEG_PATH")
    resolved = _resolve_candidate(env_ffmpeg)
    if resolved:
        return resolved

    env_ffmpeg_dir = os.environ.get("FFMPEG_DIR") or os.environ.get("FFMPEG_BIN")
    if env_ffmpeg_dir:
        exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        resolved = _resolve_candidate(Path(env_ffmpeg_dir) / exe_name)
        if resolved:
            return resolved

    # 5. System PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return Path(which_ffmpeg).resolve()

    # 6. Common Windows fallbacks
    common_fallbacks = [
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"),
        Path(r"D:\ffmpeg\bin\ffmpeg.exe"),
    ]
    for fallback in common_fallbacks:
        resolved = _resolve_candidate(fallback)
        if resolved:
            return resolved

    raise FFmpegNotFoundError(
        "Could not find ffmpeg executable. Please configure 'ffmpeg_path' in config.json "
        "or ensure ffmpeg is available in system PATH or at 'F:\\discord bot\\bin\\ffmpeg.exe'."
    )


def find_ffprobe(
    custom_path: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
) -> Path:
    """
    Locates the ffprobe executable using a strict multi-tier fallback mechanism.

    Priority order:
    1. Explicit custom_path argument.
    2. 'ffprobe_path' defined in config.json.
    3. F:\\discord bot\\bin\\ffprobe.exe.
    4. Environment variable FFPROBE_PATH or FFMPEG_DIR/ffprobe.exe.
    5. Same directory as found ffmpeg binary.
    6. System PATH via shutil.which("ffprobe").
    7. Common Windows paths.

    Returns:
        Path: Absolute resolved path to ffprobe.

    Raises:
        FFprobeNotFoundError: If no valid ffprobe executable is found.
    """
    # 1. Custom path
    resolved = _resolve_candidate(custom_path)
    if resolved:
        return resolved

    # 2. Config.json
    cfg = load_config(config_path)
    cfg_ffprobe = cfg.get("ffprobe_path")
    resolved = _resolve_candidate(cfg_ffprobe)
    if resolved:
        return resolved

    # 3. Known preferred directory
    discord_bot_ffprobe = Path(r"F:\discord bot\bin\ffprobe.exe")
    resolved = _resolve_candidate(discord_bot_ffprobe)
    if resolved:
        return resolved

    # 4. Environment variables
    env_ffprobe = os.environ.get("FFPROBE_PATH")
    resolved = _resolve_candidate(env_ffprobe)
    if resolved:
        return resolved

    env_ffmpeg_dir = os.environ.get("FFMPEG_DIR") or os.environ.get("FFMPEG_BIN")
    if env_ffmpeg_dir:
        exe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
        resolved = _resolve_candidate(Path(env_ffmpeg_dir) / exe_name)
        if resolved:
            return resolved

    # 5. Look in the same directory as ffmpeg if ffmpeg is found
    try:
        ffmpeg_bin = find_ffmpeg(config_path=config_path)
        probe_in_same_dir = ffmpeg_bin.parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        resolved = _resolve_candidate(probe_in_same_dir)
        if resolved:
            return resolved
    except FFmpegNotFoundError:
        pass

    # 6. System PATH
    which_ffprobe = shutil.which("ffprobe")
    if which_ffprobe:
        return Path(which_ffprobe).resolve()

    # 7. Common Windows fallbacks
    common_fallbacks = [
        Path(r"C:\ffmpeg\bin\ffprobe.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffprobe.exe"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe"),
        Path(r"D:\ffmpeg\bin\ffprobe.exe"),
    ]
    for fallback in common_fallbacks:
        resolved = _resolve_candidate(fallback)
        if resolved:
            return resolved

    raise FFprobeNotFoundError(
        "Could not find ffprobe executable. Please configure 'ffprobe_path' in config.json "
        "or ensure ffprobe is available in system PATH or at 'F:\\discord bot\\bin\\ffprobe.exe'."
    )


def _extract_version_from_output(first_line: str) -> str:
    """Extract standard version string from the first line of ffmpeg/ffprobe -version."""
    match = re.search(r"version\s+([^\s]+)", first_line, re.IGNORECASE)
    if match:
        return match.group(1)
    return first_line.strip()


def get_ffmpeg_version(ffmpeg_path: Optional[str | Path] = None) -> str:
    """
    Executes ffmpeg -version and parses the version string.

    Returns:
        str: Detected version string (e.g. 'N-93762-ge384f6f2f9' or '6.0').
    """
    exe = find_ffmpeg(custom_path=ffmpeg_path)
    try:
        res = subprocess.run(
            [str(exe), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_get_creation_flags(),
            check=True,
            timeout=10,
        )
        first_line = res.stdout.splitlines()[0] if res.stdout else ""
        return _extract_version_from_output(first_line)
    except Exception as exc:
        raise FFmpegExecutionError(f"Failed to query FFmpeg version from {exe}: {exc}") from exc


def get_ffprobe_version(ffprobe_path: Optional[str | Path] = None) -> str:
    """
    Executes ffprobe -version and parses the version string.

    Returns:
        str: Detected version string (e.g. 'N-93762-ge384f6f2f9' or '6.0').
    """
    exe = find_ffprobe(custom_path=ffprobe_path)
    try:
        res = subprocess.run(
            [str(exe), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_get_creation_flags(),
            check=True,
            timeout=10,
        )
        first_line = res.stdout.splitlines()[0] if res.stdout else ""
        return _extract_version_from_output(first_line)
    except Exception as exc:
        raise FFprobeExecutionError(f"Failed to query FFprobe version from {exe}: {exc}") from exc


def verify_ffmpeg_installation(
    ffmpeg_path: Optional[str | Path] = None,
    ffprobe_path: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
) -> FFmpegVerification:
    """
    Comprehensive verification of both FFmpeg and FFprobe binaries.
    Validates presence and basic execution sanity.

    Returns:
        FFmpegVerification dataclass instance with status, paths, and version information.
    """
    err_msgs: list[str] = []
    actual_ffmpeg: Optional[Path] = None
    actual_ffprobe: Optional[Path] = None
    ffmpeg_ver: Optional[str] = None
    ffprobe_ver: Optional[str] = None

    try:
        actual_ffmpeg = find_ffmpeg(custom_path=ffmpeg_path, config_path=config_path)
        ffmpeg_ver = get_ffmpeg_version(actual_ffmpeg)
    except Exception as exc:
        err_msgs.append(f"FFmpeg error: {exc}")

    try:
        actual_ffprobe = find_ffprobe(custom_path=ffprobe_path, config_path=config_path)
        ffprobe_ver = get_ffprobe_version(actual_ffprobe)
    except Exception as exc:
        err_msgs.append(f"FFprobe error: {exc}")

    is_valid = (actual_ffmpeg is not None) and (actual_ffprobe is not None) and len(err_msgs) == 0

    return FFmpegVerification(
        is_valid=is_valid,
        ffmpeg_path=str(actual_ffmpeg) if actual_ffmpeg else None,
        ffprobe_path=str(actual_ffprobe) if actual_ffprobe else None,
        ffmpeg_version=ffmpeg_ver,
        ffprobe_version=ffprobe_ver,
        error_message="; ".join(err_msgs) if err_msgs else None,
    )
