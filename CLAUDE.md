# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

snapback: a TrueNAS SCALE custom app (Docker) that lets users browse ZFS snapshots of an SMB share by folder and by time, and see what changed or was deleted. `docs/PLAN.md` is the plan (phases, constraints, risks); `docs/UI-BRIEF.md` and `docs/DESIGN-HANDOFF.md` are the UI spec (the handoff wins on status colours; copy and layout come from there). Phases 0–2 are done and checked on real TrueNAS snapshots (`spike/README.md` has the results). Phase 3 (sign-in) is built.

## Commands

Backend (Python ≥ 3.10, venv in `backend/.venv`):

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd backend && .venv/bin/pytest                                   # all tests
cd backend && .venv/bin/pytest tests/test_browse.py::test_history_versions   # one test
backend/.venv/bin/python backend/scripts/make_demo.py dev/demo    # simulated dataset + config
cd backend && SNAPSHOT_PREFIXES=auto- SNAPBACK_CONFIG=../dev/demo/snapback.yaml .venv/bin/python scripts/dev_server.py   # sign in with demo / demo
```

Frontend (`frontend/`, npm):

```bash
npm run dev      # Vite on :5173, proxies /api to :8080
npm run build    # tsc -b && vite build
npm run lint     # oxlint
npx tsc -b       # type-check only
```

`.claude/launch.json` defines `backend` and `frontend` preview servers for the demo data. The backend serves the built SPA when `SNAPBACK_STATIC_DIR` points at `frontend/dist` (the Dockerfile does this).

## Architecture

**No zfs CLI.** Snapshots are read as plain directories under `<dataset_mountpoint>/.zfs/snapshot/<name>/`. Tests and the demo generator simulate this with ordinary folders; identical file versions are hard links so they share inode and ctime, as unchanged files do across real snapshots.

**Path model** (`backend/snapback/paths.py`). A configured `share_root` is the user-visible root. `dataset_mountpoint` is detected by walking up to the nearest `.zfs/snapshot`; `relative_prefix` is the share root relative to it. `Root.base(snapshot_name | None)` gives the share root live or inside a snapshot. `parse_rel()` is the only entry point for client paths: it rejects any segment starting with `.` (covers `..`, `.zfs` and hidden files). `opened_dir()` then walks segments with `O_NOFOLLOW` via `dir_fd`, so symlinks are never followed. `Missing`/`Denied` exceptions map to folder states, not errors.

**Snapshot filter** (`snapshots.py`). `SNAPSHOT_PREFIXES` env var is mandatory; `config.load_config` raises `ConfigError` before anything else and `__main__` exits 2. Only prefixed snapshots are enumerated; clients see them only as opaque ids (`sha256(name)[:16]`), never names. Time comes from the timestamp in the name, read in `TZ` (fallback: directory mtime). Same-minute snapshots are deduped. Lists are cached ~30 s per dataset.

**Sign-in** (`auth.py`). A password is valid if the SMB server at `SMB_HOST` accepts it, so local and AD accounts need no configuration here. Signing is required, which makes smbprotocol refuse guest sessions. `SMB_HOST` must be the NAS's network address. Sessions are in memory behind an HttpOnly, SameSite=Strict cookie, with a sliding idle timeout. Failures are throttled per username and per address, below typical AD lockout thresholds. The same SMB session then connects to each root's `smb_share` and opens its top folder for listing, and the session keeps only the roots where both succeed (Samba accepts the connection alone for an account that can't read the folder, then refuses every request, even the disconnect, so the SMB connection is dropped without logging off); other roots answer 404 `unknown_root`, like roots that don't exist. `ALLOWED_USERS` (required: `all` or usernames, casefolded) is checked after the password, so the answer never reveals who is listed, and shares aren't tried for unlisted accounts. `create_app(verify=…)` injects a fake `(username, password, shares) -> allowed shares | None`; there's deliberately no flag that turns sign-in off, so tests and `scripts/dev_server.py` use that parameter. Inside a root, everyone still sees everything the container can read until Phase 4's privilege-dropping workers.

**Timeline and search** (`browse.py`). A `Timeline` is filtered snapshots oldest→newest, plus Now at index `len(snapshots)`. Every first access to a snapshot triggers a ZFS automount, so nothing walks all snapshots:
- `last_seen` (deleted rows) bisects between the selected point and Now, sharing folder listings across names.
- `nearest` (folder didn't exist / is gone) gallops then bisects.
- `file_history` recursively splits the range and stops when both ends have equal `Entry.identity` (ino, ctime, size, mtime) or, when absent, equal parent-folder identity.
Change status compares `Entry.content` (kind, size, mtime) with the live entry.

**API** (`api.py`): `/api/info` and `/api/login` are public (`info` gives only the server name until signed in); `/api/logout`; `/api/roots/{id}/points`, `/folder?path=&at=` and `/history?path=` need a session. Errors are `{"error": code}`; validation errors are replaced with `bad_request` because FastAPI's default echoes submitted values, including passwords. Responses never contain absolute paths, snapshot names or OS error text; `tests/test_api.py` asserts this.

**Frontend** (`frontend/src`). React 19, Tailwind v4, shadcn/ui (`new-york`, `zinc`) copied into `components/ui/` (ours to edit). Theme is System/Light/Dark from `lib/theme.ts`, which sets `.dark` on `<html>`; an inline script in `index.html` does the same before first paint, so keep the two in step. Status and timeline colours are theme tokens in `index.css`; never hardcode colours in components. No router library: `lib/router.ts` maps `/r/<root>/<folder…>?at=<point id>`; folder changes push history, time changes replace it. `lib/timeline.ts` holds the square-root time scale, point labels ("Yesterday, end of day" for midnight points) and date snapping. A 401 from any GET fires `SIGNED_OUT_EVENT`, and `App.tsx` shows the sign-in screen without changing the URL, so sign-in returns to the same place. `components/browse/browse-page.tsx` owns state and keeps the previous listing visible while the same folder loads at another time. ← → are handled on the Browse root element, not `window`.

## Gotchas

- `@tanstack/react-table` is pinned to v8: shadcn's Data Table recipe targets v8 and v9 changed the API.
- The shadcn CLI (v4.x) rewrote `@/lib/utils` imports to `"cn"` and installed an unrelated npm package named `cn`. After `npx shadcn add`, check imports and `package.json`, and pass `--overwrite` or it stops at a prompt. `shadcn init` hangs on a prompt; `components.json`, `lib/utils.ts` and `index.css` were written by hand.
- Container runs as root with read-only bind mounts until Phase 4 (privilege-dropping workers).
- `rslave` on the dataset bind mount is required: without it snapshots list as empty with no error. It needs "Install via YAML"; the Custom App wizard can't set it.
- The app serves plain HTTP, so passwords need a TLS reverse proxy in front before real use.
- This repo is meant to go public: keep real server, pool, share and client names and addresses out of it. `dev/` is gitignored and holds the real NAS config.
- Known limits: a child dataset inside a share appears empty in the parent's snapshots.
