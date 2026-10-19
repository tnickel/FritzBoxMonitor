"""
Fritz!Box Solar Monitor - Main Application
Runs as a compact, fixed desktop widget in the top-right corner of the screen.
"""

import ctypes
from ctypes import wintypes
from datetime import datetime
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, Optional
import webview

from app_paths import get_base_dir, get_resource_dir
from autostart import is_autostart_enabled, set_autostart as set_autostart_registry
from config_manager import load_config, save_config
from energy_tracker import EnergyTracker
from fritz_client import FritzClient

BASE_DIR = get_base_dir()
UI_DIR = get_resource_dir() / "ui"

# Make this process per-monitor DPI aware BEFORE any window is created.
# Without this, pywebview only sets system-DPI-aware, and on scaled monitors
# the WinForms window scale, DWM virtualization and the WebView2 rasterization
# scale diverge — the widget renders inflated or cut off.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
    except Exception:
        pass


def get_primary_monitor_scale() -> float:
    """Effective DPI scale of the primary monitor (1.0, 1.25, 1.5, ...)."""
    try:
        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        hmon = ctypes.windll.user32.MonitorFromPoint(PT(100, 100), 1)  # MONITOR_DEFAULTTOPRIMARY
        dx = ctypes.c_uint()
        dy = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))  # MDT_EFFECTIVE_DPI
        return (dx.value / 96.0) if dx.value > 0 else 1.0
    except Exception:
        return 1.0


# The WinForms WebView2 control derives a wrong rasterization scale on scaled
# monitors (observed 2.54 instead of 1.75), inflating the whole rendering.
# Forcing the device scale factor at the Chromium level is the only reliable
# counter. WebView2 reads this documented environment variable.
_monitor_scale = get_primary_monitor_scale()
if _monitor_scale != 1.0:
    _extra_args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        (_extra_args + " " if _extra_args else "")
        + f"--force-device-scale-factor={_monitor_scale}"
    )

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


MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def get_top_right_coords(widget_width: int = 370, widget_height: int = 260, margin: int = 20) -> tuple[int, int]:
    """
    Computes top-right screen coordinates in logical pixels taking the Windows taskbar into account.
    """
    try:
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        scale = (dpi / 96.0) if dpi > 0 else 1.0

        logical_right = rect.right / scale
        logical_top = rect.top / scale

        pos_x = int(logical_right - widget_width - margin)
        pos_y = int(logical_top + margin)
        return pos_x, pos_y
    except Exception as e:
        logger.warning(f"Could not calculate work area, using fallback: {e}")
        return 1800, 20


