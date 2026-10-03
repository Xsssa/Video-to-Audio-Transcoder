import os
import threading
from pathlib import Path
import pystray
from PIL import Image, ImageDraw
from plyer import notification

class SystemTrayManager:
    """Manages the Windows System Tray icon and native notifications."""
    
    def __init__(self, app_name: str, on_show_window, on_exit):
        self.app_name = app_name
        self.on_show_window = on_show_window
        self.on_exit = on_exit
        self.icon = None
        self._thread = None
        
    def _create_default_icon(self) -> Image.Image:
        """Create a simple default icon if none exists."""
        width = 64
        height = 64
        color1 = (43, 43, 43)
        color2 = (0, 120, 215)
        image = Image.new('RGB', (width, height), color1)
        dc = ImageDraw.Draw(image)
        dc.ellipse((16, 16, 48, 48), fill=color2)
        return image

    def start(self):
        """Starts the tray icon in a separate thread."""
        if self._thread is not None:
            return
            
        def setup_icon():
            image = self._create_default_icon()
            menu = pystray.Menu(
                pystray.MenuItem('Show', self._on_show_clicked, default=True),
                pystray.MenuItem('Exit', self._on_exit_clicked)
            )
            self.icon = pystray.Icon(
                "video_to_audio_tray", 
                image, 
                self.app_name, 
                menu
            )
            self.icon.run()

        self._thread = threading.Thread(target=setup_icon, daemon=True)
        self._thread.start()

    def _on_show_clicked(self, icon, item):
        if self.on_show_window:
            self.on_show_window()

    def _on_exit_clicked(self, icon, item):
        self.stop()
        if self.on_exit:
            self.on_exit()

    def stop(self):
        """Stops the tray icon."""
        if self.icon:
            self.icon.stop()
            self.icon = None

    def send_notification(self, title: str, message: str):
        """Sends a native desktop notification."""
        try:
            notification.notify(
                title=title,
                message=message,
                app_name=self.app_name,
                timeout=5
            )
        except Exception:
            pass # Ignore if notification fails

