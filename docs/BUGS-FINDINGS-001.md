# Security and bug audit 001

- Date: 2026-11-07
- Scope: whole repository (backend, frontend, Dockerfile, deploy, CI, dev scripts, spike)
- Commit: `9853239` on `feature/read-only-browser`
- Method: full read of the source, `pytest` (235 passed, 1 skipped; the privilege-drop test needs root and runs only in CI), `npm audit`, `oxlint`, and throwaway scripts that reproduced items 1, 2, 6, 7 and 10. The scripts are not committed.
- Status key: **Reproduced** = triggered with a script. **Read** = found by reading the code, not triggered.

## Summary

| # | Kind | Severity | Status | Title |
|---|---|---|---|---|
| 1 | Bug | Medium | Reproduced | Empty history for files in deleted folders |
| 2 | Bug | Low | Reproduced | Single-file download fetches the wrong name |
| 3 | Bug | Low | Read | Download stuck in `running`, fd leaked |
| 4 | Bug | Low | Read | Download progress polling never ends |
| 5 | Bug | Low | Read | Toast says "Saved to your Mac" on every platform |
| 6 | Security | Medium | Reproduced | Login throttle can be bypassed by concurrent requests |
| 7 | Security | Medium | Reproduced | Audit log can be forged |
| 8 | Security | Medium | Read | No request-body size limit |
| 9 | Security | Low | Read | One user can block everyone's sign-in |
| 10 | Security | Low | Reproduced | FIFO hangs a worker forever |
| 11 | Security | Low | Read | HTTP always on; missing CSP and HSTS |
| 12 | Security | Low | Read | Hardening and supply chain |

## What held up

