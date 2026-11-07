#!/usr/bin/env python3
"""Local development server: no SMB, sign in with demo / demo.

Fake sign-in lives here and not behind a flag in the app, so a deployed image can't enable it.

    set -a; . ../dev/demo/snapshots-browser.env; set +a; python scripts/dev_server.py
"""

from __future__ import annotations

import logging
import os

import uvicorn

from snapshots_browser.api import create_app
from snapshots_browser.config import load_config
from snapshots_browser.ops import Ops
from snapshots_browser.workers import InProcessBackend


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config({"ALLOWED_USERS": "all", **os.environ, "SMB_HOST": "unused.invalid"})

    def sign_in(username: str, password: str, shares: list[str]) -> set[str] | None:
        return set(shares) if (username, password) == ("demo", "demo") else None

    # Reads files as whoever runs this script: no privilege drop.
    app = create_app(config, verify=sign_in, backend=InProcessBackend(Ops.from_config(config)))
    uvicorn.run(app, host="127.0.0.1", port=config.port)


if __name__ == "__main__":
    main()
