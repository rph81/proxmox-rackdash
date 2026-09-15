"""Proxmox VE API client.

Only the read-only endpoints the dashboard needs, over `urllib` so the Pi needs
nothing from pip.  Authentication is an API token, never a password: a token
can be scoped to PVEAuditor and revoked on its own, and it needs no ticket
refresh or CSRF header the way a login session does.

Every getter is defensive about missing keys.  Proxmox returns slightly
different shapes across 7.x/8.x/9.x and between clustered and single nodes, and
a dashboard must degrade to "unknown" rather than crash the poll loop.
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("rackdash.proxmox")


class ProxmoxError(RuntimeError):
    """Raised when the API cannot be reached or refuses the request."""


def _as_float(value, fallback=None):
    try:
        if value is None:
            return fallback
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return fallback if number != number else number


class Proxmox:
    def __init__(self, host: str, token_id: str, token_secret: str,
                 verify_tls: bool = False, timeout: float = 8.0):
        self.host = (host or "").rstrip("/")
        self.token_id = token_id or ""
        self.token_secret = token_secret or ""
        self.timeout = timeout
        self._context = ssl.create_default_context()
        if not verify_tls:
            # PVE ships a self-signed certificate; on a LAN, refusing to talk to
            # it is not a meaningful security win, so this defaults to off and
            # is a documented config switch.
            self._context.check_hostname = False
            self._context.verify_mode = ssl.CERT_NONE

    @property
    def configured(self) -> bool:
        return bool(self.host and self.token_id and self.token_secret)

    # -- transport --------------------------------------------------------

    def get(self, path: str, params: dict | None = None):
        if not self.configured:
            raise ProxmoxError("Proxmox host or API token is not configured")
        url = f"{self.host}/api2/json{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization",
                           f"PVEAPIToken={self.token_id}={self.token_secret}")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout,
                                        context=self._context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProxmoxError(
                    "Proxmox rejected the API token (401/403). Check token_id, "
                    "token_secret and that the token has the PVEAuditor role."
                ) from exc
            raise ProxmoxError(f"Proxmox returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ProxmoxError(
                    "TLS certificate rejected. Proxmox uses a self-signed "
                    "certificate by default; set proxmox.verify_tls to false."
                ) from exc
            raise ProxmoxError(f"cannot reach Proxmox at {self.host}: {reason}") from exc
        except socket.timeout as exc:
            raise ProxmoxError(f"Proxmox timed out after {self.timeout:g}s") from exc
        except (ValueError, OSError) as exc:
            raise ProxmoxError(f"bad response from Proxmox: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        return data

    # -- endpoints --------------------------------------------------------

    def nodes(self) -> list:
        data = self.get("/nodes")
        return data if isinstance(data, list) else []

    def pick_node(self, preferred: str = "") -> str:
        """Resolve the node name to poll, preferring the configured one."""
        nodes = self.nodes()
        names = [n.get("node") for n in nodes if isinstance(n, dict) and n.get("node")]
        if preferred and preferred in names:
            return preferred
        if preferred:
            LOG.warning("node %r not found; Proxmox reports %s", preferred, names)
        online = [n.get("node") for n in nodes
                  if isinstance(n, dict) and n.get("status") == "online" and n.get("node")]
        if online:
            return online[0]
        if names:
            return names[0]
        raise ProxmoxError("Proxmox reported no nodes")

    def node_status(self, node: str) -> dict:
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/status")
        return data if isinstance(data, dict) else {}

    def node_rrd(self, node: str, timeframe: str = "hour") -> list:
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/rrddata",
                        {"timeframe": timeframe, "cf": "AVERAGE"})
        return data if isinstance(data, list) else []

    def storage(self, node: str) -> list:
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/storage")
        return data if isinstance(data, list) else []

    def resources(self) -> list:
        data = self.get("/cluster/resources")
        return data if isinstance(data, list) else []

    def tasks(self, node: str, limit: int = 8) -> list:
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/tasks",
                        {"limit": limit, "start": 0})
        return data if isinstance(data, list) else []

    def disks(self, node: str) -> list:
        """Physical disks. Needs Sys.Audit, so failure here is not fatal."""
        try:
            data = self.get(f"/nodes/{urllib.parse.quote(node)}/disks/list")
        except ProxmoxError as exc:
            LOG.debug("disk list unavailable: %s", exc)
            return []
        return data if isinstance(data, list) else []

    def guest_status(self, node: str, kind: str, vmid: int) -> dict:
        """Live status for one VM or container."""
        if kind not in ("qemu", "lxc"):
            raise ProxmoxError(f"unknown guest type: {kind}")
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/{kind}/{int(vmid)}/status/current")
        return data if isinstance(data, dict) else {}

    def guest_rrd(self, node: str, kind: str, vmid: int, timeframe: str = "hour") -> list:
        if kind not in ("qemu", "lxc"):
            raise ProxmoxError(f"unknown guest type: {kind}")
        data = self.get(f"/nodes/{urllib.parse.quote(node)}/{kind}/{int(vmid)}/rrddata",
                        {"timeframe": timeframe, "cf": "AVERAGE"})
        return data if isinstance(data, list) else []

    def netstat(self, node: str) -> list:
        """Cumulative per-guest network counters.

        Proxmox exposes no live node-level byte counter, so node throughput has
        to come from the RRD (one-minute buckets). These per-guest counters are
        cumulative, which means consecutive samples can be differenced into a
        live rate and a "top talkers" list.
        """
        try:
            data = self.get(f"/nodes/{urllib.parse.quote(node)}/netstat")
        except ProxmoxError as exc:
            LOG.debug("netstat unavailable: %s", exc)
            return []
        return data if isinstance(data, list) else []

    def version(self) -> dict:
        try:
            data = self.get("/version")
        except ProxmoxError:
            return {}
        return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# shaping
# --------------------------------------------------------------------------

def shape_status(status: dict) -> dict:
    """Flatten /nodes/<n>/status into the numbers the dashboard draws."""
    memory = status.get("memory") if isinstance(status.get("memory"), dict) else {}
    swap = status.get("swap") if isinstance(status.get("swap"), dict) else {}
    rootfs = status.get("rootfs") if isinstance(status.get("rootfs"), dict) else {}
    cpuinfo = status.get("cpuinfo") if isinstance(status.get("cpuinfo"), dict) else {}

    loadavg = status.get("loadavg")
    if isinstance(loadavg, list):
        load = [_as_float(v, 0.0) for v in loadavg[:3]]
    else:
        load = []

    mem_total = _as_float(memory.get("total"), 0.0) or 0.0
    mem_used = _as_float(memory.get("used"), 0.0) or 0.0
    disk_total = _as_float(rootfs.get("total"), 0.0) or 0.0
    disk_used = _as_float(rootfs.get("used"), 0.0) or 0.0
    swap_total = _as_float(swap.get("total"), 0.0) or 0.0
    swap_used = _as_float(swap.get("used"), 0.0) or 0.0

    return {
        "cpu": (_as_float(status.get("cpu"), 0.0) or 0.0) * 100.0,
        "cpus": int(_as_float(cpuinfo.get("cpus"), 0) or 0),
        "cpu_model": cpuinfo.get("model") or "",
        "cpu_mhz": _as_float(cpuinfo.get("mhz")),
        "load": load,
        "memory": {
            "used": mem_used, "total": mem_total,
            "percent": (mem_used / mem_total * 100.0) if mem_total else 0.0,
        },
        "swap": {
            "used": swap_used, "total": swap_total,
            "percent": (swap_used / swap_total * 100.0) if swap_total else 0.0,
        },
        "disk": {
            "used": disk_used, "total": disk_total,
            "percent": (disk_used / disk_total * 100.0) if disk_total else 0.0,
        },
        "uptime": int(_as_float(status.get("uptime"), 0) or 0),
        "kversion": status.get("kversion") or "",
        "pveversion": status.get("pveversion") or "",
    }


def shape_rrd(rows: list) -> dict:
    """Turn rrddata rows into parallel series for the charts.

    Proxmox returns one dict per bucket with a `time` key; buckets that have
    not been filled yet omit their value keys entirely, so every series is
    built with None gaps rather than assumed zeros.
    """
    series = {"time": [], "cpu": [], "memory": [], "netin": [], "netout": [],
              "diskread": [], "diskwrite": [], "iowait": []}
    if not isinstance(rows, list):
        return series

    for row in rows:
        if not isinstance(row, dict) or "time" not in row:
            continue
        series["time"].append(int(_as_float(row.get("time"), 0) or 0))
        cpu = _as_float(row.get("cpu"))
        series["cpu"].append(cpu * 100.0 if cpu is not None else None)
        iowait = _as_float(row.get("iowait"))
        series["iowait"].append(iowait * 100.0 if iowait is not None else None)

        total = _as_float(row.get("memtotal"), 0.0) or 0.0
        used = _as_float(row.get("memused"))
        series["memory"].append(used / total * 100.0 if used is not None and total else None)

        for key in ("netin", "netout", "diskread", "diskwrite"):
            series[key].append(_as_float(row.get(key)))
    return series


def shape_guests(resources: list, node: str) -> list:
    """VMs and containers on this node, running first then by name."""
    guests = []
    for item in resources:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("qemu", "lxc"):
            continue
        if node and item.get("node") and item.get("node") != node:
            continue
        maxmem = _as_float(item.get("maxmem"), 0.0) or 0.0
        mem = _as_float(item.get("mem"), 0.0) or 0.0
        guests.append({
            "id": item.get("vmid"),
            "name": item.get("name") or f"{item.get('type')}/{item.get('vmid')}",
            "type": "VM" if item.get("type") == "qemu" else "LXC",
            "status": item.get("status") or "unknown",
            "cpu": (_as_float(item.get("cpu"), 0.0) or 0.0) * 100.0,
            "cpus": int(_as_float(item.get("maxcpu"), 0) or 0),
            "mem": mem,
            "maxmem": maxmem,
            "mem_percent": (mem / maxmem * 100.0) if maxmem else 0.0,
            "uptime": int(_as_float(item.get("uptime"), 0) or 0),
            "tags": item.get("tags") or "",
        })
    guests.sort(key=lambda g: (g["status"] != "running", str(g["name"]).lower()))
    return guests


def shape_guest_status(status: dict) -> dict:
    """Flatten a guest's status/current into the numbers the detail view draws."""
    maxmem = _as_float(status.get("maxmem"), 0.0) or 0.0
    mem = _as_float(status.get("mem"), 0.0) or 0.0
    maxdisk = _as_float(status.get("maxdisk"), 0.0) or 0.0
    disk = _as_float(status.get("disk"), 0.0) or 0.0
    balloon = _as_float(status.get("balloon"))

    return {
        "name": status.get("name") or "",
        "status": status.get("status") or "unknown",
        "cpu": (_as_float(status.get("cpu"), 0.0) or 0.0) * 100.0,
        "cpus": int(_as_float(status.get("cpus"), 0) or 0),
        "mem": mem,
        "maxmem": maxmem,
        "mem_percent": (mem / maxmem * 100.0) if maxmem else 0.0,
        "balloon": balloon,
        # A QEMU guest without the agent reports disk 0; the UI says so rather
        # than drawing a misleading empty gauge.
        "disk": disk,
        "maxdisk": maxdisk,
        "disk_percent": (disk / maxdisk * 100.0) if maxdisk and disk else 0.0,
        "disk_known": bool(disk),
        "netin": _as_float(status.get("netin"), 0.0) or 0.0,
        "netout": _as_float(status.get("netout"), 0.0) or 0.0,
        "diskread": _as_float(status.get("diskread"), 0.0) or 0.0,
        "diskwrite": _as_float(status.get("diskwrite"), 0.0) or 0.0,
        "uptime": int(_as_float(status.get("uptime"), 0) or 0),
        "pid": status.get("pid"),
        "ha": (status.get("ha") or {}).get("managed") if isinstance(status.get("ha"), dict) else None,
        "tags": status.get("tags") or "",
    }


