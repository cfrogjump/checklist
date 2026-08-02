#!/usr/bin/env python3
"""
Party Checklist microservice.

Zero external dependencies — uses only the Python standard library so the
Docker image needs nothing from PyPI. Serves a single-page checklist UI and
a tiny JSON API backed by a file on disk, so multiple people hitting the
same URL (e.g. via a Cloudflare Tunnel) see and update one shared state.

Access is gated by HTTP Basic Auth against a hardcoded guest list: you type
your first name as the username and leave the password blank. See
ALLOWED_NAMES below.

Env vars (all optional):
  PORT              - port to listen on (default 8080)
  DATA_DIR          - where to persist state.json (default /data)
"""

import base64
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from checklist_items import GROUPS, all_item_ids

PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATE_FILE = DATA_DIR / "state.json"

# Who's allowed in. Sign in with any of these as the Basic Auth username
# (any capitalization); the password box is ignored, so leave it blank.
# This keeps strangers who stumble onto the tunnel URL out — it is not real
# security, since anyone who knows the household can guess a name.
ALLOWED_NAMES = [
    "Cade", "Cailin", "Benton", "Harley", "Misty", "Chelsea", "Cami",
]
_NAMES_BY_KEY = {name.casefold(): name for name in ALLOWED_NAMES}

INDEX_HTML_PATH = Path(__file__).parent / "index.html"

_lock = threading.Lock()


def _load_state():
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                known = ("checked", "order", "extras", "sections")
                if any(key in data for key in known):
                    checked = data.get("checked")
                    order = data.get("order")
                    extras = data.get("extras")
                    sections = data.get("sections")
                    return {
                        "checked": checked if isinstance(checked, dict) else {},
                        "order": order if isinstance(order, list) else [],
                        "extras": extras if isinstance(extras, dict) else {},
                        "sections": sections if isinstance(sections, list) else [],
                    }
                # Legacy format: a flat {item_id: bool} dict.
                return {"checked": data, "order": [], "extras": {}, "sections": []}
        except (json.JSONDecodeError, OSError):
            pass
    return {"checked": {}, "order": [], "extras": {}, "sections": []}


def _save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(state, f)
    tmp.replace(STATE_FILE)


# In-memory cache of state, mirrored to disk on every write.
# Shape: {"checked": {item_id: bool}, "order": [group_id, ...],
#         "extras": {group_id: [{"id": "<gid>-x<n>", "text": str}, ...]},
#         "sections": [{"id": "custom-<n>", "section": str, "title": str}]}
# "extras" and "sections" are user-added; the baked-in checklist lives in
# checklist_items.py. User-added sections have no baked-in items of their
# own — everything in them is an "extras" entry.
_state = _load_state()

MAX_ITEM_TEXT = 200
MAX_EXTRAS_PER_GROUP = 100
MAX_TITLE_LEN = 60
MAX_CUSTOM_SECTIONS = 30

# One queue per open /api/events stream. Every write broadcasts the new
# checklist to all of them, so viewers update in well under a second instead
# of waiting for a poll. A comment line every SSE_HEARTBEAT_SECONDS keeps
# idle streams from being closed by a proxy (Cloudflare's is ~100s).
_subscribers = set()
_subscribers_lock = threading.Lock()
SSE_HEARTBEAT_SECONDS = 25
SSE_QUEUE_DEPTH = 32


def _next_extra_id(group_id):
    """Extra-item ids look like "kitchen-x7". The "x" keeps them out of the
    baked-in "<gid>-<index>" namespace; the counter is the max suffix across
    all groups so ids stay unique for a group even if items move later."""
    n = 0
    for items in _state["extras"].values():
        for item in items:
            parts = str(item.get("id", "")).rsplit("-x", 1)
            if len(parts) == 2 and parts[1].isdigit():
                n = max(n, int(parts[1]))
    return f"{group_id}-x{n + 1}"


