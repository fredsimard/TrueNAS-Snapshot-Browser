"""Configuration: one YAML file per server, plus environment variables set at app creation.

SNAPSHOT_PREFIXES, SMB_HOST and ALLOWED_USERS are mandatory. Without the first, users could see an
unfiltered snapshot list; without the second, nobody can sign in; without the third, a forgotten
field would open the app to every account. The app refuses to start without them.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULT_CONFIG_PATH = "/config/snapback.yaml"
DEFAULT_LISTEN = "0.0.0.0:8080"
DEFAULT_MAX_SNAPSHOTS_LISTED = 500

ROOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TOP_KEYS = {"server_label", "listen", "roots", "limits"}
ROOT_KEYS = {"id", "label", "share_root", "smb_share", "dataset_mountpoint"}
LIMIT_KEYS = {"max_snapshots_listed"}


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
    host: str
    port: int
    roots: tuple[RootConfig, ...]
    snapshot_prefixes: tuple[str, ...]
    # Where sign-in sends credentials. 127.0.0.1 would be the container itself.
    smb_host: str = ""
    # Casefolded usernames allowed to use the app. None: every account Samba accepts.
    allowed_users: frozenset[str] | None = None
    # Timezone that timestamps in snapshot names are written in. None: the system's local time.
    timezone: ZoneInfo | None = None
    max_snapshots_listed: int = DEFAULT_MAX_SNAPSHOTS_LISTED
    snapshot_cache_seconds: float = 30.0


def load_config(environ: Mapping[str, str] = os.environ) -> Config:
    prefixes = parse_prefixes(environ.get("SNAPSHOT_PREFIXES"))
    smb_host = parse_smb_host(environ.get("SMB_HOST"))
    allowed_users = parse_allowed_users(environ.get("ALLOWED_USERS"))
    timezone = parse_timezone(environ.get("TZ"))
    path = environ.get("SNAPBACK_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from None
    return parse_config(raw, prefixes, timezone, smb_host, allowed_users)


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


def parse_timezone(raw: str | None) -> ZoneInfo | None:
    name = (raw or "").strip().lstrip(":")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"TZ: unknown timezone {raw!r}") from None


def parse_config(
    raw: object,
    prefixes: tuple[str, ...],
    timezone: ZoneInfo | None = None,
    smb_host: str = "",
    allowed_users: frozenset[str] | None = None,
) -> Config:
    top = _mapping(raw, "config", TOP_KEYS)

    server_label = top.get("server_label")
    if not isinstance(server_label, str) or not server_label.strip():
        raise ConfigError("server_label: required, a non-empty string")

    host, port = _parse_listen(top.get("listen", DEFAULT_LISTEN))

    raw_roots = top.get("roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ConfigError("roots: required, a non-empty list")
    roots = tuple(_parse_root(r, i) for i, r in enumerate(raw_roots))
    seen: set[str] = set()
    for r in roots:
        if r.id in seen:
            raise ConfigError(f"roots: duplicate id {r.id!r}")
        seen.add(r.id)

    limits = _mapping(top.get("limits") or {}, "limits", LIMIT_KEYS)
    max_listed = limits.get("max_snapshots_listed", DEFAULT_MAX_SNAPSHOTS_LISTED)
    if type(max_listed) is not int or max_listed < 1:
        raise ConfigError("limits.max_snapshots_listed: must be a positive integer")

    return Config(
        server_label=server_label.strip(),
        host=host,
        port=port,
        roots=roots,
        snapshot_prefixes=prefixes,
        smb_host=smb_host,
        allowed_users=allowed_users,
        timezone=timezone,
        max_snapshots_listed=max_listed,
    )


def _mapping(raw: object, where: str, allowed: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping")
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    return raw


def _parse_listen(raw: object) -> tuple[str, int]:
    if not isinstance(raw, str) or ":" not in raw:
        raise ConfigError('listen: expected "host:port"')
    host, _, port_s = raw.rpartition(":")
    host = host.strip("[]")
    if not host or not port_s.isdigit() or not 0 < int(port_s) < 65536:
        raise ConfigError('listen: expected "host:port"')
    return host, int(port_s)


def _abs_path(raw: object, where: str) -> str:
    if not isinstance(raw, str) or not raw.startswith("/") or "\x00" in raw:
        raise ConfigError(f"{where}: expected an absolute path")
    return posixpath.normpath(raw)


def _parse_root(raw: object, index: int) -> RootConfig:
    where = f"roots[{index}]"
    r = _mapping(raw, where, ROOT_KEYS)
    root_id = r.get("id")
    if not isinstance(root_id, str) or not ROOT_ID_RE.match(root_id):
        raise ConfigError(f"{where}.id: required, lowercase letters, digits and dashes")
    share_root = _abs_path(r.get("share_root"), f"{where}.share_root")
    if share_root == "/":
        raise ConfigError(f"{where}.share_root: must not be /")
    smb_share = r.get("smb_share", posixpath.basename(share_root))
    if not isinstance(smb_share, str) or not smb_share.strip() or BAD_SHARE_CHARS.search(smb_share):
        raise ConfigError(f"{where}.smb_share: expected an SMB share name")
    label = r.get("label", smb_share)
    if not isinstance(label, str) or not label.strip():
        raise ConfigError(f"{where}.label: expected a non-empty string")
    dataset = r.get("dataset_mountpoint")
    if dataset is not None:
        dataset = _abs_path(dataset, f"{where}.dataset_mountpoint")
    return RootConfig(
        id=root_id,
        label=label.strip(),
        share_root=share_root,
        dataset_mountpoint=dataset,
        smb_share=smb_share.strip(),
    )
