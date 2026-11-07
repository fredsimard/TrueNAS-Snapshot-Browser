"""Simulated dataset: plain directories laid out like <dataset>/.zfs/snapshot/<snap>/…

Identical file versions are hard links to one blob, so they share inode and ctime across points
in time, as unchanged files do across real ZFS snapshots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

DIR_MTIME = 1_700_000_000


@dataclass(frozen=True)
class Blob:
    path: Path
    mtime: int


class Dataset:
    def __init__(self, base: Path):
        self.mountpoint = base / "mnt" / "tank" / "data"
        self.snapdir = self.mountpoint / ".zfs" / "snapshot"
        self.snapdir.mkdir(parents=True)
        self._blobs = base / "blobs"
        self._blobs.mkdir()
        self._n = 0

    def blob(self, content: bytes, mtime: int) -> Blob:
        self._n += 1
        p = self._blobs / f"b{self._n}"
        p.write_bytes(content)
        os.utime(p, (mtime, mtime))
        return Blob(p, mtime)

    def point(self, snapshot: str | None) -> Path:
        return self.mountpoint if snapshot is None else self.snapdir / snapshot

    def populate(self, snapshot: str | None, files: dict[str, Blob | None]) -> Path:
        """files: path relative to the dataset → Blob, or None for an empty folder."""
        root = self.point(snapshot)
        root.mkdir(parents=True, exist_ok=True)
        dirs = {root}
        for rel, blob in files.items():
            p = root / rel
            if blob is None:
                p.mkdir(parents=True, exist_ok=True)
                dirs.add(p)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            os.link(blob.path, p)
            d = p.parent
            while d != root:
                dirs.add(d)
                d = d.parent
        for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
            os.utime(d, (DIR_MTIME, DIR_MTIME))
        return root


@pytest.fixture
def ds(tmp_path: Path) -> Dataset:
    return Dataset(tmp_path)
