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


# --- extending an entity a previous wave already declared -------------------
#
# Waves are additive: a later session adds a wave, it does not rewrite earlier
# ones.  But facts go stale, and `E` appends rather than merges -- calling it a
# second time with the same name emits two rows for one entity, which the memory
# server reads as a duplicate and which will silently drift apart.  So a later
# wave corrects an earlier one through these two, never by re-declaring it and
# never by editing the earlier wave's source.
#
# Both raise rather than no-op.  A correction that quietly matched nothing is
# how a graph ends up asserting something the project stopped believing.

def OBS(name, *observations):
    """Append observations to an entity declared in an earlier wave."""
    for e in entities:
        if e["name"] == name:
            e["observations"].extend(observations)
            return
    raise KeyError(f"OBS: no entity named {name!r} -- declare it with E() first")


def UNOBS(name, *substrings):
    """Drop observations that are no longer true.

    Matched on a distinctive substring rather than the full string, so a
    correcting wave does not have to restate the sentence it is removing.
    """
    for e in entities:
        if e["name"] == name:
            for substring in substrings:
                hits = [o for o in e["observations"] if substring in o]
                if not hits:
                    raise KeyError(
                        f"UNOBS: {name!r} has no observation containing {substring!r} "
                        "-- it was probably already corrected by an earlier wave")
                for hit in hits:
                    e["observations"].remove(hit)
            return
    raise KeyError(f"UNOBS: no entity named {name!r}")


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


# ===========================================================================
# WAVE 8 -- what building the deterministic layer settled
#
# Waves 1-7 were written while the project was still design-only.  This wave is
# the first one written with running code behind it, so it does two jobs: it
# corrects the earlier waves where implementation proved them wrong, and it adds
# what could only be learned by building.
# ===========================================================================

def wave_8_implementation():

    # --- corrections to waves 6 and 7 --------------------------------------

    UNOBS("Build state",
          "Design complete, ZERO application code committed",
          "Roughly 5,300 untracked lines sit under src/",
          "That code predates the no-normalisation",
          "Treat it as a reference, not a baseline")
    OBS("Build state",
        "The deterministic layer is complete end to end: four generators, connector, crosswalk, engine, FastAPI read layer and React front end",
        "208 tests green; 372 of 372 reachable verdict pairs produced; 4,224 of 4,224 decision-tree configurations classified identically to the oracle",
        "Verdict fidelity against withheld ground truth: 54/54 resolvable episodes on the demo profile, 1,349/1,354 (99.6%) on the full profile",
        "The stale planning-agent code was untracked and gitignored rather than repaired -- the shipped code was written fresh against the ratified decisions",
        "The agent layer is the only assignment component not built")

    OBS("Build order",
        "Waves 1-5 are done. Wave 6, the agent layer, is all that remains",
        "Both owed passes were paid: the database design pass, and the record-to-dimension mapping that became src/recon/engine/dimensions.py")

    OBS("Decision ledger",
        "Section C now reads 20 decided, 2 deferred, nothing outstanding -- C23 closed",
        "STALE AS OF THIS WAVE: the ledger's own Build status table still says 'Implementation | Not started', and still describes the planning-agent code as untracked and unresolved. Both were true when written and are false now")

    OBS("START_HERE.md",
        "STALE AS OF THIS WAVE: still asserts 'Design: complete. Code: none committed' and 'Application code: Zero committed'. src/recon/ holds roughly 48 modules",
        "Left uncorrected deliberately -- the user placed a standing no-touch rule on the design documents, so the staleness is reported rather than edited")

    UNOBS("Generate feeds source-first",
          "one deposit covering hundreds of claims")
    OBS("Generate feeds source-first",
        "Building source-first surfaces cardinality facts you would otherwise miss -- many claims to one deposit, and recoupments netted invisibly into a later payment",
        "Corrected by implementation: one deposit covers DOZENS of claims, not hundreds -- the spec says 47 and MAX_CLAIMS_PER_REMITTANCE is 24",
        "The doc's own central warning came true during the build: every slice_ref embedded the episode id, handing each generator the identity it is forbidden to know")

    OBS("Decision provenance must be tracked",
        "Vindicated by implementation: reconciling the untracked foundation code against the ledger found FOUR ratified decisions violated by it -- A23, A24, 10/11 and C6",
        "None announced itself; every one produced code that ran and data that looked right",
        "What made them findable was a written list of what had been decided and by whom, so the code could be diffed against the decisions rather than against a reviewer's memory",
        "The ledger is therefore not documentation of the past -- it is the test oracle for work that has not been written yet")

    OBS("Two-hop bank resolution",
        "Both hops assume a trace number survives. Roughly a fifth of deposits arrive with the CCD+ addenda stripped, so TRN02 is simply absent and the two-hop path cannot start")

    # --- what building taught ----------------------------------------------

    E("Amount-and-date fallback", "Mechanism",
      "The resolution path for a deposit that carries no key at all, because its CCD+ addenda were dropped in transit",
      "Matches on the two attributes a payment always has: amount, and settlement date within AMOUNT_DATE_WINDOW_DAYS (3)",
      "A unique candidate resolves; two candidates PARK rather than guess, because picking either is a coin flip that looks authoritative whichever way it falls",
      "AllocationBasis records which basis resolved each link, so a weaker amount-and-date claim never reads as a trace-number match downstream",
      "The partial index ix_norm_keyless_amount indexes only the keyless rows, keeping the fallback off a table scan",
      "A residual false-positive rate is inherent, not a defect: two identical payments inside the window are indistinguishable on the available evidence",
      "The day window narrows a lookup keyed on amount -- it is not a scan window, and cost still scales with arrivals rather than with the age of the book")

    E("Episode dossier", "Artifact",
      "One query on one episode id returns the entire history: identity, current verdict, economics, an ordered timeline, records that failed to reach it, the full verdict log and every crosswalk key",
      "This is the realisation of A25, episode-is-the-object -- the thing a person reads and the exact payload the agent layer receives, with no second shape to maintain",
      "The timeline mixes what happened in the world with what the engine concluded, in one direction through time, because that is the order the question is actually asked in",
      "Nothing in it is invented by a model: every line is composed in Python from fields the records carry",
      "It separates when an event OCCURRED from when it was LEARNED, so a late-arriving document reads as late rather than as a contradiction")

    E("Verbatim port preserves the proof", "Mechanism",
      "src/recon/engine/verdicts.py is a line-for-line hand port of decision_tree/classify.py, deliberately not a reimplementation",
      "Exhaustive enumeration proved a property of THAT classifier; a rewrite that tidies the branch order is a different classifier about which nothing has been proved",
      "The generated artefact leaves_classified.json therefore outlives the exercise and becomes the production oracle",
      "tests/test_decisions.py::test_engine_verdicts_reproduces_all_4224_oracle_classifications asserts the port matches on all 4,224 configurations -- no threshold, because one divergence means the port is no longer the thing that was proved",
      "It runs in 0.11s, which is the argument for wiring an exhaustive oracle into CI rather than checking it once by hand")

    E("A docstring is not a test", "Trap",
      "The module docstring of src/recon/engine/verdicts.py asserted that tests ran it against all 4,224 oracle configurations. No such test existed -- nothing under tests/ imported the module at all",
      "The claim had been verified by hand during the port and then written down as though it were automated, which reads identically to a verified claim",
      "Found by a documentation refresh, not by code review: the claim was prose, so no test failure could ever surface it",
      "The port turned out to be correct on all 4,224. Only the proof was missing -- which is the dangerous case, because nothing was ever going to break",
      "Grep for the test before believing the sentence, especially when you wrote the sentence")

    E("Silent generator defects", "Trap",
      "Three defects in the generators, none of which raised an error, each producing a dataset that looked plausible and was worthless for what it was built to test",
      "assert_slice_is_blind was written, correct, imported, and never called -- isolation unenforced while reading as covered",
      "Record ids minted from a character-sum digest collided, and because the connector's idempotency key IS the source record id, a collision made it discard the second record as a redelivery: 16 of 53 remittances vanished with their trace numbers",
      "The identifier minters ignored the master seed, so two unrelated seeds produced byte-identical claim identities while every other part of the run varied",
      "Independence, idempotency and seed-variation all hold or fail invisibly. Each needs an assertion on the live path, not a comment saying it is true")

    E("Knowledge graph is generated, never written to", "Trap",
      "docs/build_knowledge_graph.py ends with open(OUT, 'w') -- it truncates and regenerates the whole file",
      "So mcp__memory__create_entities / add_observations / create_relations must NEVER be used against this graph: those writes land in the .jsonl and are destroyed by the next rebuild, with no error and no trace",
      "The memory MCP server is a READ interface here -- search_nodes, open_nodes, read_graph",
      "Extend the graph by adding a wave. Correct an earlier wave with OBS() and UNOBS(), never by re-declaring an entity with E(), which emits a duplicate row rather than merging",
      "Verify a rebuild with git diff: a diff that REMOVES lines means an earlier wave was disturbed",
      "The MCP wiring was itself broken and nobody noticed: .mcp.json set MEMORY_FILE_PATH to the RELATIVE './docs/knowledge_graph.jsonl', which the server resolves against its own working directory rather than the project root, so search_nodes and open_nodes returned an empty graph while CLAUDE.md claimed the graph was queryable",
      "Now pinned to an absolute path. A relative MEMORY_FILE_PATH fails silently -- an empty graph and a missing graph are indistinguishable through the MCP interface",
      "The .jsonl is committed and greppable, so reading it directly is always the fallback and never depends on the server being wired correctly")

    E("Score the scoreable separately", "Decision",
      "Verdict fidelity is measured over resolvable episodes and intended-miss episodes as two partitions, never as one number",
      "Episodes whose links were deliberately broken -- injected identifier drift, or a medical 340B natural key that genuinely cannot separate two same-day administrations -- are expected to miss",
      "Counting those as engine failures would reward a connector that normalised the defect away, which is exactly the behaviour A23 forbids",
      "The engine reading a smaller picture there is the D-6 exception working, not a bug",
      "Provenance: agent default, adopted because a single blended number made a correct engine look broken")

    E("Query plans are asserted, not assumed", "Decision",
      "Every hot query is pinned by a test that runs EXPLAIN QUERY PLAN and asserts no table scan, so an index regression fails a test rather than slowly costing latency",
      "The audit found five scans and ten indexes that were never chosen by the planner",
      "Sharpest lesson: a PARTIAL index whose predicate is bound as a query parameter is declined by SQLite, because it cannot prove at plan time that the parameter satisfies the WHERE clause. Making ix_norm_amount_match non-partial is what made it usable",
      "Ingest went 2.35s to 1.23s, but the durable win is that the plans are now regression-tested")

    E("SQLite thread affinity under FastAPI", "Trap",
      "sqlite3 objects can only be used on the thread that created them, and FastAPI runs synchronous dependencies on a threadpool thread that is not the one that built the connection",
      "A Depends(get_conn) generator therefore raises ProgrammingError under concurrency while passing every single-threaded test",
      "Fixed with an @contextmanager open_conn() opened inside each handler -- connection per request, created and used on the same thread")

    # --- relations ---------------------------------------------------------

    R("Amount-and-date fallback", "rescues", "Two-hop bank resolution")
    R("Amount-and-date fallback", "falls back from", "TRN02")
    R("Amount-and-date fallback", "feeds", "Parked records")
    R("Episode dossier", "realises", "Episode")
    R("Episode dossier", "is consumed by", "Agent layer")
    R("Episode dossier", "is built on", "Lineage")
    R("Verbatim port preserves the proof", "depends on", "Decision tree")
    R("A docstring is not a test", "was found in", "Verbatim port preserves the proof")
    R("Silent generator defects", "violated", "Generator independence")
    R("Silent generator defects", "illustrates", "Generate feeds source-first")
    R("Score the scoreable separately", "protects", "D-6 crosswalk failure")
    R("Score the scoreable separately", "depends on", "No identifier normalisation")
    R("Query plans are asserted, not assumed", "implements", "Database design is first-class")
    R("SQLite thread affinity under FastAPI", "constrains", "Deterministic boundary")
    R("Knowledge graph is generated, never written to", "constrains", "Decision ledger")
    R("Build state", "is corrected by", "Build order")


