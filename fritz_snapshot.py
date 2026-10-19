"""
Fritz!Box Snapshot: pulls everything available from the box into one simple
JSON structure (data/fritz_snapshot.json).

- The widget app maintains the snapshot at every poll (live readings, energy
  stats arrays, and a persistent per-day counter log used to reconstruct the
  day sum after a restart).
- Run as a script (python fritz_snapshot.py) it performs a full diagnostic
  dump of ALL stats (including temperature/voltage/power histories) into
  data/fritzbox_dump.json and seeds the counter log.
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("FritzSnapshot")

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_FILE = DATA_DIR / "fritz_snapshot.json"
DUMP_FILE = DATA_DIR / "fritzbox_dump.json"

GRIDS = {
    900: "15min",
    3600: "hourly",
    86400: "daily",
    2678400: "monthly",
}


def load_snapshot() -> Dict[str, Any]:
    if SNAPSHOT_FILE.exists():
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load snapshot: {e}")
    return {"counter_log": {}}


def save_snapshot(snap: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving snapshot: {e}")


def parse_stats_xml(xml_text: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Parses getbasicdevicestats XML into a plain structure:
    {element: [{"grid": "daily", "grid_s": 86400, "datatime": iso, "values": [...]}]}
    """
    root = ET.fromstring(xml_text)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for element in root:
        entries = []
        for stats in element.findall("stats"):
            try:
                grid_s = int(stats.get("grid", "0"))
            except ValueError:
                grid_s = 0
            try:
                datatime = int(stats.get("datatime", "0"))
            except ValueError:
                datatime = 0
            values: List[float] = []
            if stats.text:
                for v in stats.text.strip().split(","):
                    v = v.strip()
                    if v and v != "-":
                        try:
                            values.append(float(v))
                        except ValueError:
                            pass
            entries.append({
                "grid": GRIDS.get(grid_s, f"{grid_s}s"),
                "grid_s": grid_s,
                "datatime": datetime.fromtimestamp(datatime).strftime("%Y-%m-%d %H:%M:%S") if datatime else None,
                "values": values,
            })
        if entries:
            out.setdefault(element.tag, []).extend(entries)
    return out


def record_counter_reading(snap: Dict[str, Any], date_key: str, ain: str, wh: float, time_str: str) -> None:
    """Records first/last observed counter value per day and socket."""
    log = snap.setdefault("counter_log", {})
    day = log.setdefault(date_key, {})
    entry = day.get(ain)
    if entry is None:
        day[ain] = {"first_wh": wh, "first_ts": time_str, "last_wh": wh, "last_ts": time_str}
    else:
        entry["last_wh"] = wh
        entry["last_ts"] = time_str


def last_known_reading(snap: Dict[str, Any], ain: str, max_date_key: str) -> Optional[Dict[str, Any]]:
    """
    Most recent persisted counter reading for a socket up to (and including)
    max_date_key. Returns {"value_wh", "date"} or None.
    """
    log = snap.get("counter_log", {})
    for date_key in sorted(log.keys(), reverse=True):
        if date_key > max_date_key:
            continue
        entry = log[date_key].get(ain)
        if entry and entry.get("last_wh", 0) > 0:
            return {"value_wh": entry["last_wh"], "date": date_key}
    return None


def prune_counter_log(snap: Dict[str, Any], keep: int = 30) -> None:
    log = snap.get("counter_log", {})
    if len(log) > keep:
        for key in sorted(log.keys())[:-keep]:
            del log[key]


def build_live_snapshot(
    now_dt: datetime,
    devices: List[Dict[str, Any]],
    tracked_ains: List[str],
    stats_by_ain: Dict[str, Dict[str, List[Dict[str, Any]]]],
    totals: Dict[str, Any],
    snap: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Builds the lean snapshot structure the app keeps up to date at every poll.
    """
    tracked = set(tracked_ains or [])
    dev_out = []
    for d in devices:
        entry = {
            "ain": d["ain"],
            "name": d.get("name", d["ain"]),
            "product": d.get("productname", ""),
            "present": d.get("present", False),
            "power_w": d.get("power_w", 0.0),
            "voltage_v": d.get("voltage_v"),
            "energy_wh": d.get("energy_wh"),
        }
        if d["ain"] in tracked and d["ain"] in stats_by_ain:
            stats = stats_by_ain[d["ain"]]
            for name, key in (("energy_daily_wh", "daily"), ("energy_monthly_wh", "monthly")):
                for series in stats.get("energy", []):
                    if series.get("grid") == key:
                        entry[name] = series["values"]
            if "energy" in stats:
                entry["stats_datatime"] = stats["energy"][0].get("datatime") if stats["energy"] else None
        dev_out.append(entry)

    return {
        "updated": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "totals": totals,
        "devices": dev_out,
        "counter_log": snap.get("counter_log", {}),
    }


def fetch_full_data(client, selected_ains: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Pulls everything from the box: all devices with live readings, plus full
    stats for every metering device (or only the selected ones if given).
    """
    now_dt = datetime.now()
    devices = client.get_devices()
    stats_by_ain: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for d in devices:
        if selected_ains is not None and d["ain"] not in selected_ains:
            continue
        try:
            raw = client._execute_cmd("getbasicdevicestats", {"ain": d["ain"]})
            stats_by_ain[d["ain"]] = parse_stats_xml(raw)
        except Exception as e:
            logger.warning(f"Stats for {d['ain']} failed: {e}")

    return {
        "updated": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(now_dt.timestamp()),
        "host": getattr(client, "host", ""),
        "devices": [
            {
                "ain": d["ain"],
                "name": d.get("name", d["ain"]),
                "product": d.get("productname", ""),
                "present": d.get("present", False),
                "power_w": d.get("power_w"),
                "voltage_v": d.get("voltage_v"),
                "energy_wh": d.get("energy_wh"),
                "stats": stats_by_ain.get(d["ain"], {}),
            }
            for d in devices
        ],
    }


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from config_manager import load_config
    from fritz_client import FritzClient

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_config()
    client = FritzClient(
        host=cfg.get("host", "192.168.178.1"),
        password=cfg.get("password", ""),
        username=cfg.get("username", ""),
    )
    client.authenticate()

    data = fetch_full_data(client)
    DUMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DUMP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Seed the app's counter log with the current readings so the day-sum
    # reconstruction has a starting point even before the widget ran today.
    now_dt = datetime.now()
    snap = load_snapshot()
    for d in data["devices"]:
        if d.get("energy_wh"):
            record_counter_reading(snap, now_dt.strftime("%Y-%m-%d"), d["ain"], d["energy_wh"], now_dt.strftime("%H:%M"))
    prune_counter_log(snap)
    save_snapshot(snap)

    # Human-readable summary
    print(f"Dump gespeichert: {DUMP_FILE}")
    print(f"Snapshot aktualisiert: {SNAPSHOT_FILE}")
    print(f"Stand: {data['updated']}, Ger\u00e4te: {len(data['devices'])}\n")
    for d in data["devices"]:
        print(f"--- {d['name']} ({d['product']}, AIN {d['ain']}) ---")
        print(f"    present={d['present']}  Leistung={d['power_w']} W  Zaehler={d['energy_wh']} Wh")
        for element, series_list in d["stats"].items():
            for s in series_list:
                vals = s["values"]
                span_h = s["grid_s"] * len(vals) / 3600.0 if s["grid_s"] else 0
                print(f"    {element}/{s['grid']}: {len(vals)} Werte, ~{span_h:.1f} h bis {s['datatime']}"
                      f" | letzte 5: {vals[-5:]}")


if __name__ == "__main__":
    main()
