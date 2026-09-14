"""Agent-layer configuration: environment variables and an optional ``.env``, never
a framework.

Three things about this module mirror ``recon.config`` on purpose (same author,
same house rules, and a reviewer who has read one should not have to re-learn the
other):

**No mutable module-level singleton.** :func:`load_agent_settings` returns a fresh
frozen instance every call; nothing here remembers state between calls.

**Real environment variables beat ``.env``.** The ``.env`` file is a convenience for
a local `RECON_AGENT_API_KEY`, not a second source of truth -- if both are set, the
process environment is assumed to be the more deliberate one.

**``AgentSettings.__repr__`` must never print the key.** Logging a settings object,
a stack trace, or a debugger repr is a routine way a secret leaks into a terminal
scrollback or a bug report; the override makes that structurally impossible rather
than relying on every caller to remember not to print ``.api_key``.

There is no ``python-dotenv`` import here. The base install has zero runtime
dependencies (``pyproject.toml``'s own comment says so), and one fifteen-line parser
for ``KEY=VALUE`` lines does not justify pulling in a package for it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

__all__ = ["AgentSettings", "load_agent_settings"]

_ENV_PREFIX = "RECON_AGENT_"
_VALID_MODES: tuple[str, ...] = ("live", "record", "replay")


def _default_repo_root() -> Path:
    # <repo>/src/recon/agents/config.py -> parents[0]=agents,[1]=recon,[2]=src,[3]=<repo>
    return Path(__file__).resolve().parents[3]


def _parse_env_file(path: Path) -> dict[str, str]:
    """A hand-rolled ``.env`` reader: ``KEY=VALUE`` per line, ``#`` comments, blank
    lines skipped, optional matching quotes stripped. Not a general-purpose parser
    (no multi-line values, no variable expansion) -- it only has to cover the seven
    variables this module reads.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _lookup(name: str, dotenv: dict[str, str]) -> str | None:
    """The real environment wins; ``.env`` only fills gaps the environment leaves."""
    if name in os.environ:
        return os.environ[name]
    return dotenv.get(name)


@dataclass(frozen=True, slots=True)
class AgentSettings:
    base_url: str
    api_key: str | None  # None is legal -- replay mode needs no key
    proposer_model: str
    evaluator_model: str
    #: Used only when the primary evaluator model fails the output schema twice in a
    #: row -- a capacity problem rather than a slip (design doc S10). Set to the same
    #: value as `evaluator_model`, or empty, to disable escalation.
    evaluator_fallback_model: str
    #: "core" grades the four judge criteria that carry the argument; "full" grades
    #: all eight. Deterministic criteria, including every veto, always run either way.
    rubric_profile: str
    investigator_model: str
    #: The Portfolio Analyst's model (spec_analyst.md).  A single-pass, tool-calling
    #: role with no propose/evaluate loop -- the same shape as the Investigator, not
    #: the Proposer/Evaluator pair -- so it gets its own env-driven setting rather
    #: than reusing either of theirs, for the reason `investigator_model`'s own
    #: comment already gives: a hardcoded model can't be pointed at a working one the
    #: day the provider's chosen tier stops answering.
    analyst_model: str
    max_iterations: int
    threshold: float
    max_tool_rounds: int
    request_timeout_s: float
    token_budget: int  # per run; 0 disables the budget check
    journal_dir: Path
    fixture_dir: Path
    mode: Literal["live", "record", "replay"]

    def __repr__(self) -> str:  # noqa: D105 -- the whole point is what this omits
        key_state = "set" if self.api_key else "unset"
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, api_key=<{key_state}>, "
            f"proposer_model={self.proposer_model!r}, evaluator_model={self.evaluator_model!r}, "
            f"investigator_model={self.investigator_model!r}, analyst_model={self.analyst_model!r}, "
            f"max_iterations={self.max_iterations!r}, "
            f"threshold={self.threshold!r}, max_tool_rounds={self.max_tool_rounds!r}, "
            f"request_timeout_s={self.request_timeout_s!r}, token_budget={self.token_budget!r}, "
            f"journal_dir={self.journal_dir!r}, fixture_dir={self.fixture_dir!r}, mode={self.mode!r})"
        )