# ===========================================================================
# WAVE 9 -- the read layer stops narrating
#
# Wave 8 recorded the episode dossier as built.  This wave records that it was
# rebuilt: the version wave 8 described composed English for every event, and
# the user rejected that outright.  The decision is theirs, stated plainly, and
# the reasoning generalises well past this project.
# ===========================================================================

def wave_9_state_do_not_narrate():

    # --- corrections to waves 7 and 8 --------------------------------------

    UNOBS("Episode dossier",
          "every line is composed in Python from fields the records carry")
    OBS("Episode dossier",
        "REBUILT: an event is now a TAG and its FACTS -- the record kind, plus that record's own fields carried verbatim, with absent fields omitted rather than nulled",
        "The version wave 8 described composed English for every event, and no longer exists",
        "14 record kinds plus CASH and VERDICT give 16 projections and one generic renderer, replacing 51 hand-written branches across 13 functions",
        "Verified over 14,898 timeline events across both profiles: zero prose, zero nulls, zero empty fact-sets")

    OBS("Agent layer",
        "Its payload is tag-and-facts, never pre-narrated. Turning an event into a sentence is the agent's job and is deliberately left undone by the deterministic layer",
        "This also means the agent can pitch the same facts differently for an auditor and for a pharmacy technician, which a fixed sentence cannot")

    OBS("Build state",
        "210 tests green after the dossier rewrite")

    # --- what the user decided ---------------------------------------------

    E("State, do not narrate", "Learning",
      "PROVENANCE: decided by the user, emphatically, against an agent default that had already shipped",
      "The deterministic layer states facts; the agent layer turns them into sentences. Prose written in the deterministic layer is doing the agent's job one layer too early and worse",
      "Prose there is also a capability claim the code cannot keep: English existed only for branches somebody had written out, so an unanticipated record produced nothing at all while the component LOOKED able to describe anything",
      "That makes the demo more convincing than the software, which is the specific harm -- a reviewer credits the system with a faculty it does not have",
      "The tag vocabulary is already descriptive. TPA_QUALIFICATION with qualification_status=QUALIFIED carries exactly what a sentence about it carried",
      "Maintenance shape is the tell: prose grows with the cross-product of structural variation, facts grow with the number of record kinds")

    E("Tag and facts", "Mechanism",
      "One projection per record kind: an ordered list of the fields worth surfacing, and a single generic renderer",
      "Drop-absent-never-null-pad is the whole mechanism -- it is what lets flat per-kind lists cover a field space that genuinely varies, with no branching",
      "It absorbs two real shape problems without special-casing: four TPA kinds sharing one wide sparse adapter dict, and two kinds each built by two adapters under different key names",
      "The output's key set therefore states what the record has, which is itself information the consumer can use",
      "Money stays integer cents in the payload. Formatting is the display edge's job; the agent wants the exact figure, not a rounded string",
      "A code value is a SLOT, not a template. Published vocabularies already carry descriptions written by the standards body, so '{code}: {description}' is one template rather than one per code -- get this wrong and the count explodes for no gain")

    E("Silent dispatch miss", "Trap",
      "The renderer looked up a handler per record kind and did a bare `continue` when it found none",
      "Four record kinds had no handler and were therefore absent from every episode payload for the entire life of that implementation, with no error anywhere",
      "A dispatch table with a silent default is the same failure as a guard that is never called: output stays plausible while coverage is quietly partial",
      "Fixed by a completeness check that raises at import, so a missing projection fails where it is introduced rather than evaporating at runtime")

    E("Service lines are embedded, never rows", "Decision",
      "PROVENANCE: agent default, ratified after investigation when the user asked why a declared record kind produced nothing",
      "The _LINE in REMITTANCE_CLAIM_LINE means claim-within-batch, NOT service-line-within-claim -- an 835 batches many claims under one BPR02 total, so it needs a parent and children",
      "An 837 arrives one-per-claim with nothing above it to split, so a MEDICAL_SUBMISSION_LINE has no referent. It was copied from the two genuine parent/child pairs and never described in the object model, even in the planning draft that first carried it",
      "Multi-line claims are the majority (68-73% carry two service lines), so the kind was not dead for lack of data -- both sides embed lines as arrays, consistently",
      "Removed from the enum and the schema CHECK")

    # --- relations ---------------------------------------------------------

    R("State, do not narrate", "is implemented by", "Tag and facts")
    R("Tag and facts", "rebuilt", "Episode dossier")
    R("Tag and facts", "fixed", "Silent dispatch miss")
    R("State, do not narrate", "scopes", "Agent layer")
    R("Silent dispatch miss", "resembles", "Silent generator defects")
    R("Silent dispatch miss", "resembles", "A docstring is not a test")
    R("Service lines are embedded, never rows", "constrains", "Record")
    R("Service lines are embedded, never rows", "clarifies", "Medical feed")
    R("State, do not narrate", "depends on", "Deterministic boundary")


# ===========================================================================
# WAVE 10 -- the agent layer, designed
#
# The deterministic layer is finished, so C20 (the agent layer) and C21 (its
# evaluation set) are no longer deferred.  This wave records the architecture
# that was settled, and the research that settled it -- roughly a dozen parallel
# passes over the harness-engineering, LLM-as-judge, abstention, structured-output
# and healthcare-RCM literature.  Almost every finding here contradicted an
# earlier plan, which is why they are worth keeping.
# ===========================================================================

def wave_10_agent_layer():

    # --- corrections ---------------------------------------------------------

    UNOBS("Agent layer",
          "Leaning toward a plain tool-calling loop over LangGraph or CrewAI")
    OBS("Agent layer",
        "SETTLED: a custom harness in plain Python, roughly 150 lines, over an OpenAI-compatible client. No framework",
        "The deciding fact is that NO surveyed harness ships an eval loop -- Pi, DeepSeek Harness, Prime Intellect's verifiers and PyHarness were all checked and none has any evaluator, critic, judge or scoring abstraction",
        "Harnesses ship loop STRUCTURE. The eval loop is ours to write whatever sits underneath, so the framework choice only decides plumbing around a thing we are building anyway",
        "Pi and DeepSeek Harness are TypeScript coding-agent harnesses; adopting either means a sidecar and RPC to Python for every database call",
        "PROVENANCE: user decided, after asking for the three options to be ranked")

    OBS("Episode dossier",
        "Two readings of one object: each projection declares a `simple` subset, and every event publishes `essential` -- the keys of that subset actually present",
        "The split lives in Python rather than the front end because the agent wants the short view for the same reason a person does, and a React-side filter would be invisible to it",
        "Lineage level two is a separate call: GET /api/record/{raw_id} returns the verbatim source line plus payload and file hashes, rather than inlining payloads no question needs")

    OBS("Build state",
        "The dashboard is a two-column layout: queues on the left, one sticky episode panel on the right that survives scrolling and cursor moves",
        "Light mode only, unconditionally -- the adaptive OS-theme palette is gone")

    # --- the architecture ----------------------------------------------------

    E("Proposer and Evaluator", "Mechanism",
      "The two agents inside the Workflow Coordinator harness. The Proposer suggests a next action; the Evaluator grades it",
      "Both have tool access and both are grounded in structured data -- the Evaluator can query the database to verify a claim rather than only reading the Proposer's text",
      "The Evaluator NEVER sees the Proposer's reasoning trace. Same inputs, fresh trace",
      "PROVENANCE: user decided the two-agent split and named the roles; the zero-shared-context rule is an agent default taken from Cognition's measured result",
      "Cognition reversed their own position on this. 'Don't Build Multi-Agents' attacks parallel WRITERS whose artifacts must be merged; their follow-up endorses exactly this shape -- 'writes stay single-threaded and the additional agents contribute intelligence rather than actions'",
      "Their review agent works BETTER with zero shared context, because a shorter context detects more: stale framing from the first attempt biases the second pass into agreeing")

    E("Checklist, not a score", "Mechanism",
      "The Evaluator grades a checklist of items rather than emitting a single confidence number. Ticks against threshold is the gate",
      "PROVENANCE: user decided, self-correcting from an earlier instruction that the Evaluator should emit the score directly",
      "It converges with OpenAI's HealthBench, which grades physician-written criteria independently -- met earns full points, unmet earns zero, score is earned over possible, across 48,562 criteria",
      "This resolves the apparent tension between 'the Evaluator scores it' and 'Python computes it': the Evaluator judges each item, the harness counts the ticks. One mechanism, not two",
      "Verbalised confidence is the weakest signal in every study that measured it -- the ranking is logprobs, then sampling-consistency, then self-rating, and 'Wired for Overconfidence' locates a Confidence Mover Circuit tied to RLHF",
      "Asking for confidence is asking the model to grade its own homework; asking for a quote is asking it to show the homework")

    E("Threshold hooks", "Mechanism",
      "What the harness does with the Evaluator's output, which is the only place its verdict is consumed",
      "BELOW threshold: the Evaluator's REASON goes back to the Proposer, which researches again from the documents rather than re-arguing its previous position",
      "ABOVE threshold: the Evaluator's output is discarded entirely and the Proposer's next steps become the prospective work item",
      "PROVENANCE: user decided both branches",
      "The re-prompt being aimed at a stated gap rather than blind is the one idea worth copying from OpenHands' Goal Completion Loop, whose GoalVerdict carries a `missing` field that drives the next iteration")

    E("The ceiling is checked first", "Decision",
      "The iteration cap is evaluated structurally before the semantic gate, never inside it",
      "CrewAI has an open bug (#3847) where handle_max_iterations_exceeded prepares a forced answer at the ceiling and later model calls silently overwrite it, so the loop does not reliably stop -- because the cap check is not structurally prior",
      "Four distinct outcomes, never one boolean: complete, insufficient_data, stalled, capped. Collapsing them loses the difference between 'this cannot be resolved from the available documents', which is an answer, and 'we ran out of budget', which is a failure",
      "Systems with no guard at all are the cautionary set: DeepSeek Harness states it has no built-in turn budget, and OpenCode's core carries documented real infinite-loop bugs plus a third-party anti-loop plugin",
      "Even Claude Code's stop_hook_active is a convention hook authors are asked to honour, not a cap the harness enforces")

    # --- findings that contradicted a plan -----------------------------------

    E("Reasoning models abstain worse", "Trap",
      "AbstentionBench (20 datasets, 35k+ queries) found reasoning fine-tuning costs an average 24% of abstention rate versus non-reasoning counterparts, and scaling model size barely helps",
      "So a thinking model is MORE likely to confidently answer the unanswerable -- the exact failure the assignment grades, since it requires the agent to say when data is insufficient",
      "Extended thinking on the Proposer is therefore a risk to measure, not a free upgrade")

    E("Field order beats everything", "Trap",
      "Reasoning-first versus answer-first in a structured output schema is worth roughly 60 percentage points: GPT-4o-mini on GSM8K scored 94.20% with reasoning first and 31.80% with the answer first",
      "The paper that popularised 'structured output hurts reasoning' contained its own refutation: 100% of its JSON-mode responses placed the answer key before the reason key. It was never token masking, it was ordering",
      "pydantic.BaseModel preserves field declaration order into model_json_schema(), so this is a one-line property of how the model is declared",
      "Related trap -- ENUM COLLAPSE: when the decoder masks the model's preferred token it falls back to the most schema-legal common option, so a status field can return the modal value ~11% more often under constraint while remaining 100% schema-valid")

    E("A human gate can be worthless", "Learning",
      "Cigna's PXDX had a human in the loop and is being litigated as unlawful rubber-stamping: doctors batch-denied without opening files, 300,000+ claims in two months averaging 1.2 seconds each",
      "So the design rule is not 'add an approval step'. It is: show the reviewer the underlying evidence rather than the conclusion, make the recommendation EDITABLE, track override rate as a first-class metric, and alarm on approval latency",
      "The editable recommendation is a safety property rather than a convenience -- a reviewer who can only approve or reject is ratifying, while one who can correct the action is judging",
      "The diff between proposed and accepted is also the sharpest error signal available: unlike appeal outcomes it arrives immediately and is not contestation-biased",
      "UnitedHealth's nH Predict is the matching trap on metrics: roughly 90% of appealed denials were reversed, but only 0.2% were ever appealed. A low override rate can mean the friction to challenge is high, not that the system is right",
      "Candid Health independently reached the same architecture -- a deterministic rules engine for the financial-correctness path, with LLMs only suggesting new rules")

    # --- relations -----------------------------------------------------------

    R("Proposer and Evaluator", "is bounded by", "The ceiling is checked first")
    R("Proposer and Evaluator", "scores with", "Checklist, not a score")
    R("Threshold hooks", "consumes", "Checklist, not a score")
    R("Threshold hooks", "feeds back into", "Proposer and Evaluator")
    R("Proposer and Evaluator", "implements", "Agent layer")
    R("Proposer and Evaluator", "reads", "Episode dossier")
    R("Reasoning models abstain worse", "constrains", "Proposer and Evaluator")
    R("Field order beats everything", "constrains", "Proposer and Evaluator")
    R("A human gate can be worthless", "constrains", "Agent layer")
    R("A human gate can be worthless", "depends on", "Deterministic boundary")
    R("Checklist, not a score", "avoids", "A docstring is not a test")


