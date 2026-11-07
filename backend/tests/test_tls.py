import datetime
import os
import socket
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from snapback.api import SESSION_COOKIE, create_app
from snapback.config import Config, ConfigError, RootConfig
from snapback.ops import Ops
from snapback.tls import HostTLS
from snapback.workers import InProcessBackend


def make_pair(tmp_path, name="nas.example.test"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    crt, pem = tmp_path / "tls.crt", tmp_path / "tls.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return crt, pem


def test_no_certificate_means_plain_http(tmp_path):
    tls = HostTLS(str(tmp_path / "tls.crt"), str(tmp_path / "tls.key"))
    assert not tls.enabled and not tls.changed()


def test_certificate_without_its_key_is_refused(tmp_path):
    crt, pem = make_pair(tmp_path)
    pem.unlink()
    with pytest.raises(ConfigError, match="tls.key: missing"):
        HostTLS(str(crt), str(pem))


def test_key_that_doesnt_match_is_refused(tmp_path):
    crt, pem = make_pair(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _, other_key = make_pair(other)
    with pytest.raises(ConfigError, match="TLS certificate"):
        HostTLS(str(crt), str(other_key))


def test_serves_tls_with_the_certificate(tmp_path):
    crt, pem = make_pair(tmp_path)
    tls = HostTLS(str(crt), str(pem))
    assert tls.enabled
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        with tls.context.wrap_socket(conn, server_side=True) as s:
            s.sendall(b"hello")

    t = threading.Thread(target=serve)
    t.start()
    client = ssl.create_default_context(cafile=str(crt))
    with socket.create_connection(("127.0.0.1", port)) as raw, client.wrap_socket(raw, server_hostname="nas.example.test") as s:
        assert s.recv(5) == b"hello"
        assert s.version() in ("TLSv1.2", "TLSv1.3")
    t.join()
    server.close()


@pytest.mark.parametrize("how", ["replace", "rewrite"])
def test_a_renewed_certificate_is_noticed(tmp_path, how):
    crt, pem = make_pair(tmp_path)
    tls = HostTLS(str(crt), str(pem))
    assert not tls.changed()
    if how == "replace":
        new = tmp_path / "new.crt"
        new.write_bytes(crt.read_bytes())
        os.replace(new, crt)
    else:
        with open(crt, "ab") as f:
            f.write(b"\n")
    assert tls.changed()


def test_cookie_is_secure_under_https(ds):
    (ds.mountpoint / "S").mkdir()
    config = Config(server_label="N", host="h", port=0, roots=(RootConfig("s", "S", str(ds.mountpoint / "S")),),
                    snapshot_prefixes=("auto-",))
    for secure in (True, False):
        app = create_app(config, verify=lambda u, p, s: set(s), backend=InProcessBackend(Ops.from_config(config)),
                         secure_cookies=secure)
        r = TestClient(app).post("/api/login", json={"username": "a", "password": "b"})
        cookie = r.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=") and ("Secure" in cookie) == secure
