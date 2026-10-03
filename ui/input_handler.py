"""
Input handling system for the Enterprise Video to Audio Transcoder.
Provides smart Windows CMD / PowerShell input parsing, directory expansion,
video format filtering, and clipboard file extraction.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Iterable, List, Optional, Set, Union

import pyperclip

# Standard supported enterprise video formats
SUPPORTED_VIDEO_EXTENSIONS: Set[str] = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".ts",
    ".vob",
    ".3gp",
    ".m4v",
    ".mts",
    ".m2ts",
    ".ogv",
}

# Regex to extract quoted or whitespace-delimited tokens
# Handles both single and double quotes, ignoring leading PowerShell '&' invocation operator
_TOKEN_PATTERN = re.compile(r'"([^"]+)"|\'([^\']+)\'|(\S+)')


def strip_quotes(text: str) -> str:
    """Strips outer quotes and whitespace from a string."""
    cleaned = text.strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (
        cleaned.startswith("'") and cleaned.endswith("'")
    ):
        return cleaned[1:-1].strip()
    return cleaned


def is_supported_video(path: Union[str, Path]) -> bool:
    """
    Checks if the given file has a supported video extension.
    Case-insensitive.
    """
    p = Path(path)
    return p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS


def filter_video_files(paths: Iterable[Union[str, Path]]) -> List[Path]:
    """Filters an iterable of paths, keeping only existing supported video files."""
    results: List[Path] = []
    seen: Set[Path] = set()

    for item in paths:
        p = Path(item).resolve()
        if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            if p not in seen:
                seen.add(p)
                results.append(p)
    return results


def expand_path(
    path: Union[str, Path], recursive: bool = True
) -> List[Path]:
    """
    Expands a single path into a list of supported video files.
    - If path is a video file, returns [path].
    - If path is a directory, searches for video files (recursively or flat).
    - If path contains wildcards (* or ?), globs matching files.
    """
    cleaned_str = strip_quotes(str(path))
    p = Path(cleaned_str).expanduser()

    # Wildcard expansion check
    if any(char in cleaned_str for char in ("*", "?")):
        parent = p.parent if p.parent.exists() else Path.cwd()
        pattern = p.name
        matched: List[Path] = []
        try:
            glob_iter = parent.rglob(pattern) if recursive else parent.glob(pattern)
            for match in glob_iter:
                if match.is_file() and match.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                    matched.append(match.resolve())
            return sorted(matched)
        except Exception:
            return []

    p = p.resolve()

    if p.is_file():
        if p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            return [p]
        return []

    if p.is_dir():
        found: List[Path] = []
        try:
            if recursive:
                for root, _, files in os.walk(p):
                    for file in files:
                        ext = os.path.splitext(file)[1].lower()
                        if ext in SUPPORTED_VIDEO_EXTENSIONS:
                            found.append(Path(root, file).resolve())
            else:
                for entry in p.iterdir():
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                        found.append(entry.resolve())
        except (PermissionError, OSError):
            pass

        return sorted(found)

    return []


def parse_tokens(raw_input: str) -> List[str]:
    """
    Parses a raw input string into token strings.
    Handles:
    - Multiple quoted paths: "F:\\vid1.mp4" "F:\\vid2.mkv"
    - Single quoted paths: 'F:\\vid 1.mp4' 'F:\\vid 2.mkv'
    - Space separated paths: F:\\vid1.mp4 F:\\vid2.mkv
    - Unquoted path containing spaces if the full string matches an existing path
    - Multiline input (separated by \\n or \\r\\n)
    """
    cleaned_all = raw_input.strip()
    if not cleaned_all:
        return []

    # Check if the whole string without quotes is an existing file or directory
    stripped_full = strip_quotes(cleaned_all)
    try:
        full_path = Path(stripped_full).expanduser().resolve()
        if full_path.exists():
            return [str(full_path)]
    except Exception:
        pass

    # Handle multiline input
    lines = [line.strip() for line in cleaned_all.splitlines() if line.strip()]
    tokens: List[str] = []

    for line in lines:
        # Check if line as a whole is an existing path
        stripped_line = strip_quotes(line)
        try:
            line_path = Path(stripped_line).expanduser().resolve()
            if line_path.exists():
                tokens.append(str(line_path))
                continue
        except Exception:
            pass

        # Tokenize using regex
        matches = _TOKEN_PATTERN.findall(line)
        current_tokens: List[str] = []
        for match in matches:
            val = match[0] or match[1] or match[2]
            if not val:
                continue
            # Ignore standalone PowerShell call operator
            if val == "&":
                continue
            current_tokens.append(strip_quotes(val))

        # Re-combination heuristic for unquoted paths with spaces:
        # e.g., ['F:\\My', 'Videos\\Movie.mp4'] -> if combined exists, combine them
        i = 0
        while i < len(current_tokens):
            combined = current_tokens[i]
            best_match = combined
            best_idx = i

            # Try combining with subsequent tokens to see if it forms a real path
            for j in range(i + 1, min(i + 6, len(current_tokens) + 1)):
                candidate = " ".join(current_tokens[i:j])
                try:
                    if Path(candidate).expanduser().resolve().exists():
                        best_match = candidate
                        best_idx = j - 1
                except Exception:
                    pass

            tokens.append(best_match)
            i = best_idx + 1

    return tokens


def parse_input_paths(raw_input: str, recursive: bool = True) -> List[Path]:
    """
    High-level smart parser: takes raw input string, tokenizes it,
    expands directories/wildcards, and returns unique video Paths.
    """
    tokens = parse_tokens(raw_input)
    results: List[Path] = []
    seen: Set[Path] = set()

    for token in tokens:
        for p in expand_path(token, recursive=recursive):
            if p not in seen:
                seen.add(p)
                results.append(p)

    return results


def _get_windows_hdrop_files() -> List[str]:
    """
    Reads files copied in Windows Explorer (CF_HDROP format) via ctypes.
    Returns list of absolute file paths as strings.
    """
    if sys.platform != "win32":
        return []

    CF_HDROP = 15
    try:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE

        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
        ]
        shell32.DragQueryFileW.restype = wintypes.UINT

        if not user32.OpenClipboard(None):
            return []

        try:
            h_drop = user32.GetClipboardData(CF_HDROP)
            if not h_drop:
                return []

            count = shell32.DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
            files: List[str] = []
            buf = ctypes.create_unicode_buffer(1024)
            for i in range(count):
                length = shell32.DragQueryFileW(h_drop, i, buf, 1024)
                if length > 0:
                    files.append(buf.value)
            return files
        finally:
            user32.CloseClipboard()
    except Exception:
        return []


def get_clipboard_files(recursive: bool = True) -> List[Path]:
    """
    Gets file paths from clipboard.
    1. Checks Windows Explorer CF_HDROP clipboard (e.g. copied files/folders in File Explorer).
    2. Falls back to text clipboard via pyperclip and parses text into video paths.
    Expands directories and filters for supported video formats.
    """
    # Try Windows CF_HDROP first
    hdrop_files = _get_windows_hdrop_files()
    if hdrop_files:
        collected: List[Path] = []
        seen: Set[Path] = set()
        for f in hdrop_files:
            for p in expand_path(f, recursive=recursive):
                if p not in seen:
                    seen.add(p)
                    collected.append(p)
        if collected:
            return collected

    # Try textual clipboard
    try:
        text = pyperclip.paste()
        if text and text.strip():
            return parse_input_paths(text, recursive=recursive)
    except Exception:
        pass

    return []


class InputParser:
    """Class wrapper for input parsing with stateful options."""

    def __init__(self, recursive: bool = True):
        self.recursive = recursive

    def parse(self, raw_input: str) -> List[Path]:
        return parse_input_paths(raw_input, recursive=self.recursive)

    def from_clipboard(self) -> List[Path]:
        return get_clipboard_files(recursive=self.recursive)

    def expand(self, path: Union[str, Path]) -> List[Path]:
        return expand_path(path, recursive=self.recursive)
