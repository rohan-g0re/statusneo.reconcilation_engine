"""Turning a credential *name* into a credential *value*, and nothing else.

Requirement A3.  ``registry.Source.credential_ref`` is a name — that is what lets the
registry be committed at all — and this module is the only thing in the codebase that
turns such a name into material a transport can authenticate with.  Two shapes exist,
because Doc 2 names two: an SSH key for SFTP, and Beacon's Access Token plus Private
Token for HTTP.

**Why this is not in ``recon.config``, and why it must never move there.**  ``config.py``
accepts exactly two environment overrides, ``RECON_DATA_DIR`` and ``RECON_DB_PATH``, both
paths, and its own docstring says why: "the seed and the window are what reproducible
means; letting the environment move them would make every published hash meaningless."
That restriction is a property of the module, not a convention — it holds because there
is nothing else in there for an environment variable to reach.  Credentials break the
restriction by necessity: a secret that cannot come from the environment is a secret
committed to the repository.  So the two categories are kept in separate namespaces
(requirement §4.10) rather than one being allowed to erode the other.  Nothing here
imports ``recon.config``, and no credential variable is ever added to it.  "Same seed,
same bytes" then keeps holding for everything except the network, which was never
reproducible anyway.

**Where values come from, in order.**  The process environment first, then a local
secrets file.  The environment wins because a variable exported for one command is the
more deliberate of the two, the same rule ``recon.agents.config`` applies to its ``.env``.

**Where the secrets file lives.**  Outside the repository — ``~/.recon/connector_secrets.json``
by default, moved with ``RECON_CONNECTOR_SECRETS``.  Not a git-ignored path inside the
tree, which is the more common choice and a weaker one: ``.gitignore`` does not apply to
a file that is already tracked, it is overridable with ``git add -f``, and it advertises
a repo-local location as a legitimate place to keep a token.  A path under the user's home
cannot be reached by any ``git add`` run in this checkout, which is what A3's acceptance —
*no credential value appears anywhere in tracked files* — actually asks for.

**Every secret is wrapped in :class:`Secret`, whose ``repr``, ``str`` and ``format`` all
redact.**  This is the part that has to be structural rather than remembered.  The routine
way a token leaks is not a deliberate print; it is a dataclass repr in a traceback, an
f-string in a debug line, or a journal event built from a dict that happened to contain
one.  ``recon.agents`` learned this twice — ``AgentSettings.__repr__`` omits the key, and
``Journal`` scrubs the configured key byte-for-byte because its regex missed real key
shapes — and the discipline here is deliberately stricter: the redaction lives on the value
itself, so it survives being put inside something else.  :meth:`Secret.reveal` is the one
and only way out, so every use site is one ``grep`` away.

**Beacon's two tokens are ``BEACON-011``, which is SECOND_HAND and SINGLE-SOURCED.**
``docs/vendor_evidence/beacon.md`` records the two-token model as sourced from ``DOC2-006``
and ``DOC2-007`` page 3 only; every first-hand Beacon page that would confirm it
(``BEACON-001`` through ``BEACON-007``) returned HTTP 403 and is UNAVAILABLE.  How the two
tokens are *presented* on a request — header names, scheme, ordering — is listed there as
genuinely UNKNOWN.  So this module models that two named tokens exist and must both be
present; it does not model a wire format it has no source for.  ``http.py`` owns that
choice and must tag it INVENTED.

**Failures are named, never a ``KeyError`` and never a traceback from a parser.**  A3's
acceptance is "a wrong credential produces a clear named failure, not a stack trace", so
a missing value says which ref, which environment variables were looked for, which file
was consulted and what to do about it; a secrets file that is not JSON says so with the
path rather than raising ``JSONDecodeError`` from inside ``json``.

**And a named failure names the variable, never the value.**  The two halves of A3's
acceptance pull against each other: the clearer the message, the more tempting it is to
quote what was found.  Two places where that tipped over are fixed and commented in
:func:`_build_ssh_key` and :func:`_load_entry` — an SSH key pasted into the *path*
variable used to print in full, and a malformed secrets file used to stay reachable
through the raised error's ``__context__``, carrying every credential in the file.  The
rule both now follow: describe the value's shape, never its content, and make sure nothing
the parser touched survives the raise.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

__all__ = [
    "ENV_PREFIX",
    "SECRETS_PATH_ENV_VAR",
    "CredentialError",
    "CredentialKind",
    "MalformedCredentialError",
    "MissingCredentialError",
    "Secret",
    "SshKeyCredential",
    "TokenPairCredential",
    "Credential",
    "default_secrets_path",
    "env_var_names",
    "resolve",
    "resolve_ssh_key",
    "resolve_token_pair",
]

#: Every credential variable this module reads starts here.  Distinct from ``RECON_``
#: (``recon.config``'s paths) and from ``RECON_AGENT_`` (the agent layer's own settings),
#: so "which namespace does this variable belong to" is answerable from its name alone.
ENV_PREFIX = "RECON_CONNECTOR_"

#: Where the secrets file lives, if not in the default location.
SECRETS_PATH_ENV_VAR = "RECON_CONNECTOR_SECRETS"

#: What a secret renders as, everywhere.  One constant, so a test asserting "the value did
#: not leak" and a human reading a log are looking at the same string.
REDACTED = "<redacted>"

#: Salt for :meth:`Secret.__hash__`, drawn once per process and never written down.
#:
#: Python already salts ``hash(str)``, but only until someone sets ``PYTHONHASHSEED`` —
#: and this repository is one flag away from that, because "same seed, same bytes" is its
#: whole discipline and six modules explain that they avoid ``hash()`` for exactly that
#: reason.  Pinned, an unsalted ``hash(secret)`` becomes reproducible across processes and
#: therefore an offline oracle: guess, hash, compare.  This makes the number a fact about
#: this process rather than about the value.
_HASH_SALT = os.urandom(32)

#: Longer than this and a value offered as a filesystem path is not one.  ``PATH_MAX`` is
#: 4096 on Linux and 260 on unextended Windows; a PEM private key body is 1700-3300
#: characters.  512 sits between the two with room on both sides.
_IMPLAUSIBLE_PATH_LENGTH = 512


# --- failures --------------------------------------------------------------


class CredentialError(RuntimeError):
    """Something went wrong resolving or handling a credential.

    A ``RuntimeError`` rather than a ``ValueError`` for the same reason
    :class:`~recon.connectors.transport.TransportError` is: this is a fact about the
    environment the process is running in, not about an argument a caller passed.
    """


class MissingCredentialError(CredentialError):
    """A credential was asked for and is not available anywhere.

    Carries the lookup as structured fields as well as prose, so a readiness report
    (requirement F3) can list the variables an operator still has to set without
    re-deriving the naming rule or scraping a message.
    """

    def __init__(
        self,
        message: str,
        *,
        credential_ref: str,
        kind: "CredentialKind | None",
        env_vars: tuple[str, ...],
        secrets_file: Path,
    ) -> None:
        super().__init__(message)
        self.credential_ref = credential_ref
        self.kind = kind
        self.env_vars = env_vars
        self.secrets_file = secrets_file


class MalformedCredentialError(CredentialError):
    """A credential was found and is not usable as one.

    Unparseable secrets file, an entry that is not a mapping of strings, an SSH key path
    that points at nothing, an entry claiming to be both shapes at once.  Separate from
    :class:`MissingCredentialError` because the remedies differ: one is "set the variable",
    the other is "the value you set is wrong."
    """


# --- the wrapper -----------------------------------------------------------


class Secret:
    """A string that refuses to render itself.

    ``repr``, ``str`` and ``format`` all return :data:`REDACTED`, so a secret stays
    redacted no matter which of the three a caller reaches for — and, more to the point,
    no matter which one something *else* reaches for on its behalf.  A dataclass repr, an
    f-string in a log line, ``"%s" %``, ``"{}".format`` and the default ``json.dumps(...,
    default=str)`` all route through one of those three.

    Deliberately absent:

    * ``__len__``.  A length is information about the value.
    * a ``str`` comparison.  ``secret == "token"`` returns ``False`` rather than comparing,
      because a bare string on the other side of that ``==`` is usually a hardcoded
      credential someone is about to commit.
    * every serialisation hook.  ``__reduce__``, ``__reduce_ex__``, ``__getstate__``,
      ``__copy__`` and ``__deepcopy__`` all raise, because a pickled secret is a secret
      written to disk by something that never intended to write one, and an immutable
      wrapper has no need of a copy.  All five, not just the one that blocks ``pickle``:
      see the refusal block below for the default ``__getstate__`` that used to hand the
      raw value to any caller who asked.

    :meth:`reveal` is the single escape hatch, and it is named to be greppable: the
    complete list of places this process can emit a credential is the list of ``.reveal()``
    call sites.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise MalformedCredentialError(
                f"a credential value must be a string, got {type(value).__name__}"
            )
        if not value.strip():
            # Refused rather than carried.  An exported-but-empty variable is the most
            # common way this goes wrong, and an empty Secret authenticates nothing: it
            # buys a 401 from a vendor hours later instead of a named failure here.
            raise MalformedCredentialError("a credential value must not be empty or blank")
        self._value = value

    def reveal(self) -> str:
        """The value itself.  The only way out, called at the last possible moment.

        Call this where the credential is handed to a transport — inside the header
        assembly, inside the SSH connect — never to put it in a variable, a dict, a log
        record or a journal event, because anything that holds a bare ``str`` has left
        this module's protection.
        """
        return self._value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, format_spec: str) -> str:
        # The spec is discarded, not applied to the placeholder.  Applying it would mean
        # a spec meant for the value gets evaluated against a string that is not the
        # value -- `f"{secret:.2f}"` would raise ValueError out of `format`, which is the
        # stack trace A3 says not to produce.  Ignoring it can never fail.
        del format_spec
        return REDACTED

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        # Salted with :data:`_HASH_SALT`, not taken over the value.  ``hash(secret)`` is
        # otherwise a confirmation oracle: anything that emits a hash -- a dict key in a
        # debug dump, a set repr, a cache key in a log -- lets a holder of that number test
        # a guess against it.  The salt keeps the half of the contract that matters, equal
        # Secrets hashing equal within one process, and drops the half that leaks.
        return hash(hmac.digest(_HASH_SALT, self._value.encode("utf-8"), "sha256"))

    # --- no route out but reveal() -----------------------------------------
    #
    # Five hooks, one refusal.  Blocking ``__reduce__`` alone was not enough: Python 3.11
    # gave every object a default ``__getstate__``, and on a slotted class it returns
    # ``(None, {"_value": <the value>})`` -- the raw credential, out of a method nobody
    # wrote and no ``.reveal()`` grep would ever find.  Generic serialisers reach for
    # exactly that: ``json.dumps(obj, default=lambda o: o.__getstate__())`` printed the
    # token in full.  So the refusal is now stated on every documented serialisation hook
    # rather than on the one that happened to have been thought of.

    def _refuse(self, verb: str) -> NoReturn:
        raise CredentialError(
            f"a Secret must not be {verb}: it has no form other than the one reveal() "
            "returns. Resolve it again from the environment instead."
        )

    def __reduce__(self) -> NoReturn:
        self._refuse("pickled")

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        del protocol
        self._refuse("pickled")

    def __getstate__(self) -> NoReturn:
        self._refuse("serialised")

    def __copy__(self) -> NoReturn:
        self._refuse("copied")

    def __deepcopy__(self, memo: dict) -> NoReturn:
        del memo
        self._refuse("copied")


