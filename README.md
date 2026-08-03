# Party Checklist microservice

A tiny, dependency-free shared checklist. Runs as one Docker container,
persists state to a JSON file on a mounted volume, and serves a web UI +
small JSON API. Everyone who opens the URL sees and checks off the same
list, and changes are pushed to every open page over Server-Sent Events —
so a tick, a new task, or a reorder shows up for everyone in well under a
second, with no refresh. A 30-second poll runs alongside it as a safety
net in case something between you and the server won't pass a stream.

Sections can be reordered by dragging the ⠿ handle next to each title
(works with mouse and touch). The order is saved server-side, so everyone
sees the same arrangement. The three "Floor Side Quest" groups move as a
single box.

Individual tasks work the same way: drag the small grip at the left of a
row to reorder it **within its section**, and **press and hold** the task
text (mouse or finger, about half a second) to edit the wording inline.
Editing works on the built-in tasks too — it records an override rather
than changing `checklist_items.py`, so no rebuild is needed.

Tasks default to the order they were entered: built-in ones as listed in
`checklist_items.py`, then added ones oldest-first. Dragging overrides that
for the tasks you move. **Completed tasks drop to the bottom of their
section**, which happens at display time only — the saved order is left
alone, so unticking something puts it back where it was.

Each section also has a "+" button on the right of its title (one per
sub-list inside the quest box) that opens an inline input for adding a
task. A "+ New section" button at the bottom of the page creates a whole
new section: give it a name and pick which area heading it belongs under
(Outside / House), and it drops in alongside the other sections in that
area rather than at the very bottom. Drag it wherever you like from there.

Added tasks and sections are saved server-side in `state.json` — not in
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

## Add it to a phone home screen

On iPhone, open the checklist in Safari → Share → **Add to Home Screen**. It
gets a proper icon (a cream checkmark on the app's terracotta) labelled
"Checklist", not a screenshot of the page.

The icons are committed files — `apple-touch-icon.png` (180×180 for iOS),
`favicon.svg` and `favicon-32.png` (browser tabs). They're served without a
sign-in, because iOS fetches the home screen icon in contexts that don't
always carry saved credentials and a 401 there gets you a blank icon.

To recolour or redraw them, edit and run `tools/make_icons.py` (needs
`pip install Pillow` — only to regenerate; the server itself still has no
dependencies), then rebuild.

Tapping the home screen icon opens the checklist in a normal Safari view.
Adding `<meta name="apple-mobile-web-app-capable" content="yes">` to
`index.html` would make it launch fullscreen like a native app instead —
left off on purpose, because a standalone web app keeps its own credential
store and would likely re-prompt for your name on every cold launch.

## Signing in

Opening the checklist pops up your browser's login box. Type your **first
name** as the username and **leave the password blank** — that's the whole
login. Capitalization doesn't matter (`cade`, `Cade`, and `CADE` all work),
and surrounding spaces are trimmed.

The guest list is hardcoded as `ALLOWED_NAMES` near the top of
`server.py`:

> Cade · Cailin · Benton · Harley · Misty · Chelsea · Cami

To add or remove someone, edit that list and rebuild with
`docker compose up -d --build`. Browsers cache Basic Auth credentials for
the session, so a removed name stays signed in until they close the
browser.

`GET /healthz` is deliberately left open so container health checks and
uptime monitors don't need credentials — point them at that path, and use
GET rather than HEAD, which this server doesn't implement. Every other
route and API endpoint requires a name.

**This is a "keep strangers out" gate, not real security.** There is no
password, the names are guessable by anyone who knows the household, and
Basic Auth sends credentials on every request — safe enough over the
HTTPS a Cloudflare Tunnel gives you, but don't put anything sensitive on
this checklist. If you need the real thing, put Cloudflare Access in front
of the hostname.

## Exposing it via Cloudflare Tunnel (Ubuntu)

You don't need to open any ports on your router or touch `ufw` —
`cloudflared` only makes outbound connections. Run all of this on the
Ubuntu box that's running the container.

### 1. Install cloudflared

Cloudflare's apt repo (recommended — you get updates with normal
`apt upgrade`):

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

The `any` codename works on every Debian/Ubuntu release; Cloudflare also
publishes `focal`, `jammy`, `noble`, and `bookworm` if you'd rather pin to
your exact release.

Or grab the `.deb` directly (also the answer for a Raspberry Pi — check
with `dpkg --print-architecture` and swap `amd64` for `arm64`):

```bash
curl -fsSL -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb
```

Confirm with `cloudflared --version`.

