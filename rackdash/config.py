"""Configuration loading, normalisation and atomic persistence.

Plain JSON so nothing has to be installed on the Pi beyond python3.  Anything
that arrives from the browser goes through `normalize()`, which fills in
defaults and clamps values, so the rest of the program can trust the config
without re-validating it.

Two fields are deliberately *not* settable through the HTTP API, because they
are credentials or hosts the daemon connects to as a service:
`proxmox.token_secret` and `proxmox.host`.  See `Collector.update_config`.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

MAX_FAVORITES = 10

# Colour stops for the arc dials: [percent-of-scale, colour].  The dial colour
# is interpolated between them, so a value sweeps green -> amber -> orange ->
# red as it climbs rather than snapping at a threshold.
# Usage stops are keyed on percent; temperature stops on degrees C, because
# that is how you actually think about a drive or a CPU package.
DEFAULT_USAGE_STOPS = [[0, "#3fb950"], [55, "#d29922"], [75, "#db6d28"], [90, "#f85149"]]
DEFAULT_TEMP_STOPS = [[30, "#3fb950"], [50, "#d29922"], [65, "#db6d28"], [80, "#f85149"]]

PAGES = ("overview", "performance", "temps", "vms", "proxmox", "settings")

# A theme is a whole palette applied across every page. The CSS carries the
# background/panel/text colours; the accent, the dial ramps and the chart
# series colours travel in the config because canvas cannot read CSS custom
# properties. web/app.js holds the matching definitions.
THEMES = ("midnight", "deck", "bambu", "nord", "synth", "matrix", "carbon",
          "ember", "ice", "slate", "daylight", "paper")

DEFAULT_SERIES = ["#4aa3ff", "#3fb950", "#d29922", "#f85149",
                  "#a371f7", "#39c5cf", "#db6d28", "#db61a2"]

# Background patterns drawn behind the pages. Each is one CSS declaration in
# web/style.css, drawn in a neutral at low alpha so a single value reads on
# both dark and light grounds. A pattern is independent of the theme, so
# switching theme keeps whichever backdrop you picked.
PATTERNS = ("none", "dots", "grid", "blueprint", "plate", "hatch", "crosshair",
            "hex", "circuit", "topo", "scan", "carbon", "grain", "glow", "vignette")


def default_config() -> dict:
    return {
        "version": 1,
        "http": {"bind": "127.0.0.1", "port": 8080},
        "proxmox": {
            "host": "",                    # https://192.168.1.10:8006
            "node": "",                    # blank = first node the API reports
            "token_id": "",                # root@pam!rackdash
            "token_secret": "",            # the UUID, file-only
            "verify_tls": False,           # PVE ships a self-signed cert
            "timeout": 8.0,
            "interval": 2.0,               # seconds between polls
            "link_mbit": 1000,             # NIC speed, for the network dial scale
        },
        "fanctl": {
            "enabled": True,
            "url": "",                     # http://192.168.1.10:8899
            "token": "",                   # only if http.auth_token is set there
            "timeout": 5.0,
            "interval": 2.0,
            # fanctl-selected = exactly the sensors assigned to a fan curve, so
            # the dashboard follows what you pick in the fan UI.
            "source": "fanctl-selected",   # fanctl-selected | all | list
            "sensors": [],                 # explicit ids when source == "list"
        },
        "ui": {
            "theme": "midnight",           # see THEMES
            "accent": "#4aa3ff",
            "favorites": [],
            "series": list(DEFAULT_SERIES),   # chart line colours
            "dial_color": "threshold",     # threshold | accent
            "usage_stops": [list(s) for s in DEFAULT_USAGE_STOPS],
            "temp_stops": [list(s) for s in DEFAULT_TEMP_STOPS],
            "temp_min": 20,                # dial scale for temperature arcs
            "temp_max": 90,
            "chart_fill": True,
            "animate": True,
            "start_page": "overview",
            "rotate_seconds": 0,           # >0 cycles pages automatically
            "dim_after": 0,                # >0 dims the screen after N seconds idle
            "dim_level": 25,               # percent brightness of the dim overlay
            "clock_24h": True,
            "show_vm_cpu": True,
            "pattern": "none",             # see PATTERNS
            "pattern_strength": 100,       # percent, 0 turns the backdrop off
            # Blank means "follow the accent", which is what most people want;
            # a hex value pins the corner glow to its own colour.
            "glow_color": "",
        },
    }


# --------------------------------------------------------------------------
# coercion helpers
# --------------------------------------------------------------------------

def _num(value: Any, lo: float, hi: float, fallback: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return fallback
    if n != n:  # NaN
        return fallback
    return max(lo, min(hi, n))


def _int(value: Any, lo: int, hi: int, fallback: int) -> int:
    return int(round(_num(value, lo, hi, fallback)))


def _one_of(value: Any, allowed: tuple, fallback: str) -> str:
    return value if value in allowed else fallback


def _text(value: Any, fallback: str = "", limit: int = 200) -> str:
    if isinstance(value, str):
        return value.strip()[:limit]
    return fallback


def _hex_colour(value: Any, fallback: str) -> str:
    """Accept only #rrggbb: the value is written into a CSS custom property."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if re.fullmatch(r"#[0-9a-f]{6}", candidate):
            return candidate
    return fallback


