# Phase 0 spike: snapshot automount inside a container

Gate for the whole architecture (PLAN.md §12 risk 1 and 2). Answers:

1. Is `.zfs/snapshot/<snap>/…` listable and readable from inside a container, including snapshots that
   were **not** automounted when the container started?
2. Does TrueNAS's custom app YAML accept `bind.propagation: rslave`, and is it needed?
3. Can the container drop to a user's uid/gids so the kernel enforces ACLs?
4. What does `stat` on `.zfs/snapshot/<snap>` return compared to the snapshot's creation time, and in which
   timezone are the timestamps in snapshot names?

## Steps

1. Pick a dataset mountpoint with snapshots, e.g. `/mnt/tank/studio`. Edit `compose.yaml`: replace
   `/mnt/POOL/DATASET` (four places) and `TZ`.
2. Install it as a custom app via "Install via YAML". Note whether TrueNAS accepts the `propagation` key.
3. Copy the check script to the NAS:

   ```bash
   scp spike/check.py root@NAS:/tmp/check.py
   ```

4. On the NAS shell, find the two containers, then make sure the snapshots the script will test are
   **not** mounted on the host. Unmounting a snapshot automount is harmless; ZFS remounts it on next access.

   ```bash
   docker ps --format '{{.Names}}' | grep -E 'rslave|private'
   grep '/.zfs/snapshot/' /proc/mounts | awk '{print $2}'
   umount /mnt/POOL/DATASET/.zfs/snapshot/<snap>     # for any snapshot listed above
   ```

5. Run the check in both containers:

   ```bash
   docker exec -i <rslave-container>  python3 - /mnt/POOL/DATASET < /tmp/check.py
   docker exec -i <private-container> python3 - /mnt/POOL/DATASET < /tmp/check.py
   ```

6. Optional: privilege drop. Use a real user's uid, primary gid and groups (`id <user>` on the NAS), and a
   folder that user can or can't read:

   ```bash
   docker exec -i <rslave-container> python3 - /mnt/POOL/DATASET \
       --as-uid 3000 --as-gid 3000 --as-groups 3000,545 --rel "Projects/Some Folder" < /tmp/check.py
   ```

7. Record the host's view for comparison:

   ```bash
   zfs list -H -p -t snapshot -o name,creation -d 1 POOL/DATASET | tail -n 5
   ls /mnt/POOL/DATASET/.zfs/snapshot/<snap> | wc -l
   ```

8. Remove the app.

## Reading the result

- `PASS` in `rslave` and `FAIL` (empty listings) in `private`: propagation is required and works. The app's
  compose file uses `rslave`.
- `PASS` in both: propagation isn't needed on this kernel; keep `rslave` anyway.
- `FAIL` in both: the architecture changes (filesystem access moves to the host). Stop.
- Name time vs `lstat mtime before`: if the mtime matches `zfs list … creation`, it can replace name parsing.
  If the name's time is offset from creation by the UTC offset, names are written in UTC.
