#!/usr/bin/env python3
"""Run rackdash against a simulated Proxmox host and fan controller.

Stands up two fake JSON APIs on loopback, points a throwaway config at them
and starts the daemon, so the whole dashboard can be built and checked on a
laptop with no Pi, no hypervisor and no screen attached.

The fakes are not static fixtures: CPU, memory and network wander, guests idle
and spike, and drive temperatures follow fan duty, so the dials and charts
actually move the way they will in the rack.

    python3 tools/devsim.py --port 8080    # then open http://127.0.0.1:8080/
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rackdash import collector as collector_module  # noqa: E402
from rackdash import config as config_module  # noqa: E402
from rackdash.__main__ import main  # noqa: E402

NODE = "pve"
GIB = 1024 ** 3

GUESTS = [
    {"vmid": 100, "name": "truenas", "type": "qemu", "cores": 4, "maxmem": 16 * GIB,
     "maxdisk": 64 * GIB, "load": 0.22, "status": "running"},
    {"vmid": 101, "name": "home-assistant", "type": "qemu", "cores": 2, "maxmem": 4 * GIB,
     "maxdisk": 32 * GIB, "load": 0.09, "status": "running"},
    {"vmid": 102, "name": "docker-host", "type": "qemu", "cores": 6, "maxmem": 24 * GIB,
     "maxdisk": 200 * GIB, "load": 0.41, "status": "running"},
    {"vmid": 103, "name": "windows-11", "type": "qemu", "cores": 4, "maxmem": 8 * GIB,
     "maxdisk": 128 * GIB, "load": 0.05, "status": "stopped"},
    {"vmid": 200, "name": "plex", "type": "lxc", "cores": 4, "maxmem": 8 * GIB,
     "maxdisk": 16 * GIB, "load": 0.55, "status": "running"},
    {"vmid": 201, "name": "nginx-proxy", "type": "lxc", "cores": 1, "maxmem": 1 * GIB,
     "maxdisk": 8 * GIB, "load": 0.03, "status": "running"},
    {"vmid": 202, "name": "pihole", "type": "lxc", "cores": 1, "maxmem": 1 * GIB,
     "maxdisk": 8 * GIB, "load": 0.04, "status": "running"},
    {"vmid": 203, "name": "unifi", "type": "lxc", "cores": 2, "maxmem": 2 * GIB,
     "maxdisk": 16 * GIB, "load": 0.12, "status": "running"},
    {"vmid": 204, "name": "backup-runner", "type": "lxc", "cores": 2, "maxmem": 2 * GIB,
     "maxdisk": 32 * GIB, "load": 0.02, "status": "stopped"},
]

FAN_CHANNELS = [
    {"index": 1, "name": "80mm x 3 - Drives Exhaust", "sensors": ["arcconf:1:max"], "type": "PWM"},
    {"index": 2, "name": "92mm x2 - Rear Exhaust", "sensors": ["hwmon:coretemp:temp1"], "type": "PWM"},
    {"index": 3, "name": "SAS Card", "sensors": ["cpro:temp1"], "type": "DC (forced)"},
    {"index": 4, "name": "Fan 4", "sensors": [], "type": None},
    {"index": 5, "name": "Fan 5", "sensors": [], "type": None},
    {"index": 6, "name": "Fan 6", "sensors": [], "type": None},
]

SENSOR_CATALOG = [
    {"id": "cpro:temp1", "label": "Commander Pro · Probe 1", "source": "device"},
    {"id": "cpro:temp2", "label": "Commander Pro · Probe 2", "source": "device"},
    {"id": "hwmon:coretemp:temp1", "label": "CPU (Intel) · Package id 0", "source": "host"},
    {"id": "hwmon:coretemp:temp2", "label": "CPU (Intel) · Core 0", "source": "host"},
    {"id": "hwmon:acpitz:temp1", "label": "ACPI · temp1", "source": "host"},
    {"id": "hwmon:nvme:temp1", "label": "NVMe · Composite", "source": "host"},
    {"id": "arcconf:1:slot1", "label": "Disk · Slot 1 (/dev/sda)", "source": "storage"},
    {"id": "arcconf:1:slot2", "label": "Disk · Slot 2 (/dev/sdb)", "source": "storage"},
    {"id": "arcconf:1:max", "label": "Disk · Hottest drive", "source": "storage"},
]


class World:
    """A hypervisor that wanders, so the dials and charts have something to do."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time() - 9 * 86400 - 4 * 3600
        self.load = 0.30
        self.cpu = 0.22
        self.mem_used = 38.0 * GIB
        self.mem_total = 128.0 * GIB
        self.net_in = 4.0e6
        self.net_out = 1.2e6
        self.counters = {str(g["vmid"]): {"in": random.uniform(1e8, 9e9),
                                          "out": random.uniform(1e8, 4e9)} for g in GUESTS}
        self.guest_cpu = {str(g["vmid"]): g["load"] for g in GUESTS}
        self.temps = {"cpu": 46.0, "probe1": 33.0, "probe2": 30.0, "nvme": 41.0,
                      "disk1": 41.0, "disk2": 43.0}
        self.duties = {1: 50.0, 2: 44.0, 3: 51.0}
        self.history: list = []          # one bucket a minute, like PVE's RRD
        self.guest_history: dict = {str(g["vmid"]): [] for g in GUESTS}
        self._seed_history()

    def _seed_history(self):
        now = time.time()
        for index in range(70):
            stamp = now - (70 - index) * 60
            phase = index / 70 * math.pi * 3
            self.history.append({
                "time": int(stamp),
                "cpu": max(0.02, 0.22 + 0.16 * math.sin(phase) + random.uniform(-.03, .03)),
                "iowait": max(0.0, 0.02 + 0.02 * math.sin(phase * 1.7)),
                "memtotal": self.mem_total,
                "memused": self.mem_total * (0.28 + 0.05 * math.sin(phase * 0.7)),
                "netin": max(0.0, 4.0e6 + 3.4e6 * math.sin(phase * 1.3) + random.uniform(-6e5, 6e5)),
                "netout": max(0.0, 1.2e6 + 9e5 * math.sin(phase * 0.9) + random.uniform(-2e5, 2e5)),
                "diskread": max(0.0, 2.0e6 * abs(math.sin(phase * 2.1))),
                "diskwrite": max(0.0, 3.0e6 * abs(math.sin(phase * 1.1))),
            })
            for guest in GUESTS:
                key = str(guest["vmid"])
                running = guest["status"] == "running"
                self.guest_history[key].append({
                    "time": int(stamp),
                    "cpu": max(0.0, guest["load"] * (0.6 + 0.8 * abs(math.sin(phase + guest["vmid"]))))
                           if running else 0.0,
                    "maxmem": guest["maxmem"],
                    "mem": guest["maxmem"] * (0.35 + 0.3 * abs(math.sin(phase * 0.8 + guest["vmid"])))
                           if running else 0.0,
                    "netin": max(0.0, 8e5 * abs(math.sin(phase * 1.4 + guest["vmid"]))) if running else 0.0,
                    "netout": max(0.0, 4e5 * abs(math.sin(phase * 1.1 + guest["vmid"]))) if running else 0.0,
                    "diskread": max(0.0, 3e5 * abs(math.sin(phase * 2.0))) if running else 0.0,
                    "diskwrite": max(0.0, 5e5 * abs(math.sin(phase * 1.6))) if running else 0.0,
                })

    def tick(self, dt: float):
        with self.lock:
            self.load = min(1.0, max(0.05, self.load + random.uniform(-0.05, 0.05)))
            self.cpu += (self.load - self.cpu) * 0.25
            target_mem = self.mem_total * (0.26 + 0.18 * self.load)
            self.mem_used += (target_mem - self.mem_used) * 0.05
            self.net_in += (self.load * 2.2e7 - self.net_in) * 0.15 + random.uniform(-3e5, 3e5)
            self.net_out += (self.load * 6.0e6 - self.net_out) * 0.15 + random.uniform(-1e5, 1e5)
            self.net_in = max(2e5, self.net_in)
            self.net_out = max(1e5, self.net_out)

            for guest in GUESTS:
                key = str(guest["vmid"])
                if guest["status"] != "running":
                    self.guest_cpu[key] = 0.0
                    continue
                target = guest["load"] * (0.5 + 1.4 * self.load)
                self.guest_cpu[key] += (target - self.guest_cpu[key]) * 0.2
                self.counters[key]["in"] += max(0.0, self.guest_cpu[key] * 9e5 * dt)
                self.counters[key]["out"] += max(0.0, self.guest_cpu[key] * 4e5 * dt)

            # Temperatures chase load, and the drives chase fan duty, so the
            # thermal dials respond to what the fan app is doing.
            airflow = sum(self.duties.values()) / (len(self.duties) * 100.0)
            self.temps["cpu"] += ((34 + 46 * self.cpu) - self.temps["cpu"]) * 0.1
            self.temps["probe1"] += ((24 + 16 * self.load - 6 * airflow) - self.temps["probe1"]) * 0.08
            self.temps["probe2"] += ((22 + 12 * self.load - 5 * airflow) - self.temps["probe2"]) * 0.08
            self.temps["nvme"] += ((33 + 22 * self.cpu) - self.temps["nvme"]) * 0.09
            self.temps["disk1"] += ((44 + 9 * self.load - 11 * airflow) - self.temps["disk1"]) * 0.05
            self.temps["disk2"] += ((46 + 9 * self.load - 11 * airflow) - self.temps["disk2"]) * 0.05

            hottest = max(self.temps["disk1"], self.temps["disk2"])
            self.duties[1] = _curve(hottest, [(35, 20), (40, 35), (45, 60), (50, 85), (55, 100)])
            self.duties[2] = _curve(self.temps["cpu"], [(30, 20), (45, 40), (60, 70), (75, 100)])
            self.duties[3] = _curve(self.temps["probe1"], [(25, 35), (35, 50), (45, 80), (55, 100)])

            now = time.time()
            if not self.history or now - self.history[-1]["time"] >= 60:
                self.history.append({
                    "time": int(now), "cpu": self.cpu, "iowait": 0.02 * self.load,
                    "memtotal": self.mem_total, "memused": self.mem_used,
                    "netin": self.net_in, "netout": self.net_out,
                    "diskread": 1.5e6 * self.load, "diskwrite": 2.2e6 * self.load,
                })
                self.history = self.history[-70:]
                for guest in GUESTS:
                    key = str(guest["vmid"])
                    self.guest_history[key].append({
                        "time": int(now), "cpu": self.guest_cpu[key],
                        "maxmem": guest["maxmem"],
                        "mem": guest["maxmem"] * (0.35 + 0.25 * self.guest_cpu[key]),
                        "netin": self.guest_cpu[key] * 9e5,
                        "netout": self.guest_cpu[key] * 4e5,
                        "diskread": self.guest_cpu[key] * 3e5,
                        "diskwrite": self.guest_cpu[key] * 5e5,
                    })
                    self.guest_history[key] = self.guest_history[key][-70:]

    # -- payloads ---------------------------------------------------------

    def node_status(self) -> dict:
        with self.lock:
            return {
                "cpu": round(self.cpu, 4),
                "cpuinfo": {"cpus": 24, "model": "Intel(R) Xeon(R) Silver 4310 CPU @ 2.10GHz",
                            "mhz": "2100.000", "sockets": 1},
                "memory": {"total": int(self.mem_total), "used": int(self.mem_used),
                           "free": int(self.mem_total - self.mem_used)},
                "swap": {"total": 8 * GIB, "used": int(0.6 * GIB), "free": int(7.4 * GIB)},
                "rootfs": {"total": 100 * GIB, "used": int(31.4 * GIB),
                           "avail": int(68.6 * GIB)},
                "loadavg": [f"{self.load * 6:.2f}", f"{self.load * 5.4:.2f}", f"{self.load * 5:.2f}"],
                "uptime": int(time.time() - self.started),
                "kversion": "Linux 6.8.12-4-pve #1 SMP PREEMPT_DYNAMIC PVE",
                "pveversion": "pve-manager/8.2.4/faa83925c9641325",
                "wait": 0.02 * self.load,
            }

    def guest_status(self, guest: dict) -> dict:
        key = str(guest["vmid"])
        with self.lock:
            running = guest["status"] == "running"
            cpu = self.guest_cpu[key] if running else 0.0
            return {
                "name": guest["name"], "status": guest["status"],
                "cpu": round(cpu, 4), "cpus": guest["cores"],
                "maxmem": guest["maxmem"],
                "mem": int(guest["maxmem"] * (0.35 + 0.25 * cpu)) if running else 0,
                "maxdisk": guest["maxdisk"],
                # A QEMU guest with no agent reports 0 here, which the UI calls out.
                "disk": int(guest["maxdisk"] * 0.42) if guest["type"] == "lxc" else 0,
                "netin": int(self.counters[key]["in"]), "netout": int(self.counters[key]["out"]),
                "diskread": int(cpu * 4e8), "diskwrite": int(cpu * 6e8),
                "uptime": int(time.time() - self.started - guest["vmid"] * 97) if running else 0,
                "ha": {"managed": 0},
            }