def _stops(raw: Any, fallback: list, lo: int = 0, hi: int = 100) -> list:
    """Normalise colour stops to sorted [[at, #rrggbb], ...].

    `at` is a percentage for usage dials and a temperature for thermal ones,
    hence the caller-supplied range.
    """
    stops: list = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                at, colour = item.get("at"), item.get("color")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                at, colour = item[0], item[1]
            else:
                continue
            colour = _hex_colour(colour, "")
            if not colour:
                continue
            stops.append([_int(at, lo, hi, lo), colour])
    if not stops:
        return [list(s) for s in fallback]
    stops.sort(key=lambda s: s[0])
    deduped: list = []
    for stop in stops:
        if deduped and deduped[-1][0] == stop[0]:
            deduped[-1] = stop
        else:
            deduped.append(stop)
    return deduped[:8]


def _normalize_url(value: Any, fallback: str = "") -> str:
    """Keep scheme://host[:port] and drop any path, so joins stay predictable."""
    url = _text(value, fallback)
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def normalize(raw: Any) -> dict:
    """Return a fully-populated, range-checked config from arbitrary input."""
    cfg = default_config()
    if not isinstance(raw, dict):
        return cfg

    http_raw = raw.get("http") if isinstance(raw.get("http"), dict) else {}
    bind = http_raw.get("bind")
    cfg["http"]["bind"] = bind if isinstance(bind, str) and bind.strip() else cfg["http"]["bind"]
    cfg["http"]["port"] = _int(http_raw.get("port"), 1, 65535, cfg["http"]["port"])

    pve_raw = raw.get("proxmox") if isinstance(raw.get("proxmox"), dict) else {}
    pve = cfg["proxmox"]
    pve["host"] = _normalize_url(pve_raw.get("host"), pve["host"])
    pve["node"] = _text(pve_raw.get("node"), pve["node"], 64)
    pve["token_id"] = _text(pve_raw.get("token_id"), pve["token_id"], 128)
    pve["token_secret"] = _text(pve_raw.get("token_secret"), pve["token_secret"], 256)
    pve["verify_tls"] = bool(pve_raw.get("verify_tls", pve["verify_tls"]))
    pve["timeout"] = round(_num(pve_raw.get("timeout"), 1.0, 60.0, pve["timeout"]), 1)
    pve["interval"] = round(_num(pve_raw.get("interval"), 1.0, 60.0, pve["interval"]), 1)
    pve["link_mbit"] = _int(pve_raw.get("link_mbit"), 1, 100000, pve["link_mbit"])

    fan_raw = raw.get("fanctl") if isinstance(raw.get("fanctl"), dict) else {}
    fan = cfg["fanctl"]
    fan["enabled"] = bool(fan_raw.get("enabled", fan["enabled"]))
    fan["url"] = _normalize_url(fan_raw.get("url"), fan["url"])
    fan["token"] = _text(fan_raw.get("token"), fan["token"], 256)
    fan["timeout"] = round(_num(fan_raw.get("timeout"), 1.0, 60.0, fan["timeout"]), 1)
    fan["interval"] = round(_num(fan_raw.get("interval"), 1.0, 60.0, fan["interval"]), 1)
    fan["source"] = _one_of(fan_raw.get("source"),
                            ("fanctl-selected", "all", "list"), fan["source"])
    sensors = fan_raw.get("sensors")
    if isinstance(sensors, str):
        sensors = [sensors]
    if isinstance(sensors, list):
        fan["sensors"] = [s.strip()[:128] for s in sensors
                          if isinstance(s, str) and s.strip()][:16]

    ui_raw = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    ui = cfg["ui"]
    # "dark"/"light" were the only themes before the palette system; map them
    # onto their replacements so an older config keeps working.
    legacy = {"dark": "midnight", "light": "daylight"}
    theme = ui_raw.get("theme")
    theme = legacy.get(theme, theme)
    ui["theme"] = _one_of(theme, THEMES, ui["theme"])
    ui["accent"] = _hex_colour(ui_raw.get("accent"), ui["accent"])
    ui["dial_color"] = _one_of(ui_raw.get("dial_color"), ("threshold", "accent"),
                               ui["dial_color"])
    ui["usage_stops"] = _stops(ui_raw.get("usage_stops"), DEFAULT_USAGE_STOPS, 0, 100)
    ui["temp_stops"] = _stops(ui_raw.get("temp_stops"), DEFAULT_TEMP_STOPS, -20, 150)
    ui["temp_min"] = _int(ui_raw.get("temp_min"), -20, 150, ui["temp_min"])
    ui["temp_max"] = _int(ui_raw.get("temp_max"), -20, 200, ui["temp_max"])
    if ui["temp_max"] <= ui["temp_min"]:
        ui["temp_max"] = ui["temp_min"] + 10
    ui["chart_fill"] = bool(ui_raw.get("chart_fill", ui["chart_fill"]))
    ui["animate"] = bool(ui_raw.get("animate", ui["animate"]))
    ui["start_page"] = _one_of(ui_raw.get("start_page"), PAGES, ui["start_page"])
    ui["rotate_seconds"] = _int(ui_raw.get("rotate_seconds"), 0, 3600, ui["rotate_seconds"])
    ui["dim_after"] = _int(ui_raw.get("dim_after"), 0, 86400, ui["dim_after"])
    ui["dim_level"] = _int(ui_raw.get("dim_level"), 0, 90, ui["dim_level"])
    ui["clock_24h"] = bool(ui_raw.get("clock_24h", ui["clock_24h"]))
    ui["pattern"] = _one_of(ui_raw.get("pattern"), PATTERNS, ui["pattern"])
    ui["pattern_strength"] = _int(ui_raw.get("pattern_strength"), 0, 200,
                                  ui["pattern_strength"])
    glow = ui_raw.get("glow_color")
    ui["glow_color"] = _hex_colour(glow, "") if isinstance(glow, str) and glow.strip() else ""
    ui["show_vm_cpu"] = bool(ui_raw.get("show_vm_cpu", ui["show_vm_cpu"]))

    favorites: list = []
    raw_favorites = ui_raw.get("favorites")
    if isinstance(raw_favorites, list):
        for item in raw_favorites:
            colour = _hex_colour(item, "")
            if colour and colour not in favorites:
                favorites.append(colour)
    ui["favorites"] = favorites[:MAX_FAVORITES]

    series: list = []
    raw_series = ui_raw.get("series")
    if isinstance(raw_series, list):
        for item in raw_series:
            colour = _hex_colour(item, "")
            if colour:
                series.append(colour)
    ui["series"] = (series or list(DEFAULT_SERIES))[:8]

    return cfg


# --------------------------------------------------------------------------
# merging
# --------------------------------------------------------------------------

def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively overlay `patch` on `base`. Lists and scalars replace."""
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge(current: dict, patch: Any) -> dict:
    """Overlay a partial config on the running one, then normalise.

    Without this a PUT naming only `ui` would reset the Proxmox connection,
    because `normalize()` fills every absent key with its default.
    """
    if not isinstance(patch, dict):
        return normalize(current)
    return normalize(_deep_merge(current, patch))


def public(cfg: dict) -> dict:
    """The config with secrets removed, safe to hand to the browser."""
    redacted = json.loads(json.dumps(cfg))
    secret = redacted.get("proxmox", {})
    secret["token_secret"] = "set" if cfg["proxmox"]["token_secret"] else ""
    fan = redacted.get("fanctl", {})
    fan["token"] = "set" if cfg["fanctl"]["token"] else ""
    return redacted


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def load(path: str) -> dict:
    """Load and normalise the config, falling back to defaults when missing."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return normalize(json.load(handle))
    except FileNotFoundError:
        return default_config()
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"cannot read config {path}: {exc}") from exc


def save(path: str, cfg: dict) -> None:
    """Write the config atomically so a crash can never truncate it."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)   # holds the API token
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
