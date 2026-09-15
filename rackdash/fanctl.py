"""Client for the corsair-fanctl daemon.

The dashboard shows *the temperatures you chose to control fans with*, not
every sensor on the host.  corsair-fanctl already holds that decision: each fan
carries a list of sensor ids, a catalog of id -> human label, and the live
readings.  Reading its `/api/state` therefore keeps the two apps in step with
no configuration of its own — tick a new sensor in the fan UI and it appears
here on the next poll.

See https://github.com/rph81/corsair-fanctl.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request

LOG = logging.getLogger("rackdash.fanctl")


class FanctlError(RuntimeError):
    """Raised when the fan controller cannot be reached."""


class Fanctl:
    def __init__(self, url: str, token: str = "", timeout: float = 5.0):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def state(self) -> dict:
        if not self.configured:
            raise FanctlError("fan controller URL is not configured")
        request = urllib.request.Request(f"{self.url}/api/state", method="GET")
        request.add_header("Accept", "application/json")
        if self.token:
            request.add_header("X-Auth-Token", self.token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise FanctlError(
                    "fan controller rejected the token (401); set fanctl.token "
                    "to the value of http.auth_token in its config"
                ) from exc
            raise FanctlError(f"fan controller returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise FanctlError(
                f"cannot reach the fan controller at {self.url}: "
                f"{getattr(exc, 'reason', exc)}"
            ) from exc
        except socket.timeout as exc:
            raise FanctlError(f"fan controller timed out after {self.timeout:g}s") from exc
        except (ValueError, OSError) as exc:
            raise FanctlError(f"bad response from the fan controller: {exc}") from exc

        if not isinstance(payload, dict):
            raise FanctlError("fan controller returned an unexpected payload")
        return payload


def selected_sensor_ids(state: dict) -> list:
    """Sensor ids bound to at least one live fan curve, in fan order.

    Only fans that are enabled and in curve mode count: a channel set to fixed
    or off is not following a temperature, so its sensors are not what the
    dashboard should be showing.
    """
    ids: list = []
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    fans = config.get("fans") if isinstance(config.get("fans"), list) else []
    for fan in fans:
        if not isinstance(fan, dict):
            continue
        if not fan.get("enabled") or fan.get("mode") != "curve":
            continue
        for sensor_id in fan.get("sensors") or []:
            if isinstance(sensor_id, str) and sensor_id and sensor_id not in ids:
                ids.append(sensor_id)
    return ids


def shape(state: dict, source: str = "fanctl-selected",
          explicit: list | None = None) -> dict:
    """Build the temperature panel from a fan controller snapshot."""
    catalog = state.get("sensors") if isinstance(state.get("sensors"), list) else []
    labels = {entry.get("id"): entry.get("label") or entry.get("id")
              for entry in catalog if isinstance(entry, dict) and entry.get("id")}
    kinds = {entry.get("id"): entry.get("source") or ""
             for entry in catalog if isinstance(entry, dict) and entry.get("id")}
    temps = state.get("temps") if isinstance(state.get("temps"), dict) else {}

    if source == "all":
        ids = [entry.get("id") for entry in catalog
               if isinstance(entry, dict) and entry.get("id")]
    elif source == "list":
        ids = [i for i in (explicit or []) if isinstance(i, str)]
    else:
        ids = selected_sensor_ids(state)

    # Which fans each sensor drives, so the dashboard can say why it matters.
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    fan_configs = config.get("fans") if isinstance(config.get("fans"), list) else []
    used_by: dict = {}
    for fan in fan_configs:
        if not isinstance(fan, dict) or not fan.get("enabled"):
            continue
        name = fan.get("name") or f"Fan {fan.get('index')}"
        for sensor_id in fan.get("sensors") or []:
            used_by.setdefault(sensor_id, []).append(name)

    sensors = []
    for sensor_id in ids:
        value = temps.get(sensor_id)
        sensors.append({
            "id": sensor_id,
            "label": labels.get(sensor_id, sensor_id),
            "kind": kinds.get(sensor_id, ""),
            "value": value if isinstance(value, (int, float)) else None,
            "fans": used_by.get(sensor_id, []),
        })

    fans = []
    live = {f.get("index"): f for f in (state.get("fans") or [])
            if isinstance(f, dict)}
    device_fans = {}
    device = state.get("device") if isinstance(state.get("device"), dict) else {}
    for entry in (device.get("fans") or []):
        if isinstance(entry, dict):
            device_fans[entry.get("index")] = entry
    for fan in fan_configs:
        if not isinstance(fan, dict):
            continue
        index = fan.get("index")
        snapshot = live.get(index) or {}
        hardware = device_fans.get(index) or {}
        fans.append({
            "index": index,
            "name": fan.get("name") or f"Fan {index}",
            "mode": fan.get("mode"),
            "enabled": bool(fan.get("enabled")),
            "connected": bool(hardware.get("connected")),
            "type": hardware.get("type") or "",
            "rpm": snapshot.get("rpm"),
            "duty": snapshot.get("duty"),
            "control_temp": snapshot.get("control_temp"),
            "reason": snapshot.get("reason") or "",
        })

    return {
        "connected": bool(state.get("connected")),
        "version": state.get("version") or "",
        "error": state.get("error") or state.get("warning") or None,
        "sensors": sensors,
        "fans": fans,
        "storage": _shape_storage(state),
    }


def _shape_storage(state: dict) -> dict:
    """The drive table from the fan controller's Storage panel, if present."""
    storage = state.get("storage") if isinstance(state.get("storage"), dict) else {}
    if not storage.get("enabled"):
        return {"enabled": False, "drives": []}
    drives = []
    for drive in storage.get("drives") or []:
        if not isinstance(drive, dict):
            continue
        drives.append({
            "slot": drive.get("slot"),
            "path": drive.get("path"),
            "model": drive.get("model"),
            "temperature": drive.get("temperature"),
            "temperature_max": drive.get("temperature_max"),
            "temperature_threshold": drive.get("temperature_threshold"),
            "usage_remaining": drive.get("usage_remaining"),
            "smart_warning": bool(drive.get("smart_warning")),
        })
    return {
        "enabled": True,
        "error": storage.get("error"),
        "stale": bool(storage.get("stale")),
        "drives": drives,
    }
