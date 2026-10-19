"""
Autostart helper: registers/unregisters the widget in the per-user Windows
registry Run key, so it starts automatically when the user logs in.
No admin rights required; the widget starts silently via pythonw.exe.
"""

import logging
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg

logger = logging.getLogger("Autostart")

BASE_DIR = Path(__file__).parent.resolve()
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FritzBoxSolarMonitor"


def _launch_command() -> str:
    """
    Command for the Run key: the running interpreter (preferring pythonw.exe,
    so no console window appears) plus the path to main.py.
    """
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{BASE_DIR / "main.py"}"'


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.error(f"Could not read autostart state: {e}")
        return False


def set_autostart(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        logger.error(f"Could not change autostart: {e}")
        return False