def _valid_item_ids_locked():
    ids = set(all_item_ids())
    for items in _state["extras"].values():
        ids.update(str(item.get("id")) for item in items)
    return ids


def _next_section_id():
    """User-added section ids look like "custom-3". The "custom-" prefix keeps
    them clear of the ids in checklist_items.py, and containing no "-x" keeps
    _next_extra_id's parsing unambiguous."""
    n = 0
    for section in _state["sections"]:
        sid = str(section.get("id", ""))
        suffix = sid[len("custom-"):]
        if sid.startswith("custom-") and suffix.isdigit():
            n = max(n, int(suffix))
    return f"custom-{n + 1}"


def _all_groups_locked():
    """Baked-in groups plus user-added sections, in canonical (unordered)
    form. User-added sections carry no baked-in items."""
    groups = list(GROUPS)
    for section in _state["sections"]:
        groups.append({
            "section": section["section"],
            "id": section["id"],
            "title": section["title"],
            "items": [],
        })
    return groups


def _all_group_ids_locked():
    return {g["id"] for g in _all_groups_locked()}


def _checklist_payload_locked():
    groups = []
    for g in _ordered_groups(_state["order"], _all_groups_locked()):
        g2 = dict(g)
        g2["extras"] = list(_state["extras"].get(g["id"], []))
        groups.append(g2)
    return {"groups": groups, "state": dict(_state["checked"])}


def _broadcast_locked():
    """Push the current checklist to every open event stream. Call while
    holding _lock. Lock order is always _lock -> _subscribers_lock."""
    payload = json.dumps(_checklist_payload_locked())
    with _subscribers_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(payload)
            except queue.Full:
                # A wedged client; it'll resync from its next full fetch.
                pass


def _ordered_groups(order, groups):
    """Groups sorted by the saved order. Unknown ids in the order are ignored.
    Groups missing from it — a section someone just added, or a new entry in
    checklist_items.py — slot in after the last group sharing their area
    heading, so a new "Outside" section doesn't strand itself under a second
    OUTSIDE heading at the bottom. Falls back to the end."""
    by_id = {g["id"]: g for g in groups}
    ordered = [by_id[gid] for gid in order if gid in by_id]
    seen = set(order)
    for group in groups:
        if group["id"] in seen:
            continue
        pos = len(ordered)
        for i, placed in enumerate(ordered):
            if placed["section"] == group["section"]:
                pos = i + 1
        ordered.insert(pos, group)
    return ordered