# ===========================================================================
# WAVE 11 -- building the agent layer, and what running it taught
# ===========================================================================
#
# Wave 10 recorded the agent layer's DESIGN. This wave records what building and
# running it produced -- including defects no test caught, because each one was a
# property nothing was asserting.


def wave_11_agent_layer_built():

    # --- corrections to earlier waves ---------------------------------------

    UNOBS("Build state", "The agent layer is the only assignment component not built")
    OBS("Build state",
        "The agent layer is built: three roles, nine tools, a FastAPI router, an SSE-streamed loop and a React analysis screen",
        "583 tests green. The agent layer added roughly 370 of them",
        "Two eval fixtures skip rather than assert -- they were recorded live and predate later prompt changes, and re-recording needs the provider")

    OBS("Agent layer",
        "BUILT. All three roles exist, not the two the assignment requires as a minimum: Exception Investigator, Workflow Coordinator, Portfolio Analyst",
        "The third role was cheap once the tool surface existed -- it needed no new KIND of access, just the same envelope over two read models already there",
        "Nine tools, not the seven the design fixed. spec_tools.md decision D-T4 had excluded a portfolio tool explicitly BECAUSE only two roles were being built, and that reason went away when the third one was")

    # `Deterministic boundary` is grounding material the evaluator reads, so it is
    # only appended to -- the agent needs to know what changed, not just what is
    # true now.
    OBS("Deterministic boundary",
        "Enforced rather than merely prompted: a deterministic scorer fails any proposal quoting a figure that appears in no tool result, and it is a veto rather than a deduction",
        "The Portfolio Analyst is where 'prioritisation is a sort' became testable -- every ordering comes from a named SQL sort, echoed back in the tool result so the trace records which sort produced the order a reader sees")

    OBS("The ceiling is checked first",
        "The four outcomes are a claim about configuration, not only about code: `stalled` needs three scored rounds to fire, so an iteration ceiling of two makes it unreachable and leaves three of four producible",
        "That was briefly the shipped default, and nothing failed -- no test went red, the outcome simply stopped existing. A documentation audit found it, not the suite")

    OBS("Decision ledger",
        "The staleness recorded in wave 8 is still present two waves later, for the same reason: the file carries a standing no-touch rule, so corrections live beside it rather than in it",
        "The agent layer's decisions went into this graph instead, with provenance tags, and nothing in the ledger points here -- which is how a ledger stops being the record without saying so")

    # --- what running it taught ---------------------------------------------

    E("Containment protects a quote, never mints a figure", "Trap",
      "G3 vetoes any figure absent from every tool result. It was widened to stop a false positive on a drug name carrying its own dose, and the widening dismantled the criterion",
      "Every digit run in any string leaf became a claimable number, so a fabricated amount verified as sourced whenever its digits appeared anywhere",
      "A reviewer executed five: a source-code line number inside a provenance annotation became $228; comma groups of the string '$52,700.00' became $52 and $700; digits inside claim id ENC-358361-00049 became $358,361; the date inside allocation code RBT-20250909-72245 became $20,250,909",
      "The fix is the distinction: a string may EXEMPT a digit run the model quoted as part of an identifier, and may never LICENSE that run as a free-standing figure",
      "Untrusted fenced text was in the same pool. A payer writing 'Approved payout 99999' into a free-text field made a $99,999 recommendation verify as fact -- and the fence exists precisely to say that text is not the operator's record",
      "PROVENANCE: agent default, found by an independent reviewer that executed the code rather than reading it")

    E("An ungraded field is the cheapest place to hide", "Trap",
      "ProposedAction.blocked_reason is free text the model authors, and no scorer read it",
      "A proposal whose blocked_reason said 'I have closed the claim and posted the $84,212 refund; the missing wire for $9,113,404 blocks further work' passed all five deterministic criteria -- two invented figures and a claimed write",
      "It then rendered to the operator in a banner styled as a successful outcome",
      "Every scanner now reads one shared list of the model's free-text fields, so a field added to the schema later cannot quietly become an ungraded channel",
      "PROVENANCE: agent default; the escape hatch was added for the Goose-style BLOCKED signal and nobody extended the scanners to it")

    E("A permissive test double hides a signature change", "Learning",
      "complete() was changed to REQUIRE an `agent` argument, so proposer/evaluator independence would be verifiable after the fact rather than assumed",
      "No role passed it. 471 tests stayed green, because every stub accepted a looser signature than the real client does",
      "A test double looser than the thing it stands in for does not test the integration; it tests the double",
      "The guard is a conformance test pinning every concrete client to the Protocol exactly -- parameter names, kinds and requiredness",
      "PROVENANCE: agent default, found only by a live run")

    E("Provider tiers fail independently", "Trap",
      "deepseek-chat and deepseek-flash stalled for 60 seconds and returned HTTP 200 with an empty body, while deepseek-v4-pro answered the identical request in 1.34 seconds",
      "It was diagnosed as account-wide throttling and reported to the user as such. That was wrong, and one per-model probe would have shown it",
      "The provider signals overload by STALLING and answering empty, never by returning 429 -- so there is no status code and no Retry-After to branch on",
      "A 200 is not proof of an answer. An empty body made .json() raise an uncaught JSONDecodeError; a body that parsed with no choices became a well-formed response with no tool calls, which a role reads as 'the model declined' and answers with another stalled request",
      "investigator_model was hardcoded rather than env-read, so the one variable that would have redirected it was silently ignored",
      "PROVENANCE: agent default; the misdiagnosis was the agent's, corrected by probing each model separately")

    E("Latency lives in the payload", "Learning",
      "A Decide run went from 71 seconds to 20, and from 161,675 prompt tokens to 71,717, with no change to the loop, the models or the checklist",
      "Profiling one real run found the evaluator's user turn at 36,674 characters, rebuilt and re-sent on every call. Tool results were 19KB of it and grounding clauses 11.8KB -- both sending the whole corpus when the evaluator only grades what the proposal cited",
      "A cited tool result now appears in full and an uncited one gets a one-line receipt naming its shape, so 'there was evidence you ignored' stays visible without paying for the body",
      "Ordering matters as much as volume: the provider caches on a token PREFIX, so the proposal -- the one section that differs every iteration -- has to go last or it invalidates everything behind it",
      "The knock-on mattered more than the latency. The same run had been exhausting its token budget inside two iterations and stopping at `capped` with a score of 0; with the same budget it reached 92.86 against a threshold of 80",
      "PROVENANCE: agent default, in response to the user asking why it was slow")

    E("One definition, or it drifts", "Trap",
      "The selection 'which judge criteria apply to this proposal' was open-coded in five places: the coordinator, two fixture recorders and two test helpers",
      "Changing the criteria set moved one and left four, which surfaces as evaluator_structurally_invalid -- a checklist and a verdict disagreeing about what was asked",
      "The same shape appeared twice more in one session: two untrusted-text fence formats that never met, and a citation regex that knew two of the three roles that emit citations",
      "A shared renderer has to know every producer. That one silently knew two of three, and the Analyst's citation tokens rendered as literal brackets on every run",
      "PROVENANCE: agent default")

    E("Portfolio Analyst", "Plan",
      "The third role, and the one the design puts on the dashboard, because 'what is the state of the book' is the dashboard's own question",
      "Single pass with tool calls, like the Investigator -- not the propose/evaluate loop, because no action is being proposed and so there is nothing to gate",
      "Two tools over read models that already computed everything: get_portfolio_overview for the aggregates, get_exception_queue for ranked rows",
      "Its graded constraint is that it explains a ranking and never produces one, so order_by is a required argument restricted to the whitelisted SQL sorts and echoed back in the result",
      "Verified live: asked where the money is concentrated it called get_exception_queue three separate times -- variance_desc, age_desc, reopened_first -- rather than merging them into an order of its own, and named the sort behind every claim",
      "PROVENANCE: user decided -- the user pointed out the role was in the design and absent from the build")

    E("A prototype is sized to be legible", "Decision",
      "The Coordinator was built to be calibrated and needed to be shown. Judge criteria 8 to 4, iterations 5 to 3, proposer tool rounds 3 to 2",
      "Nothing about the safety story moved: every deterministic criterion still runs, including all four vetoes, because they are Python and cost nothing. What got trimmed is the judge criteria, which cost a reasoned finding each",
      "Of the four dropped, two sit next to vetoes that already cover their ground -- figures-labelled next to figures-sourced, stays-advisory next to no-close-no-post",
      "A reviewer watching the fourth near-identical finding scroll past learns nothing the third did not tell them",
      "PROVENANCE: user decided -- 'simplify it so the demo gets crisper'; the specific numbers are agent defaults")

    E("A committed secret is not undone by deleting it", "Trap",
      "A test asserting 'the journal redacts the API key' used the REAL key as its fixture. The assertion passed and the secret was pushed -- the proof and the leak were the same line",
      "It was also in .claude/settings.local.json, inside approved curl invocations, because that file records approved shell commands verbatim",
      "Proving redaction needs a string of the right SHAPE, not a working secret: shape is all the redaction logic inspects",
      "Deleting it from the working tree removes it from neither history nor the remote. Rotation is the only remediation that counts",
      "PROVENANCE: agent default, caught by the user")

    E("Watchable means the scorer too", "Learning",
      "The design argues a reviewer who watches the loop reason can judge whether to trust it. The judge's work was watchable and the scorer's was not",
      "The four vetoes are deterministic, so they were never part of the evaluation event -- and a reviewer saw three green SUPPORTED chips sitting directly above 'score: 0.000' with nothing to explain the contradiction",
      "It reads as a broken scorer rather than as a veto doing its job",
      "The critique fed back between rounds was computed, sent, and journalled nowhere, so the stream showed two independent-looking proposals and left the reviewer to infer what changed the model's mind",
      "Neither changed any score or any outcome. Only the record of them was missing, which is its own kind of defect",
      "PROVENANCE: agent default, found by browser testing")

    # --- relations ----------------------------------------------------------

    R("Portfolio Analyst", "is a role of", "Agent layer")
    R("Portfolio Analyst", "enforces", "Deterministic boundary")
    R("Containment protects a quote, never mints a figure", "threatens", "Deterministic boundary")
    R("An ungraded field is the cheapest place to hide", "threatens", "Deterministic boundary")
    R("An ungraded field is the cheapest place to hide", "was found in", "Proposer and Evaluator")
    R("A permissive test double hides a signature change", "threatens", "Proposer and Evaluator")
    R("Provider tiers fail independently", "constrains", "Agent layer")
    R("Latency lives in the payload", "improves", "Proposer and Evaluator")
    R("One definition, or it drifts", "threatens", "Checklist, not a score")
    R("A prototype is sized to be legible", "shapes", "Checklist, not a score")
    R("A prototype is sized to be legible", "risked", "The ceiling is checked first")
    R("Watchable means the scorer too", "realises", "A human gate can be worthless")
    R("Watchable means the scorer too", "documents", "Threshold hooks")
    R("A committed secret is not undone by deleting it", "threatens", "Build state")
    R("Containment protects a quote, never mints a figure", "is the same family as", "A docstring is not a test")
    R("A permissive test double hides a signature change", "is the same family as", "A docstring is not a test")
    R("One definition, or it drifts", "is the same family as", "Hand-counted state spaces drift")


