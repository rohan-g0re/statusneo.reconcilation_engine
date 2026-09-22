# Post-Claim Pharmacy Financial Reconciliation

A hospital dispenses a drug. Money is owed twice, by two different parties, on two
different clocks — the payer reimburses the claim, and the manufacturer pays a 340B rebate
against the same dispense. Nothing in the industry joins those two halves. This is a
deterministic engine that does, plus an agent layer that explains and proposes but never
decides.

Everything here is synthetic and reproducible: same seed, same bytes, no network, no API key
required for the deterministic half.

---

## Run it

```bash
pip install -e ".[api,agent,dev]"          # api = FastAPI; agent = httpx; dev = pytest
uvicorn recon.api.app:create_app --factory --port 8000
```

`--factory` is mandatory — `create_app` builds the app, it is not one.

```bash
curl -X POST "http://127.0.0.1:8000/api/regenerate?profile=demo"   # build the dataset
cd web && npm install && npm run dev
```

Then open **http://localhost:5173/**. Use `localhost`, not `127.0.0.1` — Vite binds IPv6.

**Choosing which TPA the engine reads.** The default is Craneware's export, not the generic
feed, because in production there is no generic TPA feed:

```bash
curl -X POST "http://127.0.0.1:8000/api/regenerate?profile=demo&tpa_source=verity"
curl -X POST "http://127.0.0.1:8000/api/regenerate?profile=demo&tpa_source=generic"
```

All three produce the same 60 episodes; the verdicts differ by 1 (Craneware) and 4 (Verity)
against the generic baseline, and every difference is reconciled to a named cause in
`tests/test_vendor_sourced_parity.py`.

```bash
pytest -q        # 988 passed, 2 skipped, 1 known failure (test_query_plans, index name only)
```

---

## Diagrams

All in [docs/images/](docs/images/). The `.excalidraw` files open at
[excalidraw.com](https://excalidraw.com) via *File → Open*; the two `.png` files render
inline anywhere.

| File | What it shows |
|---|---|
| `notes.excalidraw` | **The domain map, newest.** Every actor and both money paths: Healthcare Provider, PBM, Clearinghouse → Medical Payer, the TPAs (Verity, Craneware), Beacon, the Shields fabric, the 340B rebate path and the Manufacturer's Bank. Start here. |
| `project-overview.excalidraw` | **The whole project, end to end** — generator, decision tree, ingest, crosswalk, engine, queues, agent layer. The build rather than the domain. |
| `engine-pipeline.png` | The deterministic pipeline: feeds → raw → normalized → crosswalk → episode → verdict → queue. |
| `human-gate.png` | The agent layer's propose/evaluate loop and where a human approves. |

`test1.excalidraw` and `test2.excalidraw` are earlier iterations of the domain map, kept for
history. `notes.excalidraw` supersedes both.

> The `.excalidraw` files have no exported PNGs yet — for a deck, open and export at the
> size you need rather than screenshotting.

---

## Where everything lives

**Read in this order.**

| Document | What it answers |
|---|---|
| [DESIGN_NOTE.md](DESIGN_NOTE.md) | The architecture, the decisions, what is measured, and §9 — what I would build next and the known limits. |
| [docs/claim_walkthrough.md](docs/claim_walkthrough.md) | One claim end to end, with a pharmacy/340B primer folded in. The deterministic half, for a reader who wants the flow. |
| [docs/agent_walkthrough.md](docs/agent_walkthrough.md) | The same episode through the agent layer: tool loop, fence, figure check, human gate. |
| [DEMO.md](DEMO.md) | The spoken demo script. |

**Reference.**

| Document | What it is |
|---|---|
| [docs/glossary.md](docs/glossary.md) | Vocabulary authority — NDC, 340B, TPA, TRN02, every term. |
| [docs/reconciliation_state_space.md](docs/reconciliation_state_space.md) | The 50 rules and the verdict codes they produce. |
| [docs/feed_formats.md](docs/feed_formats.md) | The six generated feeds, field by field. |
| [docs/decision_ledger.md](docs/decision_ledger.md) | Every decision with provenance — user-approved, delegated, or agent default. |
| [docs/agent_layer_design.md](docs/agent_layer_design.md) | The two agents, nine tools, the harness, the rubric. |
| [docs/connectivity_layer_requirements.md](docs/connectivity_layer_requirements.md) | The response to Assignment Doc 2 — connectors, vendors, the access gate. |
| [docs/knowledge_graph.jsonl](docs/knowledge_graph.jsonl) | 133 entities: the domain, the decisions, the traps, and why each is what it is. Queryable over MCP; also just readable. |

**Code.** `src/recon/generators/` makes the data · `src/recon/connectors/` fetches and maps
it · `src/recon/ingest/` normalises and crosswalks · `src/recon/engine/` decides ·
`src/recon/db/repository.py` is the only module that writes SQL · `src/recon/agents/` is the
agent layer · `web/` is the dashboard.

---

## The six areas the assignment asks about

| Area | Where it is answered |
|---|---|
| **Source connectivity** | `src/recon/connectors/` — registry rows, pluggable transports, schema contracts per dataset, control totals. A new TPA is a config row plus a mapping module, demonstrated by running the same engine on Verity and on Craneware. Live status at `/api/connectivity`. |
| **Data foundation** | Raw → normalized → semantic. Every source row stored verbatim with its SHA-256 before an adapter interprets it; 18 canonical record kinds; 12 crosswalk key types; failed joins parked, never dropped. |
| **Reconciliation services** | `src/recon/engine/` — 50 deterministic rules over a 4,224-configuration decision tree, reproduced exactly by an oracle test with no tolerance. Tolerances are literally zero. |
| **Agent/tool boundary** | Agents read through nine tools and propose; one write tool is `INSERT`-only and appears in no model's tool list. Every figure must be sourced or the evaluator vetoes. |
| **Scale and tenancy** | Cursor-based replay rather than snapshots; queries pinned by `EXPLAIN QUERY PLAN` tests. Tenancy is named as not built — see DESIGN_NOTE §9. |
| **Security and operations** | PHI redaction at the tool boundary, untrusted payer text fenced with a per-run nonce, immutability enforced by `RAISE(ABORT)` triggers on nine tables. Three known gaps are named rather than hidden. |

Depth for all six is in [DESIGN_NOTE.md](DESIGN_NOTE.md).

---

## What is real and what is not

Honest framing matters more than a good demo here.

- **The data is synthetic and the vendor field names are ours.** Of the 190 Verity and
  Craneware field names in this repository, **0 are published** — neither vendor publishes a
  column list, and the gate is a customer login, not a paywall we could argue past. Every
  field is tagged `SPEC` / `STANDARD` / `INVENTED` in `src/recon/mocks/MOCK_FIELDS.md`.
- **No connector has ever reached a vendor system.** The connectivity report says so in
  those words, and a test arms the sentence.
- **Running on a vendor's shape is not running on a vendor's data.** It proves the engine
  does not depend on a feed we invented, and that onboarding a sixth TPA is configuration —
  not that the mapping is correct.
