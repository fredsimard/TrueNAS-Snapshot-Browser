import pytest

from snapback.config import (
    ConfigError,
    load_config,
    parse_allowed_users,
    parse_config,
    parse_prefixes,
    parse_smb_host,
    parse_timezone,
)

MINIMAL = {"server_label": "NAS01", "roots": [{"id": "projects", "share_root": "/mnt/tank/studio/Projects"}]}


@pytest.mark.parametrize("raw", [None, "", "   ", ",", " , ,"])
def test_prefixes_required(raw):
    with pytest.raises(ConfigError, match="SNAPSHOT_PREFIXES is required"):
        parse_prefixes(raw)


def test_prefixes_trimmed_and_deduplicated():
    assert parse_prefixes(" auto- , daily-,auto-,") == ("auto-", "daily-")


def test_prefix_with_slash_rejected():
    with pytest.raises(ConfigError):
        parse_prefixes("auto-,a/b")


def test_load_config_checks_prefixes_before_reading_file(tmp_path):
    with pytest.raises(ConfigError, match="SNAPSHOT_PREFIXES"):
        load_config({"SNAPBACK_CONFIG": str(tmp_path / "missing.yaml")})


def test_load_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("server_label: X\nroots:\n  - id: a\n    share_root: /mnt/a/b\n")
    cfg = load_config(
        {
            "SNAPBACK_CONFIG": str(p),
            "SNAPSHOT_PREFIXES": "auto-",
            "SMB_HOST": "192.0.2.10",
            "ALLOWED_USERS": "Alice, bob",
            "TZ": "America/Toronto",
        }
    )
    assert cfg.snapshot_prefixes == ("auto-",)
    assert cfg.smb_host == "192.0.2.10"
    assert cfg.allowed_users == {"alice", "bob"}
    assert cfg.timezone.key == "America/Toronto"
    assert (cfg.host, cfg.port) == ("0.0.0.0", 8080)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(
            {
                "SNAPBACK_CONFIG": str(tmp_path / "nope.yaml"),
                "SNAPSHOT_PREFIXES": "auto-",
                "SMB_HOST": "192.0.2.10",
                "ALLOWED_USERS": "all",
            }
        )


def test_load_config_requires_allowed_users(tmp_path):
    with pytest.raises(ConfigError, match="ALLOWED_USERS is required"):
        load_config({"SNAPBACK_CONFIG": str(tmp_path / "missing.yaml"), "SNAPSHOT_PREFIXES": "auto-", "SMB_HOST": "192.0.2.10"})


@pytest.mark.parametrize("raw", [None, "", "  ", ", ,"])
def test_allowed_users_required(raw):
    with pytest.raises(ConfigError, match="ALLOWED_USERS is required"):
        parse_allowed_users(raw)


@pytest.mark.parametrize("raw", ["all", " ALL ", "All,"])
def test_allowed_users_all(raw):
    assert parse_allowed_users(raw) is None


def test_allowed_users_list_is_casefolded_and_trimmed():
    assert parse_allowed_users(" Alice ,bob,,ALICE") == {"alice", "bob"}


def test_allowed_users_all_must_be_alone():
    with pytest.raises(ConfigError, match="on its own"):
        parse_allowed_users("all,bob")


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_smb_host_required(raw):
    with pytest.raises(ConfigError, match="SMB_HOST is required"):
        parse_smb_host(raw)


@pytest.mark.parametrize("raw", ["127.0.0.1", "127.1.2.3", "localhost", "::1"])
def test_smb_host_cannot_be_the_container(raw):
    with pytest.raises(ConfigError, match="localhost is the container"):
        parse_smb_host(raw)


def test_label_and_share_default_to_share_folder_name():
    cfg = parse_config(MINIMAL, ("auto-",))
    assert cfg.roots[0].label == "Projects"
    assert cfg.roots[0].share_name == "Projects"
    assert cfg.roots[0].dataset_mountpoint is None


def test_label_defaults_to_smb_share():
    root = {"id": "v", "share_root": "/mnt/tank/v", "smb_share": "VIDEO_ARCHIVE"}
    cfg = parse_config({**MINIMAL, "roots": [root]}, ("auto-",))
    assert (cfg.roots[0].label, cfg.roots[0].share_name) == ("VIDEO_ARCHIVE", "VIDEO_ARCHIVE")
    root["label"] = "Video Archive"
    cfg = parse_config({**MINIMAL, "roots": [root]}, ("auto-",))
    assert (cfg.roots[0].label, cfg.roots[0].share_name) == ("Video Archive", "VIDEO_ARCHIVE")


def test_unknown_keys_rejected():
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({**MINIMAL, "auth": {"method": "smb"}}, ("auto-",))
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({**MINIMAL, "roots": [{"id": "a", "share_root": "/x/y", "prefixes": "z"}]}, ("auto-",))


@pytest.mark.parametrize(
    "root",
    [
        {"id": "Bad Id", "share_root": "/x/y"},
        {"id": "a", "share_root": "relative/path"},
        {"id": "a", "share_root": "/"},
        {"id": "a", "share_root": "/x/y", "dataset_mountpoint": "x"},
        {"id": "a", "share_root": "/x/y", "label": ""},
        {"id": "a", "share_root": "/x/y", "smb_share": ""},
        {"id": "a", "share_root": "/x/y", "smb_share": "a\\b"},
        {"id": "a", "share_root": "/x/y", "smb_share": "a/b"},
    ],
)
def test_invalid_roots(root):
    with pytest.raises(ConfigError):
        parse_config({**MINIMAL, "roots": [root]}, ("auto-",))


def test_duplicate_root_ids():
    roots = [{"id": "a", "share_root": "/x/y"}, {"id": "a", "share_root": "/x/z"}]
    with pytest.raises(ConfigError, match="duplicate"):
        parse_config({**MINIMAL, "roots": roots}, ("auto-",))


@pytest.mark.parametrize("listen,expected", [("127.0.0.1:9000", ("127.0.0.1", 9000)), ("[::]:8080", ("::", 8080))])
def test_listen(listen, expected):
    cfg = parse_config({**MINIMAL, "listen": listen}, ("auto-",))
    assert (cfg.host, cfg.port) == expected


@pytest.mark.parametrize("listen", ["8080", "host:", "host:99999", 8080])
def test_bad_listen(listen):
    with pytest.raises(ConfigError, match="listen"):
        parse_config({**MINIMAL, "listen": listen}, ("auto-",))


@pytest.mark.parametrize("value", [0, -1, "10", True])
def test_bad_max_snapshots(value):
    with pytest.raises(ConfigError):
        parse_config({**MINIMAL, "limits": {"max_snapshots_listed": value}}, ("auto-",))


def test_timezone():
    assert parse_timezone(None) is None
    assert parse_timezone(":Europe/Paris").key == "Europe/Paris"
    with pytest.raises(ConfigError):
        parse_timezone("Mars/Olympus")