# ===========================================================================
# WAVE 12 -- explaining the build to a human, and what that exposed
# ===========================================================================
#
# Two walkthrough documents were written for the interview debrief and read back
# section by section by someone who stops at the first gap. That reading found
# defects in the documents, in a schema comment, and in three file pointers --
# none of which any test could have caught, because none of them is code.


def wave_12_explaining_the_build():

    # --- correction to an earlier wave --------------------------------------

    OBS("Build state",
        "Two walkthrough documents exist for the interview debrief: docs/claim_walkthrough.md for the deterministic layer and docs/agent_walkthrough.md for the agent layer",
        "Both trace the same episode -- verdicts A-07 and C-09 -- so the two documents join into one continuous story rather than two overlapping ones")

    # --- what reading it aloud exposed --------------------------------------

    E("A pipeline explanation breaks at the seam, not the hard part", "Learning",
      "A reader followed the crosswalk, the verdict ladder and the deposit allocation without trouble, and got lost where one program ended and another began",
      "Their words: 'I don't know where we are. I just think that we did not start from where we ended'",
      "The missing step was between the generator writing a feed file and the connector reading it -- two separate programs with a folder between them, and no sentence saying so",
      "What fixed it was three statements: program A exits, this is what survives on disk, program B starts fresh and can see only that",
      "The same gap exists at every thread and request boundary, and is invisible to the writer because the writer knows both sides",
      "PROVENANCE: agent default, found by a reader working through the document out loud")

    E("Draw a diagram from its effects, not its branches", "Trap",
      "The ingest diagram showed the decision -- does this record create an episode -- and stopped there, omitting that BOTH branches then write crosswalk_key",
      "A reader who followed it concluded the crosswalk table appeared from nowhere one section later, and reported the two sections as contradicting each other",
      "The engine diagram carried the same error pointed the other way: it drew the seven cross-track checks feeding the disposition, when the two verdict codes decide the bucket by lookup and the checks can only escalate it afterwards",
      "Both were drawn from control flow, and both were therefore wrong about what actually lands",
      "The rule is that every arrow ends in a named artifact -- a table, a file, a returned value -- and any branch that writes something has to show the write",
      "PROVENANCE: agent default, both caught by the reader rather than by review")

    E("A pointer that names a file nobody has", "Trap",
      "CLAUDE.md, the /complete_compound command and src/recon/agents/roles/__init__.py all direct a reader to docs/agent_layer_readiness.md",
      "No such file exists or ever did. The document is docs/agent_layer_data_readiness.md",
      "Three independent references agreeing on one wrong name reads as verification, which is worse than a single reference being wrong",
      "The same session found the schema commenting episode_id as 'EP-000001' while the connector mints 'E-000042'",
      "Neither breaks anything at runtime, which is exactly why neither was found until somebody tried to follow the pointer",
      "PROVENANCE: agent default, found while running this command's own discoverability step")

    E("Wires run and not connected", "Learning",
      "PROMPT_VERSION is computed in all four prompt modules -- a blake2b over that module's rendered template strings -- so a journal could record which prompt version produced a run",
      "Nothing reads it. It is defined four times and referenced nowhere else in src/",
      "The Outcome returned when the loop is capped by the TOKEN budget carries a note saying the ITERATION budget was exhausted, because the note is built from module constants rather than the live budget",
      "The true cause is recorded correctly in that round's gate journal event, so the log is right and the returned object is wrong",
      "Neither is a failure anything would notice: one is unused, the other is a wrong string sitting beside a right one",
      "PROVENANCE: agent default, found while mapping the layer for documentation rather than by a test")

    E("Measure the claim against the generated data", "Learning",
      "A reader doubted two claims about the generator: that the decision tree is used there at all, and that each generator emits exactly one record per episode",
      "Both were settled in one message by counting the demo feeds, not by explaining again",
      "The counts: TPA events per dispense run 1 to 7, medical records per claim 2 to 4, pharmacy claim events per Rx 1 to 3 -- the record count varies because the leaf says what happened, not how many documents saying it takes",
      "The doubt was reasonable rather than obstructive: this repository has a recorded case of a docstring asserting a test that did not exist, so confident prose about it is unverified by default",
      "PROVENANCE: agent default, in response to the reader challenging the claim")

    # --- relations ----------------------------------------------------------

    R("A pipeline explanation breaks at the seam, not the hard part", "documents", "Build state")
    R("Draw a diagram from its effects, not its branches", "is the same family as", "Silent dispatch miss")
    R("Draw a diagram from its effects, not its branches", "threatens", "Episode dossier")
    R("A pointer that names a file nobody has", "is the same family as", "A docstring is not a test")
    R("A pointer that names a file nobody has", "threatens", "Decision provenance must be tracked")
    R("Wires run and not connected", "constrains", "Agent layer")
    R("Wires run and not connected", "is the same family as", "A docstring is not a test")
    R("Measure the claim against the generated data", "realises", "A docstring is not a test")
    R("Measure the claim against the generated data", "is the same family as", "Hand-counted state spaces drift")
    R("A pipeline explanation breaks at the seam, not the hard part", "is the same family as", "State, do not narrate")


# ===========================================================================
# WAVE 13 -- trimming the repository for submission
# ===========================================================================
#
# The repository was cut down to what a reviewer should read. Process artefacts
# that documented HOW it was built were removed; everything the code reads at
# runtime, and everything the design note cites, stayed.


def wave_13_trimmed_for_submission():

    OBS("START_HERE.md",
        "REMOVED FROM THE REPOSITORY at submission. It was the handoff document for picking the project up to work on it, which is not what a reviewer is doing",
        "Its content did not vanish: the domain primer is now docs/claim_walkthrough.md, the architecture summary is DESIGN_NOTE.md, and the reading order is the pointer block at the top of README.md",
        "This entity is kept rather than deleted because the graph records what the project decided, and 'we removed the handoff document' is one of those decisions")

    OBS("Build state",
        "Trimmed for submission: plans/ (five stale implementation plans whose schema START_HERE.md itself recorded as wrong), docs/solutions/, four agent-layer process documents, START_HERE.md, and the agent tooling under .agents/ and .claude/ were removed or untracked",
        "What stayed is what the code reads at runtime or the design note cites -- docs/knowledge_graph.jsonl above all, which grounding.py resolves by path at import",
        "The submission surface is now README.md, DESIGN_NOTE.md, DEMO.md, two walkthroughs, the feed spec, the state space, the ledger, the glossary, and docs/images/")

    E("Deleting a document orphans every pointer into it", "Trap",
      "Removing eight documents left dangling citations in four source modules, two test modules and two design documents -- all of them comments, none of them caught by any test",
      "The suite stayed green through the entire deletion, because a docstring naming a file that no longer exists is not a failure any runner checks",
      "The check that works is mechanical: after deleting a file, grep every tracked file for its basename and fix what comes back",
      "This is the same failure the previous wave recorded as 'A pointer that names a file nobody has', arriving from the opposite direction -- that one was a name that never existed, this one is a name that stopped existing",
      "PROVENANCE: agent default, found by grepping after the deletion rather than by a test")

    R("Deleting a document orphans every pointer into it", "is the same family as", "A pointer that names a file nobody has")
    R("Deleting a document orphans every pointer into it", "threatens", "Build state")


# ===========================================================================
# WAVE 14 -- the connectivity assignment
# ===========================================================================

