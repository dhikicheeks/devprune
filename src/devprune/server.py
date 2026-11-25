"""Local-only HTTP server: serves the dashboard and a small token-protected JSON API."""
import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import clean, config, launcher, scan, state

TOKEN = secrets.token_urlsafe(24)
WEB = Path(__file__).parent / "web"
STATIC = {"app.js": "text/javascript", "style.css": "text/css", "logo-mark.png": "image/png"}
MAX_BODY = 1_000_000
LOCAL_HOSTS = ("127.0.0.1", "localhost")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def render_index():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    roots = html.escape(json.dumps(config.default_roots()), quote=True)
    return page.replace("__TOKEN__", html.escape(TOKEN, quote=True)).replace("__ROOTS__", roots)


class Handler(BaseHTTPRequestHandler):
    server_version = "DevPrune"

    def log_message(self, *a):
        pass

    # --- request validation -------------------------------------------------
    def _host_ok(self):
        return self.headers.get("Host", "").rsplit(":", 1)[0] in LOCAL_HOSTS

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        text = ctype.startswith(("text/", "application/json"))
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if text else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    # --- routes -------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "{}")
        if self.path == "/":
            return self._send(200, render_index(), "text/html")
        if self.path.startswith("/static/"):
            name = self.path[len("/static/"):]
            if name in STATIC:
                return self._send(200, (WEB / name).read_bytes(), STATIC[name])
        self._send(404, "{}")

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, '{"error":"forbidden"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_BODY:
                return self._send(413, '{"error":"too large"}')
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, '{"error":"bad request"}')
        route = ROUTES.get(self.path)
        if route is None:
            return self._send(404, "{}")
        self._send(200, json.dumps(route(body)))


def _scan(body):
    roots = [r for r in body.get("roots", []) if isinstance(r, str) and r.strip()]
    return {"started": scan.start_scan(roots, scan.DEFAULT_DEPTH, bool(body.get("full")))}


def _status(_):
    st = {k: state.JOB[k] for k in ("state", "phase", "done", "total", "error")}
    if state.JOB["state"] == "done":
        st["items"] = state.JOB["items"]
    return st


def _cache(_):
    from .cache import pub
    with state.LOCK:
        items = sorted((pub(c) for c in state.STATE["items"].values()), key=lambda x: -x["kb"])
    return {"ts": state.CACHE_TS[0], "items": items}


def _clean(body):
    ids = [str(i) for i in body.get("ids", [])]
    return {"started": clean.start_clean(ids, bool(body.get("dry")))}


def _clean_status(_):
    keys = ("state", "total", "done", "current", "freed_kb", "dry", "error", "started", "finished", "acked")
    st = {k: state.CJOB[k] for k in keys}
    st["plan"] = list(state.CJOB["plan"])  # what was asked for, so a reloaded page can rebuild the progress view
    st["results"] = list(state.CJOB["results"])  # live per-item results, so the UI can show progress
    return st


def _clean_ack(_):
    state.CJOB["acked"] = True  # a page has shown the outcome; do not replay it on the next reload
    return {"ok": True}


def _clean_stop(_):
    state.CJOB["stop"].set()
    return {"ok": True}


def _cancel(_):
    scan.cancel_scan()
    return {"ok": True}


def _command_status(_):
    return launcher.status()


def _command_do(fn):
    def route(body):
        try:
            return {"ok": True, **fn(body.get("name"))}
        except launcher.LauncherError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"Could not write the file: {e.strerror or e}"}
    return route


ROUTES = {"/api/command": _command_status,
          "/api/command_set": _command_do(launcher.install),
          "/api/command_remove": _command_do(launcher.remove),
          "/api/scan": _scan, "/api/status": _status, "/api/cancel": _cancel, "/api/cache": _cache,
          "/api/clean": _clean, "/api/clean_status": _clean_status, "/api/clean_stop": _clean_stop,
          "/api/clean_ack": _clean_ack}


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
