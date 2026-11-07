#!/usr/bin/env python3
"""Local development server: no SMB, sign in with demo / demo.

Fake sign-in lives here and not behind a flag in the app, so a deployed image can't enable it.

    SNAPSHOT_PREFIXES=auto- SNAPBACK_CONFIG=../dev/demo/snapback.yaml python scripts/dev_server.py
"""

from __future__ import annotations

import logging
import os

import uvicorn

from snapback.api import create_app
from snapback.config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config({**os.environ, "SMB_HOST": "unused.invalid"})
    app = create_app(config, verify=lambda username, password: (username, password) == ("demo", "demo"))
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
