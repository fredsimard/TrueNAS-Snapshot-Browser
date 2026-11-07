import os

import pytest

from snapshots_browser.config import ConfigError
from snapshots_browser.identity import HostAccounts, Identity, parse_identity

PASSWD = """\
root:x:0:0:root:/root:/bin/bash
# comment
alice:x:3000:3000:Alice:/home/alice:/usr/bin/zsh
Mixed:x:3001:3001::/nonexistent:/usr/sbin/nologin
twin:x:3002:3002::/:/bin/sh
TWIN:x:3003:3003::/:/bin/sh
wheelie:x:3004:0::/:/bin/sh
broken:x:abc:3000::/:/bin/sh
short:x:3005
"""

GROUP = """\
root:x:0:alice
alice:x:3000:
builtin_users:x:545:alice,Mixed
admins:x:544:Mixed
other:x:600:alicia
"""


def test_exact_name():
    assert parse_identity("alice", PASSWD, GROUP) == Identity("alice", 3000, 3000, (545, 3000))


def test_names_are_not_case_sensitive():
    assert parse_identity("ALICE", PASSWD, GROUP).name == "alice"
    assert parse_identity("mixed", PASSWD, GROUP) == Identity("Mixed", 3001, 3001, (544, 545, 3001))


def test_exact_match_wins_over_case_insensitive_ones():
    assert parse_identity("TWIN", PASSWD, GROUP).uid == 3003
    assert parse_identity("twin", PASSWD, GROUP).uid == 3002


def test_ambiguous_name_is_refused():
    assert parse_identity("Twin", PASSWD, GROUP) is None


@pytest.mark.parametrize("name", ["nobody", "", "broken", "short", "# comment"])
def test_unknown_or_malformed(name):
    assert parse_identity(name, PASSWD, GROUP) is None


def test_root_is_never_an_identity():
    assert parse_identity("root", PASSWD, GROUP) is None
    assert parse_identity("wheelie", PASSWD, GROUP) is None  # primary group 0


def test_group_zero_is_dropped():
    assert 0 not in parse_identity("alice", PASSWD, GROUP).groups


@pytest.fixture
def files(tmp_path):
    passwd, group = tmp_path / "passwd", tmp_path / "group"
    passwd.write_text(PASSWD)
    group.write_text(GROUP)
    return passwd, group


def test_host_accounts(files):
    accounts = HostAccounts(*map(str, files))
    assert accounts.lookup("alice").uid == 3000
    assert not accounts.replaced()


def test_missing_files_are_a_configuration_error(tmp_path):
    with pytest.raises(ConfigError, match="/host/etc/passwd"):
        HostAccounts(str(tmp_path / "passwd"), str(tmp_path / "group"))


def test_in_place_edits_are_read(files):
    passwd, group = files
    accounts = HostAccounts(str(passwd), str(group))
    with open(group, "r+") as f:  # same file, new contents
        f.write(GROUP.replace("545:alice,Mixed", "545:Mixed"))
        f.truncate()
    assert accounts.lookup("alice").groups == (3000,)
    assert not accounts.replaced()


@pytest.mark.parametrize("which", [0, 1])
def test_replacing_a_file_is_noticed(files, which):
    accounts = HostAccounts(*map(str, files))
    new = files[which].with_suffix(".new")
    new.write_text(files[which].read_text())
    os.replace(new, files[which])  # what TrueNAS does when an account changes
    assert accounts.replaced()
