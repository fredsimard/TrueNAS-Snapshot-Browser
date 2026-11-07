"""Configuration, all from environment variables set when the app is created (TrueNAS: "Install via
YAML", the environment block).

Required: SERVER_NAME, SHARES, SNAPSHOT_PREFIXES, SMB_HOST and ALLOWED_USERS. Without the prefixes,
users could see an unfiltered snapshot list; without SMB_HOST, nobody can sign in; without
ALLOWED_USERS, a forgotten field would open the app to every account. The app refuses to start
without them, and says which one is missing.

SHARES lists the shares users may browse, one per line (or comma-separated), each as the SMB share
name users connect to, "=", and the share's folder on the NAS:

    SHARES: |
      Projects = /mnt/tank/studio/Projects
      Video Archive = /mnt/tank/video

The share name is what users see, and sign-in shows a share only if Samba lets the account use it.
Each share's dataset must be mounted into the container at the same path.
"""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_MAX_SNAPSHOTS_LISTED = 500
DEFAULT_MAX_DOWNLOAD_GB = 20

# Characters Windows and Samba don't accept in a share name, plus control characters.
BAD_SHARE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RootConfig:
    id: str
    label: str
    share_root: str
    dataset_mountpoint: str | None = None
    # The SMB share users connect to for this root. Sign-in shows the root only if Samba lets the
    # account connect to it. Empty: the label.
    smb_share: str = ""

    @property
    def share_name(self) -> str:
        return self.smb_share or self.label


@dataclass(frozen=True)
class Config:
    server_label: str
    roots: tuple[RootConfig, ...]
    snapshot_prefixes: tuple[str, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # Where sign-in sends credentials. 127.0.0.1 would be the container itself.
    smb_host: str = ""
    # Casefolded usernames allowed to use the app. None: every account Samba accepts.
    allowed_users: frozenset[str] | None = None
    # Timezone that timestamps in snapshot names are written in. None: the system's local time.
    timezone: ZoneInfo | None = None
    max_snapshots_listed: int = DEFAULT_MAX_SNAPSHOTS_LISTED
    snapshot_cache_seconds: float = 30.0
    # Largest zip a user may download, in bytes of file contents. Single files have no limit.
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_GB * 10**9


def load_config(environ: Mapping[str, str] = os.environ) -> Config:
    return Config(
        snapshot_prefixes=parse_prefixes(environ.get("SNAPSHOT_PREFIXES")),
        smb_host=parse_smb_host(environ.get("SMB_HOST")),
        allowed_users=parse_allowed_users(environ.get("ALLOWED_USERS")),
        roots=parse_shares(environ.get("SHARES")),
        server_label=parse_server_name(environ.get("SERVER_NAME")),
        timezone=parse_timezone(environ.get("TZ")),
        max_download_bytes=parse_max_download(environ.get("MAX_DOWNLOAD_GB")),
        max_snapshots_listed=parse_max_listed(environ.get("MAX_SNAPSHOTS_LISTED")),
    )


def parse_prefixes(raw: str | None) -> tuple[str, ...]:
    prefixes = tuple(dict.fromkeys(p.strip() for p in (raw or "").split(",") if p.strip()))
    if not prefixes:
        raise ConfigError(
            "SNAPSHOT_PREFIXES is required: comma-separated snapshot name prefixes, "
            'e.g. "auto-,daily-". Only snapshots whose names start with one of them are shown.'
        )
    for p in prefixes:
        if "/" in p or "\x00" in p:
            raise ConfigError(f"SNAPSHOT_PREFIXES: invalid prefix {p!r}")
    return prefixes


def parse_smb_host(raw: str | None) -> str:
    host = (raw or "").strip()
    if not host:
        raise ConfigError("SMB_HOST is required: the NAS's address, used to check passwords at sign-in.")
    if host in ("localhost", "::1") or host.startswith("127."):
        raise ConfigError("SMB_HOST: must be the NAS's network address; localhost is the container itself.")
    return host


def parse_allowed_users(raw: str | None) -> frozenset[str] | None:
    names = [n.strip() for n in (raw or "").split(",") if n.strip()]
    if not names:
        raise ConfigError(
            'ALLOWED_USERS is required: "all", or comma-separated usernames allowed to use the app, '
            'e.g. "alice,bob".'
        )
    if any(n.casefold() == "all" for n in names):
        if len(names) > 1:
            raise ConfigError('ALLOWED_USERS: "all" must be on its own')
        return None
    return frozenset(n.casefold() for n in names)


def parse_max_download(raw: str | None) -> int:
    text = (raw or "").strip()
    if not text:
        return DEFAULT_MAX_DOWNLOAD_GB * 10**9
    try:
        gb = float(text)
    except ValueError:
        gb = 0
    if not 0 < gb < 10**6:
        raise ConfigError(f"MAX_DOWNLOAD_GB: expected a positive number of gigabytes, got {raw!r}")
    return int(gb * 10**9)


def parse_timezone(raw: str | None) -> ZoneInfo | None:
    name = (raw or "").strip().lstrip(":")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"TZ: unknown timezone {raw!r}") from None


def parse_server_name(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        raise ConfigError("SERVER_NAME is required: the name users know this server by, shown at the top of the app.")
    return name


def parse_shares(raw: str | None) -> tuple[RootConfig, ...]:
    text = (raw or "").strip()
    if not text:
        raise ConfigError(
            "SHARES is required: one share per line, as NAME = /mnt/pool/dataset/folder, where NAME is the "
            "SMB share name users connect to."
        )
    entries = text.splitlines() if "\n" in text else text.split(",")
    roots: list[RootConfig] = []
    names: set[str] = set()
    ids: set[str] = set()
    for entry in (e.strip() for e in entries):
        if not entry or entry.startswith("#"):
            continue
        name, sep, path = entry.partition("=")
        name, path = name.strip(), path.strip()
        if not sep or not name:
            raise ConfigError(f"SHARES: {entry!r} should be NAME = /path/to/the/share's/folder")
        if BAD_SHARE_CHARS.search(name):
            raise ConfigError(f"SHARES: {name!r} isn't an SMB share name")
        if not path.startswith("/") or "\x00" in path or posixpath.normpath(path) == "/":
            raise ConfigError(f"SHARES: {name}: {path!r} should be the share's folder, an absolute path like /mnt/pool/dataset")
        if name.casefold() in names:
            raise ConfigError(f"SHARES: {name!r} is listed twice")
        names.add(name.casefold())
        root_id = _unique(_slug(name), ids)
        ids.add(root_id)
        roots.append(RootConfig(id=root_id, label=name, share_root=posixpath.normpath(path), smb_share=name))
    if not roots:
        raise ConfigError("SHARES is required: it lists no share")
    return tuple(roots)


def _slug(name: str) -> str:
    """URL-safe id for a share: "Video Archive" → "video-archive"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60].strip("-")
    return slug or "share"


def _unique(slug: str, taken: set[str]) -> str:
    n, candidate = 2, slug
    while candidate in taken:
        candidate, n = f"{slug}-{n}", n + 1
    return candidate


def parse_max_listed(raw: str | None) -> int:
    text = (raw or "").strip()
    if not text:
        return DEFAULT_MAX_SNAPSHOTS_LISTED
    if not text.isdigit() or int(text) < 1:
        raise ConfigError(f"MAX_SNAPSHOTS_LISTED: expected a positive whole number, got {raw!r}")
    return int(text)