class Handler(BaseHTTPRequestHandler):
    server_version = "PartyChecklist/1.0"

    def log_message(self, fmt, *args):
        # Keep container logs readable: method, path, status only.
        print(f"{self.address_string()} - {fmt % args}")

    # ---- auth -----------------------------------------------------------

    def _auth_name(self):
        """Return the guest's canonical name, or None if they're not on the
        list. The password half of the credentials is deliberately ignored."""
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return None
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:
            return None
        user, _, _pw = decoded.partition(":")
        return _NAMES_BY_KEY.get(user.strip().casefold())

    def _authorized(self):
        return self._auth_name() is not None

    def _require_auth(self):
        # Header values must be latin-1 encodable, so keep the realm ASCII.
        body = (
            b"<!DOCTYPE html><meta charset=utf-8>"
            b"<p style='font:16px system-ui;padding:2rem'>"
            b"Sign in with your first name. Leave the password blank."
        )
        self.send_response(401)
        self.send_header(
            "WWW-Authenticate",
            'Basic realm="Party Checklist - your first name, no password"',
        )
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    def _serve_events(self):
        """Server-Sent Events stream. Holds the connection (and this thread)
        open for as long as the viewer has the page up, which is fine at
        household scale — one thread per open tab."""
        q = queue.Queue(maxsize=SSE_QUEUE_DEPTH)
        with _subscribers_lock:
            _subscribers.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # Ask intermediaries not to buffer, or events arrive in clumps.
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            with _lock:
                chunk = "data: %s\n\n" % json.dumps(_checklist_payload_locked())
            while True:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
                try:
                    chunk = "data: %s\n\n" % q.get(timeout=SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    chunk = ": ping\n\n"
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass  # viewer closed the tab or dropped off the network
        finally:
            with _subscribers_lock:
                _subscribers.discard(q)

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
        # Left open so container/uptime health checks don't need credentials.
        if self.path == "/healthz":
            return self._send_json({"ok": True})

        if not self._authorized():
            return self._require_auth()

        if self.path in ("/", "/index.html"):
            return self._send_file(INDEX_HTML_PATH, "text/html; charset=utf-8")

        if self.path == "/api/checklist":
            with _lock:
                payload = _checklist_payload_locked()
            return self._send_json(payload)

        if self.path == "/api/events":
            return self._serve_events()

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
            with _lock:
                if item_id not in _valid_item_ids_locked():
                    state_copy = None
                else:
                    _state["checked"][item_id] = checked
                    _save_state(_state)
                    _broadcast_locked()
                    state_copy = dict(_state["checked"])
            if state_copy is None:
                return self._send_json({"error": "unknown item id"}, status=400)
            return self._send_json({"state": state_copy})

        if self.path == "/api/add-item":
            body = self._read_json_body()
            group_id = body.get("group")
            text = str(body.get("text") or "").strip()[:MAX_ITEM_TEXT]
            if not text:
                return self._send_json({"error": "empty item text"}, status=400)
            with _lock:
                # Validated under the lock: user-added sections are valid
                # targets too, and they can appear between requests.
                if group_id not in _all_group_ids_locked():
                    payload, error = None, "unknown group"
                else:
                    items = _state["extras"].setdefault(group_id, [])
                    if len(items) >= MAX_EXTRAS_PER_GROUP:
                        payload, error = None, "section is full"
                    else:
                        items.append({"id": _next_extra_id(group_id), "text": text})
                        _save_state(_state)
                        _broadcast_locked()
                        payload, error = _checklist_payload_locked(), None
            if payload is None:
                return self._send_json({"error": error}, status=400)
            return self._send_json(payload)

        if self.path == "/api/add-section":
            body = self._read_json_body()
            title = str(body.get("title") or "").strip()[:MAX_TITLE_LEN]
            area = str(body.get("area") or "").strip()[:MAX_TITLE_LEN]
            if not title:
                return self._send_json({"error": "empty section title"}, status=400)
            with _lock:
                if len(_state["sections"]) >= MAX_CUSTOM_SECTIONS:
                    payload = None
                else:
                    _state["sections"].append({
                        "id": _next_section_id(),
                        # Unrecognized or missing area lands with the last
                        # baked-in group's heading rather than inventing one.
                        "section": area or GROUPS[-1]["section"],
                        "title": title,
                    })
                    _save_state(_state)
                    _broadcast_locked()
                    payload = _checklist_payload_locked()
            if payload is None:
                return self._send_json({"error": "too many sections"}, status=400)
            return self._send_json(payload)

        if self.path == "/api/order":
            body = self._read_json_body()
            order = body.get("order")
            if not isinstance(order, list) or len(order) != len(set(map(str, order))):
                return self._send_json({"error": "invalid order"}, status=400)
            with _lock:
                valid_ids = _all_group_ids_locked()
                ok = all(isinstance(g, str) and g in valid_ids for g in order)
                if ok:
                    _state["order"] = order
                    _save_state(_state)
                    _broadcast_locked()
            if not ok:
                return self._send_json({"error": "invalid order"}, status=400)
            return self._send_json({"ok": True})

        if self.path == "/api/reset":
            # Clears everyone's checks; the saved section order is kept.
            with _lock:
                _state["checked"].clear()
                _save_state(_state)
                _broadcast_locked()
            return self._send_json({"state": {}})

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Party Checklist serving on :{PORT}, data dir {DATA_DIR}")
    print(f"Sign-in: first name only, {len(ALLOWED_NAMES)} guests on the list")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
