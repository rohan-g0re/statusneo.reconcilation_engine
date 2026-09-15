"""The seams between the agent layer's modules, which unit tests do not cover.

Every test here exists because something passed its own file's tests and was still
broken across a boundary. Five people built this package in parallel against a frozen
contract; each one's tests were green, and the composition was not. These are the
assertions that would have caught that.

The pattern worth naming: **a permissive test double hides a signature change.** The
roles called ``client.complete()`` without the ``agent`` argument the real client had
just made required, and 471 tests stayed green -- because every stub accepted
``**kwargs``-ish signatures that the real client rejects. A test double that is looser
than the thing it stands in for does not test the integration; it tests the double.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from recon.agents import harness, journal, rubric, scorers, tools
from recon.agents.client import LLMClient, OpenAICompatClient, RecordingClient, ReplayClient
from recon.agents.roles import coordinator, investigator

REPO_ROOT = Path(__file__).resolve().parent.parent


def _complete_signature(obj: object) -> inspect.Signature:
    return inspect.signature(obj.complete)  # type: ignore[attr-defined]


# ═══ The seam that actually broke ════════════════════════════════════════════════


@pytest.mark.parametrize("impl", [OpenAICompatClient, RecordingClient, ReplayClient])
def test_every_client_implementation_matches_the_protocol_signature_exactly(impl: type) -> None:
    """A concrete client that accepts more than the Protocol lets a caller compile
    against the loose one and fail against the strict one -- which is exactly how the
    roles shipped omitting a required argument."""
    protocol = _complete_signature(LLMClient)
    concrete = _complete_signature(impl)

    protocol_params = {n: p for n, p in protocol.parameters.items() if n != "self"}
    concrete_params = {n: p for n, p in concrete.parameters.items() if n != "self"}

    assert set(concrete_params) == set(protocol_params), (
        f"{impl.__name__}.complete parameters diverge from the LLMClient Protocol: "
        f"only in Protocol={set(protocol_params) - set(concrete_params)}, "
        f"only in {impl.__name__}={set(concrete_params) - set(protocol_params)}"
    )
    for name, param in protocol_params.items():
        assert concrete_params[name].kind == param.kind, f"{impl.__name__}.complete: {name} kind differs"
        assert (concrete_params[name].default is inspect.Parameter.empty) == (
            param.default is inspect.Parameter.empty
        ), f"{impl.__name__}.complete: {name} requiredness differs from the Protocol"


def test_agent_is_a_required_argument_so_no_call_can_journal_unlabelled() -> None:
    """Proposer/evaluator independence is a claim the design says must be *verifiable
    after the fact rather than assumed*. It is only verifiable if every request is
    attributed, so an unattributable call must be impossible to make, not merely
    discouraged."""
    params = _complete_signature(LLMClient).parameters
    assert params["agent"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["agent"].default is inspect.Parameter.empty


@pytest.mark.parametrize("module", [investigator, coordinator], ids=["investigator", "coordinator"])
def test_no_role_journals_its_own_llm_events(module: object) -> None:
    """One journaller, sited where the bytes leave the process.

    The roles used to emit their own ``llm_request``/``llm_response`` pair around each
    call, back when ``complete()`` had no ``agent`` parameter. That logged the messages
    a role had assembled rather than what was actually sent, and double-counted every
    call in ``derive_state``.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    for kind in ('"llm_request"', '"llm_response"', '"llm_error"'):
        assert f"journal.event({kind}" not in source.replace(" ", "").replace("\n", ""), (
            f"{module.__name__} journals {kind} itself; the client is the sole journaller of llm_* events"
        )


def test_every_role_call_site_names_its_agent() -> None:
    """Grep-level, deliberately: a signature check cannot see a call site that was
    never executed by a test."""
    for path in (Path(investigator.__file__), Path(coordinator.__file__)):  # type: ignore[arg-type]
        text = path.read_text(encoding="utf-8")
        for index, line in enumerate(text.splitlines(), start=1):
            if ".complete(" not in line:
                continue
            window = "\n".join(text.splitlines()[index - 1 : index + 8])
            assert "agent=" in window, f"{path.name}:{index} calls complete() without naming its agent"


# ═══ One renderer, both sides ════════════════════════════════════════════════════


@pytest.mark.parametrize("module", [investigator, coordinator], ids=["investigator", "coordinator"])
def test_roles_render_tool_results_with_the_function_the_scorer_verifies_against(module: object) -> None:
    """``G5_evidence_verifies_verbatim`` is a **veto**. If a role shows the model one
    rendering of a tool result and the scorer searches a different one, an honest
    verbatim quote fails and zeroes the whole proposal. Two renderings of the same
    object is not a style question here; it is a correctness one."""
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    assert "render_tool_result" in source, f"{module.__name__} must render tool results via scorers.render_tool_result"
    assert "json.dumps(envelope" not in source, (
        f"{module.__name__} still renders an envelope with json.dumps; the scorer verifies "
        "citations against render_tool_result, so the model must be shown those same bytes"
    )


def test_a_quote_of_a_rendered_tool_result_verifies_against_the_renderer() -> None:
    """The round trip the veto depends on, including the characters that broke it:
    an embedded quote mark and a newline, which ``json.dumps`` escapes and a model
    quoting the value never reproduces."""
    envelope = tools.envelope.ok({"memo": 'the payer wrote "URGENT"\nsecond line', "cursor": "2026-03-31T23:59:59Z"})
    rendered = scorers.render_tool_result(envelope)
    assert 'the payer wrote "URGENT"' in rendered
    assert "\\n" not in rendered


# ═══ The closed vocabularies must actually close ═════════════════════════════════


