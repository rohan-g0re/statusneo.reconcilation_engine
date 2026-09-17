"""The transport both file-based vendors actually run, and the first one that dials out.

Requirement A2's second implementation, plus the transport half of A4.  ``DOC2-009`` puts
Verity's published exports on **SFTP** with a daily/weekly/monthly cadence, ``DOC2-010``
specifies how to receive them — *"direct file landing, checksum, file-level idempotency and
source-control totals"* — and ``CRANEWARE-001`` is Craneware's own announcement saying the
same thing first-hand: reports *"delivered straight to their Secure File Transfer Protocol
(SFTP) folder"*.  ``DOC2-013`` is the reason there is one module here rather than two:
*"both can share the same secure-file connector framework."*  Verity and Craneware differ by
a registry row and a field mapping, never by a transport.

**Everything that made ``LocalDirectoryTransport`` safe has to be re-earned here, because
none of it was free.**  A directory could not reach ground truth by construction; a remote
server can return any filename it likes, including one spelled ``../``.  So the root is
checked once at construction, each source's endpoint is checked against that root at every
fetch, and every name — whether we declared it or the server offered it — goes through the
same bare-filename rule ``transport.py`` applies.  That function is imported rather than
restated: two copies of a prohibition drift, and the copy that drifts is the one nobody
re-reads.

**One call opens the connection and closes it.**  :meth:`SftpTransport.fetch` returns a
tuple, not a generator, which is the one place this deliberately differs from
``LocalDirectoryTransport``.  A generator would hold an SSH session open across the
consumer's loop, and a consumer that broke out of that loop would leave a socket and
paramiko's own threads alive until the garbage collector noticed — which, under this
repository's ``filterwarnings = ["error"]``, surfaces as a ``ResourceWarning`` raised inside
whatever unrelated test happened to trigger the collection.  A transport whose failure mode
is "some other test fails later" is worse than one that holds a few documents in memory.

**The credential is revealed at the paramiko call and nowhere else.**  This module is the
first production caller of :meth:`~recon.connectors.credentials.Secret.reveal` in the
repository, which is the whole point of that method being named to be greppable: the list of
places this process can emit a credential is the list of ``.reveal()`` call sites, and until
now that list was empty.  There are three here, all inside an argument list — never assigned
to a local, never put in a dict, never logged.

**Nothing falls back to the operator's own keys.**  ``allow_agent`` and ``look_for_keys`` are
both off.  Left at their defaults, paramiko tries ``~/.ssh`` and any running SSH agent after
the supplied key fails, so a source could authenticate with a credential nobody registered —
and it would work on the developer's machine and nowhere else, which is the slowest possible
way to find out.

**A failed fetch fails loudly, by name.**  No retry, no backoff, no reconnect loop: Doc 2
files those under step 6, production hardening, and this build stops at step 5.  A wrong
credential produces :class:`SftpAuthenticationError` naming the ref, the account and the
variables to set — A3's acceptance is *"a clear named failure, not a stack trace"* — and the
paramiko exception behind an authentication or key-parsing failure is deliberately **not**
chained, because a key parser's own message can quote the material it failed on.  Network and
I/O failures *are* chained, because there the underlying message is the entire diagnostic.

**No clock is read here.**  §4.11 permits wall-clock in the checkpoint and in logs, nowhere
else.  The ``modified_at`` handed to ``should_fetch`` is the server's ``st_mtime`` rendered
as text; the connect timeout is a socket deadline, not a reading of the current time.
"""

from __future__ import annotations

import fnmatch
import io
import stat as stat_module
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from recon import config
from recon.connectors import credentials
from recon.connectors.transport import (
    Document,
    ForbiddenPathError,
    TransportError,
    _reject_unsafe_name,
)

try:
    import paramiko
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "recon.connectors.sftp needs paramiko, which is the 'connectors' optional extra: "
        "pip install -e '.[connectors]'. SFTP is a protocol rather than a file format, so "
        "there is no stdlib client to fall back to and no way to degrade gracefully."
    ) from exc

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from recon.connectors.registry import Source

