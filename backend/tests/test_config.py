import pytest

from snapback.config import (
    ConfigError,
    load_config,
    parse_allowed_users,
    parse_max_download,
    parse_max_listed,
    parse_prefixes,
    parse_shares,
    parse_smb_host,
    parse_timezone,
)

ENV = {
    "SERVER_NAME": "NAS01",
    "SHARES": "Projects = /mnt/tank/studio/Projects",
    "SNAPSHOT_PREFIXES": "auto-",
    "SMB_HOST": "192.0.2.10",
    "ALLOWED_USERS": "all",
}


@pytest.mark.parametrize("raw", [None, "", "   ", ",", " , ,"])
def test_prefixes_required(raw):
    with pytest.raises(ConfigError, match="SNAPSHOT_PREFIXES is required"):
        parse_prefixes(raw)


def test_prefixes_trimmed_and_deduplicated():
    assert parse_prefixes(" auto- , daily-,auto-,") == ("auto-", "daily-")


def test_prefix_with_slash_rejected():
    with pytest.raises(ConfigError):
        parse_prefixes("auto-,a/b")


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


def test_timezone():
    assert parse_timezone(None) is None
    assert parse_timezone(":Europe/Paris").key == "Europe/Paris"
    with pytest.raises(ConfigError):
        parse_timezone("Mars/Olympus")


@pytest.mark.parametrize("raw,want", [(None, 20 * 10**9), ("", 20 * 10**9), ("5", 5 * 10**9), (" 0.5 ", 5 * 10**8)])
def test_max_download(raw, want):
    assert parse_max_download(raw) == want


@pytest.mark.parametrize("raw", ["0", "-1", "lots", "nan", "inf"])
def test_max_download_invalid(raw):
    with pytest.raises(ConfigError, match="MAX_DOWNLOAD_GB"):
        parse_max_download(raw)


def test_load_config():
    cfg = load_config({**ENV, "ALLOWED_USERS": "Alice, bob", "TZ": "America/Toronto"})
    assert cfg.server_label == "NAS01"
    assert cfg.snapshot_prefixes == ("auto-",)
    assert cfg.smb_host == "192.0.2.10"
    assert cfg.allowed_users == {"alice", "bob"}
    assert cfg.timezone.key == "America/Toronto"
    assert (cfg.host, cfg.port) == ("0.0.0.0", 8080)
    assert [(r.id, r.label, r.share_name, r.share_root) for r in cfg.roots] == [
        ("projects", "Projects", "Projects", "/mnt/tank/studio/Projects")
    ]


@pytest.mark.parametrize("missing", ["SERVER_NAME", "SHARES", "SNAPSHOT_PREFIXES", "SMB_HOST", "ALLOWED_USERS"])
def test_required(missing):
    with pytest.raises(ConfigError, match=f"{missing} is required"):
        load_config({k: v for k, v in ENV.items() if k != missing})


def test_prefixes_are_checked_first():
    with pytest.raises(ConfigError, match="SNAPSHOT_PREFIXES"):
        load_config({})


def test_shares_one_per_line_or_comma_separated():
    lines = parse_shares("""
        Projects = /mnt/tank/studio/Projects
        # a comment
        Video Archive=/mnt/tank/video/

        Été = /mnt/tank/ete
    """)
    assert [(r.id, r.label, r.share_root) for r in lines] == [
        ("projects", "Projects", "/mnt/tank/studio/Projects"),
        ("video-archive", "Video Archive", "/mnt/tank/video"),
        ("ete", "Été", "/mnt/tank/ete"),
    ]
    assert [r.id for r in parse_shares("A=/mnt/a, B=/mnt/b")] == ["a", "b"]


def test_a_path_with_a_comma_needs_one_share_per_line():
    roots = parse_shares("Clients=/mnt/tank/Smith, Jones\nOther=/mnt/tank/other")
    assert roots[0].share_root == "/mnt/tank/Smith, Jones"


def test_ids_stay_unique():
    assert [r.id for r in parse_shares("A b=/mnt/1\nA-b=/mnt/2\n!!!=/mnt/3")] == ["a-b", "a-b-2", "share"]


@pytest.mark.parametrize(
    "raw,match",
    [
        ("Projects", "should be NAME"),
        ("= /mnt/x", "should be NAME"),
        ("Projects = mnt/x", "absolute path"),
        ("Projects = /", "absolute path"),
        ("a/b = /mnt/x", "isn't an SMB share name"),
        ("a\\b = /mnt/x", "isn't an SMB share name"),
        ("A = /mnt/x\na = /mnt/y", "listed twice"),
        ("# only a comment", "lists no share"),
    ],
)
def test_bad_shares(raw, match):
    with pytest.raises(ConfigError, match=match):
        parse_shares(raw)


@pytest.mark.parametrize("raw,want", [(None, 500), ("", 500), ("50", 50)])
def test_max_listed(raw, want):
    assert parse_max_listed(raw) == want


@pytest.mark.parametrize("raw", ["0", "-1", "ten", "1.5"])
def test_bad_max_listed(raw):
    with pytest.raises(ConfigError, match="MAX_SNAPSHOTS_LISTED"):
        parse_max_listed(raw)