def shape_guest_rrd(rows: list) -> dict:
    """Per-guest RRD rows into parallel series."""
    series = {"time": [], "cpu": [], "memory": [], "netin": [], "netout": [],
              "diskread": [], "diskwrite": []}
    if not isinstance(rows, list):
        return series
    for row in rows:
        if not isinstance(row, dict) or "time" not in row:
            continue
        series["time"].append(int(_as_float(row.get("time"), 0) or 0))
        cpu = _as_float(row.get("cpu"))
        series["cpu"].append(cpu * 100.0 if cpu is not None else None)
        total = _as_float(row.get("maxmem"), 0.0) or 0.0
        used = _as_float(row.get("mem"))
        series["memory"].append(used / total * 100.0 if used is not None and total else None)
        for key in ("netin", "netout", "diskread", "diskwrite"):
            series[key].append(_as_float(row.get(key)))
    return series


def shape_netstat(entries: list) -> dict:
    """Cumulative per-guest byte counters, keyed by vmid."""
    totals: dict = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        vmid = item.get("vmid")
        if vmid is None:
            continue
        key = str(vmid)
        bucket = totals.setdefault(key, {"in": 0.0, "out": 0.0})
        bucket["in"] += _as_float(item.get("in"), 0.0) or 0.0
        bucket["out"] += _as_float(item.get("out"), 0.0) or 0.0
    return totals