__all__ = [
    "ShouldFetch",
    "SftpAuthenticationError",
    "SftpConnectionError",
    "SftpDecodeError",
    "SftpDownloadError",
    "SftpError",
    "SftpListingError",
    "SftpTransport",
]

#: The predicate wave 3's checkpoint module supplies to skip a document already fetched.
#:
#: ``(document_name, modified_at) -> bool``, where ``modified_at`` is the remote modification
#: time as ``YYYY-MM-DDTHH:MM:SSZ`` or ``None`` when the server reports none.  Declared as a
#: type alias here rather than imported from ``checkpoint.py`` **on purpose**: this module
#: must not depend on the checkpoint, only offer it somewhere to stand.  A4's acceptance is
#: that a second run downloads zero bytes, so the predicate is consulted after the listing
#: and before the download — the listing is one round trip and is what supplies the mtime the
#: predicate needs to answer at all.
ShouldFetch = Callable[[str, "str | None"], bool]

#: Key types offered for inline key material, most modern first.  Paramiko's ``PKey`` base
#: class cannot detect a type from a file object — ``PKey.from_path`` can, but inline material
#: has no path and writing it to one to find out would put a private key on disk — so the
#: types are tried in order.  DSA is absent because it is deprecated everywhere and an unused
#: member is an invitation to authenticate a vendor a way nobody verified it supports.
_KEY_CLASSES: tuple[type, ...] = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)

#: Characters that make a ``filenames`` entry a pattern rather than a literal name.
_GLOB_MARKERS = ("*", "?", "[")

#: ``st_mtime`` rendered the way every other timestamp in this repository is spelled.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# --- failures --------------------------------------------------------------


class SftpError(TransportError):
    """A fetch over SFTP failed.  A :class:`~recon.connectors.transport.TransportError`.

    Subclassed by remedy, not by layer.  Each of the five below answers a different question
    from the operator staring at it, and collapsing them would mean answering none of them.
    """


class SftpConnectionError(SftpError):
    """The session never came up: no route, refused, timed out, or the host key was wrong.

    Remedy: the network, the port, or the pinned host key — never the credential.  Kept
    separate from :class:`SftpAuthenticationError` because "we cannot reach them" and "they
    will not have us" are different outages with different people to call.
    """


class SftpAuthenticationError(SftpError):
    """The server refused the credential, or the key material is not usable as a key.

    A3's acceptance criterion, stated as a class: a wrong credential produces this, naming
    the ref, the account and the variables to set, and never a paramiko traceback.
    """


class SftpListingError(SftpError):
    """The remote directory could not be listed.

    Almost always a source row pointing at a path that does not exist on the server, or an
    account without read permission on it.  Distinguished from a download failure because
    this one means *nothing* arrived, which downstream looks exactly like a clean empty run.
    """


class SftpDownloadError(SftpError):
    """A file was listed and then could not be read.

    The interesting case is a vendor writing an export in place: it appears in the listing
    and is gone, truncated or locked a moment later.  ``DOC2-010`` asks for *"direct file
    landing"* precisely because of this, and the honest response while retry is out of scope
    is to fail by name and be re-run by hand.
    """


class SftpDecodeError(SftpError):
    """A file arrived and is not UTF-8 text.

    A payload fact, not a transport fact.  Every format this fabric splits is text
    (``registry.PayloadFormat``), so a vendor that started shipping something else has
    changed the contract, and that is a conversation rather than a reconnect.
    """


# --- names and paths -------------------------------------------------------


def _reject_unsafe_remote_name(name: str, *, origin: str) -> None:
    """Apply ``transport.py``'s bare-filename rule to a name that came off the wire.

    ``_reject_unsafe_name`` is imported rather than reimplemented so the two transports
    cannot drift apart, with one extra check in front of it: separators are rejected
    explicitly for *both* conventions.  On a POSIX host ``Path("a\\\\b")`` is a single part,
    so the shared rule alone would wave through a remote name containing a backslash, and a
    server is free to send one.  ``origin`` says whether we asked for this name or the server
    offered it, because those are different problems.
    """
    if "/" in name or "\\" in name:
        raise ForbiddenPathError(
            f"{origin} name {name!r} contains a path separator; a transport may not traverse "
            "directories, and a remote server may not talk it into one"
        )
    _reject_unsafe_name(name)


