"""Entry point: python -m snapback"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .api import create_app
from .config import ConfigError, load_config

DEFAULT_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config()
        app = create_app(config, os.environ.get("SNAPBACK_STATIC_DIR") or DEFAULT_STATIC_DIR)
    except ConfigError as e:
        print(f"snapback: configuration error: {e}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=config.host, port=config.port, server_header=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
