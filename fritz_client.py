"""
Fritz!Box Smart Home Client
Supports PBKDF2 (FRITZ!OS 7.24+) and legacy MD5 authentication.
Handles automatic detection of the default user for single-password authentication.
"""

import hashlib
import json
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from fritz_snapshot import parse_stats_xml

logger = logging.getLogger("FritzClient")


def calculate_pbkdf2_response(challenge: str, password: str) -> str:
    """
    Computes challenge response for PBKDF2 (format: 2$iter1$salt1$iter2$salt2).
    """
    parts = challenge.split("$")
    if len(parts) < 5:
        raise ValueError(f"Invalid PBKDF2 challenge format: {challenge}")

    iter1 = int(parts[1])
    salt1 = bytes.fromhex(parts[2])
    iter2 = int(parts[3])
    salt2 = bytes.fromhex(parts[4])

    hash1 = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt1, iter1)
    hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, iter2)

    return f"{parts[4]}${hash2.hex()}"


def calculate_md5_response(challenge: str, password: str) -> str:
    """
    Computes challenge response for legacy MD5.
    Format: MD5(UTF-16LE(challenge + '-' + password))
    """
    combined = f"{challenge}-{password}"
    encoded = combined.encode("utf-16le")
    hashed = hashlib.md5(encoded).hexdigest()
    return f"{challenge}-{hashed}"