def _remote_directory(value: str | Path, *, origin: str) -> PurePosixPath:
    """Normalise a remote directory and refuse the two things it may never contain.

    Remote paths are POSIX, so this deliberately does not use :class:`~pathlib.Path`, whose
    meaning changes with the machine the client happens to run on.  Backslashes are folded to
    ``/`` first: a caller passing a local Windows path — which is exactly what a test pointing
    this at a temporary directory does — would otherwise arrive as one opaque component, and
    the ground-truth check would look at it, see one part, and find nothing to refuse.
    """
    text = str(value)
    candidate = PurePosixPath(text.replace("\\", "/"))
    parts = candidate.parts
    if config.TRUTH_SUBDIR in parts:
        raise ForbiddenPathError(
            f"{origin} {text!r} is inside the ground-truth directory. Ingestion is forbidden "
            "to read truth/, and that prohibition is what makes the crosswalk a measurement "
            "rather than a claim. A transport that dials out does not get an exemption."
        )
    if ".." in parts:
        raise ForbiddenPathError(
            f"{origin} {text!r} traverses upwards; a transport's reach is fixed at "
            "construction and a source may not widen it"
        )
    return candidate


@dataclass(frozen=True, slots=True)
class _RemoteFile:
    """One entry from a remote listing: the name, and when the server says it changed."""

    name: str
    modified_at: str | None


def _modified_at(attributes: "paramiko.SFTPAttributes") -> str | None:
    """The server's ``st_mtime`` as text, or ``None`` when it did not report one.

    Text rather than an integer because it is destined for a checkpoint row, and every other
    timestamp this repository persists is ISO-8601 with a ``Z``.  The shape also compares
    lexicographically in the same order as the epoch seconds it came from, so a checkpoint can
    ask "newer than what I have" with a string comparison.

    Not a clock reading: the number belongs to the remote server.  §4.11 confines wall-clock
    to the checkpoint and to logs, and this module reads neither.
    """
    stamp = getattr(attributes, "st_mtime", None)
    if stamp is None:
        return None
    return datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime(_TIMESTAMP_FORMAT)


def _decode(raw: bytes, name: str, source_id: str) -> str:
    """UTF-8 text with newlines folded, matching ``Path.read_text`` byte for byte.

    The folding is the part that is easy to miss and expensive to discover.  A2's acceptance
    is that *the same suite* passes with this transport substituted, and
    ``LocalDirectoryTransport`` reads through Python's text mode, which translates ``\\r\\n``
    to ``\\n``.  An SFTP read is binary.  Without this, a vendor file with Windows line endings
    would produce a :class:`~recon.connectors.transport.Document` whose text differs from the
    local one by a character per line, and every digest taken over it would disagree for a
    reason no assertion message would name.

    The failure is carried out of the handler as a string and raised after it, which is the
    shape ``credentials._load_entry`` arrived at and for the same reason read against a
    different payload.  ``UnicodeDecodeError.object`` is the **entire file**, and ``raise ...
    from None`` would leave ``__context__`` pointing at it — so any reporter that walks an
    exception chain, including pytest's own rendering, would print a vendor's whole export
    into a log because one byte in it was not UTF-8.
    """
    failure: str | None = None
    text = ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Position and reason only. `exc.object` -- the file -- is deliberately never read.
        failure = (
            f"source {source_id!r}: remote file {name!r} is not UTF-8 text (byte {exc.start} "
            f"of {len(raw)}: {exc.reason}). Every payload format this fabric splits is text, "
            "so this is a changed vendor contract rather than a transfer fault."
        )
    if failure is not None:
        raise SftpDecodeError(failure)
    return text.replace("\r\n", "\n").replace("\r", "\n")


# --- the transport ---------------------------------------------------------