def _curve(temp: float, points: list) -> float:
    if temp <= points[0][0]:
        return float(points[0][1])
    if temp >= points[-1][0]:
        return float(points[-1][1])
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if x1 <= temp <= x2:
            return y1 + (temp - x1) / (x2 - x1) * (y2 - y1)
    return float(points[-1][1])


WORLD = World()


class FakeHostStats:
    """Stands in for the Pi's sysfs, so the header readouts can be seen on a
    development machine that has no thermal zone or V3D at all."""

    def __init__(self):
        self.model = "Raspberry Pi 5 Model B Rev 1.0 (simulated)"
        self._temp = 47.0
        self._cpu = 9.0
        self._gpu = 4.0

    def read(self) -> dict:
        self._temp += random.uniform(-0.8, 0.8)
        self._temp = min(74.0, max(38.0, self._temp))
        self._cpu = min(100.0, max(2.0, self._cpu + random.uniform(-4, 5)))
        self._gpu = min(100.0, max(0.0, self._gpu + random.uniform(-3, 4)))
        # A real Pi 5 has no utilisation source, only the V3D clock, so the
        # fake reports the same shape the hardware actually produces.
        return {"model": self.model, "temp": round(self._temp, 1),
                "cpu": round(self._cpu, 1), "gpu": round(910 + self._gpu * 2.4),
                "gpu_unit": "MHz", "gpu_source": "v3d clock", "t": time.time()}