class WidgetApi:
    def __init__(self):
        logger.info("Startup: WidgetApi wird initialisiert")
        self._config = load_config()
        self._client = FritzClient(
            host=self._config.get("host", "192.168.178.1"),
            password=self._config.get("password", ""),
        )
        self._tracker = EnergyTracker()
        self._latest_data: Dict[str, Any] = {
            "status": "offline",
            "total_power_w": 0.0,
            "total_today_kwh": 0.0,
            "total_year_kwh": 0.0,
            "total_lifetime_kwh": 0.0,
            "sockets": [],
            "timestamp": "--:--",
        }
        self._main_window: Optional[webview.Window] = None
        self._hwnd: Optional[int] = None
        self._running = True
        self._poll_event = threading.Event()
        logger.info("Startup: WidgetApi initialisiert")

    def _get_hwnd(self) -> Optional[int]:
        if self._hwnd:
            return self._hwnd
        if self._main_window and hasattr(self._main_window, "native"):
            # Reading .Handle from a non-UI thread can deadlock (GIL vs STA);
            # fetch it on the UI thread instead. on_shown caches it anyway.
            res = self._on_ui_thread_result(
                lambda: self._main_window.native.Handle.ToInt64(), timeout=3.0
            )
            if "value" in res:
                self._hwnd = res["value"]
                return self._hwnd
            logger.warning(f"Could not fetch hwnd: {res.get('error')}")
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

    def nudge_size(self, fx: float, fy: float):
        """
        Scales the current window rect by the given factors. Used by the JS side
        to self-correct the window size against the actual CSS viewport, which is
        immune to any DPI-awareness quirks.
        """
        hwnd = self._get_hwnd()
        if not hwnd:
            return {"success": False}
        try:
            fx = min(max(float(fx), 0.25), 4.0)
            fy = min(max(float(fy), 0.25), 4.0)
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            new_w = int(round((rect.right - rect.left) * fx))
            new_h = int(round((rect.bottom - rect.top) * fy))
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, new_w, new_h,
                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
            )
            return {"success": True}
        except Exception as err:
            logger.error(f"Nudge error: {err}")
            return {"success": False}

    def _run_on_ui_thread(self, fn):
        """Runs fn on the WinForms UI thread (WebView2 objects are STA-bound)."""
        try:
            from System import Action
            self._main_window.native.BeginInvoke(Action(fn))
        except Exception as e:
            logger.warning(f"BeginInvoke failed ({e}), applying on calling thread")
            fn()

    def _on_ui_thread_result(self, fn, timeout=5.0):
        """
        Runs fn on the UI thread and waits for its result. The calling thread
        releases the GIL while waiting, so the UI thread can always proceed.
        Accessing .NET/WebView2 properties directly from a Python thread can
        deadlock the whole interpreter (GIL held while waiting for the STA
        marshal, UI thread waiting for the GIL) — never do that.
        """
        result: Dict[str, Any] = {}
        done = threading.Event()

        def run():
            try:
                result["value"] = fn()
            except Exception as e:
                result["error"] = e
            finally:
                done.set()

        self._run_on_ui_thread(run)
        if not done.wait(timeout):
            result["error"] = TimeoutError("UI-Thread antwortet nicht")
        return result

    def apply_display_fix(self):
        """
        Pins the WebView2 rasterization scale to the window's real DPI and
        disables user zoom. The WinForms WebView2 control derives a wrong
        rasterization scale on high-DPI monitors (observed 2.54 instead of
        1.75), which inflates the rendering and breaks the fixed layout.
        """
        def apply():
            try:
                hwnd = self._get_hwnd()
                scale = 1.0
                if hwnd:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    scale = (dpi / 96.0) if dpi > 0 else 1.0
                wv = self._main_window.native.webview
                try:
                    wv.ShouldDetectMonitorScaleChanges = False
                    wv.RasterizationScale = scale
                except Exception as e:
                    logger.warning(f"RasterizationScale not settable: {e}")
                core = wv.CoreWebView2
                if core is not None:
                    core.Settings.IsZoomControlEnabled = False
                logger.info(f"Display fix applied: RasterizationScale={scale}, zoom control disabled")
            except Exception as e:
                logger.warning(f"Display fix failed: {e}")

        self._run_on_ui_thread(apply)
        return {"success": True}

    def _apply_zoom_fix(self):
        try:
            wv = self._main_window.native.webview
            wv.ZoomFactor = 1.0
            core = wv.CoreWebView2
            if core is not None:
                core.Settings.IsZoomControlEnabled = False
                logger.info("WebView2 zoom reset to 100% and zoom control disabled")
        except Exception as e:
            logger.warning(f"Zoom fix failed: {e}")

    def disable_web_zoom(self):
        """
        Resets any page zoom to 100% and disables user zoom (ctrl+scroll/pinch).
        Accidental zooming shrinks the CSS viewport and cuts off the widget layout.
        """
        def wait_and_apply():
            ready = False
            for i in range(80):
                if not self._running:
                    return
                # NEVER touch .native.webview.CoreWebView2 directly from this
                # thread — that deadlocks the interpreter (GIL vs STA marshal).
                res = self._on_ui_thread_result(
                    lambda: self._main_window.native.webview.CoreWebView2 is not None,
                    timeout=2.0,
                )
                if res.get("value"):
                    ready = True
                    logger.info(f"Startup: CoreWebView2 bereit nach {i * 0.25:.1f}s")
                    break
                if "error" in res and not isinstance(res["error"], TimeoutError):
                    logger.warning(f"Startup: CoreWebView2-Check fehlerhaft: {res['error']}")
                time.sleep(0.25)
            if not ready:
                logger.warning("Startup: CoreWebView2 nach 20s NICHT bereit (WebView2 haengt?)")
            self._run_on_ui_thread(self._apply_zoom_fix)

        threading.Thread(target=wait_and_apply, daemon=True).start()

    def poll_loop(self):
        """
        Background loop polling Fritz!Box at configured interval.
        """
        while self._running:
            self._do_poll()
            interval = self._config.get("interval_seconds", 60)
            self._poll_event.wait(timeout=max(10, interval))
            self._poll_event.clear()

    def _do_poll(self):
        password = self._config.get("password", "")
        if not password:
            self._latest_data["status"] = "offline"
            self._latest_data["error"] = "Kein Kennwort konfiguriert"
            return

        try:
            self._client.password = password
            self._client.host = self._config.get("host", "192.168.178.1")
            devices = self._client.get_devices()

            selected_ains = self._config.get("socket_ains", [])
            socket_labels = self._config.get("socket_labels", {})

            # Fetch device stats once per poll (year sum + snapshot)
            stats_by_ain: Dict[str, Any] = {}
            for ain in selected_ains:
                try:
                    stats_by_ain[ain] = self._client.get_device_stats(ain)
                except Exception as e:
                    logger.warning(f"Could not fetch stats for {ain}: {e}")

            # Update readings
            res = self._tracker.update_reading(
                devices=devices,
                selected_ains=selected_ains,
                stats_by_ain=stats_by_ain,
            )

            # Apply user custom labels if any
            for sock in res.get("sockets", []):
                ain = sock.get("ain", "")
                if ain in socket_labels:
                    sock["name"] = socket_labels[ain]

            res["status"] = "online"
            self._latest_data = res
            logger.info(
                f"Poll ok: {res['total_power_w']} W, Tagessumme {res['total_today_kwh']} kWh"
            )

        except Exception as e:
            logger.error(f"Poll error: {e}")
            self._latest_data["status"] = "offline"
            self._latest_data["error"] = str(e)

    # JS API Methods
    def get_latest_data(self) -> Dict[str, Any]:
        return self._latest_data

    def log_event(self, message: str) -> Dict[str, Any]:
        """Startup markers and errors from the JS side, for diagnosis."""
        logger.info(f"[UI] {message}")
        return {"ok": True}

    def get_autostart(self) -> bool:
        return is_autostart_enabled()

    def set_autostart(self, enabled: bool) -> Dict[str, Any]:
        enabled = bool(enabled)
        if set_autostart_registry(enabled):
            return {"success": True, "enabled": enabled}
        return {"success": False, "error": "Autostart konnte nicht in der Windows-Registry gespeichert werden."}

    def _monthly_graph_series(self) -> Dict[str, Any]:
        """
        Monthly energy sums (kWh) of the last 12 months per socket, from the
        FritzBox monthly stats stored in the snapshot. The last array value is
        the current month (running total), the rest are complete months.
        """
        now = datetime.now()
        labels = []
        for i in range(11, -1, -1):
            total = now.month - i
            year = now.year
            while total <= 0:
                total += 12
                year -= 1
            labels.append(MONTHS_DE[total - 1])

        snap_devs = {d.get("ain"): d for d in self._tracker.snapshot.get("devices", [])}
        sockets_out = []
        for s in self._latest_data.get("sockets", []):
            d = snap_devs.get(s.get("ain"), {})
            vals = list(d.get("energy_monthly_wh") or [])[-12:]
            vals = [0.0] * (12 - len(vals)) + vals
            sockets_out.append({
                "name": s.get("name", ""),
                "values_kwh": [round(v / 1000.0, 2) for v in vals],
            })
        return {"labels": labels, "sockets": sockets_out}

    def get_graph_data(self) -> Dict[str, Any]:
        history = self._tracker.get_history_for_graph()
        socket_names = [s.get("name", "") for s in self._latest_data.get("sockets", [])]
        return {
            "current_total_w": self._latest_data.get("total_power_w", 0.0),
            "today_kwh": self._latest_data.get("total_today_kwh", 0.0),
            "history": history,
            "socket_names": socket_names,
            "monthly": self._monthly_graph_series(),
        }

    def get_config(self) -> Dict[str, Any]:
        return self._config

    def save_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self._config.update(new_cfg)
            save_config(self._config)
            self._client = FritzClient(
                host=self._config.get("host", "192.168.178.1"),
                password=self._config.get("password", ""),
            )
            self._do_poll()
            if self._latest_data.get("status") == "online":
                return {"success": True}
            else:
                return {"success": False, "error": self._latest_data.get("error", "Verbindungsfehler")}
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
        if self._main_window:
            try:
                self._main_window.destroy()
            except Exception:
                pass


