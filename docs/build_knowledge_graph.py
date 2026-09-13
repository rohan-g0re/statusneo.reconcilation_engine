"""
Build the project knowledge graph from the design conversation and documents.

Emits `docs/knowledge_graph.jsonl` in the format the MCP memory server reads, so
the same file is both a committed artefact a person can grep and a traversable
graph an agent can query via `search_nodes` / `open_nodes`.

Written in waves. Each WAVE_n function is independent and additive -- a later
session extends the graph by adding a wave, not by rewriting earlier ones.

    python docs/build_knowledge_graph.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "knowledge_graph.jsonl")

entities = []
relations = []


def E(name, kind, *observations):
    entities.append({"type": "entity", "name": name,
                     "entityType": kind, "observations": list(observations)})


def R(src, rel, dst):
    relations.append({"type": "relation", "from": src,
                      "to": dst, "relationType": rel})


# ===========================================================================
# WAVE 1 -- the domain
# ===========================================================================

def wave_1_domain():
    E("Post-Claim Pharmacy Financial Reconciliation", "Project",
      "Take-home assignment for an AI Solution Engineer role",
      "Four independent synthetic feeds are ingested, crosswalked into claim-level episodes, reconciled deterministically, then surfaced to an LLM agent layer",
      "Timebox 3-4 days, 12-16 focused hours",
      "Rubric: architecture 20, reconciliation correctness 20, agent design 20, domain understanding 15, engineering quality 15, communication 10",
      "Scope discipline is explicitly graded: a smaller correct solution is preferred over a broad incomplete one")

    E("Operator", "DomainActor",
      "A health-system specialty pharmacy -- the business that dispensed the drug and is owed money",
      "The system's user is their back-office finance and revenue-cycle team")

    E("PBM", "DomainActor",
      "Pharmacy Benefit Manager -- a contractor the insurer hires to run the drug benefit",
      "Adjudicates claims in real time at the counter, holds the contracted rate, and sends payment",
      "Pays for pills and self-administered drugs picked up at a counter",
      "Not an insurer, but for reconciliation purposes it is the party that owes the reimbursement",
      "The insurer behind it never appears in any feed and is out of scope")

    E("Medical payer", "DomainActor",
      "Pays for drugs administered by a clinician -- infusions, injections, chemo",
      "Billed via an 837 submission; responds with an 835 remittance weeks later",
      "Unlike the PBM, it is the direct counterparty and sends the remittance itself")

    E("340B TPA", "DomainActor",
      "Third Party Administrator running the 340B qualification logic for a covered entity",
      "Decides whether each individual dispense qualifies -- the patient-definition test",
      "Never sees the PBM's internal claim ID; matches on natural keys only",
      "Named real-world vendors include Verity, Macro Helix, Sentry and SunRx")

    E("Manufacturer", "DomainActor",
      "Approves and pays the 340B rebate, separately from the TPA's qualification decision",
      "Two independent failure points exist: the TPA can decline qualification, or the manufacturer can reject or simply never pay")

    E("340B", "DomainConcept",
      "A federal program letting qualifying hospitals and clinics obtain drugs at steep discounts",
      "Changes only what the drug cost, retroactively -- the patient pays the same and the payer pays the same",
      "Worked example: buy at 70, reimbursed 100 regardless; if the 340B price is 25, the pharmacy is owed 45 back",
      "Two independent qualification layers: the covered entity must be enrolled (standing status), and each dispense must pass a patient-definition test (per claim)",
      "Roughly 30-60 percent of claims at a health-system specialty pharmacy carry a rebate")

    E("Rebate model", "DomainConcept",
      "The manufacturer pays cash back, which lands in the bank",
      "Chosen for this project because the assignment's wording -- manufacturer payment, unmatched rebate -- describes it",
      "It is the only version where 340B has a bank leg to reconcile",
      "In reality a 2026 HRSA pilot, and partially vacated by a federal court in February 2026")

    E("Replenishment model", "DomainConcept",
      "The dominant real-world mechanism: discounted replacement stock via the wholesaler, not cash",
      "Produces no receivable -- the discount is an accounts-payable credit against a wholesaler invoice",
      "Deliberately not modelled; carried as a documented simplification in the design note")

    E("Reimbursement track", "Track",
      "Pharmacy benefit XOR medical benefit -- always exactly one per episode, never both, never neither",
      "Which road a drug travels is decided by how it is administered, not by choice",
      "Billing both roads is duplicate billing, i.e. fraud, so the model makes it unrepresentable rather than detecting it afterwards",
      "Mandatory because the assignment scopes the prototype to begin after a claim has been filed")

    E("340B rebate track", "Track",
      "Optional second money track on an episode",
      "Chain is strictly gated: qualification gates request, request gates manufacturer decision, manufacturer decision gates payment",
      "Rebates arrive batched -- one payment covers many dispenses")

    E("Cash verification", "DomainConcept",
      "Not a track. An attribute of every declared money movement",
      "An episode declares between zero and four movements, so cash has variable arity",
      "The bank feed is always present as a feed, but a bank record per episode is not -- its absence is the most common exception")

    for a in ["Operator", "PBM", "Medical payer", "340B TPA", "Manufacturer"]:
        R("Post-Claim Pharmacy Financial Reconciliation", "models", a)
    R("PBM", "pays", "Operator")
    R("Medical payer", "pays", "Operator")
    R("Manufacturer", "pays", "Operator")
    R("340B TPA", "decides qualification for", "340B")
    R("Manufacturer", "decides payment for", "340B")
    R("Rebate model", "is the chosen variant of", "340B")
    R("Replenishment model", "is the rejected alternative to", "Rebate model")
    R("Reimbursement track", "is part of", "Episode")
    R("340B rebate track", "is part of", "Episode")


# ===========================================================================
# WAVE 2 -- the object model and vocabulary
# ===========================================================================

def wave_2_object_model():
    E("Episode", "CoreObject",
      "The whole financial story hanging off one dispense -- the thing we reconcile",
      "The assignment calls it a Claim Financial Episode; in code it is the claim object",
      "Holds two tracks; each track points at the records touching it and carries an appended verdict history",
      "Naming convention: episode in code, claim financial episode in prose, claim reserved for the payment request")

    E("Dispense", "CoreObject",
      "The physical event -- a drug handed to a patient at a counter, or infused in a clinic",
      "One dispense creates exactly one episode")

    E("Claim", "CoreObject",
      "A request for payment sent to an insurer; exactly one per dispense",
      "Narrower than an episode -- it lives inside one, which is why the container is named episode instead")

    E("Record", "CoreObject",
      "One row from one feed, immutable, stored exactly as it arrived",
      "Records live in their own table and are linked to episodes by pointer",
      "One 835 covers 47 claims and cannot sit inside any one of them, which is why lineage is a link table rather than a field",
      "Never edited, never written back to")

    E("Verdict", "CoreObject",
      "The outcome label on ONE track, such as A-04 (paid, cash matched, settled) or C-08 (rebate paid and matched)",
      "31 reimbursement verdicts and 12 rebate verdicts",
      "Derived, never received -- recomputed at every cursor and appended",
      "Delete the whole verdict log and it rebuilds by replay")

    E("Disposition", "CoreObject",
      "The queue bucket for the whole episode: CLOSED, PENDING or EXCEPTION",
      "Exactly three values, because the question 'what do I do with this' has three answers: nothing, wait, work it",
      "Computed together with reason codes in one pass -- the rule that decides EXCEPTION is the same rule that knows why")

    E("Reason code", "CoreObject",
      "Why the disposition is what it is -- a LIST, not a single value",
      "An episode can be underpaid AND missing settlement AND short on cash simultaneously",
      "Includes INSUFFICIENT_DATA for cases the engine can deterministically detect it cannot decide")

    E("Rollup", "Rule",
      "Two track statuses become one episode disposition: worst wins, EXCEPTION > PENDING > CLOSED",
      "An episode sits in the exception queue because one track is broken while the other is legitimately still waiting")

    E("Reopened", "Rule",
      "A flag, never a fourth disposition, carrying reopened_from, previously_closed_at and reopened_on",
      "A reopened episode lands in whichever of the three dispositions recomputation produces",
      "Can originate from PENDING, not only CLOSED -- for instance a rebate clawed back while the reimbursement is still in flight",
      "Drives deterministic priority: reopened-from-closed outranks never-paid, because that money was already recognised")

    E("Lineage", "Mechanism",
      "Runs DOWNWARD: every computed number points back to the raw row that produced it",
      "Explicitly graded -- the assignment requires an investigator to trace a result back to the source record",
      "What makes an agent's citation real rather than decorative",
      "Reconstructed by joining across tables, hop by hop")

    E("Audit trail", "Mechanism",
      "Runs ACROSS TIME: the appended history of verdicts an episode has held",
      "Different direction from lineage; both are kept",
      "Free once status is append-only, and it is what gives the reopened flag meaning")

    R("Dispense", "creates", "Episode")
    R("Claim", "belongs to", "Episode")
    R("Record", "is linked by pointer to", "Episode")
    R("Verdict", "is derived from", "Record")
    R("Disposition", "is a rollup of", "Verdict")
    R("Rollup", "produces", "Disposition")
    R("Reason code", "accompanies", "Disposition")
    R("Reopened", "is a flag on", "Episode")
    R("Lineage", "traces", "Verdict")
    R("Audit trail", "records history of", "Disposition")


# ===========================================================================
# WAVE 3 -- the load-bearing decisions
# ===========================================================================

def wave_3_decisions():
    E("No SLA thresholds", "Decision",
      "Aging is never a verdict dimension -- it is a read-time sort key only",
      "The payoff: an episode with no new inbound record CANNOT change disposition",
      "That makes event-driven incremental processing provably complete rather than an optimisation you hope is safe",
      "With a threshold, an untouched episode could flip overnight from within-SLA to past-SLA, forcing a nightly sweep over everything",
      "Real industry deadlines exist -- Medicare's 30-day ceiling, state prompt-pay laws at 30/45 days, Iowa's 20-day PBM rule, CAQH CORE 370's plus-or-minus 3 business days, the 340B pilot's 45-day submission and 10-day payment windows",
      "Those are policy, never fields on a record. If reintroduced they belong in engine config, never as literals",
      "Removing SLA moved the state space from 528 verdict pairs to 372 and from 56 rules to 50")

    E("received_at", "Decision",
      "The single system-added field on every record -- when it entered our pipeline",
      "Every date native to the four real formats points backward; no record promises anything about the future",
      "One documented exception: the NACHA CCD+ Effective Entry Date, a near-term settlement instruction rather than a promise about an uncertain outcome",
      "Without it, two of the assignment's named edge cases -- late-arriving status and duplicate delivery -- cannot be represented",
      "A separate event_date field was rejected as double-counting, since the formats already carry their own domain dates as ordinary content")

    E("Cursor replay", "Decision",
      "The cursor lives at read time; the generator has no concept of now and writes the entire timeline",
      "The engine processes records where received_at <= cursor",
      "Forward and backward use the same code path -- there is no separate replay mode",
      "Gives the audit answer free: what did we believe on 31 March is just a cursor value",
      "Out-of-order arrival lives BETWEEN records, not inside one -- different records carry different received_at")

    E("Event-driven ingestion", "Decision",
      "The inbound document is the trigger and carries its own lookup keys",
      "Parse, extract keys, fetch only the episodes they resolve to, recompute those, append a status row",
      "Rejected alternative: scanning episodes under a time window, which is brute force -- you must widen the window indefinitely to catch a 200-day-old episode and correctness depends on the window being wide enough",
      "With key resolution, age is irrelevant: a 200-day-old episode is touched the instant a document referencing it arrives")

    E("Recompute never mutate", "Decision",
      "Verdicts are derived per (episode, cursor) and appended, never edited in place",
      "The exception queue is not a place things move into -- it is a query",
      "With mutation you would need undo logic for every kind of change and could never go backwards",
      "The verdict log is a record of what was derived, not the source of truth")

    E("Parked records", "Decision",
      "A document whose keys resolve to nothing is held, not discarded",
      "Every inbound document does TWO things: resolve its own keys forward, and check the parked pool backward",
      "Without the backward re-check, out-of-order arrivals never recover and an unmatched deposit stays unmatched even after the explanation lands",
      "Worked example: a bank line arrives Tuesday and parks because no remittance with its trace exists; the remittance arrives Friday, resolves its own claims AND clears the parked line")

    E("No identifier normalisation", "Decision",
      "Injected identifier drift is MEANT to cause a miss on the primary match path",
      "The miss IS the D-6 crosswalk-failure exception -- the claim is not missing, the mapping failed, which is a different fix with a different owner",
      "Normalising drift away would be the engine quietly covering up the thing it exists to surface",
      "A tiered fuzzy fallback flagged low-confidence is how real cash-posting waterfalls work, but that is a later engine decision")

    E("Generator independence", "Decision",
      "Each generator emits what its real source system would know -- its own identifiers, format and timing",
      "No generator ever sees another's output",
      "Rejected alternative: designing the generator backwards from the canonical model, which would give every feed a convenient shared claim_id and reduce the connector to a no-op join",
      "The orchestrator holds the only complete picture and writes ground_truth.json, which the ingestion layer is FORBIDDEN to read",
      "That is what makes the crosswalk scoreable rather than assumed")

    E("Deterministic boundary", "Decision",
      "Every number is born in Python. The LLM may investigate, synthesize, classify, prioritize and recommend",
      "Expected amounts, payment allocation, reconciliation status and ledger updates are deterministic and auditable",
      "calculate_reconciliation(claim_id) is a TOOL the agent calls rather than logic it reproduces -- that enforces the boundary mechanically rather than by prompt",
      "Prioritisation is a sort, not a judgement: the agent explains a ranking, it never produces one",
      "One write tool only, creating a work item, never a ledger entry",
      "The assignment states this constraint three times -- it is the main test")

    E("Database design is first-class", "Decision",
      "Store everything, including incomplete, parked and orphan rows -- nothing is discarded for not fitting",
      "Design from the query patterns backward, not the entities forward",
      "Treat read-heavy and write-heavy tables differently: raw records are write-once with minimal indexing; crosswalk and parked are read on EVERY inbound and heavily indexed; the verdict log is append-heavy with one hot read")

    E("D-6 crosswalk failure", "Exception",
      "An identifier exists in one feed but cannot be resolved in another",
      "The claim is not missing -- the MAPPING failed, which is a different fix with a different owner",
      "Deliberately produced by injected identifier drift such as leading zeros or truncation",
      "One of seven feed-level exceptions that sit orthogonal to episode state")

    R("No SLA thresholds", "enables", "Event-driven ingestion")
    R("received_at", "enables", "Cursor replay")
    R("Cursor replay", "requires", "Recompute never mutate")
    R("Event-driven ingestion", "requires", "Parked records")
    R("No identifier normalisation", "preserves", "D-6 crosswalk failure")
    R("Generator independence", "protects", "Crosswalk")
    R("Deterministic boundary", "constrains", "Agent layer")


# ===========================================================================
# WAVE 4 -- feeds, identifiers, the crosswalk
# ===========================================================================

def wave_4_feeds():
    E("Crosswalk", "Mechanism",
      "Resolving a key carried by an inbound document to the episode it belongs to",
      "Necessary because NO identifier is shared across all four feeds",
      "There are three separate identifier universes, and the pharmacy and medical feeds share literally nothing -- different standards bodies, different rails, different vocabularies",
      "Keyed on (key_type, key_value) with a partial index on live rows, so every lookup is a point seek and never a scan",
      "Eight key types: NCPDP_CLAIM, MEDICAL_CLM01, PAYER_ICN, TRN02, ALLOCATION_CODE, NATURAL_340B_PHARMACY, NATURAL_340B_MEDICAL, PBM_AUTH")

    E("PBM feed", "Feed",
      "Two files: NCPDP claim events (B1 adjudication, B2 reversal) and a separate X12 835 remittance",
      "Adjudication returns in seconds at the counter; the money arrives weeks later in a batch",
      "Identity is the composite {pharmacy NPI, Rx number, fill number, date of service} plus NDC -- there is no surrogate key",
      "The PBM separately assigns an opaque Authorization Number (503-F3) that never reaches the TPA",
      "CLP01 on a pharmacy 835 is '7845102FILL00' -- the Rx number with the fill number glued on after the literal word FILL, which is NCPDP's documented convention",
      "A B2 reversal carries no identity of its own; it repeats the transaction key")

    E("Medical feed", "Feed",
      "Two files: an 837 submission plus a 277CA acknowledgment, and an X12 835 remittance",
      "CLM01 is the provider's own claim number; CLP01 echoes it back",
      "CLP07 is the PAYER's internal control number and typically CHANGES when a claim is reprocessed, while CLP01 stays constant",
      "The 277CA was added because a clearinghouse rejection is otherwise indistinguishable on the wire from a claim still awaiting remittance -- verdict B-01 would be unreachable",
      "Specialty drugs use J-codes, with the NDC also appearing in the 2410 loop; the two unit bases rarely match numerically, which is a legitimate denial source")

    E("340B feed", "Feed",
      "A vendor JSON export. No X12, no 835, no trace number, no standards body at all",
      "Deliberately the structurally poorest feed -- that weakness is the real state of the industry, not a modelling shortcut",
      "It is why 'unmatched rebate' is one of the assignment's named exceptions",
      "Joins to the pharmacy feed on the natural key only; for medical episodes the key is {provider_npi, ndc11, service_date}, because a clinic-infused drug has no prescription and therefore no Rx number",
      "Carries the HRSA covered entity ID (e.g. DSH310074) and a 9-character HIN",
      "Reversals are submitted as the same claim with a NEGATIVE unit quantity, not a delete")

    E("Bank feed", "Feed",
      "A CSV export, deliberately the thinnest feed: date, description, amount, type, running balance, trace number",
      "Carries TWO independent identifiers that only sometimes correlate: a 15-digit ACH trace (pure banking plumbing, zero business content, always present) and TRN02 (payer-assigned, present only if the addenda survived -- roughly 80 percent of the time)",
      "Company Entry Description for healthcare claim payments is the mandated literal HCCLAIMPMT, which classifies a line before anything is parsed",
      "Deposits are separate per sender -- pharmacy, medical and rebate are three different lines, never one combined amount",
      "One deposit covers many claims, so allocation is unavoidable")

    E("TRN02", "Identifier",
      "The reassociation trace number -- the only formal link between a remittance and a bank deposit",
      "CAQH CORE Rule 370 requires the payer to put the identical TRN segment in both the CCD+ ACH addenda and the 835",
      "Breaks constantly because most basic bank exports drop the addenda entirely",
      "An industry operating rule exists purely to make this work, and it still fails in practice")

    E("Two-hop bank resolution", "Mechanism",
      "A bank line carries NO claim identifier at all",
      "TRN02 resolves to a remittance; the remittance holds the claim list",
      "One bank line can therefore touch 47 claims, found through the intermediate record and never by scanning")

    E("PLB netting", "Trap",
      "A payer nets a recoupment into a later batch and the bank sees ONLY the net amount",
      "sum(claim payments) minus PLB equals what hits the bank",
      "The explanation lives solely in the 835's PLB segment, which the bank layer is structurally blind to",
      "This is the most valuable reconciliation trap in the dataset -- the engine sees a deposit that does not match the claims it supposedly covers, and the reason is a line item about a different prescription from weeks ago",
      "Codes: WO overpayment recovery, FB forward balance, L6 interest owed (negative, increases payment), RA retroactive adjustment")

    E("Reversal versus recoupment", "Trap",
      "Same money moving the same direction, opposite meanings",
      "A reversal is pharmacy-initiated -- a B2 transaction, routine housekeeping, you already knew",
      "A recoupment is payer-initiated -- a PLB WO line with no B2 anywhere, discovered after the fact, and possibly worth disputing",
      "Actor is the discriminator and it is visible on the wire",
      "Different recommended actions, which makes it good material for the Workflow Coordinator agent")

    E("NATURAL_340B_MEDICAL", "Identifier",
      "The composite key {provider_npi, ndc11, service_date}",
      "Exists because a clinic-infused drug has no prescription and therefore no Rx number",
      "Without it roughly half the 372 verdict pairs are unreachable and the coverage assertion fails")

    for f in ["PBM feed", "Medical feed", "340B feed", "Bank feed"]:
        R("Post-Claim Pharmacy Financial Reconciliation", "ingests", f)
        R("Crosswalk", "resolves", f)
    R("TRN02", "links", "Bank feed")
    R("Two-hop bank resolution", "uses", "TRN02")
    R("PLB netting", "hides information from", "Bank feed")
    R("340B feed", "joins on", "NATURAL_340B_MEDICAL")


# ===========================================================================
# WAVE 5 -- the state space
# ===========================================================================

def wave_5_state_space():
    E("Decision tree", "Artifact",
      "Exhaustive programmatic generation of every valid path through the reconciliation state space",
      "Lives in decision_tree/ -- spec.py, build_tree.py, classify.py, verify.py, count_ifs.py",
      "Every choice at every depth is validated against the choices above it, so invalid combinations are never generated rather than generated and filtered",
      "The validator is written INDEPENDENTLY of the generator, re-deriving every constraint from the finished leaf",
      "12,093,235,200 unconstrained combinations reduce to 4,224 valid configurations -- over 99.99 percent is structurally impossible",
      "31 reimbursement verdicts times 12 rebate verdicts equals 372 pairs, and ALL 372 are reachable",
      "That exact product proves the two tracks are independent at the verdict level, so every cross-track rule is an annotation and never a prohibition",
      "3,860 coherent and 364 anomalous configurations; coherence is a clean partition with no mixed pairs",
      "50 deterministic rules: 43 track rules plus 7 cross-track",
      "About 21 if-checks run per episode -- minimum 10, maximum 32",
      "pairs.json is loaded as a LIVE TEST ORACLE: the coverage test fails if any of the 372 pairs is unproduced")

    E("Verdict frequency skew", "Trap",
      "Configuration frequency spans 252x across verdict pairs",
      "99 of the 372 pairs have exactly ONE configuration",
      "Sampling must be stratified over VERDICTS, never over configurations, or the rarest and most valuable cases vanish from every sample")

    E("X-1 compliance case", "Trap",
      "Reimbursement denied or rejected while rebate money is held",
      "Net position is negative and the rebate rests on a claim the payer refused",
      "Duplicate-discount and eligibility exposure, with repayment risk in an audit",
      "Invisible to any single-track system, which makes it the strongest walkthrough material available")

    E("Test suite", "Plan",
      "Asserts coverage over all 372 verdict pairs, byte-identical regeneration under a fixed seed, the immutability triggers actually firing, and index usage on both hot paths",
      "Turns the decision tree from a design reference into a live check")

    E("Orchestrator", "CoreObject",
      "Holds the ONLY complete picture of each episode and hands each generator a narrow slice",
      "Writes truth/ground_truth.json, which the ingestion layer is forbidden to read",
      "Has no concept of now -- it writes the entire timeline and lets the cursor live at read time",
      "Must stratify sampling over verdicts rather than configurations")

    R("Decision tree", "generates", "Verdict")
    R("Decision tree", "is the oracle for", "Test suite")
    R("Verdict frequency skew", "constrains", "Orchestrator")
    R("X-1 compliance case", "is surfaced by", "Rollup")


# ===========================================================================
# WAVE 6 -- process learnings
# ===========================================================================

def wave_6_learnings():
    E("Hand-counted state spaces drift", "Learning",
      "A hand-enumerated 34 x 15 = 510 was wrong in four separate ways that partially cancelled",
      "One state was financially identical to another already counted; one belonged in a different table entirely; two in-flight states were silently missing; one was mis-bucketed",
      "Exhaustive generation with per-depth validity predicates caught all four",
      "Two figures in the write-up were themselves asserted rather than measured -- a claimed 60x spread was actually 252x",
      "The fix is the one the finding recommends: measure, do not assert")

    E("Write the validator independently", "Learning",
      "The validator's first run reported 15 violations and the VALIDATOR was wrong, not the generator",
      "It had assumed a point-of-sale-rejected claim could carry no cash at all, forgetting that the 340B track runs off the dispense record",
      "It made the exact mistake the whole architecture exists to prevent: assuming that because the claim failed, nothing financial could follow",
      "Had one pass written both, the shared assumption would have been invisible to both")

    E("Decision provenance must be tracked", "Learning",
      "A planning agent instructed to produce a plan only wrote 5,309 lines of implementation code",
      "A reconciliation agent wrote twelve new numbered decisions into the binding architecture document, formatted identically to human-made ones",
      "An invented requirement -- malformed records -- propagated into every downstream plan despite appearing nowhere in the source assignment",
      "The fix is a decision ledger recording not just WHAT was decided but WHO decided it: approved by the user, delegated, or an agent default",
      "Conversation counts as evidence only once it is written down")

    E("Generate feeds source-first", "Learning",
      "Designing a synthetic generator from the canonical model backwards silently destroys the crosswalk it is meant to test",
      "Every feed would carry the same primary key, the join becomes a no-op, and the hardest part of the system has nothing to do",
      "The withheld ground-truth file is what makes matching scoreable rather than merely observable",
      "Building source-first also surfaces cardinality facts you would otherwise miss -- one deposit covering hundreds of claims, and recoupments netted invisibly")

    R("Hand-counted state spaces drift", "was learned from", "Decision tree")
    R("Write the validator independently", "was learned from", "Decision tree")
    R("Decision provenance must be tracked", "produced", "Decision ledger")
    R("Generate feeds source-first", "justifies", "Generator independence")


# ===========================================================================
# WAVE 7 -- artefacts and current state
# ===========================================================================

def wave_7_artifacts():
    E("START_HERE.md", "Artifact",
      "The handoff document at the repo root -- domain primer, current state, reading order, build order, traps",
      "Written because the design was settled across a long conversation and a new session can only see files")

    E("Decision ledger", "Artifact",
      "docs/decision_ledger.md -- every decision with provenance",
      "Section A settled, Section B proposed-then-resolved, Section C agent defaults now ratified",
      "Nothing is ever deleted; items that move between sections keep a row showing where they went")

    E("Build state", "Status",
      "Design complete, ZERO application code committed as of this graph's last wave",
      "Roughly 5,300 untracked lines sit under src/ from a planning agent that exceeded its brief",
      "That code predates the no-normalisation, crosswalk-indexing and database-first decisions, so it has no eight-key-type crosswalk, no partial indexes and no read/write split",
      "Treat it as a reference, not a baseline -- rewriting is likely cheaper than repairing")

    E("Build order", "Plan",
      "Wave 1 foundation and schema; Wave 2 four generators; Wave 3 ingestion, cursor and crosswalk; Wave 4 reconciliation engine; Wave 5 FastAPI and React; Wave 6 agent layer",
      "Two passes are owed before the engine: the database design pass, and the record-to-dimension mapping",
      "The mapping is the missing bridge -- spec.py defines verdicts in abstract dimensions, feed_formats.md defines records, and nothing says how to derive one from the other")

    E("Agent layer", "Plan",
      "Deliberately deferred until the deterministic layer exists, so it is designed against a working object rather than a guess",
      "Leaning toward a plain tool-calling loop over LangGraph or CrewAI, because the logic is simple and a framework would obscure the tool boundary the assignment is grading",
      "Three roles named by the assignment: Exception Investigator, Workflow Coordinator, Ops Analyst -- at least two must be built",
      "The evaluation set needs 10+ scenarios with an expected answer OR an expected tool path; tool-path assertions are the cheap win because they are deterministic to check")

    R("START_HERE.md", "summarises", "Decision ledger")
    R("Build state", "blocks", "Build order")
    R("Build order", "precedes", "Agent layer")


WAVES = [wave_1_domain, wave_2_object_model, wave_3_decisions,
         wave_4_feeds, wave_5_state_space, wave_6_learnings, wave_7_artifacts]


def main():
    for w in WAVES:
        w()
    with open(OUT, "w", encoding="utf-8") as f:
        for row in entities + relations:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    obs = sum(len(e["observations"]) for e in entities)
    kinds = {}
    for e in entities:
        kinds[e["entityType"]] = kinds.get(e["entityType"], 0) + 1

    print(f"waves        : {len(WAVES)}")
    print(f"entities     : {len(entities)}")
    print(f"observations : {obs}")
    print(f"relations    : {len(relations)}")
    print(f"written      : {OUT}")
    print()
    for k in sorted(kinds, key=lambda x: -kinds[x]):
        print(f"  {k:<16} {kinds[k]}")


if __name__ == "__main__":
    main()
