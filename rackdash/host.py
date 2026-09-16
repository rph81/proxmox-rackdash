"""The Pi's own vitals: its SoC temperature, CPU load and GPU load.

These are about the machine the screen is bolted to, not the hypervisor, so
they are kept apart from everything else in the snapshot and labelled `PI` on
screen.  Temperature and CPU come from sysfs and /proc and always work.

The GPU is the awkward one.  A Raspberry Pi has no single agreed place to read
V3D utilisation from, and what exists differs by model, kernel and whether the
reader is root, so this tries the known sources in order and reports which one
answered.  When none do, the dashboard shows a dash rather than a made-up
number.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import threading
import time

LOG = logging.getLogger("rackdash.host")

THERMAL_GLOB = "/sys/class/thermal/thermal_zone*/type"
PROC_STAT = "/proc/stat"
MODEL_PATHS = ("/sys/firmware/devicetree/base/model", "/proc/device-tree/model")

# What the GPU figure actually is depends on what the machine will tell us.
# A percentage is only reported when something genuinely measures utilisation;
# otherwise the V3D clock is reported as a clock, in MHz. Inferring a
# percentage from the clock was tempting and wrong: on a Pi 5 the V3D clock
# often sits at a fixed value regardless of load, so the "utilisation" would
# have read 100% forever.
GPU_PERCENT = "%"
GPU_CLOCK = "MHz"

# The v3d driver publishes accumulated busy time per scheduling queue here,
# alongside the clock those totals are measured against. Differencing both
# between two reads gives real utilisation, and because the ratio is of two
# values from the same clock, the units cancel and never need to be assumed.
# This is the same source the Raspberry Pi desktop's GPU widget uses.
V3D_STATS_GLOBS = (
    "/sys/devices/platform/axi/*.v3d/gpu_stats",
    "/sys/devices/platform/*/*.v3d/gpu_stats",
    "/sys/bus/platform/devices/*.v3d/gpu_stats",
    "/sys/class/drm/card*/device/gpu_stats",
)
# Rows that are not GPU work queues.
V3D_SKIP_QUEUES = frozenset({"cpu"})


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip("\x00").strip()
    except OSError:
        return None


class HostStats:
    """Reads the local machine's vitals. Cheap enough to call every tick."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_cpu: tuple | None = None
        self._last_cpu: float | None = None
        self._thermal: str | None = None
        self._gpu_source: str | None = None
        self._gpu_warned = False
        self._v3d_stats: str | None = None
        self._prev_v3d: tuple | None = None
        self._last_gpu: float | None = None
        self._vcgencmd: str | None | bool = False   # False = not looked for yet
        self.model = self._read_model()

    # -- model ------------------------------------------------------------

    @staticmethod
    def _read_model() -> str:
        for path in MODEL_PATHS:
            value = _read(path)
            if value:
                return value
        return ""

    @property
    def is_pi(self) -> bool:
        return "raspberry pi" in self.model.lower()

    def _pi_generation(self) -> str:
        match = re.search(r"Raspberry Pi\s+(\d+)", self.model)
        return match.group(1) if match else ""

    # -- temperature ------------------------------------------------------

    def _thermal_path(self) -> str | None:
        """Find the SoC thermal zone once and remember it.

        Zone 0 is the CPU on a Pi, but on other boards it can be a battery or
        a wireless chip, so the zone is matched by type rather than assumed.
        """
        if self._thermal is not None:
            return self._thermal or None
        candidates = sorted(glob.glob(THERMAL_GLOB))
        best = ""
        for type_path in candidates:
            kind = (_read(type_path) or "").lower()
            if any(tag in kind for tag in ("cpu", "soc", "package", "x86_pkg")):
                best = type_path[: -len("type")] + "temp"
                break
        if not best and candidates:
            best = candidates[0][: -len("type")] + "temp"
        self._thermal = best
        return best or None

    def temperature(self) -> float | None:
        path = self._thermal_path()
        if not path:
            return None
        raw = _read(path)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        # Kernels report millidegrees; a few report whole degrees.
        return value / 1000.0 if value > 200 else value

    # -- cpu --------------------------------------------------------------

    def cpu_percent(self) -> float | None:
        """Busy percentage since the previous call, from /proc/stat."""
        line = None
        try:
            with open(PROC_STAT, "r", encoding="utf-8") as handle:
                line = handle.readline()
        except OSError:
            return None
        if not line or not line.startswith("cpu "):
            return None
        try:
            fields = [int(v) for v in line.split()[1:]]
        except ValueError:
            return None
        if len(fields) < 4:
            return None

        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)   # idle + iowait
        total = sum(fields)
        with self._lock:
            previous = self._prev_cpu
            self._prev_cpu = (total, idle)
        if previous is None:
            return None                       # first sample has nothing to diff
        total_delta = total - previous[0]
        idle_delta = idle - previous[1]
        if total_delta <= 0:
            # Polled faster than the kernel advances its counters. Nothing has
            # changed, so the previous figure still stands; reporting None here
            # would blink the readout to a dash for no reason.
            return self._last_cpu
        busy = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        self._last_cpu = busy
        return busy

    # -- gpu --------------------------------------------------------------

    def gpu(self) -> dict:
        """{value, unit, source}. Unit says whether this is load or a clock."""
        for reader in (self._gpu_from_v3d_stats, self._gpu_from_debugfs,
                       self._gpu_from_devfreq, self._gpu_from_vcgencmd):
            result = reader()
            if result is not None:
                self._gpu_source = result[2]
                return {"value": result[0], "unit": result[1], "source": result[2]}
        self._gpu_source = None
        return {"value": None, "unit": GPU_PERCENT, "source": None}

    @property
    def gpu_source(self) -> str | None:
        return self._gpu_source

    def _v3d_stats_path(self) -> str | None:
        if self._v3d_stats is not None:
            return self._v3d_stats or None
        for pattern in V3D_STATS_GLOBS:
            found = sorted(glob.glob(pattern))
            if found:
                self._v3d_stats = found[0]
                LOG.info("reading GPU utilisation from %s", found[0])
                return found[0]
        self._v3d_stats = ""
        return None

    @staticmethod
    def _parse_v3d_stats(text: str) -> tuple | None:
        """(clock, {queue: busy}) from the gpu_stats table, or None."""
        clock = None
        queues: dict = {}
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 4 or fields[0] == "queue":
                continue
            name = fields[0]
            try:
                stamp, busy = int(fields[1]), int(fields[3])
            except ValueError:
                continue
            clock = stamp if clock is None else max(clock, stamp)
            if name not in V3D_SKIP_QUEUES:
                queues[name] = busy
        if clock is None or not queues:
            return None
        return (clock, queues)

    def _gpu_from_v3d_stats(self) -> tuple | None:
        """Real GPU utilisation, differenced from the driver's own counters."""
        path = self._v3d_stats_path()
        if not path:
            return None
        text = _read(path)
        if not text:
            return None
        sample = self._parse_v3d_stats(text)
        if sample is None:
            return None

        clock, queues = sample
        previous = self._prev_v3d
        self._prev_v3d = sample
        if previous is None:
            return None                       # needs two reads to mean anything
        elapsed = clock - previous[0]
        if elapsed <= 0:
            # The clock has not advanced, or the counters were reset by a
            # driver reload. Hold the last figure rather than blink to a dash.
            return ((self._last_gpu, GPU_PERCENT, "v3d stats")
                    if self._last_gpu is not None else None)

        # Queues run concurrently, so summing them can exceed the wall clock.
        # The busiest queue is the honest single answer to "how hard is the
        # GPU working", and it is naturally bounded at 100%.
        busiest = 0.0
        for name, busy in queues.items():
            delta = busy - previous[1].get(name, busy)
            if delta > 0:
                busiest = max(busiest, delta / elapsed * 100.0)
        self._last_gpu = max(0.0, min(100.0, busiest))
        return (self._last_gpu, GPU_PERCENT, "v3d stats")

    def _gpu_from_debugfs(self) -> tuple | None:
        """Real utilisation, where the kernel exposes it.

        Only some builds create this file, and it is root-only, so on a stock
        Pi this almost always misses and the clock reader below answers.
        """
        for path in glob.glob("/sys/kernel/debug/dri/*/gpu_usage"):
            text = _read(path)
            if not text:
                continue
            match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            if match:
                return (float(match.group(1)), GPU_PERCENT, "debugfs")
        return None

    def _gpu_from_devfreq(self) -> tuple | None:
        """Some kernels expose the V3D through devfreq with a real load figure."""
        for base in glob.glob("/sys/class/devfreq/*v3d*") + glob.glob("/sys/class/devfreq/*gpu*"):
            load = _read(os.path.join(base, "device/load"))
            if load and load.isdigit():
                return (max(0.0, min(100.0, float(load))), GPU_PERCENT, "devfreq")
            current = _read(os.path.join(base, "cur_freq"))
            if current and current.isdigit():
                return (int(current) / 1e6, GPU_CLOCK, "devfreq clock")
        return None

    def _gpu_from_vcgencmd(self) -> tuple | None:
        """The V3D clock, reported as a clock.

        Needs the `video` group (the installer grants it) *and* a device
        sandbox that leaves /dev/vcio reachable (the unit allows it). When
        either is missing the tool fails, so the reason is logged once.
        """
        if self._vcgencmd is False:
            from shutil import which
            self._vcgencmd = which("vcgencmd")
            if not self._vcgencmd:
                LOG.info("vcgencmd not found; no GPU reading on this machine")
        if not self._vcgencmd:
            return None
        try:
            done = subprocess.run([self._vcgencmd, "measure_clock", "v3d"],
                                  capture_output=True, text=True, timeout=2.0,
                                  stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.warning("vcgencmd failed, GPU reading disabled: %s", exc)
            self._vcgencmd = None            # do not keep retrying a broken tool
            return None

        match = re.search(r"=(\d+)", done.stdout or "")
        if not match:
            if not self._gpu_warned:
                detail = (done.stderr or done.stdout or "").strip() or "no output"
                LOG.warning("vcgencmd gave no V3D clock (%s). If this is a Pi, the "
                            "service needs the video group and access to /dev/vcio.",
                            detail.splitlines()[0])
                self._gpu_warned = True
            return None
        hertz = int(match.group(1))
        if hertz <= 0:
            return None
        return (hertz / 1e6, GPU_CLOCK, "v3d clock")

    # -- combined ---------------------------------------------------------

    def read(self) -> dict:
        gpu = self.gpu()
        return {
            "model": self.model,
            "temp": self.temperature(),
            "cpu": self.cpu_percent(),
            "gpu": gpu["value"],
            "gpu_unit": gpu["unit"],
            "gpu_source": gpu["source"],
            "t": time.time(),
        }
