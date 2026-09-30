# snapback

Self-service browsing of ZFS snapshots of an SMB share, as a TrueNAS SCALE app. Users move through
their share by folder and by time, and see what changed or was deleted. Plan and design:
[docs/PLAN.md](docs/PLAN.md), [docs/UI-BRIEF.md](docs/UI-BRIEF.md), [docs/DESIGN-HANDOFF.md](docs/DESIGN-HANDOFF.md).

## Status

| Phase | |
|---|---|
| 0: automount spike | Written, not yet run on TrueNAS: [spike/README.md](spike/README.md). **Gate for everything else.** |
| 1–2: read-only browser, config, multi-root | Done, tested against a simulated `.zfs` tree |
| 3–7: sign-in, privilege drop, downloads, AD, packaging | Not started |

Without sign-in, the app is for the administrator only. [deploy/compose.yaml](deploy/compose.yaml)
binds it to the NAS's localhost; reach it through an SSH tunnel, then open http://localhost:8080.
It runs as root in the container, so it can read everything under the mounted datasets.

```bash
ssh -N -L 8080:127.0.0.1:8080 root@NAS
```

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
| `TZ` | Timezone the timestamps in snapshot names are written in. Defaults to the container's local time. |
| `SNAPBACK_CONFIG` | Config file path. Default `/config/snapback.yaml`. |

Config file: see [deploy/snapback.example.yaml](deploy/snapback.example.yaml). Each root is a share
folder; the dataset mountpoint is detected by walking up to the nearest `.zfs/snapshot`. Mount each
distinct dataset mountpoint read-only at the same path inside the container.

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
cd backend && SNAPSHOT_PREFIXES=auto- SNAPBACK_CONFIG=../dev/demo/snapback.yaml .venv/bin/python -m snapback
```

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server on :5173 proxies `/api` to :8080. Tests:

```bash
cd backend && .venv/bin/pytest
```

## Known limits

- A child dataset mounted inside a share shows as an empty folder in the parent's snapshots.
- Hidden entries (names starting with `.`) are never listed or reachable, as in Finder.
- Symlinks are not listed or followed.
- Snapshots taken at the same minute by different tasks show as one point in time.
