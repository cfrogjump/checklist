# Party Checklist microservice

A tiny, dependency-free shared checklist. Runs as one Docker container,
persists state to a JSON file on a mounted volume, and serves a web UI +
small JSON API. Everyone who opens the URL sees and checks off the same
list (state syncs via a 4-second poll).

Sections can be reordered by dragging the ⠿ handle next to each title
(works with mouse and touch). The order is saved server-side, so everyone
sees the same arrangement. The three "Floor Side Quest" groups move as a
single box.

Each section also has a "+" button on the right of its title (one per
sub-list inside the quest box) that opens an inline input for adding a
task. Added tasks are saved server-side in `state.json` — not in
`checklist_items.py` — so they survive restarts and show up for everyone,
no rebuild needed.

No external packages are installed — the server uses only the Python
standard library, so the image builds fully offline once the base
`python:3.12-alpine` layer is pulled.

## Run it

```bash
docker compose up -d --build
```

Then open http://localhost:8080

Data persists in `./data/state.json` on the host, so `docker compose down`
/ `up` again won't lose anyone's progress. Delete that file (or click
"Reset all" in the UI) to start over.

### Without compose

```bash
docker build -t party-checklist .
docker run -d --name party-checklist \
  -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  party-checklist
```

## Optional password

By default anyone with the URL can view and check items — fine if you're
only sharing the link with a few people via a Cloudflare Tunnel. If you
want a login prompt too, set both env vars (in `docker-compose.yml` or
`docker run -e ...`):

```
BASIC_AUTH_USER=party
BASIC_AUTH_PASS=something-not-guessable
```

## Exposing it via Cloudflare Tunnel

You don't need to open any ports on your router. From the machine running
the container:

```bash
# one-time: install cloudflared, then either
cloudflared tunnel login

# quick, no-account "Try" tunnel (random *.trycloudflare.com URL, resets
# if it restarts — good for a one-off party):
cloudflared tunnel --url http://localhost:8080

# or a persistent named tunnel on your own domain:
cloudflared tunnel create party-checklist
cloudflared tunnel route dns party-checklist checklist.yourdomain.com
cloudflared tunnel run --url http://localhost:8080 party-checklist
```

Share whichever URL cloudflared gives you (or your own subdomain) with
the people you want checking things off. Nothing else needs to be public.

## Editing the checklist itself

All the checklist content lives in `checklist_items.py` as a plain Python
list — edit the `GROUPS` list, rebuild (`docker compose up -d --build`),
and the new items show up. State is keyed by `<group_id>-<index>`, so if
you reorder items within a group after people have already started
checking things off, existing checks may land on the wrong item — safest
to add new items at the end of a group, or do a reset after editing.

## API (in case you want to script against it)

- `GET /api/checklist` → `{ groups: [...], state: { "<id>": true/false } }`
  (groups come back in the saved display order)
- `POST /api/toggle` body `{ "id": "kitchen-2", "checked": true }` → returns updated state
- `POST /api/order` body `{ "order": ["patio", "yardwork", ...] }` → saves the
  group display order (ids must all be valid group ids, no duplicates)
- `POST /api/add-item` body `{ "group": "kitchen", "text": "Buy ice" }` → adds
  a task to that section (text capped at 200 chars, 100 added tasks per
  section); returns the same payload as `/api/checklist`
- `POST /api/reset` → clears all checked state (keeps the saved order and
  any added tasks)
- `GET /healthz` → `{ "ok": true }`

`state.json` is stored as `{ "checked": {...}, "order": [...], "extras": {...} }`.
Old-format files (a flat map of item ids) are migrated automatically on
startup. Added tasks get ids like `kitchen-x3` ("x" keeps them clear of the
index-based ids of the baked-in items).
