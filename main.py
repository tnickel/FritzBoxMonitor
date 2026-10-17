"""
Fritz!Box Solar Monitor - Main Application
Runs as a compact, fixed desktop widget in the top-right corner of the screen.
"""

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, Optional
import webview

from config_manager import load_config, save_config
from energy_tracker import EnergyTracker
from fritz_client import FritzClient

BASE_DIR = Path(__file__).parent.resolve()
UI_DIR = BASE_DIR / "ui"

# Safe stdout/stderr redirection for pythonw.exe
LOG_FILE = BASE_DIR / "widget.log"
if sys.stdout is None:
    sys.stdout = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
if sys.stderr is None:
    sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

# Configure logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("SolarWidget")

# Win32 Constants for butter-smooth native dragging & resizing
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


def get_top_right_coords(widget_width: int = 370, widget_height: int = 260, margin: int = 20) -> tuple[int, int]:
    """
    Computes top-right screen coordinates taking the Windows taskbar into account.
    """
    try:
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        pos_x = rect.right - widget_width - margin
        pos_y = rect.top + margin
        return int(pos_x), int(pos_y)
    except Exception as e:
        logger.warning(f"Could not calculate work area, using fallback: {e}")
        return 1800, 20


class WidgetApi:
    def __init__(self):
        self.config = load_config()
        self.client = FritzClient(
            host=self.config.get("host", "192.168.178.1"),
            password=self.config.get("password", ""),
        )
        self.tracker = EnergyTracker()
        self.latest_data: Dict[str, Any] = {
            "status": "offline",
            "total_power_w": 0.0,
            "total_today_kwh": 0.0,
            "total_year_kwh": 0.0,
            "total_lifetime_kwh": 0.0,
            "sockets": [],
            "timestamp": "--:--",
        }
        self.main_window: Optional[webview.Window] = None
        self._running = True
        self._poll_event = threading.Event()

    def _get_hwnd(self) -> Optional[int]:
        if self.main_window and hasattr(self.main_window, "native"):
            try:
                return self.main_window.native.Handle.ToInt64()
            except Exception:
                return None
        return None

    def start_drag(self):
        """
        Triggers Windows native non-client mouse drag.
        Zero jitter, zero jumping across high-DPI monitors.
        """
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
            except Exception as err:
                logger.error(f"Drag error: {err}")

    def resize_widget(self, width: int, height: int):
        """
        Thread-safe Win32 window resizing respecting per-monitor DPI scaling.
        """
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                scale = (dpi / 96.0) if dpi > 0 else 1.0
                phys_w = int(round(width * scale))
                phys_h = int(round(height * scale))
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, phys_w, phys_h,
                    SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
                )
            except Exception as err:
                logger.error(f"Resize error: {err}")

    def poll_loop(self):
        """
        Background loop polling Fritz!Box at configured interval.
        """
        while self._running:
            self._do_poll()
            interval = self.config.get("interval_seconds", 60)
            self._poll_event.wait(timeout=max(10, interval))
            self._poll_event.clear()

    def _do_poll(self):
        password = self.config.get("password", "")
        if not password:
            self.latest_data["status"] = "offline"
            self.latest_data["error"] = "Kein Kennwort konfiguriert"
            return

        try:
            self.client.password = password
            self.client.host = self.config.get("host", "192.168.178.1")
            devices = self.client.get_devices()

            selected_ains = self.config.get("socket_ains", [])
            socket_labels = self.config.get("socket_labels", {})

            # Update readings
            res = self.tracker.update_reading(
                devices=devices,
                selected_ains=selected_ains,
                stats_callback=self.client.get_device_stats,
            )

            # Apply user custom labels if any
            for sock in res.get("sockets", []):
                ain = sock.get("ain", "")
                if ain in socket_labels:
                    sock["name"] = socket_labels[ain]

            res["status"] = "online"
            self.latest_data = res

        except Exception as e:
            logger.error(f"Poll error: {e}")
            self.latest_data["status"] = "offline"
            self.latest_data["error"] = str(e)

    # JS API Methods
    def get_latest_data(self) -> Dict[str, Any]:
        return self.latest_data

    def get_graph_data(self) -> Dict[str, Any]:
        history = self.tracker.get_history_for_graph()
        socket_names = [s.get("name", "") for s in self.latest_data.get("sockets", [])]
        return {
            "current_total_w": self.latest_data.get("total_power_w", 0.0),
            "today_kwh": self.latest_data.get("total_today_kwh", 0.0),
            "history": history,
            "socket_names": socket_names,
        }

    def get_config(self) -> Dict[str, Any]:
        return self.config

    def save_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.config.update(new_cfg)
            save_config(self.config)
            self.client = FritzClient(
                host=self.config.get("host", "192.168.178.1"),
                password=self.config.get("password", ""),
            )
            self._do_poll()
            if self.latest_data.get("status") == "online":
                return {"success": True}
            else:
                return {"success": False, "error": self.latest_data.get("error", "Verbindungsfehler")}
        except Exception as err:
            return {"success": False, "error": str(err)}

    def discover_devices(self, host: str, password: str) -> Dict[str, Any]:
        try:
            temp_client = FritzClient(host=host, password=password)
            temp_client.authenticate()
            devs = temp_client.get_devices()
            return {"success": True, "devices": devs}
        except Exception as err:
            return {"success": False, "error": str(err)}

    def close_app(self):
        self._running = False
        self._poll_event.set()
        if self.main_window:
            try:
                self.main_window.destroy()
            except Exception:
                pass


def main():
    api = WidgetApi()

    # Pre-poll once before showing window
    api._do_poll()

    # Start background polling thread
    poll_thread = threading.Thread(target=api.poll_loop, daemon=True)
    poll_thread.start()

    widget_w, widget_h = 370, 260
    pos_x, pos_y = get_top_right_coords(widget_w, widget_h, margin=20)

    index_html = str((UI_DIR / "index.html").resolve())

    # Create Main Widget Window with easy_drag=False (drag handled via native Win32)
    api.main_window = webview.create_window(
        title="Solar Monitor",
        url=index_html,
        js_api=api,
        width=widget_w,
        height=widget_h,
        x=pos_x,
        y=pos_y,
        resizable=False,
        frameless=True,
        easy_drag=False,
        on_top=False,
    )

    webview.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        with open(BASE_DIR / "crash.log", "w", encoding="utf-8") as f:
            import traceback
            traceback.print_exc(file=f)
