#!/usr/bin/env python3
"""Phase 4 spike: does the kernel apply share permissions to a process that has become the user?

Run in a throwaway container, with the host's user and group files mounted read-only:

    docker run --rm -it \
        --mount type=bind,source=/etc/passwd,target=/host/passwd,readonly \
        --mount type=bind,source=/etc/group,target=/host/group,readonly \
        --mount type=bind,source=/mnt/POOL/DATASET,target=/mnt/POOL/DATASET,readonly,bind-propagation=rslave \
        --mount type=bind,source=/path/to/drop.py,target=/drop.py,readonly \
        python:3.12-slim python3 /drop.py /mnt/POOL/DATASET/SHARE [FOLDER]

It asks for a username, reads its uid and groups from the host's files, then lists FOLDER (default:
the share's top folder) and every folder in it, live and in the newest snapshot. It does this first
in a child process that has dropped to that user (setgroups, setgid, setuid), then as root, and
prints the folders where the two answers differ. No password. Nothing is written.
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys

PASSWD = "/host/passwd"
GROUP = "/host/group"
NAME_TIME_RE = re.compile(
    r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?:[_T\-. ](\d{2})[-:.]?(\d{2})(?:[-:.]?(\d{2}))?)?(?!\d)"
)


def lookup(username: str) -> tuple[str, int, int, dict[int, str]] | None:
    """(name, uid, primary gid, {gid: group name}) from the host's files, matching case-insensitively."""
    want = username.casefold()
    with open(PASSWD, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(":")
            if len(p) >= 4 and p[0].casefold() == want:
                name, uid, gid = p[0], int(p[2]), int(p[3])
                break
        else:
            return None
    groups = {gid: "?"}
    with open(GROUP, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(":")
            if len(p) < 4 or not p[2].isdigit():
                continue
            g = int(p[2])
            if g == gid or name in p[3].split(","):
                groups[g] = p[0]
    return name, uid, gid, groups


def answer(e: OSError) -> str:
    if e.errno in (errno.EACCES, errno.EPERM):
        return "DENIED"
    if e.errno == errno.ENOENT:
        return "missing"
    return f"error {errno.errorcode.get(e.errno, e.errno)}"


def survey(path: str) -> dict[str, str]:
    """Answer for path itself (".") and for each folder in it."""
    try:
        names = sorted(os.listdir(path))
    except OSError as e:
        return {".": answer(e)}
    out = {".": "listed"}
    for n in names:
        p = os.path.join(path, n)
        if n.startswith(".") or os.path.islink(p) or not os.path.isdir(p):
            continue
        try:
            os.listdir(p)
            out[n] = "listed"
        except OSError as e:
            out[n] = answer(e)
    return out


def as_user(uid: int, gid: int, gids: list[int], path: str) -> dict[str, str]:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            os.setgroups(gids)
            os.setgid(gid)
            os.setuid(uid)
            try:
                os.setuid(0)
                regain = "REGAINED ROOT"
            except PermissionError:
                regain = "refused"
            out = survey(path)
            out["!regain"] = regain
        except BaseException as e:
            out = {"!failed": f"{type(e).__name__}: {e}"}
        with os.fdopen(w, "w") as f:
            json.dump(out, f)
        os._exit(0)
    os.close(w)
    with os.fdopen(r) as f:
        out = json.load(f)
    os.waitpid(pid, 0)
    return out


def newest_snapshot(snapdir: str) -> str | None:
    best: tuple[tuple[int, ...], str] | None = None
    for name in os.listdir(snapdir):
        m = NAME_TIME_RE.search(name)
        if m:
            key = tuple(int(g) if g else 0 for g in m.groups())
            if best is None or key > best[0]:
                best = (key, name)
    return best[1] if best else None


def report(label: str, user: dict[str, str], root: dict[str, str]) -> None:
    if "!failed" in user:
        print(f"  {label}: privilege drop FAILED: {user['!failed']}")
        return
    print(f"  {label}: top: user {user['.']}, root {root['.']}; setuid(0) afterwards: {user['!regain']}")
    if user["."] != "listed":
        return
    subs = [k for k in root if k != "."]
    differ = [k for k in subs if user.get(k) != root[k]]
    print(f"    {len(subs)} folders in it, {len(subs) - len(differ)} same answer, {len(differ)} different")
    for k in differ[:20]:
        print(f"      {k}: user {user.get(k, 'not listed')}, root {root[k]}")
    if len(differ) > 20:
        print(f"      … and {len(differ) - 20} more")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: drop.py SHARE_ROOT [FOLDER]")
        return 2
    share = os.path.realpath(sys.argv[1])
    folder = sys.argv[2].strip("/") if len(sys.argv) == 3 else ""
    if any(p in ("", ".", "..") for p in folder.split("/")) and folder:
        print("FOLDER must be a plain path inside the share")
        return 2

    dataset = share
    while not os.path.isdir(os.path.join(dataset, ".zfs", "snapshot")):
        if dataset == "/":
            print(f"no .zfs/snapshot at {share} or above it")
            return 1
        dataset = os.path.dirname(dataset)
    prefix = os.path.relpath(share, dataset)
    snapdir = os.path.join(dataset, ".zfs", "snapshot")
    snap = newest_snapshot(snapdir)
    targets = [("live", os.path.join(share, folder))]
    if snap:
        targets.append((f"snapshot {snap}", os.path.normpath(os.path.join(snapdir, snap, prefix, folder))))
    else:
        print("no snapshot with a timestamp in its name; testing live only")

    while True:
        username = input("\nUsername (empty to quit): ").strip()
        if not username:
            return 0
        found = lookup(username)
        if found is None:
            print(f"  not in {PASSWD}")
            continue
        name, uid, gid, groups = found
        print(f"  {name}: uid {uid}, gid {gid}, groups: "
              + ", ".join(f"{n} {g}" for g, n in sorted(groups.items())))
        for label, path in targets:
            user = as_user(uid, gid, sorted(groups), path)  # first, so the user triggers automount
            report(label, user, survey(path))


if __name__ == "__main__":
    sys.exit(main())
