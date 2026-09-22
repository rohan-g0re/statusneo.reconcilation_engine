# Post-Claim Pharmacy Financial Reconciliation

A hospital pharmacy dispenses an expensive drug. The moment it goes out the door the hospital
is owed money — and that money comes back from up to three different places, on three
different timetables, in three different formats, through parties who do not talk to each
other.

Pills at a counter bill to a **PBM**. Drugs infused in a chair bill to a **medical payer**
through a clearinghouse. On top of either, if the hospital qualifies for the federal **340B**
programme, a manufacturer owes a separate cash rebate — ruled on by a **TPA**, a company whose
only job is to decide whether that dispense qualified. A fourth feed, the bank, is the only
proof any of it actually landed.

Nobody sends the hospital a statement saying *here is what you are owed and here is what you
got*. **That statement is the product.**

Three ideas run through every design decision here:

1. **Nothing carries one claim number end to end.** There is no shared ID. That is the hard
   part of the problem, not the arithmetic.
2. **The system never guesses.** When it cannot tell, it says so and holds the record, rather
   than picking an answer that looks identical to a correct one in every report afterwards.
3. **Everything is a replay.** Nothing is edited in place. Delete every answer the system has
   produced, feed it the same files, get the same answers back byte for byte.

---

## The shape of it

![System overview](docs/pitch/diagrams/01-system-overview.png)

Five blocks, top to bottom.

**The outside world** sends files and answers API calls — the PBM, the medical payer, the
340B TPAs (Verity and Craneware), the bank, and Beacon, the manufacturer-side rebate platform
and the only counterparty we also send data *to*.

**Connectors** fetch. They know where the SFTP drop is, which credential to use, whether
we're cleared to pull that entity yet, and whether a file changed since last time. They
deliberately do **not** know what any of it means — fetching and interpreting are two jobs on
opposite sides of a wall.

**Ingest and crosswalk** is where meaning arrives. Every line is stored verbatim with a hash
of itself *before* any parser touches it, so evidence can never be edited after the fact. Then
each line becomes one common shape and is attached to the right **episode** — the hardest part
of the system, below.

**The reconciliation engine** is plain Python. No network, no model. One episode plus a point
in time goes in, a verdict comes out. It cannot modify a source record; it only appends.

**The read layer** hands verdicts to the dashboard and to the agent layer. Both only read.

Ingest writes **records in**; the engine writes **verdicts out**. Those are the only two
writers on the deterministic side, and both only ever add rows.

---

## How a claim becomes an answer

**One dispense creates one episode** — the whole financial story hanging off one drug leaving
the door. It has exactly two tracks: reimbursement (pharmacy *or* medical, never both) and an
optional 340B rebate. An episode does not *contain* its records, it points at them: one 835
remittance covers dozens of claims, so it cannot live inside any one of them.

**Attaching a record is a crosswalk, not a join**, because there is no shared ID. Three
bridges, each with a failure mode you can name in advance:

| Bridge | Key | How it breaks |
|---|---|---|
| Pharmacy claim → payment | `pharmacy_npi \| rx_number \| fill_number \| date_of_service` | The two feeds spell the Rx differently — `07845102` against `7845102` |
| Medical claim → payment | `clm01`, the provider's own number | Key on `CLP07` instead and a reprocessed claim silently forks in two — the payer reassigns it every time |
| Claim → 340B rebate | `pharmacy_npi \| rx_number \| ndc11 \| fill_date` | Medical has no prescription, so it falls back to `provider_npi \| ndc11 \| service_date` — two infusions of the same drug, same site, same day are genuinely indistinguishable |

The bank takes **two hops**: a bank line has no concept of a claim, so its `trn02` resolves to
a *remittance*, and the allocator splits that deposit across the remittance's claim lines by
each line's own stated amount. Never pro rata — smearing a clawback across forty claims turns
one traceable recoupment into forty phantom underpayments.

**A record that fits nowhere is parked, never dropped.** Every later arrival does two things:
resolves its own keys forward, and looks *backwards* at the parked pool. Four park reasons are
kept distinct — no usable key, keys resolved to nothing, keys resolved to more than one
episode, or resolved cleanly but disagrees about which 340B entity owns the claim. Four
different problems with four different owners.

**And there is no identifier normalisation anywhere on the match path.** A test asserts
`7845102` and `07845102` build *different* keys. Absorbing that drift would make the numbers
look better and delete the crosswalk-failure exception the system exists to find.

---

## The engine

![Verdict engine](docs/pitch/diagrams/05-verdict-engine.png)

A pure function of two arguments: one episode, and a point in time called the **cursor**. That
cursor is why *"what did we believe on 31 March?"* is a parameter rather than a feature —
forward and backward run the same code path.

It asks eighteen boring questions of the evidence — paid in full, partly, not at all, or
twice? Did cash arrive, arrive short, or never? What did the TPA rule? The manufacturer?
Reversed before or after the money moved? Those are **dimensions**, and they carry the same
names as the decision tree that was enumerated before any of this was written.

