"""
Energy Tracker: Calculates current power, daily energy (Tagessumme),
yearly energy (Jahressumme), and keeps rolling history for the live graph.

The day sum is reconstructed after a restart from the persistent counter log
in the FritzBox snapshot (data/fritz_snapshot.json): the baseline for a new
day is the last known counter reading from a previous run, so the whole day
is covered even when the app is started later.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fritz_snapshot import (
    build_live_snapshot,
    last_known_reading,
    load_snapshot,
    prune_counter_log,
    record_counter_reading,
    save_snapshot,
)

logger = logging.getLogger("EnergyTracker")

DATA_DIR = Path(__file__).parent / "data"
BASELINES_FILE = DATA_DIR / "energy_baselines.json"
HISTORY_FILE = DATA_DIR / "history.json"


class EnergyTracker:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.baselines: Dict[str, Dict[str, float]] = self._load_baselines()
        self.snapshot: Dict[str, Any] = load_snapshot()
        self.history: List[Dict[str, Any]] = self._load_history()

    def _load_baselines(self) -> Dict[str, Dict[str, float]]:
        if BASELINES_FILE.exists():
            try:
                with open(BASELINES_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_baselines(self) -> None:
        try:
            # Keep only the last 30 days of baselines
            if len(self.baselines) > 30:
                for key in sorted(self.baselines.keys())[:-30]:
                    del self.baselines[key]
            with open(BASELINES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.baselines, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving baselines: {e}")

    def _load_history(self) -> List[Dict[str, Any]]:
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_history(self) -> None:
        try:
            # Keep max 1440 points (approx 24h at 1 point/min)
            if len(self.history) > 1440:
                self.history = self.history[-1440:]
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history, f)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def get_today_key(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def update_reading(
        self,
        devices: List[Dict[str, Any]],
        selected_ains: List[str],
        stats_by_ain: Optional[Dict[str, Dict[str, List[Dict[str, Any]]]]] = None,
    ) -> Dict[str, Any]:
        """
        Processes current readings for the selected 3 sockets.
        stats_by_ain: parsed device stats (see fritz_snapshot.parse_stats_xml).
        Returns a structured dictionary for the widget UI and graph and
        updates the persistent FritzBox snapshot (counter log + stats).
        """
        today_key = self.get_today_key()
        if today_key not in self.baselines:
            self.baselines[today_key] = {}
        stats_by_ain = stats_by_ain or {}

        # Filter devices to matching 3 sockets
        tracked_devices: List[Dict[str, Any]] = []
        if selected_ains:
            dev_map = {d["ain"]: d for d in devices}
            for ain in selected_ains:
                if ain in dev_map:
                    tracked_devices.append(dev_map[ain])
                else:
                    tracked_devices.append({
                        "ain": ain,
                        "name": f"Steckdose ({ain[-4:]})",
                        "power_w": 0.0,
                        "energy_wh": 0.0,
                        "present": False,
                        "voltage_v": 0.0,
                    })
        else:
            tracked_devices = devices[:3]

        socket_results = []
        total_power_w = 0.0
        total_today_wh = 0.0
        total_lifetime_wh = 0.0
        total_year_wh = 0.0
        now_ts = datetime.now()

        for idx, dev in enumerate(tracked_devices):
            ain = dev["ain"]
            current_wh = dev.get("energy_wh", 0.0)
            current_w = dev.get("power_w", 0.0)
            total_lifetime_wh += current_wh

            # Yearly energy from the box's monthly series (last 12 months, Wh)
            device_year_wh = 0.0
            stats = stats_by_ain.get(ain) if ain else None
            if stats:
                for series in stats.get("energy", []):
                    if series.get("grid") == "monthly" and series.get("values"):
                        device_year_wh = sum(series["values"])
                        break
            total_year_wh += device_year_wh

            # Check if baseline exists for today. On a new day, start from the
            # last known counter value of a previous run (approx. the midnight
            # value) so the day sum covers the whole day even when this app is
            # started later (e.g. at noon). The counter log is read BEFORE the
            # current reading is recorded into it.
            if ain not in self.baselines[today_key] or self.baselines[today_key][ain] <= 0:
                last = last_known_reading(self.snapshot, ain, today_key)
                if last:
                    self.baselines[today_key][ain] = last["value_wh"]
                else:
                    self.baselines[today_key][ain] = current_wh
                self._save_baselines()

            baseline_wh = self.baselines[today_key].get(ain, current_wh)

            # If the plug was reset, its counter restarts at a low value. Only
            # re-baseline then; a temporarily unreadable device (energy 0) must
            # not wipe the baseline.
            if 0 < current_wh < baseline_wh:
                baseline_wh = current_wh
                self.baselines[today_key][ain] = baseline_wh
                self._save_baselines()

            today_wh = max(0.0, current_wh - baseline_wh)
            today_kwh = round(today_wh / 1000.0, 2)

            # Persist the latest counter value so tomorrow's baseline can start
            # where today ended. Zero readings (device unreachable) are not
            # recorded so the last real value survives outages.
            if ain and current_wh > 0:
                record_counter_reading(
                    self.snapshot, today_key, ain, current_wh, now_ts.strftime("%H:%M")
                )

            socket_results.append({
                "slot": idx + 1,
                "ain": ain,
                "name": dev.get("name", f"Steckdose {idx+1}"),
                "power_w": current_w,
                "today_kwh": today_kwh,
                "lifetime_kwh": round(current_wh / 1000.0, 1),
                "present": dev.get("present", True),
                "voltage_v": dev.get("voltage_v", 230.0),
            })

            total_power_w += current_w
            total_today_wh += today_wh

        total_power_w = round(total_power_w, 1)
        total_today_kwh = round(total_today_wh / 1000.0, 2)
        total_year_kwh = round(total_year_wh / 1000.0, 1)
        total_lifetime_kwh = round(total_lifetime_wh / 1000.0, 1)

        # Record history point for live graph
        history_entry = {
            "time": now_ts.strftime("%H:%M"),
            "date": today_key,
            "epoch": int(now_ts.timestamp()),
            "total_w": total_power_w,
            "s1_w": socket_results[0]["power_w"] if len(socket_results) > 0 else 0.0,
            "s2_w": socket_results[1]["power_w"] if len(socket_results) > 1 else 0.0,
            "s3_w": socket_results[2]["power_w"] if len(socket_results) > 2 else 0.0,
        }
        self.history.append(history_entry)
        self._save_history()

        # Update and persist the FritzBox snapshot (live readings, stats
        # arrays, counter log)
        prune_counter_log(self.snapshot)
        totals = {
            "power_w": total_power_w,
            "today_kwh": total_today_kwh,
            "year_kwh": total_year_kwh,
            "lifetime_kwh": total_lifetime_kwh,
        }
        self.snapshot = build_live_snapshot(
            now_ts, devices, selected_ains, stats_by_ain, totals, self.snapshot
        )
        save_snapshot(self.snapshot)

        return {
            "timestamp": now_ts.strftime("%H:%M:%S"),
            "total_power_w": total_power_w,
            "total_today_kwh": total_today_kwh,
            "total_year_kwh": total_year_kwh,
            "total_lifetime_kwh": total_lifetime_kwh,
            "sockets": socket_results,
        }

    def get_history_for_graph(self) -> List[Dict[str, Any]]:
        today_key = self.get_today_key()
        pts = [p for p in self.history if p.get("date") == today_key]
        if not pts and self.history:
            pts = self.history[-120:]
        return pts