def _key_material_shape(value: str) -> str | None:
    """Why a value offered as a key *path* is in fact key *material* — never the value.

    The description is assembled from properties of the string, so a message can say what
    is wrong without repeating a single character of it.  ``None`` means "this is plausibly
    a path", and a path is not a secret: it is printed in full, because *which file did it
    look for* is the whole question when a key does not load.

    Lives next to :class:`Secret` rather than beside its callers because both of them are
    the same rule — a path field is the one field in this module that renders in the clear,
    so a value that is not a path must never reach one.
    """
    marks: list[str] = []
    if "\n" in value or "\r" in value:
        marks.append("contains newlines")
    if value.lstrip().startswith("-----BEGIN"):
        marks.append("starts with a PEM header")
    if len(value) > _IMPLAUSIBLE_PATH_LENGTH:
        marks.append("is longer than any filesystem path allows")
    if not marks:
        return None
    return f"{len(value)} characters, " + ", ".join(marks)


# --- the two shapes --------------------------------------------------------


class CredentialKind(StrEnum):
    """The two authentication shapes A3 names.  Not a general taxonomy.

    Doc 2 describes SFTP with an SSH key and Beacon over HTTP with two tokens.  A
    username/password member would be a shape no source asks for, and an unused member is
    an invitation to authenticate a vendor a way nobody verified it supports.
    """

    SSH_KEY = "SSH_KEY"
    TOKEN_PAIR = "TOKEN_PAIR"


