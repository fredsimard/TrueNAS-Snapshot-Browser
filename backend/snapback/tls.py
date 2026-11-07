"""HTTPS with the NAS's own certificate, when one is mounted; plain HTTP otherwise.

TrueNAS writes each certificate it holds to /etc/certificates/<name>.crt and .key (the key is
readable by root only). Mount the pair the NAS uses for its own name at TLS_CERT and TLS_KEY.

The key is read once, as root, before the web process drops to nobody. TrueNAS rewrites these files
when a certificate is renewed, so the files are held open and watched: once either changes, the
supervisor exits and the container restarts with the new certificate, as it does for accounts.
"""

from __future__ import annotations

import os
import ssl

from .config import ConfigError

TLS_CERT = "/host/tls/tls.crt"
TLS_KEY = "/host/tls/tls.key"


class HostTLS:
    def __init__(self, cert_path: str = TLS_CERT, key_path: str = TLS_KEY):
        self.context: ssl.SSLContext | None = None
        self._watched: list[tuple[int, tuple[int, int, int]]] = []
        present = [os.path.exists(p) for p in (cert_path, key_path)]
        if not any(present):
            return
        if not all(present):
            missing = cert_path if not present[0] else key_path
            raise ConfigError(f"{missing}: missing. Mount both the certificate and its key, or neither for plain HTTP.")
        try:
            for path in (cert_path, key_path):
                fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                self._watched.append((fd, _signature(os.fstat(fd))))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        except (OSError, ssl.SSLError) as e:
            self.close()
            raise ConfigError(f"TLS certificate: {e}") from None
        self.context = ctx

    @property
    def enabled(self) -> bool:
        return self.context is not None

    def changed(self) -> bool:
        """True once either file was replaced or rewritten since it was loaded."""
        for fd, sig in self._watched:
            st = os.fstat(fd)
            if st.st_nlink == 0 or _signature(st) != sig:
                return True
        return False

    def close(self) -> None:
        for fd, _ in self._watched:
            os.close(fd)
        self._watched = []


def _signature(st: os.stat_result) -> tuple[int, int, int]:
    return (st.st_ino, st.st_mtime_ns, st.st_size)