def test_every_journalled_event_kind_is_in_the_closed_vocabulary() -> None:
    """A kind emitted but not declared raises at runtime, deep inside a live run. This
    finds it statically instead."""
    emitted: set[str] = set()
    for path in Path("src/recon/agents").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if ".event(" not in line:
                continue
            fragment = line.split(".event(", 1)[1].lstrip()
            if fragment.startswith('"'):
                emitted.add(fragment[1:].split('"', 1)[0])
    unknown = emitted - journal.EVENT_KINDS
    assert not unknown, f"these kinds are emitted but not declared in EVENT_KINDS: {sorted(unknown)}"


def test_the_dead_untrusted_fence_format_appears_nowhere_in_the_source() -> None:
    """Two fence formats that never meet is not a security control. One authority."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "UNTRUSTED_FEED_TEXT" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"the retired fence format still appears in: {offenders}"


def test_every_tool_named_in_a_prompt_is_a_tool_that_exists() -> None:
    """A prompt telling the model to call a tool that was renamed is an error the model
    discovers at runtime and the developer never sees."""
    import re

    from recon.agents.prompts import evaluator, investigator as inv_prompt, proposer

    #: The two structured-output emitters are forced-tool-use names, not registry
    #: entries -- a different mechanism reaching a different dispatcher.
    emitters = {"emit_proposed_action", "emit_evaluation"}
    known = tools.tool_names() | emitters
    pattern = re.compile(r"\b(?:get|calculate|create|emit|list|fetch)_[a-z_]+\b")

    for module in (inv_prompt, proposer, evaluator):
        text = "\n".join(
            str(getattr(module, name))
            for name in dir(module)
            if name.isupper() and isinstance(getattr(module, name), str)
        )
        for mentioned in set(pattern.findall(text)):
            assert mentioned in known, f"{module.__name__} names {mentioned!r}, which is not a real tool"


# ═══ Constants the layers agree on ═══════════════════════════════════════════════


def test_the_harness_imports_its_outcome_type_rather_than_defining_a_second_one() -> None:
    """Four outcomes is the design's load-bearing distinction -- ``insufficient_data``
    ("this cannot be resolved from the documents", an answer) versus ``capped`` ("we ran
    out of budget", a failure). Two definitions of that type is how they collapse."""
    assert harness.Outcome is rubric.Outcome


def test_the_write_tool_is_never_offered_to_a_model_by_either_role() -> None:
    """The human gate is the only path to a write. A model that can see the write tool
    in its schema array can call it, and no prompt sentence changes that."""
    for module in (investigator, coordinator):
        source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "create_mock_work_item" in source, (
            f"{module.__name__} should name the write tool explicitly, if only to exclude it"
        )
        assert 'wire_schemas()' not in source, (
            f"{module.__name__} calls wire_schemas() with no filter, which would hand the model "
            "every tool including the write"
        )


# ═══ no real secret may enter the repository ═════════════════════════════════════


def test_no_tracked_file_contains_anything_shaped_like_a_live_api_key() -> None:
    """A committed key is not undone by deleting it later -- it stays in history.

    This exists because it happened: a test asserting "the journal redacts the API
    key" was written using the REAL key as its fixture, so the assertion passed while
    the secret was pushed to the remote. The lesson is that proving redaction works
    does not require a working secret; it requires a string of the right SHAPE, which
    is all the redaction logic inspects.

    Scanned with git, not a filesystem walk, so an ignored `.env` is correctly out of
    scope while anything actually tracked is in it.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")

    # Deliberately not the provider's literal prefix + length as one regex: the point
    # is to catch key SHAPES, and the synthetic fixtures in the test suite must not
    # trip it. A real DeepSeek key is 32 hex-ish chars after `sk-`; the fakes spell
    # something unmistakably non-random.
    live_key = re.compile(r"sk-(?![A-Za-z0-9]*EXAMPLE)(?![A-Za-z0-9]*REDACTED)[a-f0-9]{32}\b")

    offenders: list[str] = []
    for name in tracked:
        if not name:
            continue
        path = REPO_ROOT / name
        if not path.is_file() or path.suffix in {".png", ".pdf", ".sqlite"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in live_key.finditer(text):
            offenders.append(f"{name}: {match.group(0)[:12]}...")

    assert not offenders, (
        "a live-looking API key is tracked by git:\n  "
        + "\n  ".join(offenders)
        + "\nRotate the key at the provider, then remove it from the working tree. "
        "Use a synthetic, correctly-shaped fixture in tests instead."
    )


def test_the_shipped_default_can_reach_every_one_of_the_four_outcomes() -> None:
    """`stalled` needs three scored rounds; a ceiling of two makes it unreachable.

    This is not hypothetical -- the iteration default was lowered to two as a
    prototype simplification, and it silently removed one of the loop's four terminal
    states. Nothing failed. No test went red. The outcome simply stopped being
    producible, and a documentation audit found it rather than the suite.

    "Four distinct outcomes, never one boolean" is the design's own argument (S2):
    `stalled` says the loop had a candidate and could not improve it, `capped` says it
    ran out of budget, and collapsing them loses a distinction an operator acts on.
    So the relationship between the stall window and the iteration ceiling is a real
    invariant, and it belongs in a test rather than in someone's memory.
    """
    from recon.agents.config import load_agent_settings

    settings = load_agent_settings()
    assert settings.max_iterations >= _STALL_WINDOW, (
        f"max_iterations={settings.max_iterations} cannot reach the `stalled` outcome, "
        f"which needs {_STALL_WINDOW} scored rounds -- three of the four terminal "
        "states would be producible and the fourth silently dead"
    )


#: The number of scored rounds `rubric._stalled` requires before it can fire.
_STALL_WINDOW = 3