@dataclass(frozen=True, slots=True)
class SshKeyCredential:
    """An SSH key for :class:`SftpTransport`, as a path or as inline material.

    Both forms exist because both are real: a developer has a key file, and a deployment
    injects key material as an environment variable with no file anywhere.  Exactly one is
    present — a credential carrying both is ambiguous about which one actually
    authenticated, which is precisely the question being asked when an SFTP connection
    starts failing.

    ``username`` is not a secret and is not redacted.  It lives here rather than on the
    registry row because it is issued with the key, rotates with the key, and is useless
    without it.
    """

    credential_ref: str
    username: str | None = None
    key_path: Path | None = None
    key_material: Secret | None = None
    passphrase: Secret | None = None

    def __post_init__(self) -> None:
        # The generated repr is safe only while every secret-bearing field holds a
        # `Secret` -- a bare str assigned here would print in full in any traceback that
        # renders this object.  Checked rather than documented, because the documented
        # version is the one a later edit breaks silently.
        for name in ("key_material", "passphrase"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Secret):
                raise MalformedCredentialError(
                    f"{type(self).__name__}.{name} must be a Secret, got "
                    f"{type(value).__name__}; a bare string here would print in full"
                )
        # `key_path` is the one field on this object that is *meant* to render in the
        # clear, which makes it the one field key material must never land in.  `resolve`
        # already refuses it at the variable, with a message naming the right variable;
        # this is the same refusal stated on the type, so it also holds for an object
        # constructed directly -- by a test, a readiness report, or a later transport.
        if self.key_path is not None:
            shape = _key_material_shape(str(self.key_path))
            if shape is not None:
                raise MalformedCredentialError(
                    f"{type(self).__name__}.key_path holds key material rather than a path "
                    f"({shape}); it renders in full in this object's repr. Inline material "
                    "goes in key_material, which is a Secret. The value is described here "
                    "and not repeated -- requirement A3."
                )
        if (self.key_path is None) == (self.key_material is None):
            raise MalformedCredentialError(
                f"credential {self.credential_ref!r}: supply exactly one of an SSH key "
                "path or inline key material, not both and not neither"
            )


