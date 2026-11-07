# PLAN.md — ZFS Snapshot Self-Service Recovery (working name: `snapback`)

## 1. Objective

A web application, deployed as a container in TrueNAS SCALE's Apps section, that lets end users browse ZFS snapshots of an SMB share by time and by folder hierarchy, and download files or folders they need to recover. Deployable unchanged across ~8 TrueNAS servers, configured per server.

## 2. Constraints

- **No `zfs` CLI dependency.** Snapshots are already exposed on the filesystem at `<dataset_mountpoint>/.zfs/snapshot/<snap>/…`. The app does directory traversal and file reads only. This avoids coupling the container to the host's ZFS module version, avoids a privileged container, and means TrueNAS updates don't break it.
- **All mounts read-only.** The app never writes into the live share.
- **Users see only what their ACLs permit.** Not a shared login.
- **Share root ≠ dataset mountpoint.** The SMB share is typically a subdirectory inside a dataset. The app must handle this, and must not assume it.

## 3. Non-goals

- Restoring in place / writing back to the live share. Download only; the user puts the file back themselves through their normal SMB mount.
- Snapshot creation, deletion, rollback, or any administrative ZFS operation.
- Diffing file contents between versions.
- Replacing TrueNAS admin tooling.

## 4. Presentation principle

**The share root is the user-visible root. Nothing above it is ever displayed.**

Users navigate from the share name downward, exactly as they do in Finder. The pool, the dataset hierarchy, `/mnt/…`, and the `.zfs/snapshot/<snap>` machinery are implementation detail and never appear in the interface.

Consequences, all mandatory:

- Each root's display label defaults to the **SMB share name**, so the app's top level mirrors what users see mounted on their desktop: `Projects`, `Web Marketing`, `VIDEO_ARCHIVE`.
- Breadcrumbs begin at that label. There is no "up" affordance at the root.
- No absolute path appears in any user-facing surface: not breadcrumbs, not page titles, not download names, not error messages, not tooltips.
- Permission denials say the folder isn't available to them. They never echo a path the user couldn't otherwise see.
- Time is presented as dates and times. The snapshot's ZFS name is not shown by default — at most behind a details toggle, since snapshot names are admin vocabulary (`auto-2026-09-19_00-00`), not user vocabulary.
- Downloads are named from the relative path plus the snapshot's timestamp, never the absolute path.
- URLs address content as `root_id` + relative path. Absolute paths never appear in a URL, a form field, or an API request body.

Absolute paths remain in the server-side audit log, which is for the administrator, not for users.

## 5. Path model

This is the core of the flexibility requirement. Configuration names the share root. The app derives everything else and keeps the distinction between the two roots explicit:

```
share_root          # configured; the USER-VISIBLE root
dataset_mountpoint  # derived; nearest ancestor with an accessible .zfs
relative_prefix     = share_root - dataset_mountpoint   # internal only
```

Resolution, given a root id and a user-supplied relative path `rel`:

```
live:            share_root / rel
in snapshot X:   dataset_mountpoint / ".zfs/snapshot" / X / relative_prefix / rel
```

Worked example:

| | |
|---|---|
| share_root | `/mnt/tank/studio/Projects` |
| dataset_mountpoint | `/mnt/tank/studio` |
| relative_prefix | `Projects` |
| user sees at root | `Projects` |
| user sees browsing | `Projects / LOGOS / 2024` |
| resolves in snap X to | `/mnt/tank/studio/.zfs/snapshot/X/Projects/LOGOS/2024` |

`relative_prefix` exists solely so the app can step back down to the share root inside each snapshot. It is never rendered.

Auto-detecting `dataset_mountpoint` by walking parent directories means per-server config is just a list of share roots, which is what the administrator already knows. Allow an explicit override for cases where auto-detection is wrong.

`rel` is canonicalised and rejected if it escapes the root. Because `rel` is the only path input the app accepts, containment has a single choke point. Snapshot names are validated against the enumerated list, never interpolated from user input.

## 6. Configuration

One config file per server, same image everywhere:

```yaml
server_label: "NAS01"
listen: 0.0.0.0:8080
roots:
  - id: projects
    label: "Projects"
    share_root: /mnt/tank/studio/Projects
  - id: web-marketing
    label: "Web Marketing"
    share_root: /mnt/tank/studio/Web Marketing
  - id: video-archive
    label: "Video Archive"
    share_root: /mnt/tank/studio/VIDEO_ARCHIVE
limits:
  max_download_bytes: 21474836480
  max_snapshots_listed: 500
```

Multiple roots per instance; one instance per server. Roots sharing a dataset resolve to the same `.zfs` tree with different prefixes, which the path model handles for free.

**Snapshot filter.** Set at app creation through the `SNAPSHOT_PREFIXES` environment variable: a comma-separated list of snapshot name prefixes (e.g. `auto-,daily-`). Users only see snapshots whose names start with one of them, so the administrator picks which snapshot tasks are exposed. The field is mandatory: TrueNAS custom apps can't mark an environment variable as required, so the app refuses to start and logs why when it is empty. Snapshots outside the filter are unreachable, not just hidden: they can't be addressed through the API, and `.zfs` can't be reached through a relative path.

## 7. Authentication

Users must prove identity against the same accounts they already use for SMB — local TrueNAS users on some servers, Active Directory on others.

**Chosen approach: SMB self-authentication.** The app opens an SMB session to the NAS's own network address (`SMB_HOST`; inside the container `127.0.0.1` is the container itself) using the submitted credentials and treats a successful, signed session setup as authentication. Samba on each server is already configured for whichever backend that server uses, so the same code path covers local and AD accounts with no per-server auth configuration and no credential storage in the app.