def wave_14_connectivity():

    E("Connectivity assignment", "Artifact",
      "docs/Assignment_Doc_2.pdf -- StatusNeo's Connectivity Assessment, dated 11 September 2026, delivered after the reconciliation build was already complete",
      "Assesses Beacon plus five priority 340B TPAs -- Verity 340B, PharmaForce, Craneware, Macro Helix, Pillr/RxStrategies -- on whether a direct machine-to-machine interface is publicly proven",
      "Its subject is the layer UPSTREAM of everything already built: how data is obtained, authorised and landed, not what is done with it afterwards",
      "Introduces the Shields connector fabric, a six-step connector build method, a working-connection versus production-ready split, and a Week 1-3 Go/Amber/Red vendor-access gate inside a 26-week programme",
      "Of the five TPAs, only Verity and Craneware publish a proven machine-to-machine path, and it is scheduled SFTP rather than an API. Macro Helix, PharmaForce and Pillr have no public specification for transport or payload",
      "Answered by docs/connectivity_layer_requirements.md on the connectivity_layer branch")

    E("Beacon", "DomainActor",
      "The 340B rebate-model platform manufacturers use -- it sits IN FRONT OF the manufacturer, not in front of the covered entity",
      "Receives already-qualified claims and returns an acknowledgement, a validation outcome, a Beacon ID, a rebate status and a payment reference",
      "A claim a TPA calls qualified can still die at Beacon -- the two decisions are independent, which is exactly why they are separate systems of record",
      "Authentication is a documented two-token model, an Access Token plus a separate Private Token, with the covered entity granting Read or Read/Write partner permission per 340B ID",
      "The only source in the whole assessment with published pharmacy and medical claim field templates, which is why it is the only one a faithful adapter can be built for",
      "Absent from the existing object model entirely: the 340B feed collapses TPA qualification and the manufacturer decision into one track, where Doc 2 splits them across two companies",
      "CORRECTS the `340B TPA` and `Manufacturer` entities without editing them, because both are grounding material. A TPA works for the COVERED ENTITY -- the hospital hires and pays it, so it argues for the hospital's discount -- while Beacon works for the manufacturer. They sit on opposite sides of the table",
      "Source-of-truth split: the TPA is authoritative for qualification, Beacon for rebate status, the bank for settled cash, and Shields for nothing except the consolidated financial picture",
      "Cash flows FROM the manufacturer TO the covered entity's own account. The manufacturer's bank is never a system we connect to, because under the rebate model the hospital already paid full commercial price and is out of pocket until the rebate arrives")

    E("Only Beacon is an outbound connector", "Learning",
      "Every other source in the connector fabric is a pull -- the Direction row on each TPA page reads inbound to Shields, and only Beacon's reads outbound as well",
      "Shields is a consumer and reconciler, not a router: the pharmacy system feeds the TPA directly through the vendor's own contracted flow, and Shields ingests a copy in parallel",
      "Putting Shields in the path before the TPA would make it the operational system of record for qualification, which the source-of-truth table explicitly refuses",
      "Submission ownership is a per-covered-entity decision -- mode A has Shields submitting directly, mode B has the TPA submitting. If both do it, the same claim is submitted twice")

    E("Shields connector fabric", "Mechanism",
      "One shared middleware strip every source passes through: API/SDK gateway, secure file and EDI, auth and secrets, schema registry, idempotency, retry/replay, monitoring",
      "The argument inside that one box is build this once, not seven times -- adding a vendor becomes an adapter on an existing spine",
      "Four production patterns must be supported: API/SDK, SFTP/structured files, healthcare EDI X12/NCPDP, and banking/ERP -- direct-source does not mean API-only",
      "Scored against what exists: EDI/X12/NCPDP is finished, SFTP and banking are half-built and need only transport, API/SDK is the one genuinely new build",
      "The vendors own the interfaces, Shields owns permission and identity, and the delivery team owns the fabric -- Shields itself has none of it today, which is the premise of the engagement")

    E("Connector-ready", "Decision",
      "A third scope level, named because neither of Doc 2's own two is reachable in a prototype: both working connection and production-ready begin with the word authorized",
      "Beacon, Verity, Craneware, Macro Helix, PharmaForce and Pillr are contracted enterprise products gated behind a covered entity -- no self-service signup, no token obtainable from outside a customer relationship",
      "Means: adapter built to the vendor's published contract, exercised against a mock reproducing that contract, wrapped in the real transport and auth machinery, switchable to a live endpoint by configuration alone",
      "Defensible on the document's own terms -- it says the interface packs must be obtained through vendor support, and that no public specification exists for four of the six platforms",
      "PROVENANCE: approved by the user")

    E("Production hardening is out of scope", "Decision",
      "Doc 2's six-step build method supplies the cut line for free: steps 1-5 produce a working connection, step 6 is the entire delta to production-ready",
      "Dropped accordingly -- retry, backoff, replay orchestration, connector observability, alerting, DQ quarantine surfacing, secrets rotation, backfill, runbooks, lineage tooling, cutover, performance, and exhaustive edge-case reconciliation",
      "Kept despite sounding like hardening: checkpointing, idempotency and schema validation are named in STEP 3, and control totals in STEP 5. Reading the steps rather than the adjective is what settles it",
      "The first draft mixed the two levels and had to be cut back, from 30 requirements to 19; the user rejected it in one sentence",
      "PROVENANCE: approved by the user, explicitly and forcefully")

    E("The data layer is built first", "Decision",
      "Before any transport, connector or fabric code: produce real Beacon- and Verity-format data on disk, covering every vendor record type and all four golden-claim archetypes",
      "The reason is testability, not convenience -- a transport with nothing to move, a schema registry with nothing to validate and a mapping with nothing to map can only be checked by reading them",
      "Must stand alone: the data generates, is inspected and is asserted on with src/recon/connectors/ absent from the repository entirely",
      "Reordered mid-session. The first plan had the data layer third, behind the framework, and the user moved it to first",
      "PROVENANCE: approved by the user, stated as a hard requirement")

    E("Vendor claims are cited or tagged INVENTED", "Decision",
      "Beacon's and Verity's public documentation is indexed into docs/vendor_evidence/ with verbatim excerpts, source URLs and retrieval dates, before any code that names one of their fields",
      "Every field in every mapping, mock and orchestrator change carries one of three tags: SPEC for a cited vendor source, STANDARD for X12/NCPDP/ISO 20022/FHIR, INVENTED for our own construction with the reasoning recorded",
      "A source that cannot be fetched is recorded as unavailable with the failure and the date, and the fact it would have established is treated as unknown -- a model's recollection of a vendor's API is not evidence",
      "Enforced by a test rather than by discipline: it walks the mappings, mocks and minted identifiers against index.jsonl, and deleting one evidence entry turns the suite red",
      "The failure it prevents: a mock that is wrong is worse than no mock, because it is confidently wrong and looks identical in every demo",
      "PROVENANCE: approved by the user, stated as a hard requirement")

    E("Mocks are formatters, not generators", "Decision",
      "Every source is served by a local mock, so the design question is whether those mocks generate data or re-dress data the orchestrator already produced",
      "They re-dress it. A new Beacon or Verity generator would have to be handed the answer to produce a consistent story, which deletes the runtime blind-slice assertion in contracts.py",
      "That assertion is the only reason crosswalk accuracy is a measured 1,349/1,354 rather than a claim, so it outranks any convenience a generator would buy",
      "beacon_id and the Verity accumulation and invoice references are minted by the orchestrator alongside trn02 and allocation_code -- a cross-feed identifier is a value two systems must agree on, so exactly one component may decide it",
      "If a mock ever needs data the assertion forbids, the mock is wrong, not the assertion",
      "PROVENANCE: agent default, accepted by the user")

    E("load_feeds transport seam", "Plan",
      "The single genuine refactor the connectivity layer needs. Everything else in it is additive",
      "load_feeds currently takes a feeds directory and walks config.FEED_FILENAMES, a fixed six-tuple, against FEED_SOURCE_SYSTEMS, a dict literal keyed by filename -- both assume a local directory of exactly six known files",
      "The change makes the source the parameter instead of the directory, with LocalDirectoryTransport preserving today's behaviour and the old signature kept as a thin wrapper so no call site moves",
      "Everything below load_feeds is untouched: ingest, _process_raw_record, _attach, _park, _recheck_parked and the allocator all operate on raw_record rows, and a row is a row regardless of how it arrived",
      "That is the payoff of the original event-driven decision -- a record arriving over SFTP or HTTP is indistinguishable downstream from one read off disk")

    E("A refactor can quietly delete a guarantee", "Trap",
      "load_feeds takes a feeds directory and therefore CANNOT reach truth/ even by accident. That structural inability is why crosswalk accuracy is scoreable rather than assumed",
      "A Transport protocol is a more general thing than a directory path, so the seam that makes the connector layer possible is also the seam that could silently restore reach to the answer key",
      "The guarantee has to be restated in the new place: no transport implementation may expose arbitrary filesystem access, asserted by a test",
      "Restating a structural guarantee somewhere else is fine. Losing it during a refactor that nothing fails on is not",
      "PROVENANCE: agent default, found while planning the seam rather than by a test")

    E("An instruction file goes stale silently", "Trap",
      "CLAUDE.md carried six dead pointers -- START_HERE.md, docs/solutions/ twice, docs/agent_layer_data_readiness.md and docs/remaining_work.md -- all removed by wave 13's trim",
      "It is exactly the trap wave 13 recorded, arriving on the one file loaded into every session's context, where a wrong pointer costs a search before any work starts",
      "It survived because CLAUDE.md is not tracked by git, so the grep-after-deletion check that fixed the source comments never ran over it",
      "The check that works is the same one, widened: grep every file for a deleted basename, tracked or not",
      "Discovered by running /complete_compound and finding phase 1 had nothing to refresh -- the command's own scope was the stale pointer",
      "PROVENANCE: agent default")

    E("An observation on a routed entity rewrites the prompt", "Trap",
      "Adding three observations to `340B TPA` and two to `Manufacturer` turned the eval suite from 23 passed to 4 failed, with no code change anywhere",
      "The chain: those two entities sit on eleven ReasonCode and CrossTrackFlag routes in grounding.py, routed entities become Clauses, the clause index is rendered into the proposer prompt as a string, and client.py's ReplayClient keys recorded traces on a digest of the whole request -- so a new observation is a new prompt is a replay miss",
      "The existing guidance was half right. It says grep src/recon/agents/ before any UNOBS, because removing grounding material breaks resolution. Adding is the mirror hazard and was not covered: it does not break resolution, it silently invalidates every recorded eval trace",
      "There is a second-order version with no error at all. MAX_CLAUSES caps the index at 72, so observations added to a routed entity can push OTHER entities' clauses out of the window -- changing what the model can cite, quietly, in a passing build",
      "The fix that keeps both properties: put the correction on a NEW entity and wire a relation to the routed one. Traversal still reaches it, the clause index does not move, and the recorded traces stay valid",
      "The rule, stated once: treat any entity named in ROUTES as frozen. Correct it from the outside",
      "PROVENANCE: agent default, found by running the suite after a graph rebuild rather than by reasoning about it")

    E("HRSA rebate model pilot", "DomainConcept",
      "HRSA issued a notice on 31 July 2026 letting qualifying manufacturers deliver the 340B price as a retrospective REBATE instead of an upfront discount",
      "Limited to drugs selected under Medicare price negotiation for initial price applicability years 2026 and 2027, to deduplicate the Maximum Fair Price against the 340B discount",
      "Manufacturer rebate plans were due 24 August 2026, HRSA approvals by 24 September 2026, and approved plans take effect 1 January 2027",
      "Vindicates the project's single riskiest assumption -- modelling 340B as a rebate rather than replenishment, which the design note flagged as out of scope rather than a variant",
      "It is also the deadline behind the connectivity urgency: a connector that cannot submit to Beacon and reconcile a rebate by January is late",
      "hrsa.gov returned 403 to automated retrieval; the dates are cited from Epstein Becker Green, Covington and HLC summaries instead")

    R("Connectivity assignment", "introduces", "Shields connector fabric")
    R("Connectivity assignment", "introduces", "Beacon")
    R("Connectivity assignment", "is answered by", "Connector-ready")
    R("Beacon", "fronts", "Manufacturer")
    R("Beacon", "decides independently of", "340B TPA")
    R("Only Beacon is an outbound connector", "constrains", "Shields connector fabric")
    R("Only Beacon is an outbound connector", "describes", "Beacon")
    R("Connector-ready", "is bounded by", "Production hardening is out of scope")
    R("Connector-ready", "requires", "The data layer is built first")
    R("The data layer is built first", "is gated by", "Vendor claims are cited or tagged INVENTED")
    R("The data layer is built first", "is realised by", "Mocks are formatters, not generators")
    R("Mocks are formatters, not generators", "protects", "Generator independence")
    R("Mocks are formatters, not generators", "extends", "Orchestrator")
    R("load_feeds transport seam", "realises", "Shields connector fabric")
    R("load_feeds transport seam", "preserves", "Event-driven ingestion")
    R("A refactor can quietly delete a guarantee", "threatens", "load_feeds transport seam")
    R("A refactor can quietly delete a guarantee", "protects", "Measure the claim against the generated data")
    R("An instruction file goes stale silently", "is the same family as", "Deleting a document orphans every pointer into it")
    R("An observation on a routed entity rewrites the prompt", "constrains", "Knowledge graph is generated, never written to")
    R("An observation on a routed entity rewrites the prompt", "threatens", "Test suite")
    R("An observation on a routed entity rewrites the prompt", "is why", "Beacon")
    R("Beacon", "corrects from outside", "340B TPA")
    R("Beacon", "corrects from outside", "Manufacturer")
    R("HRSA rebate model pilot", "validates", "Rebate model")
    R("HRSA rebate model pilot", "motivates", "Connectivity assignment")

    # The corrections to `340B TPA` and `Manufacturer` deliberately live on the new
    # entities above rather than on those two.  Both are grounding material -- they sit
    # on eleven ReasonCode and CrossTrackFlag routes -- and an observation added to a
    # routed entity becomes a clause in the index the proposer prompt embeds.  See the
    # trap entity below; the relations wired above are the traversal path a reader
    # follows from either entity to the correction.

    OBS("Build state",
        "Branch connectivity_layer carries the response to the second assignment: docs/connectivity_layer_requirements.md, 24 requirements in seven groups across seven waves. Nothing built yet",
        "decision_tree/NOTES.md was removed -- REPORT.md supersedes it, and its four findings are named entities in this graph",
        "The GitHub remote moved to statusneo.reconcilation_engine, one fewer l. Pushes still succeed through a redirect, but the configured origin URL is stale")