@dataclass(frozen=True, slots=True)
class TokenPairCredential:
    """Beacon's Access Token plus Private Token (``BEACON-011``).

    ``BEACON-011`` is **SECOND_HAND and SINGLE-SOURCED**: it comes from ``DOC2-006`` and
    ``DOC2-007`` page 3 — *"Partner onboarding yields an Access Token plus a separate
    Private Token used to authenticate API requests"* — and every first-hand Beacon page
    that would corroborate it returned HTTP 403 and is recorded UNAVAILABLE in
    ``docs/vendor_evidence/beacon.md``.  If the model is wrong, it is wrong in a way
    traceable to a named source rather than to our imagination.

    Both tokens are required.  Beacon's evidence file lists the token *exchange* — header
    names, scheme, ordering — as genuinely UNKNOWN, so this type says only that two named
    tokens exist and must both be present; how they are put on a request is ``http.py``'s
    decision to make and to tag INVENTED.
    """

    credential_ref: str
    access_token: Secret
    private_token: Secret

    def __post_init__(self) -> None:
        for name in ("access_token", "private_token"):
            value = getattr(self, name)
            if not isinstance(value, Secret):
                raise MalformedCredentialError(
                    f"{type(self).__name__}.{name} must be a Secret, got "
                    f"{type(value).__name__}; a bare string here would print in full"
                )


Credential = SshKeyCredential | TokenPairCredential


# --- naming ----------------------------------------------------------------

#: Field names, shared by the environment (upper-cased and prefixed) and the secrets file
#: (verbatim).  One table, so the two sources cannot drift into different vocabularies.
_FIELDS: dict[CredentialKind, tuple[str, ...]] = {
    CredentialKind.SSH_KEY: ("ssh_username", "ssh_key_path", "ssh_key", "ssh_passphrase"),
    CredentialKind.TOKEN_PAIR: ("access_token", "private_token"),
}