def main():
    logger.info("Startup: main() beginnt")
    api = WidgetApi()

    # Pre-poll once before showing window
    logger.info("Startup: Pre-Poll startet")
    api._do_poll()
    logger.info("Startup: Pre-Poll abgeschlossen")

    # Start background polling thread
    poll_thread = threading.Thread(target=api.poll_loop, daemon=True)
    poll_thread.start()

    widget_w, widget_h = 370, 260
    pos_x, pos_y = get_top_right_coords(widget_w, widget_h, margin=20)

    index_html = str((UI_DIR / "index.html").resolve())

    # Create Main Widget Window with easy_drag=False (drag handled via native Win32)
    window = webview.create_window(
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
    api._main_window = window
    logger.info("Startup: Fensterobjekt erstellt, starte webview.start()")

    def on_shown():
        logger.info("Startup: shown-Event (Fenster wurde sichtbar)")
        try:
            api._hwnd = window.native.Handle.ToInt64()
        except Exception:
            pass
        # Kill any active/persisted page zoom and block user zooming
        api.disable_web_zoom()

    window.events.shown += on_shown
    webview.start()
    logger.info("Startup: webview.start() zurueckgekehrt (App beendet)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        with open(BASE_DIR / "crash.log", "w", encoding="utf-8") as f:
            import traceback
            traceback.print_exc(file=f)
