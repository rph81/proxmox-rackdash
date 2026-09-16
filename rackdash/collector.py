"""Background polling and the snapshot the browser renders.

Two threads, because the two sources have very different costs and neither
should be able to stall the other: Proxmox answers in milliseconds on a LAN
but its RRD endpoint is heavier, and the fan controller is a separate box that
may be down entirely.  Each thread writes into a shared snapshot under one
lock; the HTTP threads only ever read it.

Nothing here retries aggressively.  A dashboard that hammers a struggling
hypervisor makes the problem worse, so a failed poll just records its error,
keeps serving the last good values, and waits for the next tick.
"""

from __future__ import annotations

import logging
import threading
import time

from . import __version__, config as config_module, fanctl as fanctl_module, proxmox
from .history import Series
from .host import HostStats

LOG = logging.getLogger("rackdash.collector")

LIVE_SECONDS = 300.0        # how much live detail the sparklines keep
RRD_REFRESH = 60.0          # Proxmox RRD only advances once a minute
SLOW_REFRESH = 15.0         # guests, storage, tasks: cheap but not per-tick
DISK_REFRESH = 300.0        # physical disks change even less often


def _due(last: float | None, interval: float, now: float) -> bool:
    """True when a periodic job should run.

    `last is None` means "never run", which must be due immediately: comparing
    against a zero timestamp instead would silently delay the first fetch,
    because time.monotonic() starts near zero on some platforms and the first
    paint would then be missing guests, storage and history.
    """
    return last is None or (now - last) >= interval