def load_agent_settings(**overrides: Any) -> AgentSettings:
    """Build a frozen :class:`AgentSettings`.

    Resolution order per field: an explicit keyword in ``overrides`` wins outright;
    otherwise a ``RECON_AGENT_*`` environment variable; otherwise the matching key
    in a git-ignored ``.env`` at the repo root, if one exists; otherwise the
    published default. ``api_key`` additionally accepts ``DEEPSEEK_API_KEY`` as a
    fallback name, since that is the variable the provider's own docs tell you to
    set.

    Raises:
        ValueError: on an unknown ``mode``, naming the legal set.
    """
    repo_root = _default_repo_root()
    dotenv = _parse_env_file(repo_root / ".env")

    def env(name: str, default: str) -> str:
        value = _lookup(f"{_ENV_PREFIX}{name}", dotenv)
        return default if value is None else value

    kwargs: dict[str, Any] = {
        "base_url": env("BASE_URL", "https://api.deepseek.com"),
        "api_key": _lookup(f"{_ENV_PREFIX}API_KEY", dotenv) or _lookup("DEEPSEEK_API_KEY", dotenv),
        "proposer_model": env("PROPOSER_MODEL", "deepseek-chat"),
        # Measured over six real runs before this default changed: the evaluator on
        # `deepseek-v4-pro` averaged 466s per call against the proposer's 4.1s, and
        # 95% of its output tokens were reasoning tokens -- one call took 957s. It was
        # 96% of every Decide run's wall clock. A thinking model grading a 16-criterion
        # checklist in one call is simply the wrong shape of work for it.
        #
        # The cost of moving is real and should not be glossed: proposer and evaluator
        # now share a model, so the self-preference-bias mitigation that motivated the
        # split (design doc S1) is gone, and the design note has to say so rather than
        # keep claiming independence. What survives is the part that was doing the
        # heavier lifting anyway -- the evaluator still gets a fresh trace, never sees
        # the proposer's reasoning, and Python still computes the score from a
        # checklist rather than accepting a number the model volunteered.
        #
        # Set RECON_AGENT_EVALUATOR_MODEL=deepseek-v4-pro to buy the independence back
        # at roughly eight minutes a call.
        "evaluator_model": env("EVALUATOR_MODEL", "deepseek-chat"),
        "evaluator_fallback_model": env("EVALUATOR_FALLBACK_MODEL", "deepseek-v4-pro"),
        # Env-driven like the other two. It was hardcoded, which meant
        # RECON_AGENT_INVESTIGATOR_MODEL was silently ignored -- and the one time that
        # mattered was the day the provider's flash tier stopped answering and the
        # Investigator could not be pointed at a model that worked.
        "investigator_model": env("INVESTIGATOR_MODEL", "deepseek-chat"),
        # Same defaulting shape as investigator_model, for the same reason: the
        # Analyst is a third, independent single-pass role (spec_analyst.md) and
        # deserves to be pointable at a working model on its own, not silently tied
        # to whichever of the other two happens to share its default today.
        "analyst_model": env("ANALYST_MODEL", "deepseek-chat"),
        # Two, not five. Five is a backstop for a system being calibrated; a
        # prototype demonstrating the loop needs exactly enough rounds to show that
        # a critique feeds forward and the proposal changes, which is two. Measured:
        # every run so far terminated on a gate condition inside two rounds anyway,
        # so the ceiling was costing wall clock without ever changing an outcome.
        "max_iterations": int(env("MAX_ITERATIONS", "2")),
        "rubric_profile": env("RUBRIC", "core"),
        "threshold": float(env("THRESHOLD", "80.0")),
        "max_tool_rounds": 8,
        # 60s, not 120s. A healthy call on this provider is 2-10 seconds; the only
        # thing a longer ceiling buys is a longer wait before discovering the provider
        # has stalled. With three attempts, 120s meant six minutes of silence before
        # any error surfaced, which reads as a hung application rather than a throttled
        # upstream. Cutting it halves the worst case and changes nothing about a call
        # that was ever going to succeed.
        "request_timeout_s": 60.0,
        "token_budget": 120_000,
        "journal_dir": repo_root / "data" / "agent_runs",
        "fixture_dir": repo_root / "tests" / "fixtures" / "agent_traces",
        "mode": env("MODE", "live"),
    }
    kwargs.update(overrides)

    if kwargs["mode"] not in _VALID_MODES:
        raise ValueError(f"unknown agent mode {kwargs['mode']!r}; expected one of {', '.join(_VALID_MODES)}")

    return AgentSettings(**kwargs)
