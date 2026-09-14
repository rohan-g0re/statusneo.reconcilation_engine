"""Drive both agent roles against the live provider and print what actually happened.

This is not a test. It is the thing you run to find out whether the layer works
against a real model, which no stub can tell you -- the whole point of the exercise
is that a real model calls tools you did not script, quotes spans you did not plant,
and abstains (or fails to) on evidence you did not curate.

    python scripts/agent_smoke.py                       # both roles, one episode
    python scripts/agent_smoke.py --episode E-000040    # the INSUFFICIENT_DATA one
    python scripts/agent_smoke.py --role investigator
    python scripts/agent_smoke.py --record             # write a replay fixture
    python scripts/agent_smoke.py --record --role coordinator --nonce deadbeef --run-id my-fixed-id
                                                          # a REPRODUCIBLE fixture: fixing the
                                                          # nonce and run id is what lets
                                                          # tests/test_agents_evals.py rebuild the
                                                          # exact same request bytes at replay time
                                                          # that ReplayClient's request_digest
                                                          # match requires (see --nonce's own help)

Every run leaves a journal under ``data/agent_runs/``. That file is the tool-call
trace, the audit trail and the replay fixture at once, so the interesting output of
this script is the file it writes, not what it prints.
"""

from __future__ import annotations

import argparse
import json
import traceback
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# A Windows console defaults to cp1252, which cannot encode the untrusted-text fence
# characters the tools emit -- so printing a tool result would crash the script rather
# than show it. Reconfigure rather than transliterate: seeing the real fence bytes is
# most of the point of running this.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from recon.agents.client import OpenAICompatClient, RecordingClient  # noqa: E402
from recon.agents.config import load_agent_settings  # noqa: E402
from recon.agents.grounding import render_clause_index, select_clauses  # noqa: E402
from recon.agents.journal import Journal, derive_state  # noqa: E402
from recon.agents.roles.coordinator import run_coordinator  # noqa: E402
from recon.agents.roles.investigator import run_investigator  # noqa: E402
from recon.agents.tools import CallLog, ToolContext  # noqa: E402
from recon.api import dossier as dossier_module  # noqa: E402
from recon.config import load_settings  # noqa: E402
from recon.db.connection import open_db  # noqa: E402
from recon.domain import verdicts as verdict_vocab  # noqa: E402

BAR = "═" * 78


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _glossary() -> str:
    """The verdict vocabulary, so the model reads a code as a meaning rather than
    guessing from the letter."""
    lines = []
    for code in (*verdict_vocab.REIMBURSEMENT_CODES, *verdict_vocab.REBATE_CODES):
        lines.append(f"{code}: {verdict_vocab.describe(code)}")
    return "\n".join(lines)


