#!/usr/bin/env python3
"""Phase 4 spike: does Samba let this account connect to the share?

Run in a throwaway container (bridge network, like the app):

    docker run --rm -it \
        --mount type=bind,source=/path/to/smb_share.py,target=/smb_share.py,readonly \
        python:3.12-slim sh -c "pip install -q --root-user-action=ignore smbprotocol && python3 /smb_share.py HOST SHARE"

It asks for a username and password, signs in, and connects to the share: an SMB tree connect, which
opens no file and writes nothing. It never prints or stores the password. It asks again until the
username is left empty, so several accounts can be checked in one run.
"""

from __future__ import annotations

import getpass
import sys
import uuid

from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBException
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect


def attempt(host: str, share: str, username: str, password: str) -> str:
    conn = Connection(uuid.uuid4(), host, 445, require_signing=True)
    try:
        conn.connect(timeout=10)
    except (OSError, ValueError, SMBException) as e:
        return f"NO CONNECTION: {type(e).__name__}: {e}"
    try:
        session = Session(conn, username, password, require_encryption=False, auth_protocol="ntlm")
        try:
            session.connect()
        except Exception as e:  # pyspnego raises its own types during NTLM
            return f"sign-in refused: {type(e).__name__}"
        try:
            tree = TreeConnect(session, rf"\\{host}\{share}")
            try:
                tree.connect()
            except SMBException as e:
                return f"share DENIED: {type(e).__name__}"
            tree.disconnect()
            return "share ALLOWED"
        finally:
            session.disconnect()
    finally:
        conn.disconnect()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: smb_share.py HOST SHARE")
        return 2
    host, share = sys.argv[1], sys.argv[2]
    while True:
        username = input("\nUsername (empty to quit): ").strip()
        if not username:
            return 0
        password = getpass.getpass("Password (not shown): ")
        print(attempt(host, share, username, password))


if __name__ == "__main__":
    sys.exit(main())
