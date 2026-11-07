# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

snapback: a TrueNAS SCALE custom app (Docker) that lets users browse ZFS snapshots of an SMB share by folder and by time, and see what changed or was deleted. `docs/PLAN.md` is the plan (phases, constraints, risks); `docs/UI-BRIEF.md` and `docs/DESIGN-HANDOFF.md` are the UI spec (the handoff wins on status colours; copy and layout come from there). Phases 0–3 are done and checked on real TrueNAS snapshots (`spike/README.md` has the results). Phase 4 (per-user permissions) is done and checked on the NAS, except denial on a folder inside a share, which was checked only in a Linux container.

## Commands

Backend (Python ≥ 3.10, venv in `backend/.venv`):

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd backend && .venv/bin/pytest                                   # all tests
cd backend && .venv/bin/pytest tests/test_browse.py::test_history_versions   # one test
backend/.venv/bin/python backend/scripts/make_demo.py dev/demo    # simulated dataset + config
cd backend && set -a && . ../dev/demo/snapback.env && set +a && .venv/bin/python scripts/dev_server.py   # sign in with demo / demo
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

**Configuration** (`config.py`). Environment variables only, no config file: `SERVER_NAME`, `SHARES`, `SNAPSHOT_PREFIXES`, `SMB_HOST`, `ALLOWED_USERS` are required; `TZ`, `MAX_DOWNLOAD_GB`, `MAX_SNAPSHOTS_LISTED` optional. `SHARES` is one `SMB share name = /path` per line (comma-separated if it's one line); the share name is the label and the SMB share sign-in checks, and the root id is its slug (`Video Archive` → `video-archive`, made unique). The app listens on 8080 (and 8443 for HTTPS) inside the container. `deploy/compose.yaml` is the YAML users paste into TrueNAS; `.github/workflows/image.yml` tests and publishes `ghcr.io/<owner>/snapshots-browser` (`:edge` from `master`, `:X.Y.Z`/`:latest` from `vX.Y.Z` tags).

**Snapshot filter** (`snapshots.py`). `SNAPSHOT_PREFIXES` env var is mandatory; `config.load_config` raises `ConfigError` before anything else and `__main__` exits 2. Only prefixed snapshots are enumerated; clients see them only as opaque ids (`sha256(name)[:16]`), never names. Time comes from the timestamp in the name, read in `TZ` (fallback: directory mtime). Same-minute snapshots are deduped. Lists are cached ~30 s per dataset.

**Sign-in** (`auth.py`). A password is valid if the SMB server at `SMB_HOST` accepts it, so local and AD accounts need no configuration here. Signing is required, which makes smbprotocol refuse guest sessions. `SMB_HOST` must be the NAS's network address. Sessions are in memory behind an HttpOnly, SameSite=Strict cookie, with a sliding idle timeout. An account keeps at most 4 (`MAX_SESSIONS_PER_USER`): signing in again ends its least recently used one, and expired sessions are ended before a new worker is started. Failures are throttled per account (case, `DOMAIN\user` and `user@realm` count as one) and per address, below typical AD lockout thresholds; an attempt is counted when it starts and given back unless the password is refused, so concurrent guesses can't pass the limit. The same SMB session then connects to each root's `smb_share` and opens its top folder for listing, and the session keeps only the roots where both succeed (Samba accepts the connection alone for an account that can't read the folder, then refuses every request, even the disconnect, so the SMB connection is dropped without logging off); other roots answer 404 `unknown_root`, like roots that don't exist. `ALLOWED_USERS` (required: `all` or usernames, casefolded) is checked after the password, so the answer never reveals who is listed, and shares aren't tried for unlisted accounts. `create_app(verify=…)` injects a fake `(username, password, shares) -> allowed shares | None`; there's deliberately no flag that turns sign-in off, so tests and `scripts/dev_server.py` use that parameter.

**Workers** (`workers.py`, `identity.py`, `ops.py`). `python -m snapback` must start as root and forks into a supervisor (root: reads accounts, forks workers, never parses HTTP or reads a share), a web process (`nobody`: HTTP, sign-in, sessions) and per-session workers. After the password and `ALLOWED_USERS`, sign-in asks the supervisor for a worker by username; it looks the account up in the host's `/etc/passwd` and `/etc/group` (mounted at `/host/etc/…`; exact name, else one case-insensitive match; never uid or gid 0), forks, closes every inherited fd but the worker's socket, and drops with setgroups/setgid/setuid, then checks `setuid(0)` fails. No account → 403 `not_allowed`. The web process gets the worker's socket over SCM_RIGHTS; each session keeps up to 3 workers, closed when it ends. Every file access is an `ops.py` request (`points`/`folder`/`history`, JSON with a 4-byte length) answered in the API's response shape, so the web process never reads a share. TrueNAS replaces the account files on any change and a single-file bind mount keeps the old one, so `HostAccounts` holds them open and the supervisor exits with code 3 once either has no links left; the container's restart policy brings it back. `create_app(backend=…)` is required: tests and `dev_server.py` pass `InProcessBackend` (no privilege drop), `__main__` the supervised one. The drop itself needs root, so its test only runs as root.