#: Which of those fields hold material that must never be rendered.
_SECRET_FIELDS = frozenset({"ssh_key", "ssh_passphrase", "access_token", "private_token"})

_NON_SLUG = re.compile(r"[^A-Za-z0-9]+")


def _slug(credential_ref: str) -> str:
    return _NON_SLUG.sub("_", credential_ref).strip("_").upper()


def _env_var(credential_ref: str, field: str) -> str:
    return f"{ENV_PREFIX}{_slug(credential_ref)}_{field.upper()}"


def env_var_names(credential_ref: str, kind: CredentialKind | None = None) -> tuple[str, ...]:
    """Every environment variable this module would consult for ``credential_ref``.

    Exported so a readiness report can tell an operator what to set without restating the
    naming rule, and so a test can assert the rule in one place rather than in every
    message that quotes it.
    """
    kinds = (kind,) if kind is not None else tuple(CredentialKind)
    return tuple(_env_var(credential_ref, field) for k in kinds for field in _FIELDS[k])


# --- the secrets file ------------------------------------------------------


def default_secrets_path() -> Path:
    """``~/.recon/connector_secrets.json`` — outside the repository, by construction.

    See the module docstring: a home-directory path is strictly stronger than a git-ignored
    one, because no ``git add`` run in this checkout can reach it at all.
    """
    return Path.home() / ".recon" / "connector_secrets.json"


def _secrets_path(secrets_file: Path | str | None, environ: Mapping[str, str]) -> Path:
    if secrets_file is not None:
        return Path(secrets_file).expanduser()
    configured = environ.get(SECRETS_PATH_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_secrets_path()


def _load_entry(path: Path, credential_ref: str) -> dict[str, str]:
    """The ``credential_ref`` entry from the secrets file, or an empty mapping.

    An absent file is not an error — the environment alone is a complete configuration,
    and it is the one a deployment uses.  A file that exists and is wrong *is* an error,
    named here rather than left to surface as a ``JSONDecodeError`` or an ``AttributeError``
    from somewhere inside this function.
    """
    if not path.exists():
        return {}

    # The failure is carried out of the handler as a *string*, and the raise happens after
    # the handler has exited.  `raise ... from None` -- what this used to do -- is not
    # enough, and the gap is not obvious: `from None` clears `__cause__` and leaves
    # `__context__` pointing at the original exception.  `json.JSONDecodeError.doc` is the
    # *entire file text*, so the raised error still had a chain leading to every credential
    # in the secrets file, including refs this caller never asked for.  Any error reporter
    # that walks exception attributes -- Sentry, a `repr(vars(exc))` debug line, pytest's
    # own chained-traceback rendering -- prints all of them.  Outside the handler there is
    # no exception in flight, so `__context__` is None and there is no chain to walk.
    failure: str | None = None
    document: object = None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Position and reason only.  `exc.msg` is a fixed phrase ("Expecting value"), and
        # `exc.doc` -- the file -- is deliberately never read.
        failure = (
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}). "
            "The secrets file is a JSON object keyed by credential ref."
        )
    except OSError as exc:
        failure = f"{path} could not be read: {exc}"
    if failure is not None:
        raise MalformedCredentialError(failure)

    if not isinstance(document, dict):
        raise MalformedCredentialError(
            f"{path} must contain a JSON object keyed by credential ref, got "
            f"{type(document).__name__}"
        )
    entry = document.get(credential_ref)
    if entry is None:
        return {}
    if not isinstance(entry, dict):
        raise MalformedCredentialError(
            f"{path}: entry {credential_ref!r} must be a JSON object of field names to "
            f"string values, got {type(entry).__name__}"
        )
    for key, value in entry.items():
        if not isinstance(value, str):
            raise MalformedCredentialError(
                f"{path}: {credential_ref!r}.{key} must be a string, got "
                f"{type(value).__name__}"
            )
    return entry


# --- resolution ------------------------------------------------------------