Rejected alternatives:

- **PAM inside the container** — requires exposing `/etc/shadow`. Unacceptable.
- **Direct LDAP/AD bind** — covers only the AD servers, needs per-server config, doesn't cover local users. Keep as a configurable fallback if loopback SMB proves unreliable.
- **TrueNAS API login** — that's administrator authentication, not arbitrary user authentication. Wrong tool.

Sessions are short-lived, HttpOnly cookies. Credentials are held only for the duration of the auth call.

## 8. Authorization

**Do not evaluate ACLs in application code.** NFSv4 ACL evaluation — inheritance, deny-ACE ordering, trustee resolution — is exactly where a tool like this leaks data. The kernel already implements it correctly.

Instead: resolve the authenticated user to their uid and full group set, then perform *every* filesystem operation in a worker that has dropped to that identity (`setgroups`, `setgid`, `setuid`, in that order, irreversibly). Listing a directory the user can't read fails with `EACCES` and becomes a 403. Correct by construction.

Requirements this imposes:

- The web layer runs unprivileged; only a small supervisor holds `CAP_SETUID`/`CAP_SETGID` to fork workers.
- Identity resolution must produce the *same* uid/gid the host assigns. Trivial for local users. For AD, the uid comes from winbind's idmap and the container must agree with the host — see risk 4.
- Snapshots preserve the ACLs in force when the snapshot was taken. A user who has since lost access to a folder may still be able to read older copies of it, and vice versa. This is inherent to ZFS snapshots and should be stated in the UI, not worked around.

## 9. Restore behaviour

- Single file → streamed to the browser with the original name, suffixed with the snapshot timestamp.
- Folder → tar or zip stream generated on the fly, no temp staging on disk, so memory and pool usage stay flat regardless of size.
- Enforce `max_download_bytes`; refuse oversized folder restores with a clear message rather than timing out.
- Every restore logged: who, what path, which snapshot, when, bytes served.

## 10. UI

Two-axis navigation, matching how the problem was originally framed. Full detail in `UI-BRIEF.md` and `DESIGN-HANDOFF.md`.

- If one root is configured, the user lands in it directly.
- If several are, the landing page is a list of share names — the same names they mount on their Mac — with no hierarchy shown above them.
- Selecting a root opens its folder tree, breadcrumb starting at the share name.
- A date-ordered snapshot selector re-renders the current folder as of that point in time, so the user keeps their place in the hierarchy while moving through time.
- A "versions of this file" view listing the snapshots in which a given file differs, by size and mtime.
- Folders that did not exist at the selected time show an explicit empty state naming the date, not an error.

## 11. Deployment

Single container, custom app in TrueNAS Apps. Host path mounts, read-only, one per distinct `dataset_mountpoint` (not per share root — mounting the dataset mountpoint is what makes `.zfs` reachable). Exposed on the LAN only; TLS via the existing reverse proxy if wanted.

## 12. Open risks

Ordered by how much damage they do if they turn out badly.

1. **Snapshot automount propagation into the container.** ZFS automounts each snapshot on first access to its directory. Docker bind mounts default to `private` propagation, so mounts appearing on the host *after* container start may not be visible inside it — the container could see snapshot directories that are empty. Mitigations, in order of preference: set `rshared`/`rslave` propagation on the bind mount; or run the filesystem-access component on the host and keep only the web layer containerised. **Resolved: `rslave` works and is required; without it snapshots list as empty with no error.**
2. **TrueNAS Apps custom-app capabilities.** Whether the custom app YAML permits mount propagation settings, host path mounts at pool level, and the capabilities needed for privilege dropping. Resolved for propagation: "Install via YAML" accepts it, the Custom App wizard has no such setting. Capabilities for privilege dropping are still to be confirmed in Phase 4.
3. **SMB authentication from the container.** Resolved: a session to the NAS's network address accepts real logins and refuses wrong passwords and unknown users, without falling back to guest.
4. **AD uid consistency.** The container must resolve AD users to the same uids winbind assigns on the host, or ACL enforcement silently checks against the wrong identity. Options: join the container to the domain with identical idmap config, or query the host for the mapping. Affects only the AD servers.
5. **Scale of enumeration.** Hundreds of snapshots against deep trees. Enumerate lazily, one directory at a time, never walk a whole snapshot. Cache snapshot *lists* briefly; never cache directory contents across users.
6. **Portability across 8 servers.** Keep the container free of any dependency on TrueNAS internals so that a version difference between servers doesn't fragment the deployment.

## 13. Phases

- **Phase 0 — Spike (gate).** A throwaway container with a host path mount; prove that `.zfs/snapshot/<snap>/…` is listable and readable from *inside* the container, including snapshots not yet automounted at container start. If this fails and can't be made to work, the architecture changes fundamentally and the rest of this plan is void.
- **Phase 1 — Read-only browser.** One hardcoded root, no authentication, bound to localhost. Proves the path model and the two-axis navigation.
- **Phase 2 — Config and multi-root.** Auto-detection of dataset mountpoint, YAML config, path containment tests.
- **Phase 3 — Authentication.** SMB sign-in, sessions.
- **Phase 4 — Authorization.** Privilege-dropping workers; verify against a test user with deliberately restrictive ACLs that denied paths are invisible, not merely unlinked.
- **Phase 5 — Downloads.** File streaming, folder archives, limits, audit log.
- **Phase 6 — Active Directory.** Identity resolution on an AD-joined server.
- **Phase 7 — Packaging.** App template, per-server config, rollout to the remaining servers.

Phases 1–2 are usable by the administrator over SSH-forwarded localhost before any auth exists. That covers the ten-recoveries-a-year case on its own, so the project has value before Phase 3 lands.