The two tracks are judged separately — 31 reimbursement codes, 12 rebate codes — then **seven
cross-track rules run last**, for cases where each track looks fine alone and the pair is a
problem. They may only *add* a reason or force an escalation, never soften one. A rule that
can downgrade is a rule that can hide something.

It rolls up to three dispositions — `CLOSED`, `PENDING`, `EXCEPTION` — because *"what do I do
with this?"* has three answers: nothing, wait, work it. Anything finer lives in **reason
codes**, which are a list, because an episode can be underpaid *and* missing its settlement
confirmation *and* short on cash at once.

Three things worth calling out:

- **It can say "I don't know", and that is a first-class answer.** Three places emit
  `INSUFFICIENT_DATA` and escalate. The alternative is reporting variance against an expected
  of zero, which renders an unpriceable claim as perfectly clean.
- **Aging is not an input.** No SLA thresholds in the verdict logic; age is computed at read
  time and only sorts a queue. That makes *"a verdict cannot change unless a new record
  arrives"* true by construction.
- **There is no queue table.** A queue is a `SELECT` over the latest verdict per episode. A
  test asserts no table has "queue" in its name.

**The agent layer never computes a number.** Nine tools, eight read-only. The ninth creates a
work item for a human and is never in any model's tool list — the role's tools are built by
*removing* it, so no prompt reaches it.

---

## What is measured

The state space was enumerated before the generator existed: **12,093,235,200** raw
combinations collapse to **4,224** legally possible, landing on **372 verdict pairs**, all
reachable. That file is a live test oracle — the shipped engine is run against all 4,224 and
compared verdict by verdict with **no tolerance**. One disagreement fails the build.

The generator writes the answer key to `truth/ground_truth.json` and **the ingestion code is
structurally unable to read it** — `load_feeds` accepts only the feeds directory. That turns
crosswalk accuracy into a measured **1,349 of 1,354** rather than a claim.

**988 tests pass**, 2 skipped, with one known pre-existing failure reported as a failure
rather than rounded to green. No API key, no network for the deterministic half.

---

## Run it

```bash
pip install -e ".[api,agent,dev]"          # api = FastAPI; agent = httpx; dev = pytest
uvicorn recon.api.app:create_app --factory --port 8000
```

`--factory` is mandatory — `create_app` builds the app, it is not one.

```bash
curl -X POST "http://127.0.0.1:8000/api/regenerate?profile=demo"
cd web && npm install && npm run dev
```

Open **http://localhost:5173/** — `localhost`, not `127.0.0.1`, because Vite binds IPv6.

**Which TPA the engine reads.** The default is Craneware's export rather than the generic
feed, because in production there is no generic TPA feed:

```bash
curl -X POST ".../api/regenerate?profile=demo&tpa_source=verity"    # or =generic
```

All three produce the same 60 episodes. Verdicts differ by 1 (Craneware) and 4 (Verity)
against the generic baseline, and every difference is reconciled to a named cause in
`tests/test_vendor_sourced_parity.py`.

---

## Where the rest lives

**The full argument** is [docs/pitch/SOLUTION_PITCH.md](docs/pitch/SOLUTION_PITCH.md) —
twenty-five minutes, three passes over the same system, every diagram in place.

| | |
|---|---|
| [DESIGN_NOTE.md](DESIGN_NOTE.md) | Architecture, decisions, and §9: what I'd build next and the known limits |
| [docs/claim_walkthrough.md](docs/claim_walkthrough.md) | One claim end to end, with a 340B primer folded in |
| [docs/agent_walkthrough.md](docs/agent_walkthrough.md) | The same episode through the agent layer |
| [docs/pitch/diagrams/](docs/pitch/diagrams/) | All seven diagrams, `.png` and editable `.excalidraw` |
| [docs/glossary.md](docs/glossary.md) · [docs/reconciliation_state_space.md](docs/reconciliation_state_space.md) · [docs/feed_formats.md](docs/feed_formats.md) | Vocabulary · the 50 rules · the six feeds |
| [docs/decision_ledger.md](docs/decision_ledger.md) · [docs/knowledge_graph.jsonl](docs/knowledge_graph.jsonl) | Every decision with provenance · 133 entities of why |

**Code.** `src/recon/generators/` makes the data · `connectors/` fetches and maps it ·
`ingest/` normalises and crosswalks · `engine/` decides · `db/repository.py` is the only
module that writes SQL · `agents/` is the agent layer · `web/` is the dashboard.

---

## What is real and what is not

- **The vendor field names are ours.** Of the 190 Verity and Craneware field names here, **0
  are published** — the gate is a customer login, not a paywall. Every field is tagged
  `SPEC` / `STANDARD` / `INVENTED` in `src/recon/mocks/MOCK_FIELDS.md`.
- **No connector has ever reached a vendor system.** The connectivity report says exactly
  that, and a test arms the sentence.
- **Running on a vendor's shape is not running on a vendor's data.** It proves the engine
  doesn't depend on a feed we invented, and that onboarding a sixth TPA is configuration — not
  that the mapping is correct.
- **No authentication and no tenant isolation.** The repository layer is the right seam; the
  predicate isn't there yet.
