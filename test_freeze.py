"""
Freeze localization test: mirrors the production startup in additive stages.

Stage 1: bare pywebview window, inline HTML, no js_api
Stage 2: + js_api with the OLD method set only
Stage 3: + js_api with the NEW methods (get_autostart/set_autostart/log_event)
Stage 4: + real production imports (autostart, energy_tracker, fritz_client chain)
Stage 5: + real backend objects (config, FritzClient, EnergyTracker) as js_api
Stage 6: + the real ui/index.html (loads app.js/style.css/chart.js)

A background heartbeat thread logs every 2 s. The window closes itself after
18 s; "TEST DONE" is logged when webview.start() returns. A frozen stage =
heartbeats stop and TEST DONE never appears.
"""

import argparse
import ctypes
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
LOG_FILE = BASE_DIR / "test_freeze.log"

# Mirror main.py: per-monitor DPI awareness before any window
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def monitor_scale() -> float:
    try:
        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        hmon = ctypes.windll.user32.MonitorFromPoint(PT(100, 100), 1)
        dx = ctypes.c_uint()
        dy = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
        return (dx.value / 96.0) if dx.value > 0 else 1.0
    except Exception:
        return 1.0


import os  # noqa: E402
_scale = monitor_scale()
if _scale != 1.0:
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        + f" --force-device-scale-factor={_scale}"
    )

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("FreezeTest")

INLINE_HTML = "<html><body style='background:#1e293b;color:#fff;font-family:sans-serif'>"
INLINE_HTML += "<h3>Freeze-Test</h3><p>Stufe laeuft.</p></body></html>"


class OldApi:
    """Old method set (state before today's changes)."""

    def get_latest_data(self):
        return {"status": "offline", "total_power_w": 0.0, "total_today_kwh": 0.0,
                "total_year_kwh": 0.0, "total_lifetime_kwh": 0.0, "sockets": [],
                "timestamp": "--:--"}

    def get_graph_data(self):
        return {"current_total_w": 0.0, "today_kwh": 0.0, "history": [], "socket_names": []}

    def get_config(self):
        return {"host": "192.168.178.1", "password": "", "interval_seconds": 60,
                "socket_ains": [], "socket_labels": {}}

    def save_config(self, cfg):
        return {"success": True}

    def discover_devices(self, host, password):
        return {"success": True, "devices": []}

    def close_app(self):
        pass


class NewApi(OldApi):
    """Today's additions."""

    def get_autostart(self):
        return False

    def set_autostart(self, enabled):
        return {"success": True, "enabled": enabled}

    def log_event(self, message):
        logger.info(f"[UI] {message}")
        return {"ok": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=int, required=True)
    stage = parser.parse_args().stage

    logger.info(f"===== STUFE {stage} START =====")

    stop = threading.Event()

    def heartbeat():
        i = 0
        while not stop.wait(2.0):
            i += 1
            logger.info(f"Herzschlag {i} ({i * 2}s)")

    threading.Thread(target=heartbeat, daemon=True).start()

    api = None
    url = INLINE_HTML

    if stage >= 4:
        logger.info("Stufe 4: importiere Produktionsmodule...")
        from autostart import is_autostart_enabled  # noqa: F401
        from energy_tracker import EnergyTracker  # noqa: F401
        from fritz_client import FritzClient  # noqa: F401
        logger.info("Stufe 4: Importe ok")

    if stage == 5:
        logger.info("Stufe 5: erzeuge echte Backend-Objekte...")
        from config_manager import load_config
        from energy_tracker import EnergyTracker
        from fritz_client import FritzClient
        cfg = load_config()
        client = FritzClient(host=cfg.get("host", "192.168.178.1"),
                             password=cfg.get("password", ""))
        tracker = EnergyTracker()
        api = NewApi()
        logger.info("Stufe 5: Backend-Objekte ok")

    if stage in (2, 3, 4):
        api = OldApi() if stage == 2 else NewApi()

    if stage == 6:
        logger.info("Stufe 6: nutze echte ui/index.html")
        url = str((BASE_DIR / "ui" / "index.html").resolve())
        if api is None:
            api = NewApi()

    import webview

    logger.info(f"Stufe {stage}: create_window...")
    window = webview.create_window(
        title=f"Freeze-Test Stufe {stage}",
        html=url if url.startswith("<") else None,
        url=None if url.startswith("<") else url,
        js_api=api,
        width=370, height=260, x=1200, y=60,
        resizable=False, frameless=True, easy_drag=False,
    )

    def on_shown():
        logger.info(f"Stufe {stage}: shown-Event")

    window.events.shown += on_shown

    def autoclose():
        time.sleep(18)
        logger.info(f"Stufe {stage}: Auto-Close nach 18s")
        try:
            window.destroy()
        except Exception as e:
            logger.warning(f"Stufe {stage}: destroy fehlgeschlagen: {e}")

    threading.Thread(target=autoclose, daemon=True).start()

    logger.info(f"Stufe {stage}: webview.start()...")
    webview.start()
    stop.set()
    logger.info(f"===== STUFE {stage} TEST DONE =====")


if __name__ == "__main__":
    main()
