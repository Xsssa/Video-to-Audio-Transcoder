import os
import time
import threading
from pathlib import Path
from typing import Optional, Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

class WatchFolderHandler(FileSystemEventHandler):
    """Handles events in the watched folder."""
    
    def __init__(self, extensions: set[str], on_new_file: Callable[[Path], None]):
        super().__init__()
        self.extensions = {ext.lower() for ext in extensions}
        self.on_new_file = on_new_file
        
    def _is_valid_file(self, path_str: str) -> bool:
        ext = Path(path_str).suffix.lower()
        # Clean extension, remove '.' if present in self.extensions or just match
        clean_ext = ext.lstrip('.')
        return clean_ext in self.extensions or ext in self.extensions

    def _wait_for_file_ready(self, filepath: Path, timeout: int = 60) -> bool:
        """Wait until file size is stable (fully copied/downloaded)."""
        start_time = time.time()
        last_size = -1
        while time.time() - start_time < timeout:
            try:
                current_size = filepath.stat().st_size
                if current_size > 0 and current_size == last_size:
                    return True # Size stabilized
                last_size = current_size
            except FileNotFoundError:
                return False
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def on_created(self, event):
        if not event.is_directory and self._is_valid_file(event.src_path):
            self._process_file(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory and self._is_valid_file(event.dest_path):
            self._process_file(Path(event.dest_path))

    def _process_file(self, filepath: Path):
        # Run in a separate thread so we don't block the watchdog event loop
        def _worker():
            if self._wait_for_file_ready(filepath):
                self.on_new_file(filepath)
        
        threading.Thread(target=_worker, daemon=True).start()


class WatchFolderDaemon:
    """Monitors a specific folder for new video files in the background."""
    
    def __init__(
        self, 
        folder_path: str | Path, 
        on_new_file_callback: Callable[[Path], None],
        extensions: Optional[set[str]] = None
    ):
        self.folder_path = Path(folder_path)
        self.on_new_file = on_new_file_callback
        # Default common video extensions
        self.extensions = extensions or {
            ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"
        }
        self.observer = None
        
    def start(self):
        """Starts monitoring the folder."""
        self.folder_path.mkdir(parents=True, exist_ok=True)
        self.observer = Observer()
        handler = WatchFolderHandler(self.extensions, self.on_new_file)
        self.observer.schedule(handler, str(self.folder_path), recursive=False)
        self.observer.start()
        
    def stop(self):
        """Stops monitoring the folder."""
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2.0)
            self.observer = None