def _gather(
    credential_ref: str, environ: Mapping[str, str], entry: Mapping[str, str]
) -> dict[str, str]:
    """Every field present for this ref, environment first, blanks treated as absent.

    A blank is absent rather than present-and-empty for the reason :class:`Secret` refuses
    one: ``export RECON_CONNECTOR_X_ACCESS_TOKEN=`` is a typo, not a credential, and
    honouring it turns a named failure here into a vendor's 401 hours later.
    """
    found: dict[str, str] = {}
    for fields in _FIELDS.values():
        for field in fields:
            value = environ.get(_env_var(credential_ref, field), "")
            if not value.strip():
                value = entry.get(field, "")
            if value.strip():
                found[field] = value
    return found


def _describe_file(path: Path) -> str:
    state = "does not exist" if not path.exists() else "exists"
    return f"{path} ({state}; set {SECRETS_PATH_ENV_VAR} to point elsewhere)"


def _missing(
    credential_ref: str,
    kind: CredentialKind | None,
    fields: tuple[str, ...],
    path: Path,
    detail: str,
) -> MissingCredentialError:
    env_vars = tuple(_env_var(credential_ref, field) for field in fields)
    keys = ", ".join(f'"{field}"' for field in fields)
    return MissingCredentialError(
        f"no credential named {credential_ref!r} resolved: {detail}. "
        f"Looked in the environment for {', '.join(env_vars)}, then for {keys} under "
        f'"{credential_ref}" in the secrets file {_describe_file(path)}. '
        f"To fix: export those variables, or add that entry to that file. Never put a "
        f"credential value in a source row or any tracked file -- requirement A3.",
        credential_ref=credential_ref,
        kind=kind,
        env_vars=env_vars,
        secrets_file=path,
    )


def _build_token_pair(
    credential_ref: str, found: Mapping[str, str], path: Path
) -> TokenPairCredential:
    absent = [field for field in _FIELDS[CredentialKind.TOKEN_PAIR] if field not in found]
    if absent:
        raise _missing(
            credential_ref,
            CredentialKind.TOKEN_PAIR,
            _FIELDS[CredentialKind.TOKEN_PAIR],
            path,
            "Beacon's two-token model (BEACON-011) needs both an access token and a "
            f"private token, and {' and '.join(absent)} {'is' if len(absent) == 1 else 'are'} "
            "missing",
        )
    return TokenPairCredential(
        credential_ref=credential_ref,
        access_token=Secret(found["access_token"]),
        private_token=Secret(found["private_token"]),
    )


def _build_ssh_key(credential_ref: str, found: Mapping[str, str], path: Path) -> SshKeyCredential:
    has_path = "ssh_key_path" in found
    has_material = "ssh_key" in found
    if not has_path and not has_material:
        raise _missing(
            credential_ref,
            CredentialKind.SSH_KEY,
            _FIELDS[CredentialKind.SSH_KEY],
            path,
            "an SFTP source needs an SSH key, supplied either as a path to a key file "
            "or as inline key material, and neither is present",
        )
    if has_path and has_material:
        raise MalformedCredentialError(
            f"credential {credential_ref!r} supplies both an SSH key path and inline key "
            "material. Supply one. Which of the two authenticated is exactly the question "
            "being asked when an SFTP connection starts failing."
        )

    key_path: Path | None = None
    if has_path:
        offered = found["ssh_key_path"]
        path_var = _env_var(credential_ref, "ssh_key_path")
        # Checked *before* the value is treated as a path, because the "does not exist"
        # message below prints the path it looked for -- and this module offers both
        # `..._SSH_KEY` and `..._SSH_KEY_PATH`, which makes pasting the key into the path
        # variable the single most available mistake an operator can make.  Printed, that
        # message is the entire private key in a log line: A3's own "wrong credential"
        # case, answered by leaking the credential.  So the value is described and never
        # echoed.
        shape = _key_material_shape(offered)
        if shape is not None:
            raise MalformedCredentialError(
                f"credential {credential_ref!r}: {path_var} must name a key file, but the "
                f"value looks like key material rather than a path ({shape}). Inline key "
                f"material goes in {_env_var(credential_ref, 'ssh_key')}, which wraps it so "
                f"it never renders. The value is described here and not repeated, because "
                f"an error message is a log line -- requirement A3."
            )
        key_path = Path(offered).expanduser()
        if not key_path.exists():
            # Checked here rather than left to the SSH library, whose failure for a
            # missing key file is indistinguishable from its failure for a rejected one.
            raise MalformedCredentialError(
                f"credential {credential_ref!r}: SSH key file {key_path} does not exist "
                f"(from {path_var} or the secrets file)"
            )

    return SshKeyCredential(
        credential_ref=credential_ref,
        username=found.get("ssh_username"),
        key_path=key_path,
        key_material=Secret(found["ssh_key"]) if has_material else None,
        passphrase=Secret(found["ssh_passphrase"]) if "ssh_passphrase" in found else None,
    )