**Downloads** (`downloads.py` in the worker, `transfer.py` in the web process). `POST /api/roots/{id}/downloads {path, at, names}` asks the worker for a plan: one file → `kind: file`; otherwise a zip, walked with O_NOFOLLOW, hidden entries, symlinks and unreadable folders left out, stopping early with `too_large` past `MAX_DOWNLOAD_GB` or 100,000 entries. It returns an id, the name (`Name (YYYY-MM-DD HH.MM).ext` from the version's mtime; zips use the point's time) and sizes. `GET /api/downloads/{id}` (once) asks the worker again: it opens the file, or starts a thread writing a stored zip (Python `zipfile`, data descriptors) into a pipe, and passes that fd to the web process over SCM_RIGHTS; the web process streams it. Progress (`/progress`) and cancel (`DELETE`) are per session. A cancelled download, a zip without its end record or a file that shrank raises `DownloadAborted` mid-response, so the connection drops and the browser marks it failed; `ZipFile` would write the end record when garbage-collected, so a failed zip clears `zf.fp`. Security headers (a Content-Security-Policy with no inline scripts, `X-Frame-Options`, `Permissions-Policy` and more) and the request-body limit (`BodyLimit`: 16 KiB for `/api/login`, 8 MiB elsewhere, by declared Content-Length; chunked bodies get 411) are plain ASGI middleware, not `@app.middleware`, so nothing sits between a stream and the connection. Each download ends with one `snapback.audit` log line (who, absolute path, the names that were found, snapshot name, bytes); paths and names are logged with `repr`, so a name can't end the line and forge another. The page starts a download by navigating to it (Safari never starts one loaded in a hidden iframe); errors there are 204 No Content so the page stays, and the page polls progress for its toast.

**TLS** (`tls.py`). HTTP is always served on the configured port (8080). If `/host/tls/tls.crt` and `tls.key` are mounted (TrueNAS writes its certificates to `/etc/certificates/<name>.crt`/`.key`, key root-only), HTTPS is served on 8443 too: `HostTLS` builds the `SSLContext` as root before the fork, and the web process runs two uvicorn servers on one app (`serve_all`, so sessions are shared), the HTTPS one through `ssl_context_factory` (uvicorn ≥ 0.54). Only one of the two files is a `ConfigError`. The files are held open and the supervisor exits with code 3 when either is replaced or rewritten (renewal). The session cookie is `Secure` when signing in over HTTPS, so it never travels over HTTP.

**Timeline and search** (`browse.py`). A `Timeline` is filtered snapshots oldest→newest, plus Now at index `len(snapshots)`. Every first access to a snapshot triggers a ZFS automount, so nothing walks all snapshots:
- `last_seen` (deleted rows) bisects between the selected point and Now, sharing folder listings across names.
- `nearest` (folder didn't exist / is gone) gallops then bisects.
- `file_history` recursively splits the range and stops when both ends have equal `Entry.identity` (ino, ctime, size, mtime) or, when absent, equal parent-folder identity.
Change status compares `Entry.content` (kind, size, mtime) with the live entry.

**API** (`api.py`): `/api/info` and `/api/login` are public (`info` gives only the server name until signed in); `/api/logout`; `/api/roots/{id}/points`, `/folder?path=&at=` and `/history?path=` need a session. Errors are `{"error": code}`; validation errors are replaced with `bad_request` because FastAPI's default echoes submitted values, including passwords. Responses never contain absolute paths, snapshot names or OS error text; `tests/test_api.py` asserts this.

**Frontend** (`frontend/src`). React 19, Tailwind v4, shadcn/ui (`new-york`, `zinc`) copied into `components/ui/` (ours to edit). Theme is System/Light/Dark from `lib/theme.ts`, which sets `.dark` on `<html>`; `public/theme-init.js` (a file, loaded by `index.html`) does the same before first paint, so keep the two in step. Status and timeline colours are theme tokens in `index.css`; never hardcode colours in components. No router library: `lib/router.ts` maps `/r/<root>/<folder…>?at=<point id>`; folder changes push history, time changes replace it. `lib/timeline.ts` holds the square-root time scale, point labels ("Yesterday, end of day" for midnight points) and date snapping. A 401 from any GET fires `SIGNED_OUT_EVENT`, and `App.tsx` shows the sign-in screen without changing the URL, so sign-in returns to the same place. `components/browse/browse-page.tsx` owns state and keeps the previous listing visible while the same folder loads at another time. ← → are handled on the Browse root element, not `window`.

## Gotchas

- `@tanstack/react-table` is pinned to v8: shadcn's Data Table recipe targets v8 and v9 changed the API.
- The shadcn CLI (v4.x) rewrote `@/lib/utils` imports to `"cn"` and installed an unrelated npm package named `cn`. After `npx shadcn add`, check imports and `package.json`, and pass `--overwrite` or it stops at a prompt. `shadcn init` hangs on a prompt; `components.json`, `lib/utils.ts` and `index.css` were written by hand.
- The container must start as root: the supervisor needs CAP_SETUID/CAP_SETGID to fork workers as each user. Docker's default capabilities are enough.
- `rslave` on the dataset bind mount is required: without it snapshots list as empty with no error. It needs "Install via YAML"; the Custom App wizard can't set it.
- No HSTS header on purpose: browsers apply it per host, not per port, so it would turn `http://nas:18080` into `https://nas:18080` and break the HTTP port that stays on by design.
- Over HTTP (always on, alongside HTTPS when a certificate is mounted) passwords cross the network unencrypted.
- This repo is meant to go public: keep real server, pool, share and client names and addresses out of it. `dev/` is gitignored and holds the real NAS config.
- Known limits: a child dataset inside a share appears empty in the parent's snapshots.
