"""Entry point: python -m snapback, started as root (see workers.py for the processes)."""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time

import uvicorn

from .api import create_app
from .config import ConfigError, load_config
from .identity import HostAccounts
from .ops import Ops
from .workers import SupervisedBackend, become, supervise

DEFAULT_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
WEB_UID = WEB_GID = 65534  # nobody


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.geteuid() != 0:
        print("snapback: must start as root, to read files as each signed-in user", file=sys.stderr)
        return 2
    try:
        config = load_config()
        ops = Ops.from_config(config)
        accounts = HostAccounts()
    except ConfigError as e:
        print(f"snapback: configuration error: {e}", file=sys.stderr)
        return 2
    static_dir = os.environ.get("SNAPBACK_STATIC_DIR") or DEFAULT_STATIC_DIR

    web_end, supervisor_end = socket.socketpair()
    web_pid = os.fork()
    if web_pid == 0:
        code = 1
        try:
            supervisor_end.close()
            accounts.close()
            become(WEB_UID, WEB_GID, ())
            app = create_app(config, static_dir, backend=SupervisedBackend(web_end))
            uvicorn.run(app, host=config.host, port=config.port, server_header=False, proxy_headers=False)
            code = 0
        except BaseException:
            logging.getLogger("snapback").exception("web process failed")
        finally:
            os._exit(code)

    web_end.close()

    def stop(signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code = 0
    try:
        code = supervise(supervisor_end, ops, accounts, web_pid)
    finally:
        stop_web(web_pid)
    return code


def stop_web(pid: int, grace_seconds: float = 5.0) -> None:
    """Let the web process finish its requests; the container's end takes the workers with it."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.1)


if __name__ == "__main__":
    sys.exit(main())
