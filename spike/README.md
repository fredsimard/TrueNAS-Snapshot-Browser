# Spikes

Throwaway checks that prove a risky assumption on a real TrueNAS server before anything is built on it.
Run them from a root shell on the NAS, with the scripts copied somewhere the container can mount.

## `check.py`: snapshot automount in a container (Phase 0, PLAN.md §12 risks 1–2)

```bash
docker run --rm --mount type=bind,source=/path/to/check.py,target=/check.py,readonly --mount type=bind,source=/mnt/POOL/DATASET,target=/mnt/POOL/DATASET,readonly,bind-propagation=rslave python:3.12-slim python3 /check.py /mnt/POOL/DATASET
```

Add `--as-uid`, `--as-gid`, `--as-groups` and `--rel` to the script to also test privilege dropping (Phase 4).

**Result on TrueNAS, kernel 6.12:**

- With `rslave`, snapshots that weren't mounted at container start automount and read correctly.
- Without it, they list as empty with no error, so the app would show wrong folders. `rslave` is required.
- "Install via YAML" accepts `bind.propagation: rslave`. The Custom App wizard has no such setting.
- Timestamps in TrueNAS snapshot names are local time and follow daylight saving, so `TZ` must be a zone name.
- `lstat` on `.zfs/snapshot/<snap>` gives the creation time only while unmounted, so it can't replace name parsing.

## `smb_auth.py`: sign-in over SMB from a container (Phase 3, risk 3)

```bash
docker run --rm -it --mount type=bind,source=/path/to/smb_auth.py,target=/smb_auth.py,readonly python:3.12-slim sh -c "pip install -q --root-user-action=ignore smbprotocol && python3 /smb_auth.py NAS_ADDRESS"
```

It tries a real login, a wrong password and an unknown user. Use the NAS's network address: `127.0.0.1` is the
container itself.

**Result:** the real login opens a signed SMB 3.1.1 session; the other two are refused.

## `smb_share.py`: which shares an account may use (Phase 4)

```bash
docker run --rm -it --mount type=bind,source=/path/to/smb_share.py,target=/smb_share.py,readonly python:3.12-slim sh -c "pip install -q --root-user-action=ignore smbprotocol && python3 /smb_share.py NAS_ADDRESS SHARE"
```

It signs in, connects to the share and opens its top folder for listing. Nothing is listed, read or written.

**Result:**

- An account with access, and every account in `builtin_users`, is allowed. TrueNAS adds new SMB users to
  `builtin_users`, and the share and its folder allowed that group.
- An account removed from `builtin_users` is denied, but only at its top folder. Samba accepts the share
  connection, then refuses every request on it, including the disconnect. Connecting alone proves nothing.
- An empty password is refused.