class Collector:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config_warning: str | None = None
        try:
            self.config = config_module.load(config_path)
        except RuntimeError as exc:
            # A dashboard that cannot parse its config should still come up and
            # say so on screen, rather than crash-looping invisibly in a rack.
            self.config = config_module.default_config()
            self.config_warning = f"config could not be read ({exc}); using defaults"
            LOG.error(self.config_warning)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake_pve = threading.Event()
        self._wake_fan = threading.Event()
        self._threads: list = []

        self._node = ""
        self._pve = self._make_proxmox()
        self._fanctl = self._make_fanctl()

        self._system: dict = {}
        self._pve_error: str | None = None
        self._pve_seen: float = 0.0
        self._rrd: dict = {}
        self._rrd_at: float | None = None
        self._guests: list = []
        self._storage: list = []
        self._tasks: list = []
        self._disks: list = []
        self._version: dict = {}
        self._slow_at: float | None = None
        self._disks_at: float | None = None

        self._netstat: dict = {}         # vmid -> cumulative bytes, previous sample
        self._netstat_at: float | None = None
        self._talkers: list = []
        self._guest_cache: dict = {}     # (kind, vmid, timeframe) -> (when, payload)

        self._temps: dict = {"connected": False, "sensors": [], "fans": [],
                             "storage": {"enabled": False, "drives": []}}
        self._fan_error: str | None = None
        self._fan_seen: float = 0.0

        # The Pi's own vitals are local reads, so they keep working and keep
        # updating even when the hypervisor is unreachable.
        self._host = HostStats()
        self._host_stats: dict = {"model": self._host.model, "temp": None,
                                  "cpu": None, "gpu": None, "gpu_source": None}

        self._live = Series(LIVE_SECONDS, self.config["proxmox"]["interval"])
        self._temp_live = Series(LIVE_SECONDS, self.config["fanctl"]["interval"])

    # ------------------------------------------------------------------
    # construction from config
    # ------------------------------------------------------------------

    def _make_proxmox(self) -> proxmox.Proxmox:
        cfg = self.config["proxmox"]
        return proxmox.Proxmox(cfg["host"], cfg["token_id"], cfg["token_secret"],
                               verify_tls=cfg["verify_tls"], timeout=cfg["timeout"])

    def _make_fanctl(self) -> fanctl_module.Fanctl:
        cfg = self.config["fanctl"]
        return fanctl_module.Fanctl(cfg["url"], cfg["token"], timeout=cfg["timeout"])

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        for target, name in ((self._run_proxmox, "proxmox"), (self._run_fanctl, "fanctl")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake_pve.set()
        self._wake_fan.set()
        for thread in self._threads:
            thread.join(timeout=5.0)

    def refresh(self) -> None:
        self._rrd_at = None
        self._slow_at = None
        self._wake_pve.set()
        self._wake_fan.set()

    # ------------------------------------------------------------------
    # Proxmox thread
    # ------------------------------------------------------------------

    def _run_proxmox(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                interval = self.config["proxmox"]["interval"]
            try:
                # Read the local box first: it costs microseconds and must not
                # be skipped by an early return in the Proxmox poll.
                self._host_stats = self._host.read()
            except Exception:
                LOG.exception("unhandled error reading local stats")
            try:
                self._poll_proxmox()
            except Exception:  # never let the thread die
                LOG.exception("unhandled error polling Proxmox")
            self._wake_pve.wait(timeout=interval)
            self._wake_pve.clear()

    def _poll_proxmox(self) -> None:
        with self._lock:
            client = self._pve
            preferred = self.config["proxmox"]["node"]
        if not client.configured:
            self._set_pve_error("Proxmox is not configured yet — open Settings")
            return

        try:
            node = self._node or client.pick_node(preferred)
            status = proxmox.shape_status(client.node_status(node))
        except proxmox.ProxmoxError as exc:
            self._node = ""          # re-resolve on the next try
            self._set_pve_error(str(exc))
            return

        now = time.monotonic()
        rrd = None
        if _due(self._rrd_at, RRD_REFRESH, now):
            try:
                rrd = proxmox.shape_rrd(client.node_rrd(node))
                self._rrd_at = now
            except proxmox.ProxmoxError as exc:
                LOG.debug("rrd poll failed: %s", exc)

        slow = {}
        if _due(self._slow_at, SLOW_REFRESH, now):
            try:
                slow = {
                    "guests": proxmox.shape_guests(client.resources(), node),
                    "storage": proxmox.shape_storage(client.storage(node)),
                    "tasks": proxmox.shape_tasks(client.tasks(node)),
                }
                if not self._version:
                    slow["version"] = client.version()
                self._slow_at = now
            except proxmox.ProxmoxError as exc:
                LOG.debug("slow poll failed: %s", exc)

        disks = None
        if _due(self._disks_at, DISK_REFRESH, now):
            disks = proxmox.shape_disks(client.disks(node))
            self._disks_at = now

        talkers = None
        if _due(self._netstat_at, SLOW_REFRESH, now):
            talkers = self._update_talkers(client, node, now)

        # Network rates come from the RRD series: Proxmox exposes no live
        # per-node counter, so the newest RRD bucket is the freshest figure
        # available. It lags by up to a minute, which the UI labels.
        net_in = net_out = None
        source = rrd if rrd is not None else self._rrd
        if source:
            net_in = _last_number(source.get("netin"))
            net_out = _last_number(source.get("netout"))

        with self._lock:
            self._node = node
            self._system = status
            self._system["node"] = node
            self._system["net"] = {
                "in": net_in, "out": net_out,
                "link_mbit": self.config["proxmox"]["link_mbit"],
            }
            if rrd is not None:
                self._rrd = rrd
            if "guests" in slow:
                self._guests = slow["guests"]
            if "storage" in slow:
                self._storage = slow["storage"]
            if "tasks" in slow:
                self._tasks = slow["tasks"]
            if slow.get("version"):
                self._version = slow["version"]
            if disks is not None:
                self._disks = disks
            if talkers is not None:
                self._talkers = talkers
            if self._pve_error:
                LOG.info("Proxmox recovered (%s)", node)
            self._pve_error = None
            self._pve_seen = time.time()

        self._live.append({
            "cpu": round(status["cpu"], 2),
            "memory": round(status["memory"]["percent"], 2),
            "disk": round(status["disk"]["percent"], 2),
            "netin": net_in,
            "netout": net_out,
        })

    def _update_talkers(self, client, node: str, now: float) -> list:
        """Differentiate the cumulative per-guest counters into live rates."""
        sample = proxmox.shape_netstat(client.netstat(node))
        previous = self._netstat
        elapsed = (now - self._netstat_at) if self._netstat_at is not None else 0.0
        self._netstat = sample
        self._netstat_at = now
        # The first sample has nothing to difference against; rates start on
        # the second pass.
        if not previous or elapsed <= 0:
            return self._talkers

        names = {str(g["id"]): g["name"] for g in self._guests}
        talkers = []
        for vmid, current in sample.items():
            before = previous.get(vmid)
            if not before:
                continue
            # A counter that went backwards means the guest restarted; skip the
            # bucket rather than reporting a nonsense negative or huge rate.
            delta_in = current["in"] - before["in"]
            delta_out = current["out"] - before["out"]
            if delta_in < 0 or delta_out < 0:
                continue
            talkers.append({
                "id": vmid,
                "name": names.get(vmid, vmid),
                "in": delta_in / elapsed,
                "out": delta_out / elapsed,
            })
        talkers.sort(key=lambda t: t["in"] + t["out"], reverse=True)
        return talkers[:8]

    def guest(self, kind: str, vmid: int, timeframe: str = "hour") -> dict:
        """Status and history for one guest, fetched on demand.

        The detail view is opened by tapping a tile, so this is not polled for
        every guest continuously: that would be one API round trip per guest
        per tick. A short cache keeps a held-open view from hammering the API.
        """
        if kind not in ("qemu", "lxc"):
            raise ValueError(f"unknown guest type: {kind}")
        if timeframe not in ("hour", "day", "week", "month", "year"):
            raise ValueError(f"unknown timeframe: {timeframe}")

        key = (kind, int(vmid), timeframe)
        cached = self._guest_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < 2.0:
            return cached[1]

        with self._lock:
            client, node = self._pve, self._node
        if not node:
            raise proxmox.ProxmoxError("no Proxmox node resolved yet")

        status = proxmox.shape_guest_status(client.guest_status(node, kind, vmid))
        history = proxmox.shape_guest_rrd(client.guest_rrd(node, kind, vmid, timeframe))
        payload = {
            "id": int(vmid),
            "kind": "VM" if kind == "qemu" else "LXC",
            "type": kind,
            "node": node,
            "timeframe": timeframe,
            "status": status,
            "history": history,
        }
        self._guest_cache[key] = (now, payload)
        if len(self._guest_cache) > 64:   # bounded: one entry per guest/timeframe
            oldest = min(self._guest_cache, key=lambda k: self._guest_cache[k][0])
            self._guest_cache.pop(oldest, None)
        return payload

    def _set_pve_error(self, message: str) -> None:
        with self._lock:
            first = self._pve_error != message
            self._pve_error = message
        if first:
            LOG.warning("proxmox: %s", message)

    # ------------------------------------------------------------------
    # fan controller thread
    # ------------------------------------------------------------------

    def _run_fanctl(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                enabled = self.config["fanctl"]["enabled"]
                interval = self.config["fanctl"]["interval"]
            if enabled:
                try:
                    self._poll_fanctl()
                except Exception:
                    LOG.exception("unhandled error polling the fan controller")
            self._wake_fan.wait(timeout=interval if enabled else 10.0)
            self._wake_fan.clear()

    def _poll_fanctl(self) -> None:
        with self._lock:
            client = self._fanctl
            source = self.config["fanctl"]["source"]
            explicit = list(self.config["fanctl"]["sensors"])
        if not client.configured:
            self._set_fan_error("fan controller URL is not set — open Settings")
            return
        try:
            state = client.state()
        except fanctl_module.FanctlError as exc:
            self._set_fan_error(str(exc))
            return

        shaped = fanctl_module.shape(state, source, explicit)
        with self._lock:
            self._temps = shaped
            if self._fan_error:
                LOG.info("fan controller recovered")
            self._fan_error = None
            self._fan_seen = time.time()

        readings = {s["id"]: s["value"] for s in shaped["sensors"]
                    if s["value"] is not None}
        if readings:
            self._temp_live.append(readings)

    def _set_fan_error(self, message: str) -> None:
        with self._lock:
            first = self._fan_error != message
            self._fan_error = message
        if first:
            LOG.warning("fanctl: %s", message)

    # ------------------------------------------------------------------
    # API surface
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "version": __version__,
                "time": now,
                "warning": self.config_warning,
                "system": dict(self._system),
                "proxmox": {
                    "ok": self._pve_error is None and bool(self._system),
                    "error": self._pve_error,
                    "age": round(now - self._pve_seen, 1) if self._pve_seen else None,
                    "node": self._node,
                    "release": (self._version or {}).get("release", ""),
                    "version": (self._version or {}).get("version", ""),
                },
                "pi": dict(self._host_stats),
                "guests": list(self._guests),
                "talkers": list(self._talkers),
                "storage": list(self._storage),
                "tasks": list(self._tasks),
                "disks": list(self._disks),
                "temps": {
                    "ok": self._fan_error is None,
                    "error": self._fan_error,
                    "age": round(now - self._fan_seen, 1) if self._fan_seen else None,
                    "enabled": self.config["fanctl"]["enabled"],
                    **self._temps,
                },
                "config": config_module.public(self.config),
            }

    def live(self) -> dict:
        return {"system": self._live.series(), "temps": self._temp_live.series()}

    def rrd(self) -> dict:
        with self._lock:
            return dict(self._rrd)

    def update_config(self, raw: dict) -> dict:
        with self._lock:
            new_config = config_module.merge(self.config, raw)
            # Credentials and the host the daemon connects to are file-only.
            # Accepting them over HTTP would let anything that can reach this
            # port redirect the daemon's token at a server of its choosing.
            new_config["proxmox"]["token_secret"] = self.config["proxmox"]["token_secret"]
            new_config["proxmox"]["host"] = self.config["proxmox"]["host"]
            new_config["fanctl"]["token"] = self.config["fanctl"]["token"]

            connection_changed = (
                new_config["proxmox"]["node"] != self.config["proxmox"]["node"]
                or new_config["proxmox"]["verify_tls"] != self.config["proxmox"]["verify_tls"]
                or new_config["proxmox"]["timeout"] != self.config["proxmox"]["timeout"]
            )
            fan_changed = new_config["fanctl"]["url"] != self.config["fanctl"]["url"]

            self.config = new_config
            if connection_changed:
                self._pve = self._make_proxmox()
                self._node = ""
            if fan_changed:
                self._fanctl = self._make_fanctl()
            self._live.resize(LIVE_SECONDS, new_config["proxmox"]["interval"])
            self._temp_live.resize(LIVE_SECONDS, new_config["fanctl"]["interval"])
            config_module.save(self.config_path, new_config)

        self.refresh()
        return config_module.public(new_config)


def _last_number(values) -> float | None:
    """The newest non-empty value in an RRD series."""
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        if isinstance(value, (int, float)):
            return value
    return None
