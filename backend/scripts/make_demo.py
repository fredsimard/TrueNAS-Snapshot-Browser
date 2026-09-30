#!/usr/bin/env python3
"""Build a demo dataset for local development: plain folders laid out like ZFS snapshots.

    python scripts/make_demo.py ../dev/demo

Creates <out>/mnt/STUDIO/STUDIO/{Production,Scratch} with hourly (48 h), daily (14 days) and
weekly snapshots under .zfs/snapshot, plus <out>/snapback.yaml. Files are sparse and hard-linked
across snapshots, so the tree takes almost no disk space.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

KB, MB, GB = 1_000, 1_000_000, 1_000_000_000
CAMPAIGN = "Production/Clients/Maison Arôme/Fall 2026 Campaign"


def schedule(now: datetime) -> list[datetime]:
    hour = now.replace(minute=0, second=0, microsecond=0)
    today = hour.replace(hour=0)
    points = {hour - timedelta(hours=h) for h in range(48)}
    points |= {today - timedelta(days=d) for d in range(2, 16)}
    oldest_daily = today - timedelta(days=15)
    sunday = oldest_daily - timedelta(days=(oldest_daily.weekday() + 1) % 7 or 7)
    points |= {sunday - timedelta(weeks=w) for w in range(6)}
    return sorted(points)


def items(now: datetime) -> list[dict]:
    """path, versions [(time, size)], optional gone time. size None: folder."""
    ago = lambda d: now - timedelta(days=d)  # noqa: E731
    c = CAMPAIGN
    out = [
        {"path": "Production/Clients", "v": [(ago(80), None)]},
        {"path": "Production/Clients/Maison Arôme", "v": [(ago(80), None)]},
        {"path": c, "v": [(ago(60), None)]},
        {"path": "Production/Admin", "v": [(ago(80), None)]},
        {"path": "Production/Admin/Budget_2026.numbers", "v": [(ago(70), 420 * KB), (ago(2.4), 436 * KB)]},
        {"path": "Production/Admin/Old_invoices.pdf", "v": [(ago(75), 1.1 * MB)], "gone": ago(12)},
        {"path": f"{c}/Client Feedback", "v": [(ago(40), None)]},
        {"path": f"{c}/Client Feedback/Notes_round1.pdf", "v": [(ago(40), 180 * KB)]},
        {"path": f"{c}/Client Feedback/Notes_round2.pdf", "v": [(ago(7), 210 * KB)]},
        {"path": f"{c}/Client Feedback/Notes_round3.pdf", "v": [(ago(1.2), 260 * KB)]},
        {"path": f"{c}/Layouts", "v": [(ago(55), None)]},
        {"path": f"{c}/Layouts/Spread_01.indd", "v": [(ago(50), 38 * MB), (ago(11), 41 * MB), (ago(0.1), 44 * MB)]},
        {"path": f"{c}/Raw", "v": [(ago(9), None)]},
        {"path": f"{c}/Retouched", "v": [(ago(9), None)]},
        {"path": f"{c}/Selects", "v": [(ago(58), None)]},
        {"path": f"{c}/Video", "v": [(ago(30), None)]},
        {"path": f"{c}/Video/A001_C003.mov", "v": [(ago(8), 4.2 * GB)]},
        {"path": f"{c}/Archive", "v": [(ago(0.3), None)]},
        {"path": f"{c}/Archive/Old_brief.pdf", "v": [(ago(0.3), 1.9 * MB)]},
        {"path": f"{c}/Contracts", "v": [(ago(45), None)], "locked": True},
        {"path": f"{c}/Brief_MaisonArome_Fall26.pdf", "v": [(ago(27), 2.4 * MB)]},
        {"path": f"{c}/Call_sheet.pdf", "v": [(ago(0.2), 310 * KB)]},
        {"path": f"{c}/Campaign_cut_v4.mov", "v": [(ago(5), 2.84 * GB)]},
        {"path": f"{c}/Campaign_cut_v5.mov", "v": [(ago(0.05), 2.91 * GB)]},
        {"path": f"{c}/Color_notes.txt", "v": [(ago(6), 3 * KB), (ago(1.2), 4 * KB)]},
        {"path": f"{c}/Hero_edit_v3.tif", "v": [(ago(3), 312.4 * MB)], "gone": ago(0.9)},
        {"path": f"{c}/Logo_final.psd", "v": [(ago(17), 184.2 * MB), (ago(0.2), 96.7 * MB)]},
        {"path": f"{c}/Moodboard.key", "v": [(ago(10), 88 * MB), (ago(1.1), 96.1 * MB)]},
        {"path": f"{c}/Retouch_notes.docx", "v": [(ago(3.2), 38 * KB)]},
        {"path": f"{c}/Shot_list.numbers", "v": [(ago(7), 1.2 * MB)]},
        {"path": f"{c}/Typeface_licences.zip", "v": [(ago(26), 6.8 * MB)]},
        {"path": f"{c}/Usage_rights_2026.pdf", "v": [(ago(25), 640 * KB)]},
    ]
    for i in range(30):
        out.append({"path": f"{c}/Raw/DSC_{4471 + i:04d}.NEF", "v": [(ago(9) + timedelta(minutes=i), 48 * MB + i * 97 * KB)]})
    for i in range(16):
        n = 4471 + i
        it = {"path": f"{c}/Selects/DSC_{n}.jpg", "v": [(ago(4) + timedelta(minutes=4 + i), (24 + i % 4) * MB)]}
        if n in (4476, 4477):
            it["gone"] = ago(0.25)
        if n == 4480:
            it["v"].append((ago(1.1), 31.8 * MB))
        out.append(it)
    for i in range(12):
        out.append({"path": f"{c}/Retouched/DSC_{4471 + i}_ret.tif", "v": [(ago(3) + timedelta(minutes=9 * i), 140 * MB)]})
    return out


def state_at(its: list[dict], t: datetime) -> dict[str, tuple[dict, int]]:
    out = {}
    for it in its:
        if it["v"][0][0] > t or ("gone" in it and it["gone"] <= t):
            continue
        vi = max(i for i, (vt, _) in enumerate(it["v"]) if vt <= t)
        out[it["path"]] = (it, vi)
    return out


def unlock(path: Path) -> None:
    for dirpath, dirnames, _ in os.walk(path):
        for d in dirnames:
            p = Path(dirpath) / d
            if not p.is_symlink():
                p.chmod(p.stat().st_mode | stat.S_IRWXU)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "../dev/demo").resolve()
    if out.exists():
        out.chmod(0o755)
        unlock(out)
        shutil.rmtree(out)
    dataset = out / "mnt" / "STUDIO" / "STUDIO"
    blobs = out / "blobs"
    blobs.mkdir(parents=True)

    now = datetime.now()
    its = items(now)
    blob_paths: dict[tuple[str, int], Path] = {}

    def blob(it: dict, vi: int) -> Path:
        key = (it["path"], vi)
        if key not in blob_paths:
            t, size = it["v"][vi]
            p = blobs / f"{len(blob_paths)}"
            with open(p, "wb") as f:
                f.truncate(int(size))
            ts = t.timestamp()
            os.utime(p, (ts, ts))
            blob_paths[key] = p
        return blob_paths[key]

    def populate(root: Path, t: datetime) -> None:
        state = state_at(its, t)
        (root / "Production").mkdir(parents=True, exist_ok=True)
        (root / "Scratch").mkdir(parents=True, exist_ok=True)
        dir_times: dict[Path, float] = {}
        for path, (it, vi) in sorted(state.items()):
            p = root / path
            vt, size = it["v"][vi]
            if size is None:
                p.mkdir(parents=True, exist_ok=True)
                dir_times.setdefault(p, vt.timestamp())
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                os.link(blob(it, vi), p)
            parent = p.parent
            dir_times[parent] = max(dir_times.get(parent, 0), it["v"][0][0].timestamp())
        for d in sorted(dir_times, key=lambda x: len(x.parts), reverse=True):
            os.utime(d, (dir_times[d], dir_times[d]))
        for path, (it, _) in state.items():
            if it.get("locked"):
                (root / path).chmod(0)

    points = schedule(now)
    for t in points:
        populate(dataset / ".zfs" / "snapshot" / t.strftime("auto-%Y-%m-%d_%H-%M"), t)
    populate(dataset / ".zfs" / "snapshot" / (points[-3].strftime("manual-%Y-%m-%d_%H-%M")), points[-3])
    populate(dataset, now)

    (out / "snapback.yaml").write_text(
        "server_label: Studio Server\n"
        "listen: 127.0.0.1:8080\n"
        "roots:\n"
        f"  - id: production\n    share_root: {dataset / 'Production'}\n"
        f"  - id: scratch\n    share_root: {dataset / 'Scratch'}\n"
    )
    print(f"{len(points)} snapshots in {dataset}")
    print(f"SNAPSHOT_PREFIXES=auto- SNAPBACK_CONFIG={out / 'snapback.yaml'} python -m snapback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
