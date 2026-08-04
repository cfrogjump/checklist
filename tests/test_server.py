#!/usr/bin/env python3
"""Tests for the checklist server. Standard library only, like the server.

    python3 -m unittest discover -s tests -v
"""

import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# Point the server at a scratch data dir before importing it — it reads the
# environment and loads state at import time.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="checklist-test-")

import server  # noqa: E402

server.Handler.log_message = lambda *a, **k: None  # keep test output readable


def basic(name):
    return "Basic " + base64.b64encode(f"{name}:".encode()).decode()


class StateLoadingTests(unittest.TestCase):
    """state.json written by any past version must still load."""

    def _load_from(self, contents):
        d = Path(tempfile.mkdtemp())
        if contents is not None:
            (d / "state.json").write_text(contents)
        old = server.STATE_FILE
        server.STATE_FILE = d / "state.json"
        try:
            return server._load_state()
        finally:
            server.STATE_FILE = old

    def test_missing_file_gives_empty_state(self):
        self.assertEqual(self._load_from(None), server._empty_state())

    def test_corrupt_file_gives_empty_state(self):
        self.assertEqual(self._load_from("{not json"), server._empty_state())

    def test_original_flat_format_migrates(self):
        state = self._load_from(json.dumps({"kitchen-2": True}))
        self.assertEqual(state["checked"], {"kitchen-2": True})
        self.assertEqual(state["order"], [])
        self.assertEqual(state["overrides"], {})

    def test_pre_overrides_format_migrates(self):
        state = self._load_from(json.dumps({
            "checked": {"patio-0": True},
            "order": ["patio"],
            "extras": {"patio": [{"id": "patio-x1", "text": "Buy ice"}]},
            "sections": [{"id": "custom-1", "section": "Outside", "title": "Garage"}],
        }))
        self.assertEqual(state["checked"], {"patio-0": True})
        self.assertEqual(len(state["sections"]), 1)
        self.assertEqual(state["overrides"], {})
        self.assertEqual(state["item_order"], {})

    def test_wrongly_typed_keys_are_replaced(self):
        state = self._load_from(json.dumps({"checked": [], "order": {}, "extras": 5}))
        self.assertEqual(state["checked"], {})
        self.assertEqual(state["order"], [])
        self.assertEqual(state["extras"], {})


class GroupOrderingTests(unittest.TestCase):
    GROUPS = [
        {"id": "a", "section": "Outside", "title": "A", "items": []},
        {"id": "b", "section": "Outside", "title": "B", "items": []},
        {"id": "c", "section": "House", "title": "C", "items": []},
    ]

    def ids(self, order, groups=None):
        return [g["id"] for g in server._ordered_groups(order, groups or self.GROUPS)]

    def test_saved_order_is_respected(self):
        self.assertEqual(self.ids(["c", "b", "a"]), ["c", "b", "a"])

    def test_unknown_ids_in_saved_order_are_ignored(self):
        self.assertEqual(self.ids(["ghost", "c"])[0], "c")

    def test_new_group_lands_with_its_own_area(self):
        # "d" is new and belongs to Outside, so it should follow b, not fall
        # to the bottom under a second Outside heading.
        groups = self.GROUPS + [
            {"id": "d", "section": "Outside", "title": "D", "items": []}
        ]
        self.assertEqual(self.ids(["a", "b", "c"], groups), ["a", "b", "d", "c"])

    def test_empty_order_keeps_canonical_order(self):
        self.assertEqual(self.ids([]), ["a", "b", "c"])


class IdAllocationTests(unittest.TestCase):
    def setUp(self):
        server._state.clear()
        server._state.update(server._empty_state())

    def test_extra_ids_do_not_collide_across_groups(self):
        server._state["extras"] = {"kitchen": [{"id": "kitchen-x1", "text": "a"}]}
        self.assertEqual(server._next_extra_id("patio"), "patio-x2")

    def test_extra_id_starts_at_one(self):
        self.assertEqual(server._next_extra_id("milo"), "milo-x1")

    def test_section_ids_increment(self):
        server._state["sections"] = [{"id": "custom-3", "section": "House", "title": "X"}]
        self.assertEqual(server._next_section_id(), "custom-4")

    def test_extra_id_namespace_avoids_baked_in_ids(self):
        # "kitchen-2" is a baked-in item; generated ids must never look like it.
        self.assertNotIn(server._next_extra_id("kitchen"), set(server.all_item_ids()))