class FakeProxmox(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps({"data": data}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.headers.get("Authorization", "").startswith("PVEAPIToken="):
            self._json({"message": "no ticket"}, 401)
            return
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        # /api2/json/...
        route = parts[2:]

        if route == ["version"]:
            self._json({"release": "8.2", "version": "8.2.4", "repoid": "faa83925c9641325"})
            return
        if route == ["nodes"]:
            self._json([{"node": NODE, "status": "online", "type": "node"}])
            return
        if route == ["cluster", "resources"]:
            self._json([{
                "type": g["type"], "vmid": g["vmid"], "name": g["name"], "node": NODE,
                "status": g["status"], "maxcpu": g["cores"], "maxmem": g["maxmem"],
                "cpu": WORLD.guest_cpu[str(g["vmid"])] if g["status"] == "running" else 0,
                "mem": int(g["maxmem"] * 0.4) if g["status"] == "running" else 0,
                "maxdisk": g["maxdisk"], "uptime": 800000 if g["status"] == "running" else 0,
            } for g in GUESTS])
            return

        if route[:2] == ["nodes", NODE]:
            tail = route[2:]
            if tail == ["status"]:
                self._json(WORLD.node_status()); return
            if tail == ["rrddata"]:
                with WORLD.lock:
                    self._json(list(WORLD.history)); return
            if tail == ["storage"]:
                self._json([
                    {"storage": "local", "type": "dir", "active": 1, "total": 100 * GIB,
                     "used": int(31.4 * GIB), "content": "iso,vztmpl,backup"},
                    {"storage": "local-lvm", "type": "lvmthin", "active": 1, "total": 1800 * GIB,
                     "used": int(902 * GIB), "content": "images,rootdir"},
                    {"storage": "tank-nvme", "type": "zfspool", "active": 1, "total": 3600 * GIB,
                     "used": int(1210 * GIB), "content": "images,rootdir"},
                    {"storage": "backup-nas", "type": "nfs", "active": 1, "total": 16000 * GIB,
                     "used": int(11400 * GIB), "content": "backup"},
                ]); return
            if tail == ["tasks"]:
                now = int(time.time())
                self._json([
                    {"type": "vzdump", "id": "200", "user": "root@pam",
                     "starttime": now - 1800, "endtime": now - 1500, "status": "OK"},
                    {"type": "qmstart", "id": "102", "user": "root@pam",
                     "starttime": now - 5400, "endtime": now - 5390, "status": "OK"},
                    {"type": "vzdump", "id": "103", "user": "root@pam",
                     "starttime": now - 9000, "endtime": now - 8700,
                     "status": "command 'lvcreate' failed"},
                    {"type": "pveupdate", "id": "", "user": "root@pam",
                     "starttime": now - 14400, "endtime": now - 14390, "status": "OK"},
                ]); return
            if tail == ["netstat"]:
                with WORLD.lock:
                    self._json([{"vmid": vmid, "dev": "net0", "in": int(c["in"]),
                                 "out": int(c["out"])} for vmid, c in WORLD.counters.items()])
                return
            if tail == ["disks", "list"]:
                self._json([
                    {"devpath": "/dev/sda", "model": "MZILT800HBHQ0D3", "serial": "S1",
                     "size": 800 * GIB, "type": "ssd", "health": "PASSED", "wearout": 63},
                    {"devpath": "/dev/sdb", "model": "MZILT800HBHQ0D3", "serial": "S2",
                     "size": 800 * GIB, "type": "ssd", "health": "PASSED", "wearout": 71},
                    {"devpath": "/dev/nvme0n1", "model": "Samsung SSD 980 PRO", "serial": "N1",
                     "size": 2000 * GIB, "type": "nvme", "health": "PASSED", "wearout": 94},
                ]); return
            if len(tail) >= 3 and tail[0] in ("qemu", "lxc"):
                vmid = int(tail[1])
                guest = next((g for g in GUESTS if g["vmid"] == vmid), None)
                if guest is None:
                    self._json({"message": "no such guest"}, 500); return
                if tail[2:] == ["status", "current"]:
                    self._json(WORLD.guest_status(guest)); return
                if tail[2:] == ["rrddata"]:
                    with WORLD.lock:
                        self._json(list(WORLD.guest_history[str(vmid)])); return

        self._json({"message": f"no handler for {path}"}, 501)


class FakeFanctl(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if urlparse(self.path).path != "/api/state":
            self.send_error(404)
            return
        with WORLD.lock:
            temps = {
                "cpro:temp1": round(WORLD.temps["probe1"], 1),
                "cpro:temp2": round(WORLD.temps["probe2"], 1),
                "hwmon:coretemp:temp1": round(WORLD.temps["cpu"], 1),
                "hwmon:coretemp:temp2": round(WORLD.temps["cpu"] - 1.5, 1),
                "hwmon:acpitz:temp1": 27.8,
                "hwmon:nvme:temp1": round(WORLD.temps["nvme"], 1),
                "arcconf:1:slot1": round(WORLD.temps["disk1"], 1),
                "arcconf:1:slot2": round(WORLD.temps["disk2"], 1),
                "arcconf:1:max": round(max(WORLD.temps["disk1"], WORLD.temps["disk2"]), 1),
            }
            duties = dict(WORLD.duties)

        payload = {
            "version": "1.4.2", "connected": True, "error": None, "warning": None,
            "time": time.time(), "temps": temps,
            "volts": {"+12V": 12.1, "+5V": 5.04, "+3.3V": 3.31},
            "device": {"backend": "hwmon", "device": "Corsair Commander Pro",
                       "fans": [{"index": f["index"], "connected": f["type"] is not None,
                                 "type": f["type"], "note": None} for f in FAN_CHANNELS],
                       "probes": [{"index": i, "connected": i <= 2} for i in range(1, 5)]},
            "fans": [{"index": f["index"],
                      "duty": round(duties.get(f["index"], 0.0), 1),
                      "target": round(duties.get(f["index"], 0.0), 1),
                      "rpm": int(300 + duties.get(f["index"], 0) * 16) if f["type"] == "PWM" else None,
                      "control_temp": temps.get(f["sensors"][0]) if f["sensors"] else None,
                      "override": False,
                      "reason": "curve" if f["sensors"] else "no-sensors"}
                     for f in FAN_CHANNELS],
            "sensors": SENSOR_CATALOG,
            "storage": {"enabled": True, "error": None, "stale": False,
                        "command": "/usr/Arcconf/arcconf", "age": 12.0,
                        "drives": [
                            {"slot": 1, "path": "/dev/sda", "model": "MZILT800HBHQ0D3",
                             "serial": "S1", "temperature": temps["arcconf:1:slot1"],
                             "temperature_max": 48, "temperature_threshold": 74,
                             "usage_remaining": 63, "power_on_hours": 43822,
                             "smart_warning": False},
                            {"slot": 2, "path": "/dev/sdb", "model": "MZILT800HBHQ0D3",
                             "serial": "S2", "temperature": temps["arcconf:1:slot2"],
                             "temperature_max": 49, "temperature_threshold": 74,
                             "usage_remaining": 71, "power_on_hours": 43818,
                             "smart_warning": False},
                        ]},
            "config": {
                "fans": [{"index": f["index"], "name": f["name"], "enabled": True,
                          "mode": "curve" if f["sensors"] else "off",
                          "sensors": f["sensors"], "mix": "max"} for f in FAN_CHANNELS],
                "http": {"bind": "0.0.0.0", "port": 8899, "auth_token": None},
            },
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def simulate(stop: threading.Event) -> None:
    last = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        WORLD.tick(min(now - last, 5.0))
        last = now
        stop.wait(1.0)


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080, help="dashboard port")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--pve-port", type=int, default=18006)
    parser.add_argument("--fan-port", type=int, default=18899)
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    serve(FakeProxmox, args.pve_port)
    serve(FakeFanctl, args.fan_port)
    print(f"fake Proxmox on http://127.0.0.1:{args.pve_port}", flush=True)
    print(f"fake fanctl  on http://127.0.0.1:{args.fan_port}", flush=True)

    # The dashboard reads the real machine's vitals; on a laptop there is
    # nothing to read, so swap in a fake to exercise the header readouts.
    collector_module.HostStats = FakeHostStats

    stop = threading.Event()
    threading.Thread(target=simulate, args=(stop,), daemon=True).start()

    config_path = args.config or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "devsim-config.json")
    # Credentials cannot be set over the API, so seed them in the file exactly
    # the way a real operator would.
    seeded = config_module.default_config()
    try:
        seeded = config_module.load(config_path)
    except RuntimeError:
        pass
    seeded["proxmox"].update({
        "host": f"http://127.0.0.1:{args.pve_port}", "node": NODE,
        "token_id": "root@pam!rackdash", "token_secret": "simulated-secret",
        "verify_tls": False,
    })
    seeded["fanctl"].update({"enabled": True, "url": f"http://127.0.0.1:{args.fan_port}"})
    config_module.save(config_path, seeded)

    try:
        return main(["--config", config_path, "--bind", args.bind,
                     "--port", str(args.port), "--log-level", args.log_level])
    finally:
        stop.set()


if __name__ == "__main__":
    sys.exit(run())
