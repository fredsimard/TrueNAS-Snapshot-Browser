# snapback

Self-service browsing of ZFS snapshots of an SMB share, as a TrueNAS SCALE app. Users move through
their share by folder and by time, and see what changed or was deleted. Plan and design:
[docs/PLAN.md](docs/PLAN.md), [docs/UI-BRIEF.md](docs/UI-BRIEF.md), [docs/DESIGN-HANDOFF.md](docs/DESIGN-HANDOFF.md).

## Status

| Phase | |
|---|---|
| 0: automount spike | Passed on TrueNAS: [spike/README.md](spike/README.md) |
| 1–2: read-only browser, config, multi-root | Done, checked on real snapshots |
| 3: sign-in | Done, checked on TrueNAS |
| 4: per-user permissions | In progress: users see only the shares Samba lets them connect to, and `ALLOWED_USERS` limits who can sign in. Folder permissions inside a share aren't applied yet. |
| 5–7: downloads, AD, packaging | Not started |

**Not ready for staff yet.** Inside a share, every signed-in user sees everything under the mounted
datasets, because the app runs as root in the container; Phase 4's privilege-dropping workers fix that.
The app also serves plain HTTP, so passwords cross the LAN unencrypted.

## Layout

```
backend/    FastAPI app (Python ≥ 3.10), tests, demo data generator
frontend/   React + Vite + Tailwind v4 + shadcn/ui (new-york, zinc)
spike/      Phase 0 check
deploy/     Example TrueNAS compose file and server config
docs/       Plan and design
```

## Configuration

Environment (set at app creation):

| Variable | |
|---|---|
| `SNAPSHOT_PREFIXES` | **Required.** Comma-separated snapshot name prefixes users may see, e.g. `auto-,daily-`. The app refuses to start without it. |
| `SMB_HOST` | **Required.** The NAS's network address. Sign-in checks passwords by opening an SMB session to it. `127.0.0.1` is refused: in the container that's the container itself. |
| `ALLOWED_USERS` | **Required.** `all`, or comma-separated usernames allowed to use the app (case doesn't matter). Others get "This account can't use this app" after a correct password. |
| `TZ` | Timezone the timestamps in snapshot names are written in. Defaults to the container's local time. |
| `SNAPBACK_CONFIG` | Config file path. Default `/config/snapback.yaml`. |

Config file: see [deploy/snapback.example.yaml](deploy/snapback.example.yaml). Each root is a share
folder; the dataset mountpoint is detected by walking up to the nearest `.zfs/snapshot`. Mount each
distinct dataset mountpoint read-only at the same path inside the container. At sign-in, the app
connects to each root's SMB share with the user's own credentials and shows only the roots Samba
lets that account use.

Point in time for each snapshot comes from the timestamp in its name (TrueNAS `auto-%Y-%m-%d_%H-%M`,
zfs-auto-snapshot and sanoid formats). Names without one fall back to the directory's mtime.

## Development

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

```bash
backend/.venv/bin/python backend/scripts/make_demo.py dev/demo
```

```bash
cd backend && SNAPSHOT_PREFIXES=auto- SNAPBACK_CONFIG=../dev/demo/snapback.yaml .venv/bin/python scripts/dev_server.py
```

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server on :5173 proxies `/api` to :8080. Sign in with `demo` / `demo`; `dev_server.py` replaces the SMB check. Tests:

```bash
cd backend && .venv/bin/pytest
```

## Known limits

- A child dataset mounted inside a share shows as an empty folder in the parent's snapshots.
- Hidden entries (names starting with `.`) are never listed or reachable, as in Finder.
- Symlinks are not listed or followed.
- Snapshots taken at the same minute by different tasks show as one point in time.
- Sessions live in memory: restarting the app signs everyone out.
