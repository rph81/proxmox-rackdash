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
import urllib.parse
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
        payload = self._get("/api/state")
        if not isinstance(payload, dict):
            raise FanctlError("fan controller returned an unexpected payload")
        return payload

    def history(self, since: float, points: int = 400) -> list:
        """The fan app's own recorded samples since `since` (epoch seconds).

        corsair-fanctl keeps this history on disk across restarts, and each
        sample carries every sensor reading plus each fan's rpm and duty, so a
        long temperature chart or a single fan's history needs nothing stored
        on the Pi.
        """
        query = urllib.parse.urlencode({"since": f"{since:.0f}", "points": int(points)})
        payload = self._get(f"/api/history?{query}")
        samples = payload.get("samples") if isinstance(payload, dict) else None
        return samples if isinstance(samples, list) else []

    def set_fan(self, index: int, changes: dict) -> dict:
        """Change one fan's mode or fixed duty in the fan app.

        Only the two things the touchscreen offers can be sent: switching
        between curve and fixed, and the fixed duty. Everything else about a
        fan (sensors, curve, limits) stays the fan app's business.
        """
        body = clean_fan_patch(changes)
        if not body:
            raise ValueError("nothing to change: send mode and/or fixed_duty")
        return self._get(f"/api/fan/{int(index)}", method="POST", body=body)

    def _get(self, path: str, method: str = "GET", body: dict | None = None):
        if not self.configured:
            raise FanctlError("fan controller URL is not configured")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            # The fan app refuses a state-changing body sent as anything else.
            request.add_header("Content-Type", "application/json")
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
        return payload


def clean_fan_patch(changes) -> dict:
    """The subset of a fan change rackdash is allowed to forward."""
    if not isinstance(changes, dict):
        return {}
    body: dict = {}
    mode = changes.get("mode")
    if mode is not None:
        if mode not in ("curve", "fixed"):
            raise ValueError("mode must be curve or fixed")
        body["mode"] = mode
    duty = changes.get("fixed_duty")
    if duty is not None:
        if isinstance(duty, bool) or not isinstance(duty, (int, float)) or duty != duty:
            raise ValueError("fixed_duty must be a number")
        body["fixed_duty"] = int(round(max(0.0, min(100.0, float(duty)))))
    return body


def bound_fixed_duty(body: dict, fan: dict) -> dict:
    """Clamp a fixed duty to the fan's min/max duty from the fan app."""
    if "fixed_duty" not in body:
        return body
    number = lambda v, d: v if isinstance(v, (int, float)) and not isinstance(v, bool) else d  # noqa: E731
    low = number(fan.get("min_duty"), 0)
    high = max(low, number(fan.get("max_duty"), 100))
    return dict(body, fixed_duty=int(max(low, min(high, body["fixed_duty"]))))


def selected_sensor_ids(state: dict) -> list:
    """The sensors the fan app graphs, in fan order, de-duplicated.

    That is every sensor any fan is assigned, whatever the fan's mode, plus
    anything picked explicitly under Graph in the fan app's Sensors dialog.
    Mode deliberately does not filter: a fan set to fixed still reads its
    sensors and reports a control temperature, so dropping them made every
    temperature vanish from the dashboard the moment fans were fixed. This
    mirrors corsair-fanctl's own history chart, which made the same change.
    """
    ids: list = []

    def add(sensor_id) -> None:
        if isinstance(sensor_id, str) and sensor_id and sensor_id not in ids:
            ids.append(sensor_id)

    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    fans = config.get("fans") if isinstance(config.get("fans"), list) else []
    for fan in fans:
        if isinstance(fan, dict):
            for sensor_id in fan.get("sensors") or []:
                add(sensor_id)
    ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
    for sensor_id in ui.get("chart_sensors") or []:
        add(sensor_id)
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
            # What the detail view needs to draw the fan's curve and explain
            # which readings drive it.
            "sensors": [sid for sid in (fan.get("sensors") or []) if isinstance(sid, str)],
            "sensor_labels": [labels.get(sid, sid) for sid in (fan.get("sensors") or [])
                              if isinstance(sid, str)],
            "mix": fan.get("mix") or "max",
            "curve": _clean_curve(fan.get("curve")),
            "fixed_duty": fan.get("fixed_duty"),
            "min_duty": fan.get("min_duty"),
            "max_duty": fan.get("max_duty"),
            "stop_below": fan.get("stop_below"),
        })

    return {
        "connected": bool(state.get("connected")),
        "version": state.get("version") or "",
        "error": state.get("error") or state.get("warning") or None,
        "sensors": sensors,
        "fans": fans,
        "storage": _shape_storage(state),
    }


def _clean_curve(raw) -> list:
    """[[temp, duty], ...] with anything malformed dropped."""
    points = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                points.append([float(item[0]), float(item[1])])
            except (TypeError, ValueError):
                continue
    points.sort(key=lambda p: p[0])
    return points


def shape_history(samples: list, sensor_ids: list, fan_indexes: list) -> dict:
    """Turn the fan app's samples into parallel series keyed for the charts.

    Each sample is {t, temps: {id: C}, rpm: {index: rpm}, duty: {index: %}}.
    JSON turns the integer fan indexes into strings, so both are accepted and
    the output is keyed by string. Only the sensors and fans the dashboard can
    show are kept, which keeps a ten-hour payload small.
    """
    out = {"time": [],
           "temps": {sid: [] for sid in sensor_ids},
           "rpm": {str(i): [] for i in fan_indexes},
           "duty": {str(i): [] for i in fan_indexes}}
    for sample in samples if isinstance(samples, list) else []:
        if not isinstance(sample, dict) or not isinstance(sample.get("t"), (int, float)):
            continue
        out["time"].append(round(float(sample["t"]), 1))
        temps = sample.get("temps") if isinstance(sample.get("temps"), dict) else {}
        rpm = sample.get("rpm") if isinstance(sample.get("rpm"), dict) else {}
        duty = sample.get("duty") if isinstance(sample.get("duty"), dict) else {}
        for sid in sensor_ids:
            value = temps.get(sid)
            out["temps"][sid].append(value if isinstance(value, (int, float)) else None)
        for index in fan_indexes:
            key = str(index)
            for name, source in (("rpm", rpm), ("duty", duty)):
                value = source.get(key, source.get(index))
                out[name][key].append(value if isinstance(value, (int, float)) else None)
    return out


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
