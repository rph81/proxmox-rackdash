#!/usr/bin/env python3
"""Self-tests for the parts that decide what the screen shows.

Run with:  python3 tools/selftest.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rackdash import config as cfg  # noqa: E402
from rackdash import fanctl as fanctl_module  # noqa: E402
from rackdash import proxmox  # noqa: E402
from rackdash.collector import _due  # noqa: E402
from rackdash.history import Series  # noqa: E402
from rackdash import host as host_module  # noqa: E402
from rackdash.httpd import make_server  # noqa: E402

FAILURES: list = []
GIB = 1024 ** 3


def check(name, actual, expected):
    if actual == expected:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {name}: expected {expected!r}, got {actual!r}")


def close(name, actual, expected, tol=1e-6):
    if actual is not None and abs(actual - expected) <= tol:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}: expected ~{expected}, got {actual}")
        print(f"  FAIL {name}: expected ~{expected}, got {actual}")


# --------------------------------------------------------------------------

def test_config_normalisation():
    print("config normalisation")
    base = cfg.default_config()
    check("default theme", base["ui"]["theme"], "midnight")
    check("default bind is loopback", base["http"]["bind"], "127.0.0.1")
    check("series palette present", len(base["ui"]["series"]), 8)

    hostile = cfg.normalize({
        "http": {"port": 99999},
        "proxmox": {"host": "192.168.1.10:8006", "interval": 0.01, "link_mbit": -5},
        "fanctl": {"source": "banana", "sensors": "cpro:temp1"},
        "ui": {"theme": "nope", "accent": "red", "temp_min": 80, "temp_max": 20,
               "dim_level": 999, "usage_stops": [[200, "#ff0000"], ["x", "#00ff00"]]},
    })
    check("port clamped", hostile["http"]["port"], 65535)
    check("bare host gets a scheme", hostile["proxmox"]["host"], "https://192.168.1.10:8006")
    check("interval floored", hostile["proxmox"]["interval"], 1.0)
    check("link speed floored", hostile["proxmox"]["link_mbit"], 1)
    check("unknown source falls back", hostile["fanctl"]["source"], "fanctl-selected")
    check("bare sensor string accepted", hostile["fanctl"]["sensors"], ["cpro:temp1"])
    check("unknown theme falls back", hostile["ui"]["theme"], "midnight")
    check("bad colour falls back", hostile["ui"]["accent"], "#4aa3ff")
    check("temp max raised above min", hostile["ui"]["temp_max"], 90)
    check("dim level clamped", hostile["ui"]["dim_level"], 90)
    check("stop clamped and kept", hostile["ui"]["usage_stops"][-1], [100, "#ff0000"])

    # Themes replaced the old two-value setting; old configs must still load.
    check("legacy dark", cfg.normalize({"ui": {"theme": "dark"}})["ui"]["theme"], "midnight")
    check("legacy light", cfg.normalize({"ui": {"theme": "light"}})["ui"]["theme"], "daylight")
    for theme in cfg.THEMES:
        if cfg.normalize({"ui": {"theme": theme}})["ui"]["theme"] != theme:
            FAILURES.append(f"theme {theme} was not accepted")
    print(f"  ok   all {len(cfg.THEMES)} themes accepted")

    check("garbage input yields defaults", cfg.normalize("nonsense")["version"], 1)

    # Backdrops are independent of the theme, so a theme change must not
    # silently reset the pattern someone chose.
    check("default backdrop is off", base["ui"]["pattern"], "none")
    for pattern in cfg.PATTERNS:
        if cfg.normalize({"ui": {"pattern": pattern}})["ui"]["pattern"] != pattern:
            FAILURES.append(f"pattern {pattern} was not accepted")
    print(f"  ok   all {len(cfg.PATTERNS)} backdrops accepted")
    backdrop = cfg.normalize({"ui": {"pattern": "zzz", "pattern_strength": 900,
                                     "glow_color": "#FF8800"}})["ui"]
    check("unknown pattern falls back", backdrop["pattern"], "none")
    check("strength clamped", backdrop["pattern_strength"], 200)
    check("glow colour lowercased", backdrop["glow_color"], "#ff8800")
    check("blank glow means follow the accent",
          cfg.normalize({"ui": {"glow_color": "   "}})["ui"]["glow_color"], "")
    check("bad glow colour rejected",
          cfg.normalize({"ui": {"glow_color": "rebeccapurple"}})["ui"]["glow_color"], "")
    kept = cfg.merge(cfg.normalize({"ui": {"pattern": "circuit"}}),
                     {"ui": {"theme": "bambu"}})["ui"]
    check("changing theme keeps the backdrop", kept["pattern"], "circuit")


def test_secrets_never_leave():
    print("secret handling")
    current = cfg.default_config()
    current["proxmox"].update({"host": "https://pve.lan:8006", "token_secret": "uuid-here"})
    current["fanctl"]["token"] = "fan-token"

    shown = cfg.public(current)
    check("token redacted", shown["proxmox"]["token_secret"], "set")
    check("fan token redacted", shown["fanctl"]["token"], "set")
    check("original untouched", current["proxmox"]["token_secret"], "uuid-here")
    check("redaction is a copy",
          json.dumps(shown) != json.dumps(current), True)

    blank = cfg.public(cfg.default_config())
    check("absent token reads empty", blank["proxmox"]["token_secret"], "")

    # A partial PUT must not wipe the connection settings.
    merged = cfg.merge(current, {"ui": {"theme": "bambu"}})
    check("merge keeps host", merged["proxmox"]["host"], "https://pve.lan:8006")
    check("merge keeps token", merged["proxmox"]["token_secret"], "uuid-here")
    check("merge applies the patch", merged["ui"]["theme"], "bambu")


def test_proxmox_shaping():
    print("proxmox shaping")
    status = proxmox.shape_status({
        "cpu": 0.255, "cpuinfo": {"cpus": 24, "model": "Xeon"},
        "memory": {"total": 128 * GIB, "used": 32 * GIB},
        "swap": {"total": 8 * GIB, "used": 0},
        "rootfs": {"total": 100 * GIB, "used": 25 * GIB},
        "loadavg": ["1.50", "1.20", "0.90"], "uptime": 90061,
    })
    close("cpu becomes percent", status["cpu"], 25.5)
    close("memory percent", status["memory"]["percent"], 25.0)
    close("disk percent", status["disk"]["percent"], 25.0)
    check("load parsed from strings", status["load"], [1.5, 1.2, 0.9])
    check("swap with zero used", status["swap"]["percent"], 0.0)

    # Proxmox versions differ; missing sections must not raise.
    empty = proxmox.shape_status({})
    check("empty status is safe", empty["memory"]["percent"], 0.0)
    check("missing cpus", empty["cpus"], 0)

    rrd = proxmox.shape_rrd([
        {"time": 100, "cpu": 0.5, "memtotal": 100, "memused": 40, "netin": 1000},
        {"time": 160},                       # a bucket with no data yet
        {"time": 220, "cpu": 0.25, "memtotal": 100, "memused": 60, "netout": 2000},
    ])
    check("rrd times", rrd["time"], [100, 160, 220])
    check("empty bucket becomes a gap, not zero", rrd["cpu"], [50.0, None, 25.0])
    check("memory computed from the pair", rrd["memory"], [40.0, None, 60.0])
    check("absent netin is a gap", rrd["netin"], [1000.0, None, None])

    guests = proxmox.shape_guests([
        {"type": "qemu", "vmid": 101, "name": "beta", "node": "pve", "status": "stopped",
         "maxmem": 4 * GIB, "mem": 0, "cpu": 0, "maxcpu": 2},
        {"type": "lxc", "vmid": 200, "name": "alpha", "node": "pve", "status": "running",
         "maxmem": 2 * GIB, "mem": GIB, "cpu": 0.5, "maxcpu": 1},
        {"type": "storage", "storage": "local", "node": "pve"},
        {"type": "qemu", "vmid": 300, "name": "elsewhere", "node": "pve2", "status": "running"},
    ], "pve")
    check("storage and other nodes excluded", [g["id"] for g in guests], [200, 101])
    check("running sorts first", guests[0]["name"], "alpha")
    close("guest memory percent", guests[0]["mem_percent"], 50.0)
    check("type label", guests[0]["type"], "LXC")

    pools = proxmox.shape_storage([
        {"storage": "local", "type": "dir", "active": 1, "total": 100, "used": 30},
        {"storage": "gone", "type": "nfs", "active": 0, "total": 0, "used": 0},
    ])
    check("inactive pool dropped", [p["name"] for p in pools], ["local"])
    close("pool percent", pools[0]["percent"], 30.0)

    netstat = proxmox.shape_netstat([
        {"vmid": 100, "dev": "net0", "in": 10, "out": 5},
        {"vmid": 100, "dev": "net1", "in": 1, "out": 2},
        {"vmid": 101, "dev": "net0", "in": 7, "out": 7},
        {"dev": "net0", "in": 99, "out": 99},        # no vmid
    ])
    check("counters summed per guest", netstat["100"], {"in": 11.0, "out": 7.0})
    check("entry without a vmid ignored", "None" in netstat, False)

    detail = proxmox.shape_guest_status({
        "name": "plex", "status": "running", "cpu": 0.4, "cpus": 4,
        "maxmem": 8 * GIB, "mem": 2 * GIB, "maxdisk": 16 * GIB, "disk": 0,
        "netin": 500, "netout": 250, "uptime": 3600,
    })
    close("guest cpu percent", detail["cpu"], 40.0)
    close("guest memory percent", detail["mem_percent"], 25.0)
    # A QEMU guest without the agent reports disk 0; that is "unknown", not 0%.
    check("missing disk flagged rather than shown as empty", detail["disk_known"], False)
    check("disk percent stays zero when unknown", detail["disk_percent"], 0.0)


def test_fanctl_selection():
    print("fan controller sensor selection")
    state = {
        "connected": True, "version": "1.4.2",
        "temps": {"cpro:temp1": 30.5, "hwmon:coretemp:temp1": 46.0,
                  "arcconf:1:max": 43.0, "hwmon:nvme:temp1": 41.0},
        "sensors": [
            {"id": "cpro:temp1", "label": "Commander Pro · Probe 1", "source": "device"},
            {"id": "hwmon:coretemp:temp1", "label": "CPU (Intel) · Package id 0", "source": "host"},
            {"id": "arcconf:1:max", "label": "Disk · Hottest drive", "source": "storage"},
            {"id": "hwmon:nvme:temp1", "label": "NVMe · Composite", "source": "host"},
        ],
        "fans": [{"index": 1, "rpm": 1185, "duty": 50.0, "control_temp": 43.0, "reason": "curve"},
                 {"index": 4, "rpm": None, "duty": 0.0, "reason": "no-sensors"}],
        "device": {"fans": [{"index": 1, "connected": True, "type": "PWM"},
                            {"index": 4, "connected": False, "type": None}]},
        "config": {"fans": [
            {"index": 1, "name": "Drives Exhaust", "enabled": True, "mode": "curve",
             "sensors": ["arcconf:1:max"]},
            {"index": 2, "name": "Rear Exhaust", "enabled": True, "mode": "curve",
             "sensors": ["hwmon:coretemp:temp1", "arcconf:1:max"]},
            {"index": 3, "name": "SAS Card", "enabled": True, "mode": "curve",
             "sensors": ["cpro:temp1"]},
            {"index": 4, "name": "Fixed fan", "enabled": True, "mode": "fixed",
             "sensors": ["hwmon:nvme:temp1"]},
            {"index": 5, "name": "Disabled", "enabled": False, "mode": "curve",
             "sensors": ["hwmon:nvme:temp1"]},
        ]},
    }

    # This is the feature: the dashboard mirrors what drives a fan curve.
    check("only live curve sensors, in fan order, de-duplicated",
          fanctl_module.selected_sensor_ids(state),
          ["arcconf:1:max", "hwmon:coretemp:temp1", "cpro:temp1"])

    shaped = fanctl_module.shape(state)
    check("three sensors shown", [s["id"] for s in shaped["sensors"]],
          ["arcconf:1:max", "hwmon:coretemp:temp1", "cpro:temp1"])
    close("reading attached", shaped["sensors"][0]["value"], 43.0)
    check("label from the catalog", shaped["sensors"][1]["label"], "CPU (Intel) · Package id 0")
    check("names the fans it drives", shaped["sensors"][0]["fans"],
          ["Drives Exhaust", "Rear Exhaust"])
    check("fan hardware state merged", shaped["fans"][0]["connected"], True)
    check("unconnected channel flagged", shaped["fans"][3]["connected"], False)

    everything = fanctl_module.shape(state, "all")
    check("all mode shows the whole catalog", len(everything["sensors"]), 4)

    explicit = fanctl_module.shape(state, "list", ["hwmon:nvme:temp1"])
    check("explicit list honoured", [s["id"] for s in explicit["sensors"]], ["hwmon:nvme:temp1"])

    # A sensor can be selected but unreadable; it must still be listed.
    missing = dict(state, temps={})
    check("missing reading kept as None",
          fanctl_module.shape(missing)["sensors"][0]["value"], None)

    check("empty state is safe", fanctl_module.shape({})["sensors"], [])
    check("a fan with no sensors contributes nothing",
          fanctl_module.selected_sensor_ids({"config": {"fans": [
              {"index": 1, "enabled": True, "mode": "curve", "sensors": []}]}}), [])


def test_due_and_history():
    print("scheduling and history")
    # time.monotonic() starts near zero on some platforms, so a "never run" job
    # must be due immediately rather than waiting out the interval.
    check("never-run job is due now", _due(None, 60.0, 0.03), True)
    check("recent job is not due", _due(100.0, 60.0, 130.0), False)
    check("elapsed job is due", _due(100.0, 60.0, 161.0), True)

    series = Series(seconds=10, interval=2.0)
    for i in range(3):
        series.append({"cpu": i * 10.0}, when=1000 + i)
    data = series.series()
    check("times recorded", data["time"], [1000.0, 1001.0, 1002.0])
    check("values recorded", data["cpu"], [0.0, 10.0, 20.0])

    series.append({"memory": 50.0}, when=1003)
    data = series.series()
    check("a key absent from older samples is a gap",
          data["memory"], [None, None, None, 50.0])

    small = Series(seconds=4, interval=2.0)
    for i in range(200):
        small.append({"x": i})
    check("ring buffer stays bounded", len(small) <= 60, True)


class _StubCollector:
    def __init__(self):
        self.config = cfg.default_config()
        self.calls: list = []

    def snapshot(self):
        return {"config": cfg.public(self.config)}

    def live(self):
        return {"system": {}, "temps": {}}

    def rrd(self):
        return {}

    def update_config(self, patch):
        self.calls.append(patch)
        self.config = cfg.merge(self.config, patch)
        return cfg.public(self.config)

    def refresh(self):
        self.calls.append("refresh")

    def guest(self, kind, vmid, timeframe="hour"):
        self.calls.append((kind, vmid, timeframe))
        return {"id": vmid, "type": kind, "timeframe": timeframe}


def test_http():
    print("http api")
    stub = _StubCollector()
    server = make_server(stub, "127.0.0.1", 0)
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request(method, path, body=None, headers=None):
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    try:
        status, body = request("GET", "/api/state")
        check("state served", status, 200)
        check("token never reaches the browser", "token_secret\": \"\"" in body, True)

        # The dashboard holds a Proxmox token, so another site must not drive it.
        check("text/plain PUT refused",
              request("PUT", "/api/config", b'{"ui":{"theme":"bambu"}}',
                      {"Content-Type": "text/plain"})[0], 403)
        check("foreign origin refused",
              request("PUT", "/api/config", b'{"ui":{"theme":"bambu"}}',
                      {"Content-Type": "application/json",
                       "Origin": "http://evil.example"})[0], 403)
        check("nothing was applied", stub.calls, [])

        check("same-origin JSON PUT allowed",
              request("PUT", "/api/config", b'{"ui":{"theme":"bambu"}}',
                      {"Content-Type": "application/json",
                       "Origin": f"http://{host}:{port}"})[0], 200)
        check("theme applied", stub.config["ui"]["theme"], "bambu")

        check("guest route reaches the collector",
              request("GET", "/api/guest/lxc/200")[0], 200)
        check("guest args parsed", stub.calls[-1], ("lxc", 200, "hour"))
        check("timeframe honoured",
              (request("GET", "/api/guest/qemu/100?timeframe=week"),
               stub.calls[-1])[1], ("qemu", 100, "week"))
        # The vmid and type land in an upstream URL, so both are validated.
        check("bad guest type refused", request("GET", "/api/guest/root/1")[0], 400)
        check("non-numeric vmid refused", request("GET", "/api/guest/qemu/abc")[0], 400)
        check("path traversal refused", request("GET", "/api/guest/qemu/..%2f..%2fetc")[0], 400)

        check("unknown route is 404", request("GET", "/api/nope")[0], 404)
        check("static traversal blocked", request("GET", "/../rackdash/config.py")[0], 404)
    finally:
        server.shutdown()
        server.server_close()


def test_host_stats():
    print("this machine's vitals")
    with tempfile.TemporaryDirectory() as tmp:
        # Thermal zones are matched by type, not by number: zone 0 is the CPU
        # on a Pi but can be a battery or a wifi chip elsewhere.
        for index, (kind, value) in enumerate((("battery", "31000"),
                                               ("cpu-thermal", "48200"))):
            os.makedirs(os.path.join(tmp, f"thermal_zone{index}"))
            for name, text in (("type", kind), ("temp", value)):
                with open(os.path.join(tmp, f"thermal_zone{index}", name), "w",
                          encoding="utf-8") as handle:
                    handle.write(text)

        saved_glob, saved_stat = host_module.THERMAL_GLOB, host_module.PROC_STAT
        try:
            host_module.THERMAL_GLOB = os.path.join(tmp, "thermal_zone*/type")
            stats = host_module.HostStats()
            close("picks the cpu zone, not the battery", stats.temperature(), 48.2)

            # Some kernels report whole degrees instead of millidegrees.
            with open(os.path.join(tmp, "thermal_zone1", "temp"), "w",
                      encoding="utf-8") as handle:
                handle.write("52")
            stats._thermal = None
            close("whole degrees handled", stats.temperature(), 52.0)

            with open(os.path.join(tmp, "thermal_zone1", "temp"), "w",
                      encoding="utf-8") as handle:
                handle.write("not a number")
            stats._thermal = None
            check("unreadable temperature is None", stats.temperature(), None)

            host_module.THERMAL_GLOB = os.path.join(tmp, "nothing-here*/type")
            check("no thermal zone at all is None",
                  host_module.HostStats().temperature(), None)

            # CPU busy is a delta, so the first sample has nothing to report.
            stat_path = os.path.join(tmp, "stat")
            host_module.PROC_STAT = stat_path
            with open(stat_path, "w", encoding="utf-8") as handle:
                handle.write("cpu  100 0 100 800 0 0 0 0 0 0\n")
            cpu = host_module.HostStats()
            check("first cpu sample is None", cpu.cpu_percent(), None)
            with open(stat_path, "w", encoding="utf-8") as handle:
                handle.write("cpu  150 0 150 900 0 0 0 0 0 0\n")
            close("busy computed from the delta", cpu.cpu_percent(), 50.0)
            # Polling faster than the kernel ticks must hold the last figure
            # rather than blink the readout to a dash.
            close("an unchanged sample holds the last figure", cpu.cpu_percent(), 50.0)

            with open(stat_path, "w", encoding="utf-8") as handle:
                handle.write("garbage\n")
            check("malformed /proc/stat is None", cpu.cpu_percent(), None)

            host_module.PROC_STAT = os.path.join(tmp, "absent")
            check("missing /proc/stat is None",
                  host_module.HostStats().cpu_percent(), None)
        finally:
            host_module.THERMAL_GLOB, host_module.PROC_STAT = saved_glob, saved_stat

    # No GPU source is a dash on screen, never an invented zero.
    stats = host_module.HostStats()
    stats._vcgencmd = None
    saved = host_module.glob.glob
    try:
        host_module.glob.glob = lambda pattern: []
        check("no gpu source yields None", stats.gpu()["value"], None)
        check("and reports no source", stats.gpu_source, None)
    finally:
        host_module.glob.glob = saved

    # A clock must never be dressed up as utilisation: a Pi 5 often holds the
    # V3D clock steady under load, so a percentage derived from it would read
    # 100% forever.
    clocked = host_module.HostStats()
    clocked._vcgencmd = "/bin/echo-not-used"
    clocked._gpu_from_vcgencmd = lambda: (1150.0, host_module.GPU_CLOCK, "v3d clock")
    result = clocked.gpu()
    check("clock reported as a clock", result["unit"], host_module.GPU_CLOCK)
    close("clock value in MHz", result["value"], 1150.0)
    check("source named", result["source"], "v3d clock")

    loaded = host_module.HostStats()
    loaded._gpu_from_debugfs = lambda: (37.0, host_module.GPU_PERCENT, "debugfs")
    check("a real load source is a percentage", loaded.gpu()["unit"],
          host_module.GPU_PERCENT)

    reading = host_module.HostStats().read()
    for key in ("model", "temp", "cpu", "gpu", "gpu_unit", "gpu_source", "t"):
        if key not in reading:
            FAILURES.append(f"read() is missing {key}")
    print("  ok   read() returns the full shape")


def main() -> int:
    for test in (test_config_normalisation, test_secrets_never_leave,
                 test_proxmox_shaping, test_fanctl_selection,
                 test_due_and_history, test_host_stats, test_http):
        test()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