def _context(conn, *, cursor: str, role: str, model: str, journal: Journal, run_id: str, nonce: str) -> ToolContext:
    return ToolContext(
        conn=conn,
        cursor=cursor,
        role=role,
        model_id=model,
        run_id=run_id,
        now=_now(),
        nonce=nonce,
        write_token=None,  # the human gate has not been passed; only dry_run can succeed
        call_log=CallLog(),
        journal=journal,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", default="E-000006")
    ap.add_argument("--profile", default="demo")
    ap.add_argument("--role", default="both", choices=["both", "investigator", "coordinator"])
    ap.add_argument("--record", action="store_true", help="also write a replay fixture")
    ap.add_argument(
        "--nonce",
        default=None,
        help=(
            "Fix the untrusted-text fence nonce instead of drawing a fresh random one. The nonce "
            "is embedded verbatim in the system prompt and in every wrapped tool result, so two "
            "runs with different nonces produce different request bytes even when everything else "
            "is identical -- and recon.agents.client.ReplayClient matches a request by an exact "
            "digest over (model, messages, tools, tool_choice). A fixture meant to be replayed "
            "later (tests/test_agents_evals.py) needs a nonce fixed at record time and reused at "
            "replay time; omit this for an ordinary interactive run, where a fresh nonce is what "
            "you want."
        ),
    )
    ap.add_argument(
        "--run-id",
        default=None,
        help="Fix the run id (and so the journal/fixture file name) instead of drawing a fresh uuid4.",
    )
    args = ap.parse_args()
    if (args.nonce or args.run_id) and args.role == "both":
        print("--nonce/--run-id fix a single file name; pass --role investigator|coordinator with them", file=sys.stderr)
        return 2

    settings = load_settings(args.profile)
    agent_settings = load_agent_settings()
    if not agent_settings.api_key:
        print("no RECON_AGENT_API_KEY -- put one in .env", file=sys.stderr)
        return 2

    conn = open_db(settings)
    cursor = settings.max_cursor
    payload = dossier_module.build_dossier(conn, args.episode, cursor)
    if payload is None:
        print(f"{args.episode} does not exist at {cursor}", file=sys.stderr)
        return 2

    current = payload.get("current") or {}
    print(BAR)
    print(f"{args.episode}  {payload['identity']['track']}  cursor {cursor}")
    print(f"  disposition   {current.get('episode_disposition')}")
    print(f"  verdicts      {current.get('reimbursement_verdict')} / {current.get('rebate_verdict')}")
    print(f"  reasons       {', '.join(current.get('reason_codes') or []) or '-'}")
    print(f"  timeline      {len(payload['timeline'])} events, {len(payload['unresolved'])} unresolved")
    print(BAR)

    glossary = _glossary()
    clause_index = render_clause_index(select_clauses(payload))
    exit_code = 0

    for role in (["investigator", "coordinator"] if args.role == "both" else [args.role]):
        run_id = args.run_id or uuid.uuid4().hex
        nonce = args.nonce or secrets.token_hex(4)
        journal = Journal(agent_settings.journal_dir / f"{run_id}.jsonl", run_id, now=_now)
        client = OpenAICompatClient(
            base_url=agent_settings.base_url,
            api_key=agent_settings.api_key,
            journal=journal,
            timeout_s=agent_settings.request_timeout_s,
        )
        if args.record:
            client = RecordingClient(client, agent_settings.fixture_dir, run_id)

        model = (
            agent_settings.investigator_model if role == "investigator" else agent_settings.proposer_model
        )
        ctx = _context(conn, cursor=cursor, role=role, model=model, journal=journal, run_id=run_id, nonce=nonce)
        journal.event("run_started", role=role, episode_id=args.episode, cursor=cursor, model=model)

        print(f"\n{BAR}\n{role.upper()}  run {run_id[:8]}  model {model}\n{BAR}")
        try:
            if role == "investigator":
                outcome = run_investigator(
                    client=client, tool_ctx=ctx, model=model,
                    episode_id=args.episode, cursor=cursor, verdict_glossary=glossary,
                )
                if outcome.report is None:
                    print(f"  stopped: {outcome.typed_reason}")
                else:
                    r = outcome.report
                    for heading, body in (
                        ("What happened", r.what_happened),
                        ("Why it is open", r.why_it_is_open),
                        ("What I could not determine", r.what_i_could_not_determine),
                        ("What a human should check first", r.what_a_human_should_check_first),
                    ):
                        print(f"\n## {heading}\n{body}")
                    print(f"\n  citations: {len(r.citations)}")
                    if outcome.unsourced_figures:
                        print(f"  UNSOURCED FIGURES SURVIVED REPAIR: {outcome.unsourced_figures}")
                        exit_code = 1
            else:
                outcome = run_coordinator(
                    client=client, tool_ctx=ctx, settings=agent_settings, dossier=payload,
                    verdict_glossary=glossary, grounding_clause_index=clause_index, journal=journal,
                )
                # rubric.Outcome's real fields (kind/final_score/iterations_run), not the
                # status/score/iterations this used to read -- those never existed on the
                # dataclass, so this branch raised AttributeError on every real coordinator
                # run and was only ever seen wrapped in the except below, printed as
                # "RAISED AttributeError" on an otherwise fully successful run.
                print(f"  outcome   {outcome.kind}")
                print(f"  score     {outcome.final_score}  (best {outcome.best_score}, threshold {agent_settings.threshold})")
                print(f"  rounds    {outcome.iterations_run}")
                proposal = outcome.proposal
                print(f"  action    {proposal.action}")
                print(f"  clause    {proposal.grounding_clause_id}")
                print(f"  artifacts {list(proposal.required_artifacts)}")
                print(f"  evidence  {len(proposal.evidence)} spans")
        except Exception as exc:  # noqa: BLE001 -- a smoke script reports, it does not swallow
            print(f"  RAISED {type(exc).__name__}: {exc}")
            traceback.print_exc()
            exit_code = 1
        finally:
            journal.event("run_finished", role=role)
            journal.close()

        state = derive_state(journal.events)
        calls = [f"{c.name}({json.dumps(c.arguments, sort_keys=True)})" for c in state.tool_calls]
        print(f"\n  journal   {journal.path}")
        print(f"  models    {state.models}")
        print(f"  tokens    {state.token_usage}")
        print(f"  tool calls ({len(calls)}):")
        for call in calls:
            print(f"    - {call[:150]}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