class EntryOrderingTests(unittest.TestCase):
    GROUP = {"id": "g", "section": "House", "title": "G", "items": ["one", "two"]}

    def setUp(self):
        server._state.clear()
        server._state.update(server._empty_state())

    def entries(self):
        return server._ordered_entries_locked(self.GROUP)

    def test_default_is_entry_order(self):
        server._state["extras"]["g"] = [{"id": "g-x1", "text": "three"}]
        self.assertEqual([e["id"] for e in self.entries()], ["g-0", "g-1", "g-x1"])

    def test_manual_order_wins(self):
        server._state["item_order"]["g"] = ["g-1", "g-0"]
        self.assertEqual([e["id"] for e in self.entries()], ["g-1", "g-0"])

    def test_items_missing_from_manual_order_fall_in_behind(self):
        server._state["extras"]["g"] = [{"id": "g-x1", "text": "three"}]
        server._state["item_order"]["g"] = ["g-x1"]
        self.assertEqual([e["id"] for e in self.entries()], ["g-x1", "g-0", "g-1"])

    def test_stale_ids_in_manual_order_are_dropped(self):
        server._state["item_order"]["g"] = ["g-9", "g-0", "g-1"]
        self.assertEqual([e["id"] for e in self.entries()], ["g-0", "g-1"])

    def test_overrides_replace_text(self):
        server._state["overrides"]["g-0"] = "edited"
        self.assertEqual(self.entries()[0]["text"], "edited")

    def test_completed_items_are_not_sunk_server_side(self):
        # Sinking is the UI's job; the stored order must stay put so that
        # unticking restores an item's place.
        server._state["checked"]["g-0"] = True
        self.assertEqual([e["id"] for e in self.entries()], ["g-0", "g-1"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        server._state.clear()
        server._state.update(server._empty_state())

    # -- helpers --------------------------------------------------------

    def raw(self, path, method="GET", body=None, name="cade", auth=True):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if auth:
            req.add_header("Authorization", basic(name))
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:  # close it, or unittest reports a ResourceWarning
                return e.code, e.headers, e.read()

    def call(self, path, method="GET", body=None, name="cade", auth=True):
        """(status, parsed-json-or-None). Error pages aren't always JSON —
        a 401 serves a short HTML hint — so parsing is best effort."""
        status, _, raw = self.raw(path, method, body, name, auth)
        try:
            return status, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return status, None

    # -- auth -----------------------------------------------------------

    def test_app_requires_a_name(self):
        self.assertEqual(self.call("/", auth=False)[0], 401)
        self.assertEqual(self.call("/", name="cade")[0], 200)

    def test_401_offers_basic_auth(self):
        status, hdrs, _ = self.raw("/", auth=False)
        self.assertEqual(status, 401)
        self.assertIn("Basic", hdrs.get("WWW-Authenticate", ""))

    def test_every_allowed_name_gets_in(self):
        for name in server.ALLOWED_NAMES:
            self.assertEqual(self.call("/api/checklist", name=name)[0], 200, name)

    def test_names_are_case_and_space_insensitive(self):
        for variant in ("cade", "CADE", "cAdE", "  cade  "):
            self.assertEqual(self.call("/api/checklist", name=variant)[0], 200, variant)

    def test_unknown_names_are_refused(self):
        for variant in ("dave", "", "cad", "cade1"):
            self.assertEqual(self.call("/api/checklist", name=variant)[0], 401, variant)

    def test_malformed_authorization_header_is_refused(self):
        url = f"http://127.0.0.1:{self.port}/api/checklist"
        for value in ("Basic !!!not-base64", "Bearer cade", "cade"):
            req = urllib.request.Request(url)
            req.add_header("Authorization", value)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=10)
            with cm.exception as err:
                self.assertEqual(err.code, 401, value)

    def test_api_writes_require_a_name(self):
        for path, body in (
            ("/api/toggle", {"id": "milo-0", "checked": True}),
            ("/api/add-item", {"group": "milo", "text": "x"}),
            ("/api/add-section", {"title": "x"}),
            ("/api/edit-item", {"id": "milo-0", "text": "x"}),
            ("/api/order-items", {"group": "milo", "order": []}),
            ("/api/reset", None),
        ):
            self.assertEqual(self.call(path, "POST", body, auth=False)[0], 401, path)
        self.assertEqual(server._state["checked"], {}, "unauthed write leaked through")

    # -- open endpoints -------------------------------------------------

    def test_healthz_needs_no_name(self):
        self.assertEqual(self.call("/healthz", auth=False), (200, {"ok": True}))

    def test_icons_need_no_name(self):
        for path, ctype in (
            ("/favicon.svg", "image/svg+xml"),
            ("/favicon-32.png", "image/png"),
            ("/apple-touch-icon.png", "image/png"),
            ("/apple-touch-icon-precomposed.png", "image/png"),
        ):
            status, hdrs, body = self.raw(path, auth=False)
            self.assertEqual(status, 200, path)
            self.assertEqual(hdrs.get("Content-Type"), ctype, path)
            self.assertTrue(body, path)

    def test_apple_touch_icon_is_a_180px_png(self):
        _, _, body = self.raw("/apple-touch-icon.png", auth=False)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(body[16:20], "big")
        height = int.from_bytes(body[20:24], "big")
        self.assertEqual((width, height), (180, 180))

    def test_unknown_path_is_404(self):
        self.assertEqual(self.raw("/nope")[0], 404)

    # -- checklist ------------------------------------------------------

    def test_checklist_shape(self):
        status, data = self.call("/api/checklist")
        self.assertEqual(status, 200)
        self.assertIn("groups", data)
        self.assertIn("state", data)
        group = data["groups"][0]
        self.assertEqual(sorted(group), ["entries", "id", "section", "title"])
        self.assertIn("text", group["entries"][0])

    def test_toggle_round_trip(self):
        status, data = self.call("/api/toggle", "POST", {"id": "milo-0", "checked": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["state"]["milo-0"])
        self.assertEqual(
            self.call("/api/toggle", "POST", {"id": "nope-1", "checked": True})[0], 400
        )

    def test_add_item_then_toggle_and_edit_it(self):
        status, data = self.call("/api/add-item", "POST",
                                 {"group": "milo", "text": "Nail trim"})
        self.assertEqual(status, 200)
        milo = [g for g in data["groups"] if g["id"] == "milo"][0]
        new_id = milo["entries"][-1]["id"]
        self.assertEqual(self.call("/api/toggle", "POST",
                                   {"id": new_id, "checked": True})[0], 200)
        status, data = self.call("/api/edit-item", "POST",
                                 {"id": new_id, "text": "Nail trim + ears"})
        self.assertEqual(status, 200)
        milo = [g for g in data["groups"] if g["id"] == "milo"][0]
        self.assertEqual(milo["entries"][-1]["text"], "Nail trim + ears")

    def test_add_item_validation(self):
        self.assertEqual(self.call("/api/add-item", "POST",
                                   {"group": "nope", "text": "x"})[0], 400)
        self.assertEqual(self.call("/api/add-item", "POST",
                                   {"group": "milo", "text": "   "})[0], 400)

    def test_item_text_is_capped(self):
        self.call("/api/add-item", "POST", {"group": "milo", "text": "z" * 500})
        text = server._state["extras"]["milo"][0]["text"]
        self.assertEqual(len(text), server.MAX_ITEM_TEXT)

    def test_edit_applies_to_baked_in_items_without_a_rebuild(self):
        status, data = self.call("/api/edit-item", "POST",
                                 {"id": "milo-0", "text": "Brush the dog"})
        self.assertEqual(status, 200)
        milo = [g for g in data["groups"] if g["id"] == "milo"][0]
        self.assertEqual(milo["entries"][0]["text"], "Brush the dog")
        self.assertEqual(server.GROUPS[-1]["items"][0], "Brush/bath/brush again",
                         "checklist_items.py must not be mutated")

    def test_edit_validation(self):
        self.assertEqual(self.call("/api/edit-item", "POST",
                                   {"id": "milo-0", "text": " "})[0], 400)
        self.assertEqual(self.call("/api/edit-item", "POST",
                                   {"id": "ghost-1", "text": "x"})[0], 400)

    # -- ordering -------------------------------------------------------

    def test_group_order_round_trip(self):
        order = [g["id"] for g in server.GROUPS][::-1]
        self.assertEqual(self.call("/api/order", "POST", {"order": order})[0], 200)
        _, data = self.call("/api/checklist")
        self.assertEqual([g["id"] for g in data["groups"]], order)

    def test_group_order_validation(self):
        for bad in ({"order": "nope"}, {"order": ["ghost"]},
                    {"order": ["milo", "milo"]}):
            self.assertEqual(self.call("/api/order", "POST", bad)[0], 400, bad)

    def test_item_order_round_trip(self):
        self.assertEqual(self.call("/api/order-items", "POST",
                                   {"group": "milo", "order": ["milo-1", "milo-0"]})[0],
                         200)
        _, data = self.call("/api/checklist")
        milo = [g for g in data["groups"] if g["id"] == "milo"][0]
        self.assertEqual([e["id"] for e in milo["entries"]], ["milo-1", "milo-0"])

    def test_item_order_rejects_ids_from_another_section(self):
        self.assertEqual(self.call("/api/order-items", "POST",
                                   {"group": "milo", "order": ["patio-0"]})[0], 400)
        self.assertEqual(self.call("/api/order-items", "POST",
                                   {"group": "ghost", "order": []})[0], 400)
        self.assertEqual(self.call("/api/order-items", "POST",
                                   {"group": "milo", "order": ["milo-0", "milo-0"]})[0],
                         400)

    # -- sections -------------------------------------------------------

    def test_add_section_lands_in_its_area(self):
        status, data = self.call("/api/add-section", "POST",
                                 {"title": "Garage", "area": "Outside"})
        self.assertEqual(status, 200)
        ids = [g["id"] for g in data["groups"]]
        titles = {g["id"]: g["title"] for g in data["groups"]}
        new_id = next(i for i in ids if i.startswith("custom-"))
        self.assertEqual(titles[new_id], "Garage")
        # It should sit inside the Outside run, not after the House ones.
        areas = [g["section"] for g in data["groups"]]
        self.assertEqual(areas, sorted(areas, key=lambda a: 0 if a == "Outside" else 1))

    def test_added_section_accepts_items(self):
        _, data = self.call("/api/add-section", "POST", {"title": "Garage"})
        new_id = next(g["id"] for g in data["groups"] if g["id"].startswith("custom-"))
        self.assertEqual(self.call("/api/add-item", "POST",
                                   {"group": new_id, "text": "Sweep"})[0], 200)

    def test_add_section_validation(self):
        self.assertEqual(self.call("/api/add-section", "POST", {"title": "  "})[0], 400)

    def test_reset_clears_ticks_but_keeps_everything_else(self):
        self.call("/api/toggle", "POST", {"id": "milo-0", "checked": True})
        self.call("/api/add-item", "POST", {"group": "milo", "text": "Keep me"})
        self.call("/api/add-section", "POST", {"title": "Garage"})
        status, data = self.call("/api/reset", "POST")
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], {})
        self.assertEqual(len(server._state["sections"]), 1)
        self.assertEqual(len(server._state["extras"]["milo"]), 1)

    # -- live updates ---------------------------------------------------

    def test_event_stream_pushes_on_write(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/events")
        req.add_header("Authorization", basic("cade"))
        stream = urllib.request.urlopen(req, timeout=15)
        self.assertEqual(stream.headers.get("Content-Type"),
                         "text/event-stream; charset=utf-8")

        def next_frame():
            while True:
                line = stream.readline()
                if not line:
                    self.fail("event stream closed early")
                if line.startswith(b"data: "):
                    return json.loads(line[6:])

        try:
            self.assertIn("groups", next_frame())  # snapshot on connect
            self.call("/api/toggle", "POST", {"id": "milo-0", "checked": True})
            self.assertTrue(next_frame()["state"]["milo-0"])
        finally:
            stream.close()

    def test_event_stream_requires_a_name(self):
        self.assertEqual(self.raw("/api/events", auth=False)[0], 401)


if __name__ == "__main__":
    unittest.main()