class SftpTransport:
    """Fetch documents from one SFTP account, confined to one remote directory tree.

    Construction fixes the reach: a host, a port, and a root the sources bound to this
    transport may not point outside of.  ``LocalDirectoryTransport`` resolves its root once
    for the same reason, and the reason matters more here — a directory argument could not
    reach ``truth/`` by accident, while a string sent to a remote server could.

    The root is the **first positional parameter** and is validated before anything else, so
    the §4.9 refusal cannot be shadowed by an unrelated argument error.  ``host`` is keyword
    with no default purely so that ordering holds; a transport with no host is still refused,
    one line later.
    """

    kind = "SFTP"

    __slots__ = (
        "_connect_timeout",
        "_env",
        "_host",
        "_host_key",
        "_port",
        "_root",
        "_secrets_file",
    )

    def __init__(
        self,
        root: str | Path = "/",
        *,
        host: str | None = None,
        port: int = 22,
        host_key: "paramiko.PKey | None" = None,
        connect_timeout: float = 15.0,
        env: Mapping[str, str] | None = None,
        secrets_file: Path | str | None = None,
    ) -> None:
        """Bind a reach, not a session.  Nothing connects until :meth:`fetch` is called.

        Args:
            root: the remote directory tree this transport may read, POSIX-spelled.
            host: the SFTP host.  Required; see the class docstring for why it is keyword.
            port: the SFTP port.
            host_key: the server's expected host key, pinned.  When omitted the operator's
                ``known_hosts`` is consulted and an unrecognised server is **refused** — the
                one policy paramiko offers that never trusts a key it has not seen before.
                ``AutoAddPolicy`` is what this deliberately is not: it turns a
                man-in-the-middle into a successful fetch with no signal anywhere.
            connect_timeout: seconds allowed for the socket, the banner and the
                authentication exchange.  A bound on a hang, not a reading of the clock.
            env: environment to resolve the credential from.  Defaults to ``os.environ``.
            secrets_file: overrides the connector secrets file location.
        """
        self._root = _remote_directory(root, origin="transport root")
        if not isinstance(host, str) or not host.strip():
            raise ValueError(
                "SftpTransport needs a host. It is keyword-only and has no default so that "
                "the root is checked first -- a forbidden path must be refused as a "
                "forbidden path, not reported as a missing argument."
            )
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"port must be an integer between 1 and 65535, got {port!r}")
        self._host = host.strip()
        self._port = port
        self._host_key = host_key
        self._connect_timeout = float(connect_timeout)
        self._env = env
        self._secrets_file = secrets_file

    # --- what it is ------------------------------------------------------

    @property
    def root(self) -> PurePosixPath:
        return self._root

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"SftpTransport({str(self._root)!r}, host={self._host!r}, port={self._port})"

    # --- the one method --------------------------------------------------

    def fetch(
        self,
        source: "Source",
        *,
        should_fetch: ShouldFetch | None = None,
    ) -> Iterable[Document]:
        """Every document this source currently offers, in a stable order.

        Args:
            source: the registry row.  ``endpoint`` is the remote directory, ``filenames``
                are the names or patterns to take from it, ``credential_ref`` names the SSH
                key.
            should_fetch: optional predicate ``(name, modified_at) -> bool``.  Returning
                ``False`` skips the file **before** it is opened, which is what makes A4's
                *"the second run downloads zero bytes"* true rather than approximately true.
                Omitted, every match is fetched.

        Returns:
            A tuple, not a generator.  See the module docstring: the session is opened and
            closed inside this call so an abandoned iterator cannot leak one.

        Raises:
            ForbiddenPathError: the endpoint escapes the root, or a name — ours or the
                server's — is not a bare filename.
            SftpConnectionError, SftpAuthenticationError, SftpListingError,
            SftpDownloadError, SftpDecodeError: named by remedy, never swallowed.
        """
        remote_dir = self._endpoint_of(source)
        credential = self._credential_for(source)
        client = self._connect(source, credential)
        try:
            session = client.open_sftp()
            try:
                listing = self._list(session, remote_dir, source)
                documents: list[Document] = []
                for remote in self._select(source, listing):
                    if should_fetch is not None and not should_fetch(
                        remote.name, remote.modified_at
                    ):
                        continue
                    raw = self._read(session, remote_dir, remote.name, source)
                    text = _decode(raw, remote.name, source.source_id)
                    documents.append(
                        Document(
                            name=remote.name,
                            text=text,
                            modified_at=remote.modified_at,
                        )
                    )
                return tuple(documents)
            finally:
                session.close()
        finally:
            client.close()

    # --- steps -----------------------------------------------------------

    def _endpoint_of(self, source: "Source") -> PurePosixPath:
        """The source's remote directory, checked against the root fixed at construction."""
        endpoint = _remote_directory(
            source.endpoint, origin=f"source {source.source_id!r} endpoint"
        )
        if endpoint != self._root and self._root not in endpoint.parents:
            raise ForbiddenPathError(
                f"source {source.source_id!r} points at {source.endpoint!r}, which is outside "
                f"{self._root}. A source cannot widen its own transport's reach; register a "
                "second transport if it genuinely needs a different tree."
            )
        return endpoint

    def _credential_for(self, source: "Source") -> credentials.SshKeyCredential:
        """Resolve the SSH key this source names.  The ref is a name; this is the value.

        ``kind`` is stated rather than inferred, which is what
        ``credentials.resolve``'s own docstring asks a transport to do: a transport knows
        what shape it needs, and saying so is what turns "nothing configured" into a message
        naming the right variables instead of a shrug.
        """
        if not source.credential_ref:
            raise SftpAuthenticationError(
                f"source {source.source_id!r} has no credential_ref. An SFTP source "
                "authenticates with an SSH key, and the registry row names which one -- the "
                "name is the thing that is safe to commit."
            )
        credential = credentials.resolve_ssh_key(
            source.credential_ref, env=self._env, secrets_file=self._secrets_file
        )
        if not credential.username:
            variable = credentials.env_var_names(
                source.credential_ref, credentials.CredentialKind.SSH_KEY
            )[0]
            raise SftpAuthenticationError(
                f"credential {source.credential_ref!r} carries no SSH username. It is issued "
                "with the key and useless without it, so it lives beside the key: set "
                f"{variable}, or add that field to the connector secrets file."
            )
        return credential

    def _inline_key(self, credential: credentials.SshKeyCredential) -> "paramiko.PKey | None":
        """Load inline key material, trying each key type.  ``None`` when a path was supplied.

        Two of this module's three ``.reveal()`` calls are here, both inside an argument
        list.  The material does land in a :class:`io.StringIO` — paramiko's loaders take a
        file object and nothing else — and that buffer is closed on every path out, including
        the failing ones, so the window is one call long rather than one process long.

        Every failure is carried out of the handler as a string and raised once the handler
        has exited, never ``raise ... from None`` inside it.  ``from None`` clears
        ``__cause__`` and leaves ``__context__`` pointing at the exception a key parser
        raised while it had the private key in its hands — and this repository has already
        been bitten by exactly that chain, in ``credentials._load_entry``.
        """
        if credential.key_material is None:
            return None
        failure: str | None = None
        buffer = io.StringIO(credential.key_material.reveal())
        try:
            for key_class in _KEY_CLASSES:
                buffer.seek(0)
                try:
                    return key_class.from_private_key(
                        buffer,
                        password=(
                            None
                            if credential.passphrase is None
                            else credential.passphrase.reveal()
                        ),
                    )
                except paramiko.PasswordRequiredException:
                    failure = (
                        f"credential {credential.credential_ref!r}: the inline SSH key is "
                        "encrypted and no passphrase was resolved. Set the passphrase "
                        "variable for this ref, or supply an unencrypted key."
                    )
                    break
                except paramiko.SSHException:
                    continue
        finally:
            buffer.close()
        if failure is None:
            failure = (
                f"credential {credential.credential_ref!r}: the inline SSH key material is "
                f"not in a format paramiko recognises (tried "
                f"{', '.join(cls.__name__ for cls in _KEY_CLASSES)}). The value is described "
                "here and never echoed -- requirement A3."
            )
        raise SftpAuthenticationError(failure)

    def _connect(
        self, source: "Source", credential: credentials.SshKeyCredential
    ) -> "paramiko.SSHClient":
        """Open one authenticated session, or fail by name.

        Every paramiko failure is caught and re-raised as one of this module's errors, and
        every raise happens **after** the handler has exited rather than inside it.  That is
        the shape ``credentials._load_entry`` had to be rewritten into: ``raise ... from
        None`` clears ``__cause__`` but leaves ``__context__`` pointing at the original, so
        an error reporter that walks attributes — Sentry, a ``repr(vars(exc))`` debug line,
        pytest's own chained-traceback rendering — still reaches whatever the key parser was
        holding.  Outside the handler there is no exception in flight and there is no chain.

        The chaining is then deliberately split.  An authentication or key failure carries no
        cause at all, because A3's "wrong credential" case answered by printing the
        credential would be the worst available outcome.  A connection failure sets an
        explicit cause, because there the underlying text — refused, unreachable, timed out —
        is the entire diagnostic and contains nothing secret.
        """
        # Loaded before the client exists, so a key that will not parse cannot leave an
        # unclosed client behind: `SftpAuthenticationError` is a `TransportError`, which none
        # of the handlers below catch, so raising it from inside the argument list of
        # `connect` would skip every `client.close()` here.
        inline_key = self._inline_key(credential)
        client = paramiko.SSHClient()
        if self._host_key is not None:
            client.get_host_keys().add(
                self._host_key_name(), self._host_key.get_name(), self._host_key
            )
        else:
            client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        refusal: str | None = None
        unreachable: str | None = None
        cause: BaseException | None = None
        try:
            client.connect(
                hostname=self._host,
                port=self._port,
                username=credential.username,
                pkey=inline_key,
                key_filename=(None if credential.key_path is None else str(credential.key_path)),
                passphrase=(
                    None
                    if credential.key_path is None or credential.passphrase is None
                    else credential.passphrase.reveal()
                ),
                # The registered credential is the only one offered.  Left at their defaults
                # these two let paramiko fall back to ~/.ssh and a running agent, so a source
                # could authenticate with a key nobody registered -- on one machine.
                allow_agent=False,
                look_for_keys=False,
                timeout=self._connect_timeout,
                banner_timeout=self._connect_timeout,
                auth_timeout=self._connect_timeout,
            )
        except paramiko.AuthenticationException:
            variables = ", ".join(
                credentials.env_var_names(
                    str(source.credential_ref), credentials.CredentialKind.SSH_KEY
                )
            )
            refusal = (
                f"source {source.source_id!r}: {self._host}:{self._port} refused the SSH key "
                f"resolved from credential {source.credential_ref!r} for account "
                f"{credential.username!r}. Check that the key is the one enrolled with the "
                f"vendor and that the account matches it; the values come from {variables} or "
                "from the connector secrets file. No retry is attempted -- that is Doc 2 step "
                "6 and is out of scope; re-run this fetch by hand once the key is right."
            )
        except paramiko.BadHostKeyException as exc:
            unreachable = (
                f"source {source.source_id!r}: {self._host}:{self._port} presented host key "
                f"{exc.key.get_name()} with fingerprint {exc.key.get_base64()[:24]}..., which "
                "is not the one pinned for it. Either the vendor rotated their host key and "
                "the pin needs re-issuing, or this is not the vendor."
            )
        except (paramiko.SSHException, OSError) as exc:
            unreachable = (
                f"source {source.source_id!r}: could not open an SFTP session to "
                f"{self._host}:{self._port} ({type(exc).__name__}: {exc}). No retry is "
                "attempted -- retry, backoff and replay are Doc 2 step 6 and are not built."
            )
            cause = exc
        if refusal is not None:
            client.close()
            raise SftpAuthenticationError(refusal)
        if unreachable is not None:
            client.close()
            raise SftpConnectionError(unreachable) from cause
        return client

    def _host_key_name(self) -> str:
        """How paramiko spells a host in its known-hosts table: ``[host]:port`` off port 22."""
        return self._host if self._port == 22 else f"[{self._host}]:{self._port}"

    def _list(
        self, session: "paramiko.SFTPClient", remote_dir: PurePosixPath, source: "Source"
    ) -> tuple[_RemoteFile, ...]:
        """List the remote directory once, keeping names and modification times together.

        One round trip via ``listdir_attr`` rather than a listing followed by a ``stat`` per
        file: the mtime is what ``should_fetch`` needs to answer, and fetching it per file
        would mean a conversation with the server about files we are about to skip.

        Directories are dropped.  A directory entry cannot be a document, and one named like
        a file would otherwise match a declared name and fail later, further from the cause.
        """
        try:
            entries = session.listdir_attr(str(remote_dir))
        except (OSError, paramiko.SSHException) as exc:
            raise SftpListingError(
                f"source {source.source_id!r}: could not list {remote_dir} on "
                f"{self._host}:{self._port} ({type(exc).__name__}: {exc}). The usual cause is "
                "an endpoint that does not exist on the server, or an account without read "
                "permission on it."
            ) from exc

        files: list[_RemoteFile] = []
        for entry in entries:
            mode = getattr(entry, "st_mode", None)
            if mode is not None and stat_module.S_ISDIR(mode):
                continue
            name = entry.filename
            _reject_unsafe_remote_name(name, origin="remote")
            files.append(_RemoteFile(name=name, modified_at=_modified_at(entry)))
        return tuple(files)

    def _select(
        self, source: "Source", listing: Sequence[_RemoteFile]
    ) -> tuple[_RemoteFile, ...]:
        """Match ``source.filenames`` against the listing, in the order the row declares.

        **An entry is a literal name unless it contains ``*``, ``?`` or ``[``, in which case
        it is a glob.**  That choice is forced rather than preferred: ``registry.Source`` has
        no pattern field, this module may not add one, and a literal-only match would be
        useless against either vendor we are building for.  ``DOC2-010`` says to *retain the
        vendor file name and generated timestamp*, so a Verity drop is
        ``accumulations_<timestamp>.csv`` and never the same name twice; ``CRANEWARE-001``
        describes scheduled delivery of named reports into a folder, which is the same shape.
        Encoding the pattern in the name is the only seam a frozen registry row leaves, and
        it degrades exactly right: an entry with no wildcard is compared literally, so every
        existing row keeps meaning what it always meant.

        Order is the declared order across entries and lexicographic within one pattern, so a
        fetch is reproducible.  A name matched by two entries is fetched once -- ingest counts
        one batch per document, and a file fetched twice would be two.

        A declared name the server does not have is **skipped, not an error**, which is
        ``LocalDirectoryTransport``'s rule carried across unchanged: a vendor that published
        five of six datasets today is a smaller delivery, not a broken one.
        """
        available = {item.name: item for item in listing}
        chosen: list[_RemoteFile] = []
        taken: set[str] = set()
        for entry in source.filenames:
            # Checked for both kinds of entry, not only literals.  A pattern carrying a
            # directory separator would match nothing -- every name in the listing is bare --
            # so it would fetch zero documents and look exactly like a vendor that published
            # nothing today.  That is the failure this package's own docstring says a
            # transport must never produce.
            _reject_unsafe_remote_name(entry, origin=f"source {source.source_id!r} declared")
            if any(marker in entry for marker in _GLOB_MARKERS):
                matches = sorted(name for name in available if fnmatch.fnmatchcase(name, entry))
            else:
                matches = [entry] if entry in available else []
            for name in matches:
                if name in taken:
                    continue
                taken.add(name)
                chosen.append(available[name])
        return tuple(chosen)

    def _read(
        self,
        session: "paramiko.SFTPClient",
        remote_dir: PurePosixPath,
        name: str,
        source: "Source",
    ) -> bytes:
        """Download one file whole.  The name is re-checked immediately before it is joined.

        Re-checked rather than trusted from the listing, for the reason
        ``transport.py::_reject_unsafe_name`` gives about checking before joining: the join is
        the moment the forbidden path would exist, and a check that ran after it would have
        already built the thing it was refusing.
        """
        _reject_unsafe_remote_name(name, origin="remote")
        target = remote_dir / name
        try:
            with session.open(str(target), "rb") as handle:
                return handle.read()
        except (OSError, paramiko.SSHException) as exc:
            raise SftpDownloadError(
                f"source {source.source_id!r}: {target} was listed and then could not be read "
                f"({type(exc).__name__}: {exc}). A vendor writing an export in place looks "
                "exactly like this. Re-run the fetch by hand -- retry is Doc 2 step 6."
            ) from exc