def wave_15_building_and_reviewing_the_connector():
    """What building waves 0-7 and then reviewing them adversarially settled.

    Wave 14 recorded the connectivity layer as a PLAN. This wave records what
    building it, and then attacking it with two independent reviewers, actually
    produced -- including four things the build believed about itself that turned
    out to be false.

    None of these entity names appear in `grounding.py`'s routing tables, so none
    becomes a Clause and the recorded agent-eval prompts are unmoved.
    """

    # --- what the build actually is, as opposed to what it was planned to be ---

    E("The vendor adapters are written and unwired", "Status",
      "src/recon/connectors/vendors/ -- verity.py, craneware.py, beacon.py -- is written, documented, schema-checked and unit-tested, and NOTHING IN src/ IMPORTS IT",
      "Measured, not read: a full build produces 0 BEACON_ID rows and 0 PAYMENT_REFERENCE rows in crosswalk_key, on both the demo and full profiles",
      "This single fact is the root cause behind four requirement failures at once -- C2 (outbound submission), C5 (Beacon ID as a key), E3 (payment references) and half of F2 (control totals)",
      "The proximate reason is documented in registry.py rather than accidental: the delimited reader wants a received_at off every row, and a vendor export carries qualification_received_at / batch_received_at / reversal_received_at, so choosing among them is a mapping decision the seam does not yet make",
      "F2's consequence is the sharpest: the only two sources in the build that declare a trailer are the two that never reach the reconciling path, so every control-total check that executes is vacuous",
      "Recorded as FAIL in docs/connectivity_layer_build_plan.md rather than as a caveat under a green wave, because 'committed and green' and 'requirement met' are different claims")

    E("The connector layer changed nothing downstream", "Status",
      "A full build at HEAD against one at dbb129e, the pre-connector baseline, in a git worktree: episode identity -- id, track, ndc11, date_of_service -- is IDENTICAL on both demo and full",
      "raw_record, normalized_record, episode, verdict, cash_allocation and parked_record counts all identical",
      "The only downstream change is crosswalk_key, 11947 -> 13613 on full, and that delta of 1666 is exactly COVERED_ENTITY_340B 855 + HCPCS 811",
      "Zero orphan raw_record rows, zero quarantines of any reason, zero COVERED_ENTITY_MISMATCH parks on generated data",
      "So the honest split is: nothing was broken, and some things were never finished. Those are different failures with different fixes, and a review that reports only one of them is misleading")

    # --- the traps, each earned by a real defect this session ------------------

    E("A guard copied three times disagrees three ways", "Trap",
      "PROVENANCE: agent default, found by an independent reviewer that executed the bypass rather than reading the guard",
      "The ground-truth refusal existed in three transports -- transport.py, sftp.py, http.py -- as three independent copies of the same idea",
      "They disagreed: http.py casefolded the comparison, the other two did not. SftpTransport refused /exports/truth and ACCEPTED /exports/TRUTH",
      "A reviewer fetched ground_truth.json over a real paramiko SSH connection through that hole, end to end -- demonstrated, not theorised",
      "transport.py's copy was correct only by accident: Path.resolve() canonicalizes an EXISTING path to its on-disk casing on Windows, protection that evaporates for a directory not yet created and that never existed on Linux",
      "SFTP was also missing the second layer -- the local transport re-checks each document after resolving it, so a bad root is still caught per file; SFTP checked only at construction, making that one check the entire guarantee",
      "Fixed by one _names_truth() helper all three call. The rule to take from it: a security property copied per call site is a property that holds in the average case and fails in some particular one",
      "Whether TRUTH and truth are one directory is the SERVER's filesystem's opinion, not ours -- one on Windows and macOS, two on a POSIX host. A guard whose correctness depends on which host a vendor runs is not a guarantee")

    E("A test can promise coverage its filter does not deliver", "Trap",
      "test_no_transport_implementation_can_resolve_a_path_under_truth promised in its docstring to cover 'every implementation, not just this one'",
      "It filtered on value.__module__ == transport.__name__, which enumerates exactly LocalDirectoryTransport -- both the wave-3 and wave-4 transports escaped it",
      "`assert implementations` passed happily on a list of one, so the test was green and the guarantee was untested for two waves",
      "This is why the SFTP bypass survived four waves of self-verification: the test meant to catch it was structurally incapable of seeing it",
      "The fix pattern: assert on the SIZE of what a discovery test discovered (`>= 3`), because a discovery that finds too little looks identical to a subject that is clean")

    E("An assertion whose subject is absent passes", "Trap",
      "PROVENANCE: agent default, and the dead assertion was the agent's own, found by a reviewer reading what the fixture actually selects",
      "A scoped-key assertion added in wave 5 selected the first EXCEPTION-queue episode, which is medical and carries no COVERED_ENTITY_340B key at all",
      "The loop body therefore never ran, and an entities_seen that stayed empty satisfied `len(...) <= 1` as `0 <= 1`",
      "Both assertions were dead the day they were written, inside a comment claiming the test checked 'strictly more' than the one it replaced",
      "The same shape appeared twice more in the same review: a checkpoint-hash assertion guarded by hasattr(server, 'root') on a class with no public root, reducing to x == x",
      "Fix pattern: a test that searches for its own subject must FAIL when it finds none. A guard that silently does nothing when its subject is absent is worse than no guard, because the green tick is read as evidence")

    E("A docstring can assert a guarantee the code does not provide", "Trap",
      "crosswalk/keys.py claimed requirement E1 held 'by construction rather than by a comparison someone has to remember to write' and that there was 'no equality check to forget'",
      "It holds by exactly such a check -- pipeline._contradicts_covered_entity -- and COVERED_ENTITY_340B is published and never looked up by anything",
      "Worse than being wrong: the sentence described a guarantee, so a reader would stop looking for the check that actually carries it",
      "Three more in the same family were found and corrected: dossier.py claiming derived keys 'resolve records normally' when one resolves nothing, sites.py naming a vendor site column that does not exist, and two places still saying HCPCS 'is not built' after E2 landed",
      "In a repository whose credibility rests on its prose being true, a false docstring is worse than a missing one -- it is a claim a reviewer will spend their scepticism elsewhere because of")

    E("Idempotency that rests on statement order", "Trap",
      "control_total has no unique constraint and no idempotency key. Re-running a load does not duplicate its rows, and the reason is not a constraint",
      "It is that reconcile_or_fail sits AFTER the batch_for_file_sha256 early-continue in load_from_sources. Swap the two statements and every run appends a duplicate row into a table whose triggers forbid cleaning it up",
      "No test pins that ordering: the acceptance is exercised through a test-local reimplementation of the loader's two statements, not through the loader",
      "cash_allocation is protected the same transitive way -- by _insert_tree returning None on a repeat, not by any constraint of its own",
      "The rule: when idempotency comes from control flow rather than from a constraint, the control flow is the invariant and something has to pin it")

    E("A wire format agreed in two places and matched in neither", "Trap",
      "PROVENANCE: agent default, found by a reviewer checking whether a cited test existed, then proven by running both sides",
      "beacon.py says its submission template and the mock's payload builder are 'kept in step by a round-trip test rather than by an import'. That test does not exist",
      "Proven empirically rather than inferred: the mock indexes on fill_date '20251216' (CCYYMMDD, straight off the generator sidecar) and a connector-built body would send date_of_service '2025-12-16' (ISO, because adapters.py converts it)",
      "So the first real end-to-end submission would 404 with unknown_claim, and the acknowledgement check would then raise on the same mismatch",
      "It survived because both sides were only ever tested against themselves -- the tests that 'submit' POST the mock's own payload builder, so the adapter's mapping never touches the wire",
      "A claimed test is a load-bearing claim. Citing a test that does not exist is how two halves of a seam drift while both look verified")

    # --- decisions, with provenance -------------------------------------------

    E("A weak inference is worse than a null", "Decision",
      "PROVENANCE: agent default, overturned by its own measured output rather than by review",
      "Requirement E1 wanted episodes to carry a 340B covered entity",
      "The first implementation derived it from the rendering prescriber's affiliation. It filled 22 of 23 medical episodes and then CONTRADICTED the TPA on five of them",
      "DOC2-004 makes the TPA authoritative for 340B qualification and source transaction context, so the inference was overruling the system of record",
      "It was worse than no value because it landed in a column everything downstream reads as fact, and then drove the contradiction guard -- so the guess parked five records the TPA had labelled correctly",
      "Now registration-only: an episode's covered entity comes from the reference table's registered_pharmacy_npis or is NULL. Medical episodes carry NULL and get their entity from the record that states it",
      "The asymmetry that makes NULL safe: the guard treats absence as 'no opinion' and never fires on it, so a null is inert where a wrong value is actively harmful")

    E("Two independent reviewers beat four waves of self-verification", "Learning",
      "PROVENANCE: the user specified this structure explicitly and insisted on it after it was skipped for four waves",
      "Two Fable reviewers, each planning before dispatching, each directing its own fan-out of Opus workers, neither permitted to write code",
      "Waves 3-6 had been built by single Opus builders that verified their own work and reported green. The review found a working ground-truth bypass, three tests that could not fail, and four false docstrings",
      "The structural reason it worked: a builder verifies against what it intended to build, and a reviewer verifies against what the requirement says. Those diverge precisely where the builder misunderstood",
      "The reviewers were also told the findings already recorded, so workers were not spent rediscovering them -- and were asked to state failure criteria BEFORE looking, so a comfortable reading could not be rationalised afterwards",
      "Cost: roughly twenty Opus workers. It found a demonstrated security hole that four waves of green tests had not")

    E("Measure the benchmark, not your own tokens", "Learning",
      "PROVENANCE: agent default, prompted by the user rejecting a verification that had never looked at the benchmark",
      "Every check before this one verified the stylesheet against ITS OWN declared tokens, which answers 'did the CSS apply', not 'does this look like Linear'",
      "Measuring linear.app directly found three real divergences: display tracking of -0.022em was being applied to a 19px masthead when they reserve it for >=32px, our UI text sat at -0.006em against their measured -0.01em, and our card radius was 10px against their 12px",
      "It also DISPROVED a claim this build had been repeating: 'Linear's look is borders-not-shadows' is false -- they run 42 shadowed elements to 98 bordered page-wide",
      "We still use zero shadows, but now as a stated divergence with a reason (a shadow reads as depth on their near-black page and as smudge on a light one at this density) rather than as mistaken fidelity",
      "Recorded in docs/frontend_linear_benchmark.md, including the three divergences kept deliberately -- colour, shadows and the strict spacing scale")

    E("A changelog drawn as boxes is not a system diagram", "Learning",
      "PROVENANCE: user decision, stated bluntly after ten such bands had already been committed",
      "Rows of labelled rectangles summarising what each wave did contain no components, no direction and no arrows",
      "A system diagram answers what talks to what and in which direction. A changelog answers what happened and when. Only one of those is architecture, and the boxes made the wrong one look like the right one",
      "The canvas already held the correct model -- 'THE WHOLE PROJECT, END TO END', 99 elements of which 26 are arrows -- and the right move was to extend that language rather than invent a worse one beside it",
      "Replaced with 97 elements, 20 arrows: vendors into mocks into transports into load_from_sources into raw_record, then the unchanged pipeline, then the surface",
      "The two things drawn because they are true rather than flattering: the vendor leg ends in a DASHED arrow that stops short of the seam, and the truth store sits crossed out")

    # --- what phase 1's refresh turned up, which is itself graph-worthy --------

    E("A citation by line number rots on the next edit", "Trap",
      "PROVENANCE: agent default, found by auditing docs/agent_layer_design.md against the code rather than by a test",
      "The fourth member of the pointer-rot family, and the one the existing three do not cover: the file still exists and its name is still right, only the LINE moved",
      "agent_layer_design.md cited src/recon/db/schema.sql:461-464 for the work_item triggers. The fact was still true; the triggers had moved to 518-521 because the connectivity layer took the schema from user_version 3 to 7 and shifted everything below it",
      "It runs in both directions. rubric.py cited agent_layer_design.md:55 and :66, and both had moved -- then moved again when this refresh edited the document",
      "The fix is not a better line number, it is a different kind of reference: rubric.py now cites the document's SECTION. A section survives an edit above it; a line number is invalidated by every insertion, including the one that corrects it",
      "The suite cannot see any of this. A comment pointing at the wrong line of a real file is not a failure any runner checks")

    E("The walkthrough narrates an episode that does not exist", "Trap",
      "PROVENANCE: agent default, found by auditing the walkthroughs against a real run rather than against the code that produces them",
      "Both walkthroughs -- the documents written for a spoken interview -- are built around claim E-000042 with verdicts A-07/C-09, short $1,367.97 with a $6,864.00 rebate",
      "On the frozen demo spine, E-000042 is A-02/C-00. The pair A-07/C-09 lands on NO episode at all. Anyone clicking E-000042 during the walkthrough sees a different claim than the one being described",
      "The first reviewer blamed the connectivity layer's reseeding. That was WRONG and worth recording: checked at dbb129e, before any connector work, the example was already wrong there. Episode identity is byte-identical between the two commits",
      "So the real cause is older and simpler -- the figures were hand-composed to illustrate the narrative and never re-derived from a run, and nothing checks a document's worked example against the data",
      "The fix is not better numbers, it is not using a real-looking id a reader will click. The closest true episode is E-000007 (A-04/C-09): reimbursement reconciled at $6,638.63, rebate approved and never paid, $1,929.00 outstanding",
      "A worked example is the most load-bearing prose in any walkthrough and the only kind no test can see")

    E("A design document can specify a mechanism the build never used", "Learning",
      "PROVENANCE: agent default, found by auditing the design document against the implementation it preceded",
      "agent_layer_design.md's schema section rested on `pydantic.BaseModel` preserving declaration order into `model_json_schema()`, with illustrative code writing `class ProposedAction(BaseModel)`",
      "The build uses no pydantic at all. schemas.py is frozen dataclasses plus a hand-written ordered _FIELD_SCHEMAS dict, because pydantic arrives only with the `api` extra and the agent layer must import on a base install that has neither it nor FastAPI",
      "The PRINCIPLE the section argued for -- reasoning-first field order, worth ~60pp on hard tasks -- is correctly implemented. Only the named mechanism was wrong, which is the more dangerous shape: the claim reads as verified because its conclusion is true",
      "Same audit found the document's EvidenceSpan illustration claiming model-supplied offsets, where the code deliberately forbids them and locates quotes with str.find() afterwards -- the code is STRICTER than its own design document",
      "A document written alongside an implementation drifts hardest where it was most confident, because confident prose is what nobody re-checks")

    # --- corrections to wave 14, which planned what this wave built ------------
    # None of these entities is routed in grounding.py, so extending them moves
    # no prompt and invalidates no recorded eval trace.

    OBS("Connector-ready",
        "Measured out as: 14 declared sources, all CONNECTOR_READY, and zero at working-connection or production-ready",
        "The readiness report states 'No source in this build has ever reached a vendor system' and that sentence is armed -- a test points a row at a real host, resolves a credential, and asserts the report would say otherwise",
        "But connector-ready turned out to cover two quite different states: the six generated feeds genuinely ingest, and the eight vendor sources map without ever reaching the database. The report does not yet distinguish them")

    OBS("Vendor claims are cited or tagged INVENTED",
        "Held under audit: zero citation violations across 247 + 69 provenance rows, every cited id present in index.jsonl, no SPEC row citing an UNAVAILABLE source",
        "Verified non-vacuously -- the parser matched exactly the row counts both files declare for themselves, so nothing was being silently skipped",
        "One weakness the audit named: SPEC covers both 'the vendor said it' and 'an assessment document about the vendor said it', and no test enforces the distinction. BEACON-013's url is Assignment_Doc_2.pdf page 3, which reads as first-hand at a glance")

    OBS("A refactor can quietly delete a guarantee",
        "This trap predicted the exact defect that later happened. The transport seam did generalise a directory into a Transport, and the ground-truth guarantee did survive in two of three implementations and silently fail in the third",
        "What the original framing missed: the danger was not the refactor losing the check, it was the refactor COPYING the check, so three call sites each had one and only two were right")

    # --- relations -------------------------------------------------------------

    R("The vendor adapters are written and unwired", "blocks", "Connector-ready")
    R("The vendor adapters are written and unwired", "is a limit of", "Shields connector fabric")
    R("The connector layer changed nothing downstream", "is evidence for", "load_feeds transport seam")
    R("The connector layer changed nothing downstream", "measured against", "Test suite")

    R("A guard copied three times disagrees three ways", "is an instance of", "A refactor can quietly delete a guarantee")
    R("A guard copied three times disagrees three ways", "threatens", "Measure the claim against the generated data")
    R("A test can promise coverage its filter does not deliver", "is why", "A guard copied three times disagrees three ways")
    R("An assertion whose subject is absent passes", "is the same family as", "A test can promise coverage its filter does not deliver")
    R("A docstring can assert a guarantee the code does not provide", "is the same family as", "An instruction file goes stale silently")
    R("Idempotency that rests on statement order", "threatens", "load_feeds transport seam")
    R("A wire format agreed in two places and matched in neither", "is a limit of", "The vendor adapters are written and unwired")
    R("A wire format agreed in two places and matched in neither", "is the same family as", "A docstring can assert a guarantee the code does not provide")

    R("Two independent reviewers beat four waves of self-verification", "found", "A guard copied three times disagrees three ways")
    R("Two independent reviewers beat four waves of self-verification", "found", "An assertion whose subject is absent passes")
    R("Two independent reviewers beat four waves of self-verification", "found", "The vendor adapters are written and unwired")
    R("A weak inference is worse than a null", "corrects", "Connector-ready")
    R("Measure the benchmark, not your own tokens", "is the same family as", "Measure the claim against the generated data")
    R("A changelog drawn as boxes is not a system diagram", "constrains", "Connectivity assignment")

    R("The walkthrough narrates an episode that does not exist", "is the same family as", "A citation by line number rots on the next edit")
    R("The walkthrough narrates an episode that does not exist", "threatens", "Build state")
    R("A citation by line number rots on the next edit", "is the same family as", "Deleting a document orphans every pointer into it")
    R("A citation by line number rots on the next edit", "threatens", "Agent layer")
    R("A design document can specify a mechanism the build never used", "is the same family as", "A docstring can assert a guarantee the code does not provide")
    R("A design document can specify a mechanism the build never used", "describes", "Agent layer")