def shape_storage(entries: list) -> list:
    pools = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("active") in (0, False) and not item.get("enabled"):
            continue
        total = _as_float(item.get("total"), 0.0) or 0.0
        used = _as_float(item.get("used"), 0.0) or 0.0
        pools.append({
            "name": item.get("storage") or "?",
            "type": item.get("type") or "",
            "used": used,
            "total": total,
            "percent": (used / total * 100.0) if total else 0.0,
            "content": item.get("content") or "",
        })
    pools.sort(key=lambda p: p["name"])
    return pools


def shape_tasks(entries: list, limit: int = 8) -> list:
    tasks = []
    for item in entries[:limit]:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        tasks.append({
            "type": item.get("type") or "",
            "id": item.get("id") or "",
            "user": item.get("user") or "",
            "start": int(_as_float(item.get("starttime"), 0) or 0),
            "end": int(_as_float(item.get("endtime"), 0) or 0),
            "status": status or ("running" if not item.get("endtime") else "unknown"),
            "ok": status == "OK" or status is None and not item.get("endtime"),
        })
    return tasks


def shape_disks(entries: list) -> list:
    disks = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        disks.append({
            "path": item.get("devpath") or "",
            "model": item.get("model") or "",
            "serial": item.get("serial") or "",
            "size": _as_float(item.get("size"), 0.0) or 0.0,
            "type": item.get("type") or "",
            "health": item.get("health") or "",
            "wearout": _as_float(item.get("wearout")),
            "used": item.get("used") or "",
        })
    disks.sort(key=lambda d: d["path"])
    return disks
