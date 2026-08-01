#!/usr/bin/env python3
"""
Party Checklist microservice.

Zero external dependencies — uses only the Python standard library so the
Docker image needs nothing from PyPI. Serves a single-page checklist UI and
a tiny JSON API backed by a file on disk, so multiple people hitting the
same URL (e.g. via a Cloudflare Tunnel) see and update one shared state.

Env vars (all optional):
  PORT              - port to listen on (default 8080)
  DATA_DIR          - where to persist state.json (default /data)
  BASIC_AUTH_USER   - if set together with BASIC_AUTH_PASS, requires HTTP
  BASIC_AUTH_PASS     Basic Auth on every request
"""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from checklist_items import GROUPS, all_item_ids

PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATE_FILE = DATA_DIR / "state.json"
AUTH_USER = os.environ.get("BASIC_AUTH_USER")
AUTH_PASS = os.environ.get("BASIC_AUTH_PASS")

INDEX_HTML_PATH = Path(__file__).parent / "index.html"

_lock = threading.Lock()


def _load_state():
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(state, f)
    tmp.replace(STATE_FILE)


# In-memory cache of state, mirrored to disk on every write.
_state = _load_state()


class Handler(BaseHTTPRequestHandler):
    server_version = "PartyChecklist/1.0"

    def log_message(self, fmt, *args):
        # Keep container logs readable: method, path, status only.
        print(f"{self.address_string()} - {fmt % args}")

    # ---- auth -----------------------------------------------------------

    def _authorized(self):
        if not (AUTH_USER and AUTH_PASS):
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:
            return False
        user, _, pw = decoded.partition(":")
        return user == AUTH_USER and pw == AUTH_PASS

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Party Checklist"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- helpers ----------------------------------------------------------

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # ---- routes -------------------------------------------------------

    def do_GET(self):
        if not self._authorized():
            return self._require_auth()

        if self.path in ("/", "/index.html"):
            return self._send_file(INDEX_HTML_PATH, "text/html; charset=utf-8")

        if self.path == "/api/checklist":
            with _lock:
                state_copy = dict(_state)
            return self._send_json({"groups": GROUPS, "state": state_copy})

        if self.path == "/healthz":
            return self._send_json({"ok": True})

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not self._authorized():
            return self._require_auth()

        if self.path == "/api/toggle":
            body = self._read_json_body()
            item_id = body.get("id")
            checked = bool(body.get("checked"))
            valid_ids = set(all_item_ids())
            if item_id not in valid_ids:
                return self._send_json({"error": "unknown item id"}, status=400)
            with _lock:
                _state[item_id] = checked
                _save_state(_state)
                state_copy = dict(_state)
            return self._send_json({"state": state_copy})

        if self.path == "/api/reset":
            with _lock:
                _state.clear()
                _save_state(_state)
            return self._send_json({"state": {}})

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Party Checklist serving on :{PORT}, data dir {DATA_DIR}")
    if AUTH_USER and AUTH_PASS:
        print("Basic Auth: enabled")
    else:
        print("Basic Auth: disabled (set BASIC_AUTH_USER/BASIC_AUTH_PASS to enable)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