class FritzClient:
    def __init__(self, host: str = "192.168.178.1", password: str = "", username: str = ""):
        self._host = ""
        self.host = host
        self.password = password
        self.username = username
        self.sid: str = "0000000000000000"

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, val: str):
        v = val.strip().rstrip("/")
        if not v.startswith("http://") and not v.startswith("https://"):
            v = f"http://{v}"
        self._host = v

    def _get_challenge_info(self) -> tuple[str, str]:
        """
        Queries login_sid.lua?version=2 and returns (challenge, default_username).
        """
        url = f"{self.host}/login_sid.lua?version=2"
        req = urllib.request.Request(url, headers={"User-Agent": "FritzBoxSolarMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read()

        root = ET.fromstring(content)
        challenge_el = root.find("Challenge")
        challenge = challenge_el.text if challenge_el is not None and challenge_el.text else ""

        block_time_el = root.find("BlockTime")
        if block_time_el is not None and int(block_time_el.text or 0) > 0:
            raise PermissionError(f"Fritz!Box ist gesperrt. Bitte {block_time_el.text} Sekunden warten.")

        default_user = ""
        user_el = root.find('.//User[@last="1"]')
        if user_el is not None and user_el.text:
            default_user = user_el.text
        else:
            first_user = root.find('.//User')
            if first_user is not None and first_user.text:
                default_user = first_user.text

        return challenge, default_user

    def authenticate(self, password: Optional[str] = None) -> bool:
        """
        Authenticates against Fritz!Box and stores valid SID.
        """
        if password is not None:
            self.password = password

        if not self.password:
            raise ValueError("Kein Kennwort angegeben.")

        challenge, detected_user = self._get_challenge_info()

        # If user explicitly set username, use it, otherwise use detected default user from XML
        active_user = self.username if self.username else detected_user

        if challenge.startswith("2$"):
            response_hash = calculate_pbkdf2_response(challenge, self.password)
        else:
            response_hash = calculate_md5_response(challenge, self.password)

        login_url = f"{self.host}/login_sid.lua"
        params = {"response": response_hash}
        if active_user:
            params["username"] = active_user

        encoded_data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            login_url,
            data=encoded_data,
            headers={"User-Agent": "FritzBoxSolarMonitor/1.0", "Content-Type": "application/x-www-form-urlencoded"},
        )

        with urllib.request.urlopen(req, timeout=6) as resp:
            content = resp.read()

        root = ET.fromstring(content)
        sid_el = root.find("SID")
        sid = sid_el.text if sid_el is not None else "0000000000000000"

        if sid == "0000000000000000":
            raise PermissionError("Anmeldung fehlgeschlagen. Bitte Fritz!Box-Kennwort überprüfen.")

        self.sid = sid
        return True

    def _execute_cmd(self, cmd: str, extra_params: Optional[Dict[str, str]] = None) -> str:
        """
        Executes AHA HTTP command with automatic re-login if SID expired.
        """
        for attempt in range(2):
            if self.sid == "0000000000000000":
                self.authenticate()

            params = {"switchcmd": cmd, "sid": self.sid}
            if extra_params:
                params.update(extra_params)

            url = f"{self.host}/webservices/homeautoswitch.lua?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "FritzBoxSolarMonitor/1.0"})

            try:
                with urllib.request.urlopen(req, timeout=7) as resp:
                    text = resp.read().decode("utf-8", errors="replace").strip()

                # If Fritz!Box returned empty or SID invalid error
                if not text or "0000000000000000" in text or "<html>" in text.lower():
                    self.sid = "0000000000000000"
                    if attempt == 0:
                        continue
                    raise ConnectionError("Fritz!Box Session abgelaufen oder ungültig.")
                return text
            except Exception as e:
                if attempt == 0 and "403" in str(e):
                    self.sid = "0000000000000000"
                    continue
                raise

        raise ConnectionError("Befehl konnte nach Re-Login nicht ausgeführt werden.")

    def get_devices(self) -> List[Dict[str, Any]]:
        """
        Retrieves all smart home devices that measure power.
        Returns list of dicts with:
        - ain: identifier
        - name: device name
        - productname: product name (e.g. FRITZ!DECT 200)
        - present: bool (is device online/reachable)
        - power_w: current power in Watts
        - energy_wh: total energy meter in Watt-hours
        - energy_kwh: total energy meter in kWh
        - voltage_v: voltage in Volts
        """
        xml_text = self._execute_cmd("getdevicelistinfos")
        root = ET.fromstring(xml_text)

        devices = []
        for dev in root.findall("device"):
            ain = dev.get("identifier", "").strip()
            name_el = dev.find("name")
            name = name_el.text if name_el is not None and name_el.text else ain
            product_el = dev.get("productname", "")
            present_el = dev.find("present")
            present = (present_el.text == "1") if present_el is not None else False

            powermeter = dev.find("powermeter")
            if powermeter is None:
                continue

            # Power in mW -> convert to W
            power_el = powermeter.find("power")
            raw_power = int(power_el.text) if power_el is not None and power_el.text and power_el.text != "inval" else 0
            power_w = max(0.0, round(raw_power / 1000.0, 1))

            # Energy in Wh -> convert to kWh
            energy_el = powermeter.find("energy")
            raw_energy = int(energy_el.text) if energy_el is not None and energy_el.text and energy_el.text != "inval" else 0
            energy_wh = float(raw_energy)
            energy_kwh = round(energy_wh / 1000.0, 3)

            # Voltage in mV -> convert to V
            voltage_el = powermeter.find("voltage")
            raw_voltage = int(voltage_el.text) if voltage_el is not None and voltage_el.text and voltage_el.text != "inval" else 0
            voltage_v = round(raw_voltage / 1000.0, 1)

            devices.append({
                "ain": ain,
                "name": name,
                "productname": product_el,
                "present": present,
                "power_w": power_w,
                "energy_wh": energy_wh,
                "energy_kwh": energy_kwh,
                "voltage_v": voltage_v,
            })

        return devices

    def get_device_stats(self, ain: str) -> Dict[str, Any]:
        """
        Retrieves historical stats for a specific AIN, parsed into a plain
        structure: {element: [{"grid": "daily"|"monthly"|..., "grid_s": ...,
        "datatime": "...", "values": [...]}]} (see fritz_snapshot.parse_stats_xml).
        """
        xml_text = self._execute_cmd("getbasicdevicestats", {"ain": ain})
        return parse_stats_xml(xml_text)
