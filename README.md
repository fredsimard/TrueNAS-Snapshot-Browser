# Snapshots Browser

A TrueNAS SCALE app that lets people get their own files back from ZFS snapshots, without asking IT.

They open a web page, sign in with the username and password they already use for the shares, and
browse a share as it was at any point in time: folder by folder, with what has changed or been
deleted since then marked. When they find the version they want, they download it.

## What users get

- **Their shares, as they know them.** The same share names they see on their computer, starting
  at the top of the share. Nothing above it, and no technical names, is ever shown.
- **Any point in time.** A timeline of the available versions, step back and forth with ← and →,
  or pick a day on a calendar.
- **What's different since then.** Items that have changed since the chosen time are marked
  *Changed*, items that are gone now are marked *Deleted*, with a filter for each.
- **A file's history.** Every earlier version of a file, with its date and size.
- **Downloads.** A file, a folder or a selection, as of the chosen time. Folders and selections
  arrive as a zip. Downloaded names carry the version's date, e.g. `Budget (2026-09-12 14.00).xlsx`,
  so they never overwrite the current file.

## How it protects your data

- **Read-only.** The app never writes, renames or deletes anything on the NAS. Downloads are copies.
- **The NAS decides who sees what.** Each file is read as the signed-in user, with that account's
  own permissions, so people see exactly what they could open over SMB, and nothing more.
- **Your accounts, no new ones.** Sign-in checks the password with the NAS's own SMB service. The app
  stores no passwords.
- **You choose what's exposed.** Only the shares you list, only the snapshots whose names start with
  the prefixes you set, and optionally only the users you name.
- **Downloads are logged.** Each download writes one line to the app's log: who, what, which
  version, and how much was sent.
- **HTTPS** with the NAS's own certificate, no reverse proxy needed.

Snapshots keep the permissions files had when the snapshot was taken: someone who has since lost
access to a folder can still open its older copies, and the reverse.

## Requirements

- TrueNAS SCALE with the Docker-based Apps ("Install via YAML"). Tested on TrueNAS SCALE 25.10.
- Local TrueNAS user accounts. Active Directory accounts aren't supported yet
  ([#1](https://github.com/fredsimard/TrueNAS-Snapshot-Browser/issues/1)).
- Periodic snapshot tasks on the datasets that hold the shares (Data Protection → Periodic Snapshot
  Tasks).

## Install

1. Download [deploy/snapshots-browser.yaml](deploy/snapshots-browser.yaml). It's the app's whole
   setup, with a note on every line you need to change and where to find the value in TrueNAS.
2. In TrueNAS: Apps → Discover Apps → ⋮ → **Install via YAML**. The Custom App wizard can't be used:
   it can't set the mount propagation that snapshots need.
3. Name the app, paste the file, and change the values marked `◀ CHANGE` (and check those marked
   `◀ CHECK`).
4. Save. The app is at `http://<your NAS>:18080`, and at `https://<your NAS>:18443` with a certificate.

If a setting is missing or wrong, the app stops right away and its log (Apps → the app → Logs) says
which one.

## Settings

All settings are in the app's YAML, under `environment`.

| Setting | |
|---|---|
| `SERVER_NAME` | **Required.** The name your users know the server by, shown at the top of the app. |
| `SHARES` | **Required.** The shares users may browse, one per line: the SMB share name, `=`, and the share's path, e.g. `Projects = /mnt/tank/studio/Projects`. Copy both from Shares → Windows (SMB) Shares. Each user only sees the shares their account can use. |
| `SMB_HOST` | **Required.** The NAS's network address. Sign-in checks passwords against the NAS's SMB service there. Not `127.0.0.1`: inside the app, that's the app itself. |
| `SNAPSHOT_PREFIXES` | **Required.** The start of the snapshot names users may see, comma-separated, e.g. `auto-` for TrueNAS's default naming. Other snapshots stay hidden. |
| `ALLOWED_USERS` | **Required.** `all`, or the usernames allowed to use the app, comma-separated. Others are told "This account can't use this app" after a correct password. |
| `TZ` | The NAS's time zone, e.g. `America/Toronto`. Snapshot times are read in it, and download names use it. |
| `MAX_DOWNLOAD_GB` | The largest zip one download may hold, in GB. Default `20`. Larger folders and selections are refused before anything starts. Single files have no limit. |
| `MAX_SNAPSHOTS_LISTED` | How many of the most recent versions are offered per share. Default `500`. |

Besides the settings, the YAML mounts, all read-only:

- **Each dataset that holds a share**, at the same path, with `propagation: rslave`. Without it,
  every snapshot looks empty.
- **The NAS's `/etc/passwd` and `/etc/group`**, so the app can read files as each signed-in user.
- **Optionally, the NAS's certificate**, for HTTPS.

## HTTPS

HTTP is always on (port 18080). To add HTTPS (port 18443), mount the NAS's certificate as shown at the
end of the YAML: TrueNAS keeps each certificate listed under Credentials → Certificates as
`/etc/certificates/<name>.crt` and `.key`. Choose the one issued for the address your users type.
When TrueNAS renews it, the app restarts by itself to load the new one.

Over HTTP, passwords cross the network unencrypted. Prefer HTTPS wherever you can.

## Updating

Change the version at the end of the `image:` line (see
[Releases](https://github.com/fredsimard/TrueNAS-Snapshot-Browser/releases)), then save. Everyone
is signed out when the app restarts.

## Troubleshooting

- **The app stops right after starting.** Its log (Apps → the app → Logs) names the setting that's
  missing or wrong.
- **Past versions show empty folders.** The dataset isn't mounted, or its mount lacks
  `propagation: rslave`. Mount the dataset itself, not just the share's folder.
- **"This account can't use this app".** The username isn't in `ALLOWED_USERS`, or it isn't a local
  account on the NAS.
- **A user doesn't see a share.** Their account can't use that share over SMB. The app follows the
  NAS's share permissions.
- **Everyone was signed out.** The app restarts by itself when an account or group changes on the
  NAS, or when the certificate is renewed, so that it uses the new ones.
- **The browser keeps switching `http://` to `https://` on port 18080.** It remembers an earlier
  HTTPS visit to that address. Clear the site's data in the browser, or use port 18443 for HTTPS.

## Known limits

- A dataset nested inside a share shows as an empty folder in the outer dataset's snapshots.
- Hidden files and folders (names starting with `.`) are never shown, as in Finder.
- Symbolic links are not shown or followed.
- Snapshots taken in the same minute by different tasks show as one version.
- Sign-ins are kept in memory: restarting the app signs everyone out. A sign-in also ends after an
  hour without use.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

[Apache License 2.0](LICENSE).
