#!/usr/bin/env python3
"""Phase 0 spike: can a container list and read .zfs/snapshot/<snap>/... on a bind mount,
including snapshots that were not automounted when the container started?

Run inside each spike container:

    docker exec -i <container> python3 - /mnt/POOL/DATASET < check.py

Optional privilege-drop test (lists a folder as another user, kernel-enforced):

    docker exec -i <container> python3 - /mnt/POOL/DATASET --as-uid 3000 --as-gid 3000 \
        --as-groups 3000,545 --rel "SomeFolder" < check.py

Read-only. Never writes to the dataset.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

NAME_TIME_RE = re.compile(
    r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?:[_T\-. ](\d{2})[-:.]?(\d{2})(?:[-:.]?(\d{2}))?)?(?!\d)"
)


def mountinfo() -> list[tuple[str, str]]:
    """(mount point, optional fields) for every mount visible in this namespace."""
    out = []
    with open("/proc/self/mountinfo") as f:
        for line in f:
            left = line.split(" - ")[0].split()
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), left[4])
            out.append((point, " ".join(left[6:])))
    return out


def mounted_snapshots(snapdir: str) -> set[str]:
    prefix = snapdir.rstrip("/") + "/"
    return {p[len(prefix):] for p, _ in mountinfo() if p.startswith(prefix)}


def propagation_of(path: str) -> str:
    best, fields = "", ""
    for point, opt in mountinfo():
        if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            best, fields = point, opt
    return f"{best} [{fields or 'private'}]"


def cap_eff() -> str:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("CapEff:"):
                return line.split()[1]
    return "?"


def fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(ts))


def parsed_name_time(name: str) -> str:
    m = NAME_TIME_RE.search(name)
    if not m:
        return "unparsed"
    y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d} (as written in the name)"


def first_file(root: str, max_dirs: int = 200) -> str | None:
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        seen += 1
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            if os.path.isfile(p) and not os.path.islink(p):
                return p
        dirnames.sort()
        if seen >= max_dirs:
            return None
    return None


def check_snapshot(snapdir: str, name: str) -> bool:
    path = os.path.join(snapdir, name)
    before_mounted = name in mounted_snapshots(snapdir)
    st0 = os.lstat(path)
    try:
        entries = os.listdir(path)
        err = None
    except OSError as e:
        entries, err = [], e
    after_mounted = name in mounted_snapshots(snapdir)
    st1 = os.stat(path)
    print(f"  {name}")
    print(f"    name time:           {parsed_name_time(name)}")
    print(f"    lstat mtime before:  {fmt_time(st0.st_mtime)}  dev={st0.st_dev}")
    print(f"    stat mtime after:    {fmt_time(st1.st_mtime)}  dev={st1.st_dev}")
    print(f"    mounted in container before/after access: {before_mounted}/{after_mounted}")
    if err:
        print(f"    listdir FAILED: {err!r}")
        return False
    print(f"    entries: {len(entries)}  sample: {sorted(entries)[:5]}")
    if not entries:
        print("    WARNING: empty. Compare with `ls <snapshot> | wc -l` on the host;"
              " empty here but not there means automount did not propagate.")
        return False
    f = first_file(path)
    if f:
        with open(f, "rb") as fh:
            n = len(fh.read(65536))
        print(f"    read OK: {n} bytes from {os.path.relpath(f, path)}")
    else:
        print("    no regular file found within the first 200 folders (read not tested)")
    return True


def drop_and_list(uid: int, gid: int, groups: list[int], paths: list[str]) -> None:
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups(groups)
            os.setgid(gid)
            os.setuid(uid)
            for p in paths:
                try:
                    print(f"    as uid={uid}: {p}: {len(os.listdir(p))} entries")
                except OSError as e:
                    print(f"    as uid={uid}: {p}: {e.strerror}")
        except OSError as e:
            print(f"    privilege drop FAILED: {e!r}")
        sys.stdout.flush()
        os._exit(0)
    os.waitpid(pid, 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", help="dataset mountpoint as seen inside the container")
    ap.add_argument("--sample", type=int, default=3, help="snapshots to test (default 3)")
    ap.add_argument("--as-uid", type=int)
    ap.add_argument("--as-gid", type=int)
    ap.add_argument("--as-groups", default="")
    ap.add_argument("--rel", default="", help="folder relative to the dataset for the drop test")
    a = ap.parse_args()

    snapdir = os.path.join(a.dataset, ".zfs", "snapshot")
    print(f"kernel:      {os.uname().release}")
    print(f"uid/gid:     {os.getuid()}/{os.getgid()}  CapEff={cap_eff()}")
    print(f"TZ:          {os.environ.get('TZ', '(unset)')}  now={fmt_time(time.time())}")
    print(f"bind mount:  {propagation_of(a.dataset)}")

    if not os.path.isdir(snapdir):
        print(f"FAIL: {snapdir} is not reachable from the container")
        return 1
    names = sorted(os.listdir(snapdir))
    print(f"snapshots:   {len(names)} listed in .zfs/snapshot")
    if not names:
        print("FAIL: no snapshots listed")
        return 1
    mounted = mounted_snapshots(snapdir)
    print(f"             {len(mounted)} already mounted in this container's namespace")

    cold = [n for n in names if n not in mounted]
    sample = cold[: a.sample] or names[: a.sample]
    print(f"\nTesting {len(sample)} snapshot(s)"
          f" ({'not yet mounted in the container' if cold else 'all were already mounted'}):")
    ok = all([check_snapshot(snapdir, n) for n in sample])

    if a.as_uid is not None:
        gid = a.as_gid if a.as_gid is not None else a.as_uid
        groups = [int(g) for g in a.as_groups.split(",") if g.strip()] or [gid]
        live = os.path.join(a.dataset, a.rel)
        snap = os.path.join(snapdir, sample[0], a.rel)
        print(f"\nPrivilege drop test (uid={a.as_uid} gid={gid} groups={groups}):")
        drop_and_list(a.as_uid, gid, groups, [live, snap])

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