### 2a. Quick tunnel — no Cloudflare account

```bash
cloudflared tunnel --url http://localhost:8080
```

Prints a random `https://<words>.trycloudflare.com` URL. It runs in the
foreground and the URL changes every restart, so run it inside `tmux` or
`screen` if you don't want it dying with your SSH session. Fine for a
one-off party.

### 2b. Named tunnel on your own domain — survives restarts

```bash
cloudflared tunnel login
cloudflared tunnel create party-checklist
cloudflared tunnel route dns party-checklist checklist.yourdomain.com
```

`create` prints a tunnel UUID and writes credentials to
`~/.cloudflared/<UUID>.json`. `route dns` adds the CNAME for you.

Then write `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /home/<USER>/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: checklist.yourdomain.com
    service: http://localhost:8080
  - service: http_status:404
```

Test it in the foreground with `cloudflared tunnel run party-checklist`,
then Ctrl-C once you've confirmed the hostname loads.

### 3. Keep it running at boot (systemd)

```bash
sudo cloudflared --config /home/$USER/.cloudflared/config.yml service install
```

Pass `--config` explicitly: under `sudo`, `$HOME` is `/root`, so
cloudflared won't find the config in your user directory on its own. Then:

```bash
sudo systemctl enable --now cloudflared && systemctl status cloudflared
```

Edited `config.yml`? `sudo systemctl restart cloudflared`. Logs live in
`journalctl -u cloudflared -f`.

Share the tunnel URL (or your own subdomain) with whoever's helping.
Nothing else on the box becomes public.

Whoever you share it with will hit the first-name login described under
[Signing in](#signing-in), so make sure they're on the `ALLOWED_NAMES`
list before you send the link.

## Editing the checklist itself

All the checklist content lives in `checklist_items.py` as a plain Python
list — edit the `GROUPS` list, rebuild (`docker compose up -d --build`),
and the new items show up. State is keyed by `<group_id>-<index>`, so if
you reorder items within a group after people have already started
checking things off, existing checks may land on the wrong item — safest
to add new items at the end of a group, or do a reset after editing.

## API (in case you want to script against it)

- `GET /api/checklist` → `{ groups: [...], state: { "<id>": true/false } }`.
  Groups come back in the saved display order, each with an `entries` array
  of `{ id, text }` already in stored item order and with any edits applied.
  (Sinking completed tasks is the UI's job, not this endpoint's.)
- `GET /api/events` → Server-Sent Events stream. Sends the full
  `/api/checklist` payload on connect and again after every write, plus a
  `: ping` comment every 25s so idle streams survive proxy timeouts
  (Cloudflare's is ~100s). Each open page holds one connection, and one
  server thread, for as long as its tab is open.
- `POST /api/toggle` body `{ "id": "kitchen-2", "checked": true }` → returns updated state
- `POST /api/order` body `{ "order": ["patio", "yardwork", ...] }` → saves the
  group display order (ids must all be valid group ids, no duplicates)
- `POST /api/add-item` body `{ "group": "kitchen", "text": "Buy ice" }` → adds
  a task to that section (text capped at 200 chars, 100 added tasks per
  section); returns the same payload as `/api/checklist`
- `POST /api/edit-item` body `{ "id": "kitchen-2", "text": "New wording" }` →
  changes a task's text (built-in or added, 200-char cap); returns the same
  payload as `/api/checklist`
- `POST /api/order-items` body `{ "group": "kitchen", "order": ["kitchen-2", ...] }`
  → saves the task order within one section. Ids must all belong to that
  section; a partial list is fine, anything left out falls in behind by
  entry order.
- `POST /api/add-section` body `{ "title": "Garage", "area": "Outside" }` →
  adds a section (title capped at 60 chars, 30 sections max; `area` defaults
  to the last built-in area); returns the same payload as `/api/checklist`
- `POST /api/reset` → clears all checked state (keeps the saved order and
  any added tasks and sections)
- `GET /healthz` → `{ "ok": true }`

`state.json` is stored as `{ "checked": {...}, "order": [...],
"extras": {...}, "sections": [...], "overrides": {...}, "item_order": {...} }`
— `overrides` holds edited task text keyed by item id, `item_order` the
per-section task order.
Older files — including the original flat map of item ids — are migrated
automatically on startup. Added tasks get ids like `kitchen-x3` and added
sections get `custom-1`; both namespaces are kept clear of the index-based
ids of the baked-in items, so nothing shifts under existing checks.

There's currently no way to **delete** an added task or section from the UI —
edit `state.json` (`extras` / `sections`) and restart if you need to undo one.