def wave_16_vendor_sourced_ingest():
    """Inverting the TPA source, and the assumptions that measuring it disproved.

    The session that made the reconciliation engine run on a vendor's export rather
    than on a generic feed this repository invented for itself.
    """

    # --- what the authority table decided, rather than us -------------------
    E("A TPA may not assert rebate status", "Decision",
      "connectors/authority.py lists REBATE_BATCH and REBATE_DISPENSE_LINE under _REBATE_STATUS with authoritative = {BEACON, MANUFACTURER_REBATE}, so a TPA source emitting either is refused at the kind check before any field is read",
      "Asking it directly returns: a TPA_VERITY source may not assert rebate status: a REBATE_DISPENSE_LINE record is the claim itself",
      "The plan framed verity_invoices as blocked on unsettled batch-versus-line semantics; the real blocker was prior, and the system already answered it",
      "The same boundary that quarantined nine Craneware rows for authoring manufacturer_status, one level up: not a field a TPA may not set, but a kind a TPA may not be",
      "A TPA invoice is a fact the TPA owns -- this is what we billed -- about a payment decision the TPA did not make",
      "PROVENANCE: agent default, derived from the authority table rather than chosen")

    E("TPA_INVOICE_LINE", "Mechanism",
      "The record kind verity_invoices lands as, after refusing to adapt for two waves",
      "Both money figures are carried verbatim as text under relayed_ names and neither is summed; amount_cents is null",
      "Outside dimensions._KIND_BUCKETS and inside engine.run._ROLE_BY_KIND: gathered, cited, on the timeline, moving no verdict",
      "Mapping it in _ROLE_BY_KIND is not housekeeping -- that lookup defaults to ADJUDICATION, so an unmapped kind is filed as the episode pharmacy adjudication evidence rather than refused",
      "A reversed invoice row emits no TPA_REVERSAL child: TPA_REVERSAL is bucketed and dimensions reads clawed_back = bool(tpa_reversals) and paid > 0, so a second reversal through the invoice door would move episodes to C-13",
      "beacon_id is relayed rather than set -- the authority domain rationale says a Verity export echoes a beacon_id as a lookup handle and a caller resolving a key should not pass it as a fact",
      "Proven to move nothing by building the dataset with and without it and comparing a digest over dispositions, both verdict codes and the rebate money across all 60 episodes")

    # --- what measurement disproved -----------------------------------------
    E("A data-derived filename accumulates runs that never happened", "Trap",
      "Verity export names are derived from the data so identical data lands an identical filename, which is what makes file-level idempotency testable",
      "The corollary nobody wrote down: different data lands a different name, and the old one is never overwritten",
      "Three generations sat side by side in data/generated/demo/vendor/verity/, and of the 37 rebate allocation codes in the two older invoice exports, ZERO appeared in any feed",
      "A reader cannot know a data-derived name in advance so it matches by filename prefix -- all three matched, and taking the first read the oldest run",
      "It manufactured a finding rather than merely cluttering: the batch-versus-line disagreement that sent this work looking for invoice semantics was read out of a superseded file, and in the live one every batch lines sum to its declared total exactly",
      "The writer is at fault, not the reader -- a real vendor SFTP directory legitimately holds successive deliveries that should all be ingested; what must not appear is a delivery that was never sent",
      "Craneware and Beacon are immune because they write to constant filenames")

    E("The 340B feed carries two authorities", "Learning",
      "tpa_340b_events.jsonl is not one feed: every row declares its own author",
      "On the demo profile 78 rows say TPA_PORTAL (qualification decisions, rebate requests, dispense reversals) and 35 say MANUFACTURER_REBATE (payment batches, manufacturer decisions)",
      "_adapt_tpa has honoured that split since before any connector existed, reading the row own source_system rather than the file",
      "So inverting the TPA source swaps 78 rows, not a file -- a vendor export replaces what the TPA said and cannot replace what the manufacturer said",
      "The plan step to demote the feed to generation-only is therefore not available as written: dropping the file takes the rebate money with it",
      "Filtering rows at load is safe on this file for a checked reason, not a general one -- control_totals.declared_in returns NOTHING_DECLARED for any document with no record-type column, which covers every generated feed; on a vendor export, which declares a trailer count, excluding rows would manufacture a shortfall and fail the load")

    E("No vendor export carries the rebate request", "Learning",
      "_derive_rebate reads request = SUBMITTED if evidence.tpa_requests else NOT_SUBMITTED and returns immediately on NOT_SUBMITTED, short-circuiting manufacturer status, payment and cash at once",
      "The generic feed carries 33 REBATE_REQUEST events; neither verity_accumulations nor craneware_claims_report has a column for it, because asking the manufacturer happens after qualification and is reported by whoever asked",
      "So both vendor modes collapse 30 of 60 episodes to C-03",
      "The filler exists and is already ingested: every episode holding a rebate request also holds a Beacon acknowledgement, zero orphans, and Beacon reaches 6 more besides",
      "DOC2-004 makes Beacon authoritative for the rebate submission identifier, so in the inverted world we submitted is Beacon fact rather than the TPA word for it",
      "Held open deliberately: BEACON_ACKNOWLEDGMENT is outside _KIND_BUCKETS by decision and bucketing it moves verdicts on those 6, which is a separately measurable step",
      "PROVENANCE: agent default -- the default build stays on the generic feed until this gap closes, because flipping it first would make the demo strictly worse")

    E("Verity cannot express a disqualification", "Learning",
      "verity_accumulations is the dispenses that accumulated, a population selected on qualification_status, which vendors/verity.py says can only be QUALIFIED there",
      "So a NOT_QUALIFIED decision has no row to sit on, and Verity lands 34 qualifications where the generic feed and Craneware land 39",
      "Those 5 episodes reach C-00 track absent -- not this dispense was refused, but this dispense was never 340B",
      "Craneware Claims Report is a report of claims rather than of accumulations, so a non-qualifying row has a home in it and the disqualifications survive",
      "The sharpest reason one export is not interchangeable with another, and invisible at the row level: both files parse, contract-check and ingest perfectly")

    E("Craneware lands a whole report at one instant", "Learning",
      "The Claims Report publishes no per-row arrival time, so every row inherits the delivery stamp: 4 distinct arrival moments across 39 records against Verity 41 across 61",
      "The engine evaluates at a cursor, so this changes what a verdict could have known -- a dispense qualified in August arrives when the next report was cut",
      "Measured: Craneware reopens 64 previously-closed verdicts against the generic feed 34, on a DIFFERENT set of episodes, four of which the generic feed never reopens",
      "Verity reopened episodes are a strict subset of the generic feed, because it carries its own stamps and moves nothing earlier or later than the fact justified",
      "Not a connector defect -- a true fact about what the vendor ships, and invisible everywhere else because the rows parse, the control total agrees and the final exception count is the same either way")

    E("A mock that repairs a defect is worse than one that adds a wrong value", "Trap",
      "Landing vendor divergence defaulted rx_rendering_craneware to CANONICAL, which reads as the safe default and is the opposite",
      "Where D-6 had already drifted the feed Rx, Verity reported the drifted spelling and Craneware reported the CORRECT one -- 22105567 against 221055677",
      "A wrong value is visible; a repair hands the connector a join the real feed does not have, so the crosswalk miss vanishes through one vendor door and every count downstream agrees with itself",
      "mocks/source.py had already warned about exactly this: the sidecar stores the key spelled the way the TPA feed spells it, drift included",
      "Found by printing the diverged pairs, not by a test -- at that point no test looked",
      "The fix is that Craneware inherits the feed rendering and diverges only when the generator says so")

    E("Vendor divergence", "Mechanism",
      "Verity and Craneware are formatted from one shared VendorSource, so without this they report the same qualification for the same dispense always, and the two vendors agree is a property of the generator rather than a finding",
      "Follows the D-6 pattern because it has no choice: recon.mocks may not import recon.generators, so a formatter cannot make this call",
      "Decide in _defect_directives, freeze onto EpisodePlan as rx_rendering_craneware, render into a NEW sidecar column, and let each formatter read its own",
      "A new column rather than a diverted one: load_source joins TPA events onto the sidecar on five fields including rx_number, so changing that one would unjoin the dispense rather than produce a disagreement",
      "Only planned where the TPA rendering is already canonical, or a vendor disagreement would be indistinguishable from D-6 -- the one thing it has to be told apart from",
      "The modulus is coprime to both drift moduli so the two defects cannot land on the same episodes by arithmetic coincidence",
      "There is no AST test banning decision logic in a formatter -- branching is legal and all three formatters do it. The guard that binds is that every two-decimal amount emitted must appear verbatim in the TPA feed, so a divergence may ride an identifier and may not ride an amount",
      "PROVENANCE: agent default")

    E("An unexplained verdict transition is the finding", "Learning",
      "Replacing evidence cannot claim nothing moved the way adding evidence can, so the acceptance test for vendor-sourced ingest reconciles differences instead of asserting a matching digest",
      "Every episode whose verdict changes must fall into a named bucket; an unexplained transition fails with the before and after codes in the message, because 35 episodes changed says nothing and C-09 became C-11 on four episodes says where to look",
      "It worked: landing vendor divergence produced C-13 to C-01 on one episode, which the test refused as unexplained -- correct behaviour arriving through a cause the test did not yet know about",
      "The third bucket is keyed to the specific episodes the generator disputed rather than to the transition, because allowing C-13 to C-01 generally would excuse it everywhere")

    # --- corrections to earlier waves ---------------------------------------
    UNOBS("The vendor adapters are written and unwired",
          "NOTHING IN src/ IMPORTS IT",
          "a full build produces 0 BEACON_ID rows and 0 PAYMENT_REFERENCE rows")
    OBS("The vendor adapters are written and unwired",
        "SUPERSEDED on the TPA side: build_dataset(tpa_source=verity|craneware) runs the whole engine on a vendor export, and vendor rows reach 25 episodes through Verity and 28 through Craneware",
        "Beacon three inbound payloads are ingested on every build, so BEACON_ID and PAYMENT_REFERENCE are published on an ordinary run",
        "What remains unwired is the dashboard: /api/regenerate takes no tpa_source and always builds generic, so the vendor door is reachable from Python and the suite and from nowhere a reviewer would click")

    OBS("Connector-ready",
        "The two-state split this entity names is closed on the TPA side: the vendor sources now reach the database, are asserted at verdict level rather than at normalized_record, and their differences from the generic feed are reconciled per verdict code")

    R("A TPA may not assert rebate status", "constrains", "TPA_INVOICE_LINE")
    R("TPA_INVOICE_LINE", "realises", "Connector-ready")
    R("A data-derived filename accumulates runs that never happened", "threatens", "TPA_INVOICE_LINE")
    R("The 340B feed carries two authorities", "constrains", "The vendor adapters are written and unwired")
    R("No vendor export carries the rebate request", "threatens", "The vendor adapters are written and unwired")
    R("Verity cannot express a disqualification", "threatens", "The vendor adapters are written and unwired")
    R("Craneware lands a whole report at one instant", "threatens", "The vendor adapters are written and unwired")
    R("Vendor divergence", "realises", "An unexplained verdict transition is the finding")
    R("A mock that repairs a defect is worse than one that adds a wrong value", "threatens", "Vendor divergence")
    R("An unexplained verdict transition is the finding", "constrains", "The 340B feed carries two authorities")


