"""A real SFTP server on 127.0.0.1, so the SFTP transport is tested rather than mocked.

Requirement D1's other half, and §4.12's rule that *"new transport tests run against local
servers ... so the suite's no-network, no-API-key guarantee holds."*  Verity and Craneware
both deliver over SFTP — ``DOC2-009`` for Verity's scheduled exports, and ``CRANEWARE-001``
first-hand for Craneware's *"delivered straight to their Secure File Transfer Protocol (SFTP)
folder"* — and neither will issue credentials to an assignment.  So the endpoint is built
here.

**This is a server, not a stub.**  ``paramiko.SFTPServer`` speaks the real protocol over a
real socket, which means :class:`~recon.connectors.sftp.SftpTransport` is exercised through
its actual key exchange, authentication and file handles.  A fake that returned canned
``Document`` objects would prove the code after the download and nothing before it, and
everything interesting about a transport is before the download.

**Deterministic keys, because §4.11 forbids an unseeded random stream.**  Both key pairs are
Ed25519 keys derived from ``blake2b`` over a label — the same construction the generators use
for every other random stream in this repository — and then wrapped in an OpenSSH private-key
container that is assembled by hand.  The hand-assembly is the point: ``cryptography``'s own
OpenSSH serialiser draws a random check integer, so its output differs byte for byte between
two calls that produce the *same key*.  That would leave "the key is deterministic, the file
holding it is not", which is exactly the kind of almost-true statement this repository's
determinism discipline exists to avoid.  With a fixed check integer the bytes are identical
run to run, and :func:`deterministic_key` is a pure function of its label.

No private key is committed.  The material is derived at run time from a label in this file,
which is not a secret and does not authenticate anything outside a loopback socket in a test.

**Loopback only, and read-only.**  The listener binds ``127.0.0.1`` on port 0, so the OS picks
a free port and two tests can never collide on one.  Writes, renames and deletions are
refused: a transport is a reader, and a mock that could be talked into writing would let a bug
in the thing under test corrupt the fixture it is being measured against.  Every remote path
is resolved and checked against the served directory, so ``..`` reaches nothing — the server
refuses the traversal even though the client already refuses to ask for it, because the point
of the client-side check is that a *hostile* server could offer such a name, and a mock that
could not offer one would make that check untestable.

**Shutdown is explicit and complete.**  ``filterwarnings = ["error"]`` is set repository-wide,
and an unclosed socket surfaces as a ``ResourceWarning`` raised inside whichever unrelated
test happens to trigger the collection.  So every accepted session is tracked, closed on
:meth:`LoopbackSftpServer.stop`, and its thread joined; the listener is closed before the
accept loop is joined so the loop cannot block on a socket nobody will connect to.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import os
import socket
import threading
from pathlib import Path, PurePosixPath
from types import TracebackType

import nacl.signing
import paramiko

__all__ = [
    "CLIENT_KEY_LABEL",
    "HOST_KEY_LABEL",
    "LOG_CHANNEL",
    "LoopbackSftpServer",
    "USERNAME",
    "deterministic_key",
]

#: Paramiko logs this server's side of each session here rather than on ``paramiko.transport``.
#:
#: Not tidiness.  A client that disconnects cleanly makes the server's read loop fail with
#: ``ECONNABORTED`` on Windows, which paramiko logs at ``ERROR``; with no handler configured
#: Python's last-resort handler writes it straight to ``stderr``, so a passing test prints a
#: line that reads like a failure.  A :class:`logging.NullHandler` on a channel of our own
#: stops that without silencing anything: the record still propagates, so a suite that
#: configures logging in order to debug this server still sees every line of it.
LOG_CHANNEL = "recon.mocks.sftp_server"

logging.getLogger(LOG_CHANNEL).addHandler(logging.NullHandler())

#: Labels the two key pairs are derived from.  Names, not secrets: the key is reproducible
#: from the label, which is the property a deterministic test needs and the property a real
#: credential must never have.
HOST_KEY_LABEL = "recon-loopback-sftp-host"
CLIENT_KEY_LABEL = "recon-loopback-sftp-client"

#: The only account this server knows.  A real SFTP credential carries its username beside
#: the key (``credentials.SshKeyCredential.username``); this is the local counterpart.
USERNAME = "recon"

#: Personalisation for the key-derivation hash, so a label used here and a label used by some
#: other seeded stream in this repository cannot derive the same bytes.
_KEY_PERSON = b"recon-sftp"

#: The OpenSSH private-key format stores a 32-bit integer twice as an "is it decrypted" check.
#: OpenSSH randomises it.  Fixed here, which is the whole reason this container is assembled
#: by hand -- see the module docstring.
_CHECK_INT = 0x5245434F

_OPENSSH_MAGIC = b"openssh-key-v1\x00"
_KEY_TYPE = "ssh-ed25519"

#: Seconds a stopping server waits for each of its own threads.  A bound on a hang, so a
#: failing test reports a failure rather than pytest never returning.
_JOIN_TIMEOUT = 5.0


def deterministic_key(label: str) -> paramiko.Ed25519Key:
    """An Ed25519 key that is a pure function of ``label``.

    ``blake2b`` over the label gives the 32-byte Ed25519 seed, which is the same
    canonical-string-to-seed construction the generators use everywhere else, so there is one
    determinism idiom in this repository rather than two.  Ed25519 is the only common SSH key
    type that can be derived this way at all: an RSA key is found by searching for primes, and
    ``RSAKey.generate`` gives no way to seed that search.

    The OpenSSH container around it is built here rather than by ``cryptography``'s
    serialiser, because that serialiser randomises the check integer and would make two calls
    with the same label produce different bytes for the same key.
    """
    seed = hashlib.blake2b(
        label.encode("utf-8"), digest_size=32, person=_KEY_PERSON
    ).digest()
    return paramiko.Ed25519Key.from_private_key(_openssh_private_key(seed, label))


def _openssh_private_key(seed: bytes, comment: str) -> io.StringIO:
    """The seed as an OpenSSH private key file object, byte-identical for a given seed.

    The layout is OpenSSH's ``PROTOCOL.key``: a magic string, the cipher/KDF names (``none``,
    since this is unencrypted), one public key blob, then the private section — the check
    integer twice, the key type, the public half, the 64-byte signing key, a comment, and
    padding to an 8-byte boundary with the bytes 1, 2, 3...
    """
    public = bytes(nacl.signing.SigningKey(seed).verify_key)

    public_blob = paramiko.Message()
    public_blob.add_string(_KEY_TYPE)
    public_blob.add_string(public)

    private = paramiko.Message()
    private.add_int(_CHECK_INT)
    private.add_int(_CHECK_INT)
    private.add_string(_KEY_TYPE)
    private.add_string(public)
    private.add_string(seed + public)
    private.add_string(comment)
    body = bytearray(private.asbytes())
    pad = 1
    while len(body) % 8:
        body.append(pad)
        pad += 1

    container = paramiko.Message()
    container.add_string("none")  # cipher
    container.add_string("none")  # kdf
    container.add_string("")  # kdf options
    container.add_int(1)  # one key
    container.add_string(public_blob.asbytes())
    container.add_string(bytes(body))

    encoded = base64.b64encode(_OPENSSH_MAGIC + container.asbytes()).decode("ascii")
    lines = "\n".join(encoded[at : at + 70] for at in range(0, len(encoded), 70))
    return io.StringIO(
        f"-----BEGIN OPENSSH PRIVATE KEY-----\n{lines}\n-----END OPENSSH PRIVATE KEY-----\n"
    )


class _ReadOnlyFiles(paramiko.SFTPServerInterface):
    """Serve one directory, read-only, with no way out of it.

    Confinement is checked on the resolved real path, not on the requested string.  A check on
    the string would be defeated by a symlink inside the served directory, and a temporary
    directory on macOS is itself reached through one (``/var`` -> ``/private/var``), so the
    served root is resolved the same way before the comparison.
    """

    def __init__(self, server: paramiko.ServerInterface, *, root: Path) -> None:
        super().__init__(server)
        self._root = Path(root).resolve()

    # --- confinement -----------------------------------------------------

    def _resolve(self, path: str) -> Path | None:
        """The local path for a remote one, or ``None`` if it escapes the served directory."""
        relative = PurePosixPath(path.replace("\\", "/"))
        parts = [part for part in relative.parts if part not in ("/", ".")]
        if any(part == ".." for part in parts):
            return None
        candidate = self._root.joinpath(*parts).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            return None
        return candidate

    # --- the read surface ------------------------------------------------

    def canonicalize(self, path: str) -> str:
        resolved = self._resolve(path)
        if resolved is None:
            return "/"
        if resolved == self._root:
            return "/"
        return "/" + resolved.relative_to(self._root).as_posix()

    def list_folder(self, path: str):
        resolved = self._resolve(path)
        if resolved is None or not resolved.is_dir():
            return paramiko.SFTP_NO_SUCH_FILE
        listing = []
        for child in sorted(resolved.iterdir(), key=lambda item: item.name):
            attributes = paramiko.SFTPAttributes.from_stat(child.stat())
            attributes.filename = child.name
            listing.append(attributes)
        return listing

    def stat(self, path: str):
        resolved = self._resolve(path)
        if resolved is None or not resolved.exists():
            return paramiko.SFTP_NO_SUCH_FILE
        return paramiko.SFTPAttributes.from_stat(resolved.stat())

    def lstat(self, path: str):
        return self.stat(path)

    def open(self, path: str, flags: int, attr):
        """Open for reading only.  Any write intent is refused, not quietly downgraded."""
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            return paramiko.SFTP_PERMISSION_DENIED
        resolved = self._resolve(path)
        if resolved is None or not resolved.is_file():
            return paramiko.SFTP_NO_SUCH_FILE
        handle = paramiko.SFTPHandle(flags)
        # `readfile` is the attribute `SFTPHandle.read` looks for; setting it is paramiko's
        # documented way to back a handle with an open file.
        handle.readfile = resolved.open("rb")  # noqa: SIM115 - closed by SFTPHandle.close
        handle.filename = path
        return handle

    # --- the write surface, refused --------------------------------------

    def remove(self, path: str):
        return paramiko.SFTP_PERMISSION_DENIED

    def rename(self, oldpath: str, newpath: str):
        return paramiko.SFTP_PERMISSION_DENIED

    def mkdir(self, path: str, attr):
        return paramiko.SFTP_PERMISSION_DENIED

    def rmdir(self, path: str):
        return paramiko.SFTP_PERMISSION_DENIED

    def chattr(self, path: str, attr):
        return paramiko.SFTP_PERMISSION_DENIED

    def symlink(self, target_path: str, path: str):
        return paramiko.SFTP_PERMISSION_DENIED


class _OneKeyServer(paramiko.ServerInterface):
    """Public-key authentication against exactly one enrolled key, and nothing else.

    No password method is offered.  A mock that accepted a password would make
    ``SftpTransport``'s key handling optional in the only place it is ever tested.
    """

    def __init__(self, username: str, authorized_key: paramiko.PKey) -> None:
        self._username = username
        self._authorized_key = authorized_key

    def get_allowed_auths(self, username: str) -> str:
        return "publickey"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        # Constant-time over the wire format, matching the discipline `Secret.__eq__` follows.
        # A timing side channel on a loopback mock is not a real threat; comparing the same
        # way everywhere is how the habit survives into somewhere it is.
        matches = hmac.compare_digest(key.asbytes(), self._authorized_key.asbytes())
        if username == self._username and matches:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class LoopbackSftpServer:
    """An SFTP endpoint on 127.0.0.1 serving one directory, started and stopped by a test.

    Use it as a context manager, or call :meth:`start` and :meth:`stop`.  The port is assigned
    by the OS — bind to 0, read :attr:`port` back — so a suite running two of these, or two
    suites running at once, cannot collide on a fixed number.

    The keys default to :data:`HOST_KEY_LABEL` and :data:`CLIENT_KEY_LABEL` through
    :func:`deterministic_key`, so a test that wants the client half asks for it by label and
    gets the same bytes every run.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        root: Path | str,
        *,
        username: str = USERNAME,
        authorized_key: paramiko.PKey | None = None,
        host_key: paramiko.PKey | None = None,
    ) -> None:
        self._root = Path(root)
        self.username = username
        self.authorized_key = (
            deterministic_key(CLIENT_KEY_LABEL) if authorized_key is None else authorized_key
        )
        self.host_key = deterministic_key(HOST_KEY_LABEL) if host_key is None else host_key
        self._listener: socket.socket | None = None
        self._port: int | None = None
        self._accept_thread: threading.Thread | None = None
        self._sessions: list[paramiko.Transport] = []
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    # --- lifecycle -------------------------------------------------------

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("the server is not running; call start() first")
        return self._port

    def start(self) -> "LoopbackSftpServer":
        if self._listener is not None:
            raise RuntimeError("the server is already running")
        if not self._root.is_dir():
            raise NotADirectoryError(f"{self._root} is not a directory to serve")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # No SO_REUSEADDR.  It exists to reclaim a port in TIME_WAIT, and the whole point of
        # binding to 0 is that this server never asks for a particular port.
        listener.bind((self.host, 0))
        listener.listen(8)
        # A timeout on accept() is what lets the loop notice `_stopping` on a shutdown that
        # races a connection. Without it the thread blocks in accept() forever and stop()
        # joins a thread that will never return.
        listener.settimeout(0.2)
        self._listener = listener
        self._port = listener.getsockname()[1]
        self._stopping.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="recon-loopback-sftp", daemon=True
        )
        self._accept_thread.start()
        return self

    def stop(self) -> None:
        """Close everything this server opened, in the order that cannot deadlock.

        The listener first, so the accept loop stops taking new work; then the loop's thread;
        then every session, each of which closes its own socket and joins paramiko's threads.
        Closing sessions first would let the loop accept one more connection after the list
        had been drained, and that connection would outlive the test that made it.
        """
        self._stopping.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=_JOIN_TIMEOUT)
            self._accept_thread = None
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()
            # paramiko.Transport is itself a Thread; closing asks it to stop, joining is what
            # makes "stopped" true before the test looks for leaked threads or sockets.
            session.join(timeout=_JOIN_TIMEOUT)
        self._port = None

    def __enter__(self) -> "LoopbackSftpServer":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    # --- serving ---------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                # The listener was closed underneath us, which is how stop() ends this loop.
                return
            self._serve(connection)

    def _serve(self, connection: socket.socket) -> None:
        """Hand one accepted socket to paramiko, or close it if the session cannot start.

        Every failure path closes the socket explicitly.  A session that raised on
        ``start_server`` and left its socket to the garbage collector is precisely the
        ``ResourceWarning`` this repository's ``filterwarnings = ["error"]`` turns into a
        failure somewhere else entirely.
        """
        session = paramiko.Transport(connection)
        session.set_log_channel(LOG_CHANNEL)
        session.add_server_key(self.host_key)
        session.set_subsystem_handler(
            "sftp", paramiko.SFTPServer, _ReadOnlyFiles, root=self._root
        )
        try:
            session.start_server(server=_OneKeyServer(self.username, self.authorized_key))
        except (paramiko.SSHException, EOFError, OSError):
            # A client that failed or abandoned the handshake is a case under test, not an
            # error here. Both ends are closed rather than left for the collector.
            session.close()
            connection.close()
            return
        with self._lock:
            if self._stopping.is_set():
                session.close()
                connection.close()
                return
            # Finished sessions are dropped as new ones arrive. A paramiko Transport closes
            # its own socket when its thread exits, so this is about not holding a reference
            # to every session a long test ever made, not about leaking handles.
            self._sessions = [live for live in self._sessions if live.is_active()]
            self._sessions.append(session)

def client_private_key_text(label: str = CLIENT_KEY_LABEL) -> str:
    """The client key as OpenSSH text, for a test that must hand it to a credential.

    ``paramiko.Ed25519Key`` has no ``write_private_key`` in paramiko 5 — it can read the
    OpenSSH container but not emit one — so the only honest way to get the same bytes back
    out is the builder that made them. Exposed here rather than reached for privately,
    because a test importing an underscore-prefixed function is a test that breaks the next
    time this module is tidied.
    """
    seed = hashlib.blake2b(
        label.encode("utf-8"), digest_size=32, person=_KEY_PERSON
    ).digest()
    return _openssh_private_key(seed, label).getvalue()
