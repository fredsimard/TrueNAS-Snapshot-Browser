import os

import pytest

from snapback.config import ConfigError, RootConfig
from snapback.paths import Missing, PathRejected, observe_file, opened_dir, parse_rel, read_dir, resolve_root


@pytest.mark.parametrize(
    "rel,parts",
    [("", ()), ("/", ()), ("LOGOS/2024", ("LOGOS", "2024")), ("/a//b/", ("a", "b")), ("Café/été", ("Café", "été"))],
)
def test_parse_rel_accepts(rel, parts):
    assert parse_rel(rel) == parts


@pytest.mark.parametrize(
    "rel",
    ["..", "a/../b", "./a", ".zfs", ".zfs/snapshot/x", "a/.ZFS/snapshot", ".hidden", "a\x00b", "x" * 256, "a/" * 2100],
)
def test_parse_rel_rejects(rel):
    with pytest.raises(PathRejected):
        parse_rel(rel)


def test_share_below_dataset(ds):
    share = ds.mountpoint / "Projects"
    share.mkdir()
    root = resolve_root(RootConfig("p", "Projects", str(share)))
    assert root.dataset_mountpoint == os.path.realpath(ds.mountpoint)
    assert root.relative_prefix == ("Projects",)
    assert root.base("auto-1") == os.path.join(os.path.realpath(ds.snapdir), "auto-1", "Projects")
    assert root.base(None) == os.path.realpath(share)


def test_share_is_dataset(ds):
    root = resolve_root(RootConfig("p", "data", str(ds.mountpoint)))
    assert root.relative_prefix == ()
    assert root.base("auto-1") == os.path.join(os.path.realpath(ds.snapdir), "auto-1")


def test_share_deep_below_dataset(ds):
    share = ds.mountpoint / "a" / "b c"
    share.mkdir(parents=True)
    assert resolve_root(RootConfig("p", "x", str(share))).relative_prefix == ("a", "b c")


def test_no_snapshot_dir(tmp_path):
    (tmp_path / "share").mkdir()
    with pytest.raises(ConfigError, match="no .zfs/snapshot"):
        resolve_root(RootConfig("p", "x", str(tmp_path / "share")))


def test_share_root_missing(tmp_path):
    with pytest.raises(ConfigError, match="isn.t in the container. Mount the dataset"):
        resolve_root(RootConfig("p", "x", str(tmp_path / "nope")))


def test_dataset_override(ds, tmp_path):
    share = ds.mountpoint / "S"
    share.mkdir()
    root = resolve_root(RootConfig("p", "x", str(share), str(ds.mountpoint)))
    assert root.relative_prefix == ("S",)
    other = tmp_path / "elsewhere"
    (other / ".zfs" / "snapshot").mkdir(parents=True)
    with pytest.raises(ConfigError, match="one of its parents"):
        resolve_root(RootConfig("p", "x", str(share), str(other)))


def test_symlinked_folder_not_followed(ds, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    share = ds.mountpoint / "S"
    share.mkdir()
    os.symlink(outside, share / "link")
    with pytest.raises(Missing):
        with opened_dir(str(share), ["link"]):
            pass
    assert "link" not in read_dir(str(share), [])
    entry, _ = observe_file(str(share), ["link", "secret.txt"])
    assert entry is None


def test_listing_hides_dotfiles_and_symlinks(ds):
    share = ds.mountpoint / "S"
    share.mkdir()
    (share / ".DS_Store").write_text("x")
    (share / "a.txt").write_text("hello")
    (share / "sub").mkdir()
    os.symlink("a.txt", share / "alias")
    entries = read_dir(str(share), [])
    assert sorted(entries) == ["a.txt", "sub"]
    assert entries["a.txt"].size == 5 and not entries["a.txt"].is_dir
    assert entries["sub"].is_dir and entries["sub"].size is None


def test_file_is_not_a_folder(ds):
    share = ds.mountpoint / "S"
    share.mkdir()
    (share / "a.txt").write_text("x")
    with pytest.raises(Missing):
        read_dir(str(share), ["a.txt"])
