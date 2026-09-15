"""Minimal HTTP/JSON API and static file server.

Built on `http.server` so the Pi needs nothing from pip.  The server binds
127.0.0.1 by default because its only client is the kiosk browser on the same
machine; binding it wider is a config change the operator makes deliberately.

State-changing requests must be same-origin JSON.  The dashboard holds a
Proxmox API token, so a page in another tab must not be able to drive it.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import socket
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG = logging.getLogger("rackdash.http")

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

MAX_BODY = 1_000_000


class Handler(BaseHTTPRequestHandler):
    server_version = "rackdash"
    sys_version = ""                      # do not advertise the Python version
    protocol_version = "HTTP/1.1"
    timeout = 30                          # do not let an idle client hold a thread

    collector = None                      # injected by make_server()
    web_root = WEB_ROOT

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):    # noqa: A003 - stdlib signature
        LOG.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return parsed

    def _cross_site_problem(self) -> str | None:
        """Why a state-changing request looks like it came from another site.

        The API is unauthenticated on the loopback interface, so without this
        any page in another browser tab could POST here and reconfigure the
        dashboard. A cross-site POST always carries Origin, and requiring a
        JSON content type forces a preflight the browser refuses.
        """
        origin = self.headers.get("Origin")
        if origin:
            host = (self.headers.get("Host") or "").strip().lower()
            if urlparse(origin).netloc.lower() != host:
                return f"cross-site request from {origin} refused"
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return "cross-site request refused"
        if int(self.headers.get("Content-Length") or 0) > 0:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return "request body must be sent with Content-Type: application/json"
        return None

    # -- routing ----------------------------------------------------------

    def do_GET(self):                     # noqa: N802 - stdlib signature
        self._dispatch("GET")

    def do_HEAD(self):                    # noqa: N802
        self._dispatch("GET")

    def do_POST(self):                    # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):                     # noqa: N802
        self._dispatch("PUT")

    def _dispatch(self, method: str):
        path = posixpath.normpath(urlparse(self.path).path)
        try:
            if method in ("POST", "PUT"):
                problem = self._cross_site_problem()
                if problem:
                    self._error(HTTPStatus.FORBIDDEN, problem)
                    return
            if path.startswith("/api/"):
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} not allowed on {path}")
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except BrokenPipeError:
            pass                          # the kiosk reloaded mid-response
        except Exception:
            LOG.exception("error handling %s %s", method, path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _api(self, method: str, path: str):
        collector = self.collector
        route = path.strip("/").split("/")[1:]

        if route == ["healthz"] and method == "GET":
            self._json({"ok": True})
            return
        if route == ["state"] and method == "GET":
            self._json(collector.snapshot())
            return
        if route == ["live"] and method == "GET":
            self._json(collector.live())
            return
        if route == ["rrd"] and method == "GET":
            self._json(collector.rrd())
            return
        if route == ["config"]:
            if method == "GET":
                self._json(collector.snapshot()["config"])
                return
            if method == "PUT":
                self._json(collector.update_config(self._body()))
                return
        if route == ["refresh"] and method == "POST":
            collector.refresh()
            self._json({"ok": True})
            return
        if len(route) == 3 and route[0] == "guest" and method == "GET":
            kind, raw_id = route[1], route[2]
            if kind not in ("qemu", "lxc"):
                raise ValueError(f"unknown guest type: {kind}")
            try:
                vmid = int(raw_id)
            except ValueError as exc:
                raise ValueError(f"invalid guest id: {raw_id}") from exc
            timeframe = (parse_qs(urlparse(self.path).query).get("timeframe", ["hour"])[0])
            try:
                self._json(collector.guest(kind, vmid, timeframe))
            except Exception as exc:
                # A guest that is gone or an API hiccup is an expected outcome
                # of tapping a stale tile, not a server fault.
                self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            return

        self._error(HTTPStatus.NOT_FOUND, f"no route for {method} {path}")

    def _static(self, path: str):
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = os.path.normpath(os.path.join(self.web_root, relative))
        if not target.startswith(os.path.abspath(self.web_root) + os.sep):
            self._error(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not os.path.isfile(target):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        with open(target, "rb") as handle:
            body = handle.read()
        extension = os.path.splitext(target)[1]
        self._send(200, body, CONTENT_TYPES.get(extension, "application/octet-stream"))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, ipv6: bool):
        self._ipv6 = ipv6
        if ipv6:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def server_bind(self):
        # IP_FREEBIND lets the socket bind an address that is not configured
        # yet, so a pinned address works at boot before the network is up.
        if sys.platform.startswith("linux"):
            level, option = ((socket.IPPROTO_IPV6, getattr(socket, "IPV6_FREEBIND", 78))
                             if self._ipv6
                             else (socket.IPPROTO_IP, getattr(socket, "IP_FREEBIND", 15)))
            try:
                self.socket.setsockopt(level, option, 1)
            except OSError:
                pass
        super().server_bind()


def make_server(collector, bind: str, port: int, web_root: str = WEB_ROOT):
    handler = type("BoundHandler", (Handler,), {
        "collector": collector,
        "web_root": os.path.abspath(web_root),
    })
    return Server((bind, port), handler, ipv6=":" in bind)


def serve_forever(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    return thread
