#!/usr/bin/env python3
"""Phase 4 spike: does Samba let this account use the share?

Run in a throwaway container (bridge network, like the app):

    docker run --rm -it \
        --mount type=bind,source=/path/to/smb_share.py,target=/smb_share.py,readonly \
        python:3.12-slim sh -c "pip install -q --root-user-action=ignore smbprotocol && python3 /smb_share.py HOST SHARE"

It asks for a username and password, signs in, connects to the share and opens its top folder for
listing. Connecting alone isn't enough: Samba accepts the connection for an account that can't read
the share's folder, then refuses every request on it. Nothing is listed, read or written. It never
prints or stores the password. It asks again until the username is left empty, so several accounts
can be checked in one run.
"""

from __future__ import annotations

import getpass
import sys
import uuid

from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBException
from smbprotocol.open import (
    CreateDisposition,
    CreateOptions,
    DirectoryAccessMask,
    FileAttributes,
    ImpersonationLevel,
    Open,
    ShareAccess,
)
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
        tree = TreeConnect(session, rf"\\{host}\{share}")
        try:
            tree.connect()
        except SMBException as e:
            return f"share DENIED at connect: {type(e).__name__}"
        top = Open(tree, "")
        try:
            top.create(
                ImpersonationLevel.Impersonation,
                DirectoryAccessMask.FILE_LIST_DIRECTORY | DirectoryAccessMask.FILE_READ_ATTRIBUTES,
                FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
                ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                CreateDisposition.FILE_OPEN,
                CreateOptions.FILE_DIRECTORY_FILE,
            )
        except SMBException as e:
            return f"share DENIED at its top folder: {type(e).__name__}"
        top.close()
        return "share ALLOWED"
    finally:
        # No polite logoff: a share Samba refused also refuses the disconnect.
        conn.disconnect(close=False)


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