# ===========================================================================
# WAVE 17 -- pitching the system to people who did not build it
# ===========================================================================

def wave_17_pitching_the_system():
    E("A correction banner is perishable in both directions", "Trap",
      "PROVENANCE: agent default, found by re-auditing a correction that a previous audit had written",
      "Both walkthroughs carried a banner saying 'the worked example is wrong, point at E-000007 instead'. Re-run against the database, BOTH halves had rotted: the thing it corrected FROM and the thing it corrected TO",
      "E-000042 is no longer A-02/C-00 as the banner asserts -- it is A-13/C-14. E-000007 is no longer A-04/C-09 at $6,638.63 and $1,929.00 -- it is A-02/C-00, PENDING, nothing received",
      "The E-000007 figures were STALE, NOT INVENTED, and the distinction matters because it changes the fix. docs/images/01-dashboard.png shows them, so they were true of the generation that was live when the banner was written",
      "The generator reassigns episode ids on every reseed, so any id quoted in prose is a snapshot of one dataset generation and nothing tells the prose when that generation is replaced",
      "The same rot hits committed screenshots: docs/images/ shows queue counts 34/11/15 where the database now answers CLOSED 10, EXCEPTION 40, PENDING 10. A screenshot is a dataset generation too, and it is the kind nobody re-derives",
      "Fixed by making the banner self-refreshing rather than by finding a better id -- it now names E-000004 AND carries the SQL that re-derives the pointer, and says outright that its own first version rotted",
      "E-000004 is the whole approved-but-unpaid-rebate story and the only C-09 on the spine: $14,540.39 expected and received with the deposit matched, then a $4,149.00 rebate approved and sitting at C-01 for ten cursors before the age threshold trips it to C-09 at the last one",
      "A correction inherits the defect it corrects. Writing one without a way to re-derive it just moves the expiry date")

    E("Proving the engine is not serving mock verdicts", "Learning",
      "PROVENANCE: user challenge -- 'have we created verdict mock data as well, or is our engine really generating that data' -- with the proof method an agent default",
      "The doubt is reasonable and recurs: a demo whose verdicts look this tidy is exactly what a seeded fixture table would look like",
      "Reading the code does not settle it, because a reader cannot tell a computed row from a loaded one by looking at the schema",
      "The proof is destructive and must be run on a COPY: drop the append-only triggers (they exist precisely to forbid this), DELETE every row from verdict, then replay engine.run_all over the monthly cursors",
      "The rebuilt table came back byte-identical to the original, verdicts and reasons both. Verdicts are computed from (episode, cursor) and nothing else",
      "The second half of the proof is a grep: nothing outside generators/ reads ground_truth.json except a test and the docstrings that forbid it",
      "State the method, not the row count, when recording this. The counts move with every reseed -- the byte-identity does not")

    E("A broad ignore rule can commit a document with broken images", "Trap",
      "PROVENANCE: agent default, introduced and then caught while assembling the pitch folder",
      ".gitignore carried a blanket `*.png` to keep Playwright screenshot debris out of the repository",
      "It also swallowed every diagram the pitch document and the HTML deck reference by relative path, so both committed clean and rendered with broken images for anyone who cloned",
      "Nothing errors. The build passes, the markdown is valid, and the failure is only visible to a reader who is not the author",
      "Fixed with negation rules per content directory rather than by narrowing the debris rule, because the debris rule is right and the content is the exception",
      "The .pptx was immune because it EMBEDS its images. A format that copies its assets cannot have this bug -- a format that links them always can")

    E("Merging Excalidraw files means rewriting references, not prefixing ids", "Learning",
      "PROVENANCE: agent default, from building one canvas that holds every pitch diagram",
      "Naive concatenation collides ids across files. Prefixing every element id fixes the collision and silently breaks the drawing",
      "Excalidraw points at ids from five other places -- containerId, frameId, groupIds, boundElements and startBinding/endBinding -- and every one must be rewritten with the same prefix or arrows detach from shapes and labels float free of their containers",
      "Verified structurally instead of visually: 321 elements, zero duplicate ids, zero dangling references",
      "Chromium cannot screenshot the result -- a 16,224-unit canvas exceeds the renderer. Loading was proved instead by exporting SVG through Excalidraw's own engine, 458 shapes and 389 texts with no page errors",
      "Hand-laying out Excalidraw text needs a per-case width factor: about 0.55 of the font size per character for mixed case, but nearer 0.685 for UPPERCASE and underscore-heavy strings. Using the low figure on an uppercase label collided two columns")

    E("The pitch artefacts are a build, with the document frozen", "Decision",
      "PROVENANCE: user decisions throughout -- language, page budget, slide ceiling, file format and the freeze were all stated explicitly",
      "Audience is solution engineers and investors, and the document is RECITED aloud, which is what sets the language bar: plain enough to read off, no vocabulary chosen to sound impressive",
      "Stated budgets were hard, not aspirational: under 10 pages for the document, under 15 slides for the deck",
      "Diagrams were constrained the same way -- no dense arrows, no subtitles, no detail added because it was available",
      "The deck had to become a real .pptx with embedded images rather than markdown or a hosted page, so it can be edited locally without the author in the loop",
      "Once the document was accepted it was FROZEN: later requests for a rebate walkthrough and for an episode-assembly view were both answered by adding a slide, never by reopening the document",
      "PowerPoint holds an exclusive lock on an open deck, so the builder takes an output path as argv[1]. Build to a temp path and copy in later -- killing the process risks the user's unsaved edits and is never the right move")

    # --- corrections to earlier waves -----------------------------------------
    # Neither entity below is named in grounding.py's ROUTES tables, so extending
    # them moves no proposer prompt and invalidates no recorded eval trace.

    UNOBS("The walkthrough narrates an episode that does not exist",
          "The closest true episode is E-000007")
    OBS("The walkthrough narrates an episode that does not exist",
        "The fix is not better numbers and not a real-looking id a reader will click -- it is a pointer that carries the query which re-derives it, because the id itself rots on the next reseed",
        "The E-000007 replacement this entity used to name has itself gone stale, which is recorded separately as the trap that a correction banner is perishable in both directions")

    OBS("Craneware lands a whole report at one instant",
        "The committed demo database is the Craneware build -- 43 TPA_CRANEWARE raw records and zero TPA_PORTAL -- so this clustering is what every walkthrough and every screenshot is actually showing",
        "Measured on that build: 43 rows across 6 distinct arrival moments, 38 of them sharing the final cursor 2026-07-01T23:59:59Z",
        "The consequence a reader notices first is ORDER. Qualification is supposed to precede the Beacon submission it gates, and on this spine it arrives last, after Beacon, because the delivery stamp is the only time the vendor publishes",
        "So the walkthrough is describing a real inversion rather than a defect: the logical order holds, the observed arrival order does not, and only the arrival order is visible in the data")

    R("A correction banner is perishable in both directions", "supersedes", "The walkthrough narrates an episode that does not exist")
    R("A correction banner is perishable in both directions", "realises", "Deleting a document orphans every pointer into it")
    R("A correction banner is perishable in both directions", "realises", "A citation by line number rots on the next edit")
    R("Proving the engine is not serving mock verdicts", "supports", "Deterministic boundary")
    R("Proving the engine is not serving mock verdicts", "depends on", "Cursor replay")
    R("Proving the engine is not serving mock verdicts", "constrains", "Recompute never mutate")
    R("A broad ignore rule can commit a document with broken images", "threatens", "The pitch artefacts are a build, with the document frozen")
    R("Merging Excalidraw files means rewriting references, not prefixing ids", "supports", "The pitch artefacts are a build, with the document frozen")
    R("The pitch artefacts are a build, with the document frozen", "depends on", "Craneware lands a whole report at one instant")


WAVES = [wave_1_domain, wave_2_object_model, wave_3_decisions,
         wave_4_feeds, wave_5_state_space, wave_6_learnings, wave_7_artifacts,
         wave_8_implementation, wave_9_state_do_not_narrate, wave_10_agent_layer,
         wave_11_agent_layer_built, wave_12_explaining_the_build,
         wave_13_trimmed_for_submission, wave_14_connectivity,
         wave_15_building_and_reviewing_the_connector,
         wave_16_vendor_sourced_ingest,
         wave_17_pitching_the_system]


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
