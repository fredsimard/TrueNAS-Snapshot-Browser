"""Entry point: python -m snapback, started as root (see workers.py for the processes)."""

from __future__ import annotations

import asyncio
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
from .tls import HostTLS
from .workers import SupervisedBackend, become, supervise

DEFAULT_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
WEB_UID = WEB_GID = 65534  # nobody
HTTPS_PORT = 8443  # in the container; plain HTTP stays on the configured port


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.geteuid() != 0:
        print("snapback: must start as root, to read files as each signed-in user", file=sys.stderr)
        return 2
    try:
        config = load_config()
        ops = Ops.from_config(config)
        accounts = HostAccounts()
        tls = HostTLS()
    except ConfigError as e:
        print(f"snapback: configuration error: {e}", file=sys.stderr)
        return 2
    static_dir = os.environ.get("SNAPBACK_STATIC_DIR") or DEFAULT_STATIC_DIR
    logging.getLogger("snapback").info(
        "serving HTTP on %d%s", config.port,
        f" and HTTPS on {HTTPS_PORT} with the mounted certificate" if tls.enabled else "; no certificate mounted",
    )

    web_end, supervisor_end = socket.socketpair()
    web_pid = os.fork()
    if web_pid == 0:
        code = 1
        try:
            supervisor_end.close()
            accounts.close()
            tls.close()  # the key is already loaded into tls.context, as root
            become(WEB_UID, WEB_GID, ())
            app = create_app(config, static_dir, backend=SupervisedBackend(web_end))
            listeners = [uvicorn.Config(app, host=config.host, port=config.port, server_header=False, proxy_headers=False)]
            if tls.enabled:
                listeners.append(uvicorn.Config(
                    app, host=config.host, port=HTTPS_PORT, server_header=False, proxy_headers=False,
                    ssl_context_factory=lambda _config, _default: tls.context,
                ))
            asyncio.run(serve_all(listeners))
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
        code = supervise(supervisor_end, ops, accounts, web_pid, tls=tls)
    finally:
        stop_web(web_pid)
    return code


async def serve_all(listeners: list[uvicorn.Config]) -> None:
    """One app behind each listener (HTTP, and HTTPS when there's a certificate), in one process so
    they share sessions. When one stops, the others stop too."""
    servers = [uvicorn.Server(c) for c in listeners]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


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
