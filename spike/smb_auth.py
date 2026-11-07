#!/usr/bin/env python3
"""Phase 3 spike: can the app verify a password by opening an SMB session to the NAS?

Run in a throwaway container (bridge network, like the app):

    docker run --rm -it \
        --mount type=bind,source=/path/to/smb_auth.py,target=/smb_auth.py,readonly \
        python:3.12-slim sh -c "pip install -q --root-user-action=ignore smbprotocol && python3 /smb_auth.py HOST"

It asks for a username and password and never prints or stores the password. It tries three logins:
the real one, the same user with a wrong password, and a user that doesn't exist. A correct setup
accepts only the first. Signing is required, so a session the server silently downgrades to guest
fails: a signed session proves the server knew the password.

Each failed try counts against the account's lockout policy, if there is one.
"""

from __future__ import annotations

import getpass
import secrets
import sys
import uuid

from smbprotocol.connection import Connection
from smbprotocol.exceptions import LogonFailure, SMBException
from smbprotocol.session import Session


def attempt(host: str, username: str, password: str) -> str:
    conn = Connection(uuid.uuid4(), host, 445, require_signing=True)
    try:
        conn.connect(timeout=10)
    except (OSError, ValueError, SMBException) as e:
        return f"NO CONNECTION: {type(e).__name__}: {e}"
    try:
        session = Session(conn, username, password, require_encryption=False, auth_protocol="ntlm")
        session.connect()
    except LogonFailure:
        return "rejected (logon failure)"
    except SMBException as e:
        return f"rejected: {type(e).__name__}: {e}"
    except Exception as e:  # pyspnego raises its own types during NTLM
        return f"rejected: {type(e).__name__}: {e}"
    else:
        session.disconnect()
        return f"ACCEPTED, signed session, dialect {conn.dialect:#06x}"
    finally:
        conn.disconnect()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: smb_auth.py HOST")
        return 2
    host = sys.argv[1]
    username = input("Username: ").strip()
    password = getpass.getpass("Password (not shown): ")

    real = attempt(host, username, password)
    print(f"real login:      {real}")
    if real.startswith("NO CONNECTION"):
        print("\nRESULT: FAIL (cannot reach SMB from the container)")
        return 1
    wrong = attempt(host, username, password + secrets.token_hex(4))
    print(f"wrong password:  {wrong}")
    nobody = attempt(host, f"nobody-{secrets.token_hex(4)}", secrets.token_hex(8))
    print(f"unknown user:    {nobody}")

    ok = real.startswith("ACCEPTED") and wrong.startswith("rejected") and nobody.startswith("rejected")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