- No path traversal or symlink escape: `parse_rel` rejects any segment starting with `.`, `opened_dir` walks with `O_NOFOLLOW` through `dir_fd`.
- Snapshot names never reach clients (opaque ids), and API responses carry no absolute paths or OS error text.
- Privilege drop in `become()` is correct: `setgroups`, `setgid`, `setuid`, then verifies `setuid(0)` fails. Inherited fds are closed in workers.
- Session tokens are 256-bit random, the cookie is `HttpOnly` and `SameSite=Strict`, validation errors don't echo submitted values, passwords are not logged.
- No XSS sinks in the frontend (no `dangerouslySetInnerHTML`, `innerHTML`, `eval`).
- No secrets, private addresses or real hostnames in the tree or in git history.
- `httpx2` in the dev extra is the real package (Starlette's `TestClient` imports it).

## Bugs

### 1. Empty history for files in deleted folders (Medium, Reproduced)

`observe_file` returns `("absent",)` when an ancestor folder is missing (`backend/snapshots_browser/paths.py:275`). If that folder was created after the oldest snapshot and deleted before Now, the keys at both ends of the range are equal. `file_history` (`backend/snapshots_browser/browse.py:185`) then treats the whole range as unchanged and skips it.

Repro: folder `Temp/` exists in snapshots 1–3 only, with `doc.txt` in two versions. `file_history(("Temp","doc.txt"))` returns 0 versions; expected 2. This is the "deleted" use case the app exists for: the user opens an old snapshot, sees the folder, opens a file, and the panel shows no copies.

Fix: when an ancestor is missing, key on the deepest existing ancestor's `(ino, ctime)`, as is already done for a missing file.

### 2. Single-file download fetches the wrong name (Low, Reproduced)

`backend/snapshots_browser/api.py:236` builds the `file` request from `body_.names[0]`. `plan_zip` skips names that are missing or unreadable, so the plan can describe a different name.

- `names=["gone.txt","real.txt"]`: the plan says `file real.txt`, the fetch targets `gone.txt`, `GET /api/downloads/{id}` returns 204 and progress shows `failed`.
- `names=["LockedFolder","real.txt"]` (unreadable folder): same result.

Fix: have the plan return the one file's name and use it.

### 3. Download stuck in `running`, fd leaked (Low, Read)

If the client disconnects before Starlette first iterates the `body()` generator, its `try/finally` is never entered. The fd stays open, the state stays `running`, the worker's zip thread blocks on a full pipe, and that download counts against `MAX_RUNNING_PER_SESSION` (3) until the session ends. The window is narrow. Related: the `ready → running` check and the running count in `backend/snapshots_browser/api.py:254-260` are not atomic, so two concurrent GETs of one id can both start.

Fix: take a lock around the check-and-set; close the fd if the response is never started.

### 4. Download progress polling never ends (Low, Read)

`follow()` in `frontend/src/hooks/use-downloads.tsx` polls `/progress` every second while the state is `ready` (the browser never started the download). `prune` runs only in `prepare_download` (`backend/snapshots_browser/api.py:240`), so the entry is never removed and the poll never stops. Each poll goes through `signed_in`, which resets the idle timer, so an open tab keeps the session alive indefinitely and shows "Downloading…" at 0%.

Fix: stop polling after a `ready` timeout; prune on `get_download` too.

### 5. Toast says "Saved to your Mac" (Low, Read)

`frontend/src/hooks/use-downloads.tsx:199` hardcodes "Mac". Users on Windows see it too.

## Security

### 6. Login throttle bypassed by concurrent requests (Medium, Reproduced)

`backend/snapshots_browser/api.py:175-190` checks `blocked()`, then calls `verify()` (an SMB round trip), then records `failed()`. Concurrent requests all pass the check before any failure is recorded. 30 concurrent bad logins for one username called `verify()` 30 times against a limit of 5; none received a 429. This defeats the stated goal of staying below AD lockout thresholds (the attempts can lock the account) and speeds up guessing.

Related, not reproduced:
- `user_key = username.lower()` gives `DOMAIN\user` and `user@realm` their own buckets. Whether Samba accepts those forms for local accounts was not tested.
- `lower()` here versus `casefold()` in the `ALLOWED_USERS` check.
- By design, anyone can lock a user out of the app with 5 bad passwords per 5 minutes, and 20 failures from one address block every user from that address.

Fix: count the attempt before calling `verify()` and release it on success.

### 7. Audit log can be forged (Medium, Reproduced)

Client-supplied `names` are written to the audit line unescaped (`backend/snapshots_browser/ops.py:128-136`, `backend/snapshots_browser/transfer.py:97-104`). `_names` only rejects `/`, a leading `.`, NUL and over-long names, so a newline passes. A zip request with `["real.txt","keep.txt","x\n<fake log line>"]` produced a second log line indistinguishable from a real `snapshots_browser.audit: download done by 'bob'` entry. Names that don't exist are also logged as downloaded, and real file names containing newlines do the same.

Fix: log with `%r` (or JSON), and log only names that were actually in the plan.

### 8. No request-body size limit (Medium, Read)

`/api/login` is unauthenticated and FastAPI buffers the whole body before the `Field(max_length=…)` checks in `backend/snapshots_browser/api.py:45-53` run. Neither the app nor the uvicorn config (`backend/snapshots_browser/__main__.py:56`) limits body size, and uvicorn has no header-read timeout. A large POST, or many slow connections, can exhaust memory or connections in the web process. `DownloadRequest.names` items have no per-item length limit either.

Fix: an ASGI middleware that rejects bodies over a few KB for `/api/login` (and a sane cap elsewhere), or a reverse proxy in front.

### 9. One user can block everyone's sign-in (Low, Read)

There is no cap on sessions per user. Each sign-in forks a worker (`SupervisedBackend.start`), and the global limit is 64 (`backend/snapshots_browser/workers.py:44`). One account signing in 64 times makes every new sign-in return 503. `login` spawns the worker before `sessions.create` prunes expired sessions, so stale sessions at the limit also block sign-in until some request prunes them (`/api/info` does).

Fix: cap sessions per user (replace the oldest), and prune before spawning.

### 10. FIFO hangs a worker forever (Low, Reproduced)

`_FILE_FLAGS` (`backend/snapshots_browser/downloads.py:30`) has no `O_NONBLOCK`, so `open_file` blocks on a FIFO until a writer appears. Confirmed: the call was still blocked after 2 s. It needs a FIFO planted in the share (local access), or a file swapped for a FIFO between plan and fetch (see item 2, which also lets the client pick a name the plan never checked). The stuck worker also counts toward the 64 limit.

Fix: open with `O_NONBLOCK`, `fstat`, then clear the flag for regular files.

### 11. HTTP always on; missing CSP and HSTS (Low, Read)

- Plain HTTP can't be disabled, so passwords can cross the network in clear (documented in the README). There is no HTTPS-only option or redirect, and the cookie is only `Secure` when the sign-in itself was over HTTPS.
- Responses carry `nosniff`, `no-referrer`, `X-Frame-Options` and `no-store` on `/api`, but no `Content-Security-Policy`, `Strict-Transport-Security` or `Permissions-Policy`. The inline theme script in `frontend/index.html` means a CSP needs a hash or nonce.
- Sessions slide on idle timeout only; there is no absolute lifetime.
- Uvicorn's access log records folder paths and download ids.

### 12. Hardening and supply chain (Low, Read)

- Container: runs as root with Docker's default capabilities. `deploy/compose.yaml` (now `deploy/snapshots-browser.yaml`) has no `no-new-privileges`, no `cap_drop`, no read-only rootfs and no memory or pids limits. Which capabilities the supervisor can do without was not tested (it needs to traverse share folders at startup).
- Build: Python dependencies use `>=` with no lock file (`backend/pyproject.toml`). Base images are tagged, not pinned by digest (`Dockerfile:4`, `Dockerfile:11`).
- CI: actions are pinned by tag, not commit SHA. `packages: write` is granted for the whole workflow (`.github/workflows/image.yml:15`), including the test job, which only needs `contents: read`.
- Frontend: `npm audit` reports 2 high advisories on `vite` 8.3.1 (fixed in a later release): `launch-editor` command execution and several dev-server file-read issues. Dev server only; the image ships built static files.
- Dev script: `backend/scripts/make_demo.py:112` runs `shutil.rmtree` on its first argument, after a `chmod`, with no guard. `make_demo.py ~` would delete the target.
- Folder listings have no pagination and the table is not virtualized. A folder with hundreds of thousands of entries produces a very large response; the worker-to-web message cap is 256 MB and over it the request fails with 503.

## Residual risk by design

The web process runs as `nobody` and can ask the supervisor for a worker as any non-root account. A bug in FastAPI, uvicorn or pydantic that gives code execution in the web process therefore exposes everything any local non-root user can read. The privilege drop itself is sound; the trust boundary is simply the web process.

## Other observations

- Samba share settings such as `veto files`, `force user` and `hide files` are not replicated; the app applies kernel permissions only. Share-level ACLs are covered by the connect check at sign-in.
- `last_seen` and `nearest` assume a name or folder appears and disappears at most once between the points searched. A name that is deleted, recreated and deleted again can report a different "last seen" snapshot.
- `oxlint` reports only warnings (setState in an effect in `use-request.ts:29`, fast-refresh export rules).