def resolve(
    credential_ref: str,
    *,
    kind: CredentialKind | str | None = None,
    env: Mapping[str, str] | None = None,
    secrets_file: Path | str | None = None,
) -> Credential:
    """Resolve a credential ref to the material it names.

    Args:
        credential_ref: the name on a ``registry.Source``.  Never a value.
        kind: which shape is expected.  Omit to infer from what is present — useful for a
            readiness check, wrong for a transport, which knows what it needs and should
            say so, because a stated expectation is what turns "nothing configured" into
            a message naming the right variables.
        env: the environment to read.  Defaults to ``os.environ``.  An explicit ``{}``
            means an empty environment, not the default one, so a test can prove the
            failure path without unsetting anything.
        secrets_file: overrides both ``RECON_CONNECTOR_SECRETS`` and the default location.

    Raises:
        MissingCredentialError: nothing is configured, naming the ref, every variable
            consulted, the file consulted and the fix.
        MalformedCredentialError: something is configured and is not usable.

    Neither is a ``KeyError`` and neither carries a parser's traceback, which is A3's
    acceptance criterion stated as code.
    """
    if not isinstance(credential_ref, str) or not credential_ref.strip():
        raise ValueError("credential_ref must be a non-empty string naming a credential")
    if kind is not None:
        try:
            kind = CredentialKind(kind)
        except ValueError:
            valid = ", ".join(k.value for k in CredentialKind)
            raise ValueError(
                f"unknown credential kind {kind!r}; expected one of {valid}"
            ) from None

    environ = os.environ if env is None else env
    path = _secrets_path(secrets_file, environ)
    found = _gather(credential_ref, environ, _load_entry(path, credential_ref))

    if kind is None:
        token_fields = set(_FIELDS[CredentialKind.TOKEN_PAIR]) & found.keys()
        ssh_fields = set(_FIELDS[CredentialKind.SSH_KEY]) & found.keys()
        if token_fields and ssh_fields:
            raise MalformedCredentialError(
                f"credential {credential_ref!r} carries both token-pair fields "
                f"({', '.join(sorted(token_fields))}) and SSH fields "
                f"({', '.join(sorted(ssh_fields))}). One ref names one credential; pass "
                "kind= if a source legitimately holds two."
            )
        if token_fields:
            kind = CredentialKind.TOKEN_PAIR
        elif ssh_fields:
            kind = CredentialKind.SSH_KEY
        else:
            raise _missing(
                credential_ref,
                None,
                tuple(field for fields in _FIELDS.values() for field in fields),
                path,
                "nothing is configured for it, and nothing present says which shape it "
                "should be (pass kind=CredentialKind.TOKEN_PAIR or "
                "kind=CredentialKind.SSH_KEY to be told about one shape only)",
            )

    if kind is CredentialKind.TOKEN_PAIR:
        return _build_token_pair(credential_ref, found, path)
    return _build_ssh_key(credential_ref, found, path)


def resolve_token_pair(
    credential_ref: str,
    *,
    env: Mapping[str, str] | None = None,
    secrets_file: Path | str | None = None,
) -> TokenPairCredential:
    """:func:`resolve` pinned to Beacon's two-token shape (``BEACON-011``)."""
    credential = resolve(
        credential_ref, kind=CredentialKind.TOKEN_PAIR, env=env, secrets_file=secrets_file
    )
    assert isinstance(credential, TokenPairCredential)  # noqa: S101 -- narrowing, not a check
    return credential


def resolve_ssh_key(
    credential_ref: str,
    *,
    env: Mapping[str, str] | None = None,
    secrets_file: Path | str | None = None,
) -> SshKeyCredential:
    """:func:`resolve` pinned to the SFTP shape."""
    credential = resolve(
        credential_ref, kind=CredentialKind.SSH_KEY, env=env, secrets_file=secrets_file
    )
    assert isinstance(credential, SshKeyCredential)  # noqa: S101 -- narrowing, not a check
    return credential
