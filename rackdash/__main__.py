"""Daemon entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from . import __version__, config as config_module
from .collector import Collector
from .httpd import make_server, serve_forever

DEFAULT_CONFIG_PATH = "/etc/rackdash/config.json"
BIND_RETRY_SECONDS = 5.0
LOG = logging.getLogger("rackdash")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _check(config_path: str) -> int:
    """Poll both sources once and print what came back, then exit."""
    collector = Collector(config_path)
    cfg = collector.config

    print(f"config:  {config_path}")
    print(f"proxmox: {cfg['proxmox']['host'] or '(not set)'} "
          f"as {cfg['proxmox']['token_id'] or '(no token)'}")
    collector._poll_proxmox()
    snapshot = collector.snapshot()
    if snapshot["proxmox"]["error"]:
        print(f"  ERROR  {snapshot['proxmox']['error']}")
    else:
        system = snapshot["system"]
        print(f"  ok     node {system.get('node')}, "
              f"{system.get('cpus')} cpus, "
              f"cpu {system.get('cpu', 0):.1f}%, "
              f"mem {system['memory']['percent']:.1f}%, "
              f"disk {system['disk']['percent']:.1f}%")
        print(f"         {len(snapshot['guests'])} guest(s), "
              f"{len(snapshot['storage'])} storage pool(s)")

    print(f"fanctl:  {cfg['fanctl']['url'] or '(not set)'}")
    if cfg["fanctl"]["enabled"]:
        collector._poll_fanctl()
        temps = collector.snapshot()["temps"]
        if temps["error"]:
            print(f"  ERROR  {temps['error']}")
        else:
            print(f"  ok     {len(temps['sensors'])} sensor(s) selected "
                  f"({cfg['fanctl']['source']})")
            for sensor in temps["sensors"]:
                value = sensor["value"]
                reading = f"{value:.1f} C" if isinstance(value, (int, float)) else "n/a"
                fans = ", ".join(sensor["fans"]) or "-"
                print(f"         {reading:>8}  {sensor['label']}  [{fans}]")
    else:
        print("  off    fanctl.enabled is false")

    ok = snapshot["proxmox"]["error"] is None
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rackdash",
        description="Proxmox dashboard for a rack-mounted touchscreen.",
    )
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH,
                        help=f"config file path (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--bind", help="override the listen address from the config")
    parser.add_argument("--port", type=int, help="override the listen port from the config")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    parser.add_argument("--check", action="store_true",
                        help="poll both sources once, print the result and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    _setup_logging(args.log_level)

    if args.check:
        return _check(args.config)

    collector = Collector(args.config)
    # Persist immediately so a fresh install lands a complete, editable file.
    config_module.save(args.config, collector.config)

    bind = args.bind or collector.config["http"]["bind"]
    port = args.port or collector.config["http"]["port"]

    done = threading.Event()

    def _handle_signal(signum, _frame):
        LOG.info("received %s, shutting down", signal.Signals(signum).name)
        done.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    collector.start()

    server = None
    try:
        while server is None and not done.is_set():
            try:
                server = make_server(collector, bind, port)
            except OSError as exc:
                LOG.error("cannot bind %s:%s: %s -- retrying in %ss",
                          bind, port, exc, BIND_RETRY_SECONDS)
                done.wait(BIND_RETRY_SECONDS)
        if server is not None:
            serve_forever(server)
            LOG.info("listening on http://%s:%s/", bind, port)
            done.wait()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        collector.stop()
    LOG.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
