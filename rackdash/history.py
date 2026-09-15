"""Short in-memory ring buffers for the live sparklines.

Deliberately small.  Long history already exists elsewhere and is fetched on
demand rather than duplicated here: Proxmox keeps its own RRD data, and
corsair-fanctl persists its chart history since v1.4.1.  This buffer only
covers the few minutes between those coarse samples so the live traces move
smoothly.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class Series:
    """A fixed-length time series of (timestamp, {key: value}) samples."""

    def __init__(self, seconds: float = 300.0, interval: float = 2.0):
        self._lock = threading.Lock()
        self._samples: deque = deque(maxlen=self._capacity(seconds, interval))

    @staticmethod
    def _capacity(seconds: float, interval: float) -> int:
        return max(30, min(5000, int(seconds / max(interval, 0.5)) + 1))

    def resize(self, seconds: float, interval: float) -> None:
        capacity = self._capacity(seconds, interval)
        with self._lock:
            if capacity != self._samples.maxlen:
                self._samples = deque(self._samples, maxlen=capacity)

    def append(self, values: dict, when: float | None = None) -> None:
        with self._lock:
            self._samples.append({"t": when if when is not None else time.time(),
                                  "v": dict(values)})

    def series(self, keys: list | None = None) -> dict:
        """Return {"time": [...], key: [...]} with None for gaps."""
        with self._lock:
            samples = list(self._samples)
        if keys is None:
            keys = []
            for sample in samples:
                for key in sample["v"]:
                    if key not in keys:
                        keys.append(key)
        out = {"time": [round(s["t"], 1) for s in samples]}
        for key in keys:
            out[key] = [s["v"].get(key) for s in samples]
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)
