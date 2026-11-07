# snapshots-browser

Self-service browsing of ZFS snapshots of an SMB share, as a TrueNAS SCALE app. Users move through
their share by folder and by time, and see what changed or was deleted. Plan and design:
[docs/PLAN.md](docs/PLAN.md), [docs/UI-BRIEF.md](docs/UI-BRIEF.md), [docs/DESIGN-HANDOFF.md](docs/DESIGN-HANDOFF.md).

## Status

| Phase | |
|---|---|
| 0: automount spike | Passed on TrueNAS: [spike/README.md](spike/README.md) |
| 1–2: read-only browser, config, multi-root | Done, checked on real snapshots |
| 3: sign-in | Done, checked on TrueNAS |
| 4: per-user permissions | Done, checked on TrueNAS: users see only the shares Samba lets them connect to, `ALLOWED_USERS` limits who can sign in, and every file access runs as the signed-in account, so folder permissions apply. |
| 5: downloads | Done, checked on TrueNAS: files, folders and selections (as a zip made while it's sent), a size limit for zips, progress and cancel, an audit line per download in the app's log. HTTPS with the NAS's own certificate. |
| 6: Active Directory | Not started ([#1](https://github.com/fredsimard/TrueNAS-Snapshot-Browser/issues/1)) |
| 7: packaging | In progress: settings in environment variables, image on GHCR, install via YAML |

Snapshots keep the permissions they had when they were taken: someone who has since lost access to a
folder can still open its older copies, and the reverse.

## Layout

```
backend/    FastAPI app (Python ≥ 3.10), tests, demo data generator
frontend/   React + Vite + Tailwind v4 + shadcn/ui (new-york, zinc)
spike/      Checks run on a real NAS before building on them
deploy/     The YAML to paste into TrueNAS
docs/       Plan and design
.github/    Workflow: tests, then the image on ghcr.io
```

## Install on TrueNAS SCALE

1. Apps → Discover Apps → ⋮ → **Install via YAML**. (The Custom App wizard can't be used: it can't
   set the mount propagation snapshots need.)
2. Name the app, and paste [deploy/compose.yaml](deploy/compose.yaml).
3. Change the values marked `←`: the settings under `environment`, one volume per dataset that holds a
   share, and the certificate's file names (or remove those two volumes for HTTP only).
4. Save. The app is then at `http://<nas>:18080/`, and at `https://<nas>:18443/` with a certificate.

If a setting is missing or wrong, the app stops at once and its log (Apps → the app → Logs) says
which one.

## Configuration

All settings are environment variables, set in the app's YAML.

| Variable | |
|---|---|
| `SERVER_NAME` | **Required.** The name users know the server by, shown at the top of the app. |
| `SHARES` | **Required.** The shares users may browse, one per line (or comma-separated): the SMB share name users connect to, `=`, and the share's path on the NAS, e.g. `Projects = /mnt/tank/studio/Projects`. The share name is what users see, and sign-in shows a share only if Samba lets that account use it. |
| `SNAPSHOT_PREFIXES` | **Required.** Comma-separated snapshot name prefixes users may see, e.g. `auto-,daily-`. The app refuses to start without it. |
| `SMB_HOST` | **Required.** The NAS's network address. Sign-in checks passwords by opening an SMB session to it. `127.0.0.1` is refused: in the container that's the container itself. |
| `ALLOWED_USERS` | **Required.** `all`, or comma-separated usernames allowed to use the app (case doesn't matter). Others get "This account can't use this app" after a correct password. |
| `TZ` | Timezone the timestamps in snapshot names are written in. Defaults to the container's local time. Also used for the dates in download names. |
| `MAX_DOWNLOAD_GB` | Largest zip one download may hold, in gigabytes of file contents. Default `20`. Larger folders are refused before anything is sent; single files have no limit. |
| `MAX_SNAPSHOTS_LISTED` | Most recent points in time offered per share. Default `500`. |

Mount the dataset that holds each share (its mountpoint, where `.zfs/snapshot` is) read-only, at the
same path inside the container, with `rslave` propagation; without it, snapshots look empty. The
app finds each share's dataset by walking up from the share's path.

Mount the host's `/etc/passwd` and `/etc/group` read-only at `/host/etc/passwd` and `/host/etc/group`.
The app starts as root, looks each signed-in account up there, and reads files only in worker
processes running as that account's uid and groups, so the kernel applies the share's permissions.
Accounts with no entry there (Active Directory, for now) can't sign in. TrueNAS replaces both files
whenever an account or group changes; the app then exits so it restarts with the new ones, which
signs everyone out. Set a restart policy, as in [deploy/compose.yaml](deploy/compose.yaml).

The app always serves HTTP on port 8080. For HTTPS, with no proxy in front, mount the NAS's own
certificate: the pair TrueNAS writes to `/etc/certificates/<name>.crt` and `.key`, read-only at
`/host/tls/tls.crt` and `/host/tls/tls.key`. HTTPS is then served on port 8443 as well, and HTTP keeps
working. Over HTTP, passwords cross the network unencrypted. When TrueNAS renews the certificate, the
app restarts to load it.

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
cd backend && set -a && . ../dev/demo/snapshots-browser.env && set +a && .venv/bin/python scripts/dev_server.py
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
