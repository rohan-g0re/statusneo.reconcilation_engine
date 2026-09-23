# The Product Narrative — McKinsey pitch

**Audience:** McKinsey's Leader of Tech in Healthcare and Leader of Product in Healthcare.
**Premise:** engineering credibility is already won. This session is judged on whether you can see a business through a system.
**Props:** four Excalidraw diagrams, then a live demo. No deck. That is fine — say nothing about it.

---

## Part 0 — What a product narrative actually is

Not a feature list. Not a deck. It is **the story of a person's problem, told in their language, in which your product is the turn.** Five parts, always:

1. **The world** — as the buyer already lives it. No product in it yet. If they don't nod here, nothing after lands.
2. **The tension** — the one thing that is broken, stated as a cost they already pay.
3. **The turn** — what changes. One sentence. Not a feature; a change in what is possible.
4. **The new world** — what their day looks like after. Concrete.
5. **The proof** — why this is real and not a demo.

The discipline it buys you: "does this screen belong?" becomes "does this serve the narrative?", which has an answer.

An engineering narrative says *here is what I built*. A product narrative says *here is what it is worth, to whom, and why the alternative is expensive*.

---

## Part 1 — The narrative, in five beats

Memorise these. Everything else is elaboration.

### 1. The world

A hospital pharmacy hands an expensive drug to a patient. One physical act, two seconds.

From that moment the hospital is owed money — and it comes back from **up to three different places, on three different timetables, in three different formats, from parties who do not talk to each other.**

- Pills at a counter bill to a **PBM**.
- The same drug infused in a chair bills to a **medical insurer**, through a clearinghouse.
- On top of either, if the hospital qualifies for the federal **340B** programme, a **drug manufacturer** owes a separate cash rebate — and whether this dispense qualified is ruled on by a **TPA**, a company whose entire job is that one decision.
- A fourth feed, the **bank**, owes nothing. It is the only proof any of the other three actually landed.

Roughly **30–60% of a health-system specialty pharmacy's claims carry a 340B rebate**. So for a large share of the book, the hospital is chasing two receivables, from two unrelated counterparties, on one drug.

### 2. The tension

**Nobody sends the hospital a statement saying: here is what you were owed, and here is what you got.**

There is no such document, because there is no shared identifier. The four feeds were standardised by different bodies, for different rails, in different decades. The pharmacy feed and the medical feed share literally nothing. A bank line does not carry a claim number at all — it carries a trace number that resolves to a *remittance*, and the remittance holds the claim list. One deposit covers dozens of claims.

So the month-close question — *we dispensed millions of dollars of drug; where is it?* — gets answered with a spreadsheet and a VLOOKUP. And that spreadsheet cannot tell apart:

- **an underpayment from a contractual discount.** A payer paying less than you billed is usually correct — the contract says so. The test is arithmetic: charge, minus payment, minus stated adjustments. A non-zero remainder is money genuinely missing. Nobody does that subtraction on every claim by hand.
- **a rebate refused from a rebate approved and never sent.** The manufacturer's paperwork says paid. No cash arrives. Nothing on the reimbursement side ever surfaces it — different counterparty, different paperwork.
- **a missing claim from a failed match.** The money arrived. The identifier was spelled `07845102` in one feed and `7845102` in the other. The claim reads as *awaiting payment* forever. The claim is not missing — the mapping failed. Different problem, different owner, different fix.
- **a clawback from forty small underpayments.** A payer that thinks it overpaid you does not send an invoice. It pays you less in a later batch and explains it in a line belonging to no claim in that batch.

Each of those confusions is money, and each is invisible in the tool they use today.

### 3. The turn

> **The statement nobody sends is the product.**

One system reads all four feeds, rebuilds each dispense into a single financial story despite the missing identifier, and states — per claim, in dollars — **what should have arrived, what did, what is still outstanding, and which of those needs a human to pick up the phone.**

Three of those four are arithmetic. **Only the last is a judgement call.** That split is the whole design.

### 4. The new world

The analyst opens one screen and sees the book in three piles, because *"what do I do with this?"* has exactly three answers: **nothing, wait, work it.**

They open the pile that needs work, ranked by money. They click a claim and get its entire life in one view — what was filed, what the payer said, when cash moved, how the rebate went, and every point the answer changed. They drag the date backwards and see what the system believed on 31 March, because that is a parameter, not a feature.

When they want the reasoning in English, an AI layer reads that same evidence and writes it with citations that click through to the literal line in the source file. When they want a recommendation, a second model grades the first against a sixteen-point checklist before a human ever sees it. Then a **person** — editing every field, not approving a draft — commits the one row the AI layer is ever allowed to write.

### 5. The proof

- **Every number is computed in Python. The model only explains numbers it was handed.** Nine tools, eight read-only, and zero write tools in any model's tool list. A Python check scans every figure in generated prose against what the tools returned; an unsourced figure is a veto, not a deduction. *A number a model produced is a number you cannot defend in an audit.*
- **Correctness is measured, not claimed.** The rules were enumerated before they were written. The engine reproduces an independently generated oracle on 4,224 of 4,224 configurations, with no tolerance threshold. One disagreement fails the build.
- **The system never guesses, and says so.** Two genuinely indistinguishable claims are parked, not picked — because picking one attributes real money to the wrong claim, and then looks identical to a correct match in every report afterwards. *"I cannot tell"* is a first-class answer with its own code.

### The line to end on

> **The deterministic layer decides what is true. The AI layer decides what is worth saying about it. A human decides what to do.**

---

## Part 2 — The running order

Krithika's shape, timed. Target 20 minutes of talk, then demo.

| # | Beat | Minutes | Prop |
|---|---|---|---|
| 0 | Problem statement — beats 1 and 2 above | 3 | none, just talk |
| 1 | What we built, in one paragraph — beat 3 | 1 | none |
| 2 | Diagram 1 — the system | 4 | 01 System overview |
| 3 | Diagram 2 — the data we work with | 3 | 02 Current data layer |
| 4 | Diagram 3 — how we made it finite | 4 | 03 Decision layer grounding |
| 5 | Diagram 4 — how a verdict is computed | 5 | 04 Verdict computation |
| 6 | Live demo | rest | the app |

**Open with no diagram on screen.** Beats 1 and 2 are a story about a hospital, and a diagram competes with you. Bring up diagram 1 only when you say "so here is what we built."

**The one-paragraph "what we built"** (beat 1, say it once, do not elaborate):

> We built the statement nobody sends. It reads all four feeds, rebuilds every dispense into one financial story, and tells an analyst — in dollars, per claim — what should have arrived, what did, what is outstanding, and which of those needs a human. There is a dashboard for the analyst and an AI layer that explains any claim in English and recommends the next action. I'll show you it running at the end.

---

## Part 3 — What to say at each diagram

### Diagram 1 — System overview (4 min)

The job here is **shape, not mechanism**. Five blocks, top to bottom.

> Four kinds of counterparty send us data. A PBM, a medical payer, the 340B TPAs, the manufacturer's rebate platform, and the bank.
>
> **The connector layer** goes and gets it. It knows how to reach each one, which credential to use, whether we are even allowed to pull that entity's data yet, and whether a file has changed since last time. What it deliberately does *not* know is what any of it means. Fetching and interpreting are two jobs, and they live on opposite sides of a wall. It also runs in both directions — the manufacturer platform is one we send to, not just receive from.
>
> **Ingest and crosswalk** is where meaning arrives. Every line is stored word for word, with a hash of itself, before any parser touches it. Then it is turned into a common shape, and then attached to the right **episode** — one dispense, its claim, its rebate, and all its cash. Attaching it is the hard part, and it is the thing that makes this a platform rather than a report.
>
> **The reconciliation engine** takes one episode plus a point in time and returns a verdict. No network, no model. It cannot change a source record. It only appends a new answer.
>
> **The read layer** hands those verdicts to a dashboard and an AI layer. Both only read.

Then the line the diagram is really for — point at the two orange arrows:

> Those two arrows are the only writes in the whole deterministic side. Records in, verdicts out. And both only ever *add* rows — nothing in this system is ever edited in place. Which means you can delete every answer we have ever produced, feed the same files back in, and get the same answers, byte for byte. That is not a nice-to-have in a system that touches revenue. It is the difference between a report and an audit trail.

**Do not** name protocols here. **Do not** explain what an episode contains. Diagram 2 and 4 do that.

### Diagram 2 — The data layer (3 min)

**Purpose:** prove depth. You are showing that you went down to the wire format of four separate industries, not that you built a mock.

> This is what's actually flowing. Four money roads.
>
> **Pharmacy benefit** runs on NCPDP. A B1 is the billing transaction at the moment of dispense, and the payer answers paid-or-rejected in seconds. A B2 is a reversal — and here's the practitioner detail: a reversal carries no identity of its own. It matches the original by repeating the same four fields. Then weeks later an X12 835 arrives, which is the *payment advice* — what they say they paid. One line in that file can cover twenty-four claims, which is why the bank deposit turns up as a single lump.
>
> **Medical benefit** is a different standards body entirely. An 837 goes out; a 277CA comes back from the clearinghouse. Those two ride together deliberately, because without the 277CA, "the clearinghouse rejected this before the payer ever saw it" and "accepted, still waiting" look *identical on the wire*. Both are an 837 with no payment against it. Then the payer's own 835.
>
> **340B rebate** has two gates and two counterparties. The TPA rules on whether the dispense qualified — that's Verity and Craneware, over SFTP, in vendor-specific CSV. Then the manufacturer platform decides whether to pay, and that one has its own request templates and several different response shapes depending on the answer. And the reason it looks messier on this diagram is that **it is messier in reality — there is no standard here at all.** No 835, no trace number. That is the actual state of this corner of the industry.
>
> **And the bank.** A CSV of credits and debits. It was never designed to carry claims data; it was designed to reconcile a checking account. It is the weakest link in this diagram because it is the weakest link in the real workflow — and it is the only feed that proves cash actually landed.

Close on the tension, because this diagram is where it lands hardest:

> Look across those four columns and find the field they share. There isn't one. Four standards bodies, four decades, four vocabularies. That single fact is the hard part of this problem — not the arithmetic.

If you want one line that sells the connector layer, use this one:

> Verity spells the drug code `ndc_11`. Craneware spells it `ndc11`. The manufacturer calls the date `date_of_service` where the other two call it `fill_date`. Three vendors, three spellings, one fact.

### Diagram 3 — Decision layer grounding (4 min)

**Purpose:** this is the diagram that makes a consulting audience sit up, because it is the only one that is about *method*. Do not let it become a lecture on verdict codes.

> A claim goes down all of those roads. So before writing any logic, we asked a blunt question: how many different ways can one claim end?
>
> **Twelve billion.** Every combination of every decision every party can make. Obviously most of them are nonsense — you cannot have a recoupment on a claim that was never paid. So we enumerated the legal ones exhaustively. **Four thousand two hundred and twenty-four.** That is a real reduction, and it is still far too many to write rules for.
>
> But 4,224 *outcomes* are not 4,224 *answers*. Many of them deserve the same treatment. So we classified them — and they land on **372 distinct answers**, which we call a verdict pair. Two codes: one for how the reimbursement went, one for how the rebate went, because an episode always has both questions and they can disagree.
>
> And here is the payoff. Because the two tracks turn out to be independent, the engine doesn't need 372 branches. It composes **50 rules** — 31 on the reimbursement side, 12 on the rebate side, and 7 that only fire when you look at both together. Fifty rules, each of which a domain expert can read and argue with.
>
> That's twelve billion down to fifty things a human can audit.

Then the sentence that buys you more credibility than anything else in the pitch:

> And I'll tell you how I know that's real rather than a nice story. Before we built the enumerator, I hand-counted the answer space and got 510. The enumeration said 372, and my hand-count had been wrong in four separate ways that partly cancelled out. That file is now loaded as a live test — the shipped engine is run against all 4,224 cases and compared one by one, with no tolerance. One disagreement fails the build.

**Do not** read out code ranges. If someone asks what the codes are: *"31 on the reimbursement side — 16 pharmacy, 15 medical — and 12 on the rebate side, one of which just means this claim has no rebate track at all."* Then stop.

### Diagram 4 — Verdict computation (5 min)

**Purpose:** the payoff. Diagram 3 exists so you can walk this one without defining anything.

> Now, how one answer gets computed.
>
> **Evidence.** Every record attached to this episode that arrived on or before a point in time we call the cursor, plus the cash actually allocated to it. That cursor is why "what did we believe on 31 March" is a parameter and not a feature. Same code path forwards and backwards.
>
> **Eighteen plain questions.** Not clever ones — boring ones. Was it paid in full, partly, not at all, or twice? Did the cash arrive, arrive short, or never arrive? What did the TPA rule? What did the manufacturer rule? Was it reversed, and if so, before or after money moved?
>
> **Those answers pick the verdict pair** — the thing from the last diagram.
>
> **Then seven cross-track rules run last.** These are the compliance cases where each side looks fine alone and the pair is a problem. A claim that was denied, but had a rebate paid on it anyway. A dispense reversed while the rebate stayed live. And the direction is fixed: those rules may only add a reason or force an escalation. They can never soften one. A rule that can downgrade is a rule that can hide something.
>
> **And it rolls up to one disposition**, because "what do I do with this?" only has three answers. Nothing. Wait. Work it. Worst wins, so the track that needs a human is the one that surfaces.

Three things to land before you move to the demo. These are the product arguments, not engineering ones:

> **One — it can say "I don't know", and that's a first-class answer.** There are three places the engine knows it cannot decide, and each one escalates with its own code. The alternative is reporting variance against an expected amount of zero, which renders an unpriceable claim as perfectly clean. Which is worse than useless — it is confidently wrong.
>
> **Two — age is not an input.** There are no SLA thresholds anywhere in this logic. Age is computed when you look at the screen and used only to sort a queue. That sounds small and it is load-bearing: it makes "a verdict cannot change unless a new record arrives" true by construction. Resolving a two-hundred-day-old claim costs exactly what resolving this morning's costs. There is no time window to widen, because there is no sweep.
>
> **Three — there is no queue table.** A queue is a query over the latest verdict, filtered. Nothing is ever moved into it, so nothing can get stuck in it. Delete the entire history, replay the feeds, and every queue rebuilds identically.

Then hand off to the demo:

> That's the machinery. Let me show you what an analyst actually sees.

---

## Part 4 — What NOT to say

### Never say these words

**"Mock." "Synthetic." "Fake." "Dummy." "Assignment." "Take-home." "Demo project." "POC." "Prototype."**

Say **"the system"**, **"the platform"**, **"this build"**, **"the demo dataset"**.

### But never *deny* it either

If someone asks directly whether the data is real — and someone will — **do not dodge**. Dodging is the only thing here that is actually fatal. Answer once, turn it into the strength it genuinely is, and move on:

> The data is generated, and it's generated adversarially. We enumerated the legal state space first, then built the feeds backwards from it, and each of the four generators only ever sees what its real-world source system would actually know — no generator has ever seen an episode ID or an expected amount. The answer key is withheld from the code being graded; the ingestion path is structurally unable to read it. That's why I can tell you the crosswalk reassembles 1,349 of 1,354 resolvable episodes rather than just telling you it works.

Then stop. Do not keep defending it.

### Cut entirely from the spoken pitch

| Cut | Why |
|---|---|
| The four generators, blind slices, the orchestrator | This is the mocking story. It is genuinely good engineering and it is not the product. Keep it in your pocket for an engineering follow-up. |
| Field names — `CLP01`, `CLP07`, `TRN02`, splitting on the literal word `FILL` | Great detail, wrong room. One of them, once, if pushed for depth. |
| Injected defect rates — 3% duplicates, 8% late, 6% identifier drift | Belongs to the data-generation story you are not telling. |
| "LLM", "prompt", "tool loop", "harness", "the proposer and the evaluator" | Say **"the AI layer"**, **"a second model grades the first"**, **"a human commits it"**. |
| Test counts — 585 passing, 978 with connectors | Engineering credibility is already won. Spending the room's attention on it is spending it on the wrong thing. |
| What's not built — no auth, no tenancy | Do not volunteer it. If asked, answer in one sentence and name the seam: *"No auth or tenant isolation yet — the repository layer is the right place for it and the predicate isn't there."* |
| Anything about not having a deck | Never draw attention to a missing artifact. The diagrams are the artifact. |

### Say once, not three times

You have three genuinely strong lines. Each one lands hard the first time and gets weaker every repeat:

1. *"Every number is computed in Python. The model only explains numbers it was handed."*
2. *"Twelve billion down to fifty things a human can audit."*
3. *"The system never guesses — it parks, and says so."*

Place them deliberately: **(1)** at diagram 1, **(2)** at diagram 3, **(3)** at diagram 4. Then the closing line at the end of the demo.

### Tone

Do not oversell. This audience punishes it. The numbers here are strong enough that understating them reads as confidence — *"the engine reproduces the oracle on all 4,224, with no tolerance threshold"* beats *"it's extremely accurate"* every time. Quote the measurement; let them do the reacting.

---

## Part 5 — Diagram fixes before you present

### Diagram 1 — approved, two small edits

- The sticky note says *"replace the names with product related stuff"*. Do it: **Dashboard → Operations Console**. **AI Agent Layer → Analyst Assistant**.
- The other sticky asks for a different word than "connector layer". Keep **Connector Layer** — it is the industry word, McKinsey healthcare people know it, and it is what the second assignment calls it too. Add a bidirectional arrowhead on the Beacon leg so the two-way point is visible without you saying it.

### Diagram 2 — rework

| Change | Reason |
|---|---|
| **Delete the "ORCHESTRATOR" box** at the top. Replace with **Connector Layer**. | "Orchestrator" is the generator-side word — it is the thing that mints the data. That single box gives away the whole mocking story on a diagram you are showing precisely to avoid telling it. |
| **"X12 835 — payment acks" → "X12 835 — remittance advice (what they say they paid)"** on both columns. | 835 is not an acknowledgement. 277CA is. Someone in that room will know. |
| Add a right-hand column: one plain-English line per feed saying **what question it answers**. Pharmacy: *did the claim adjudicate, and what did they pay?* Medical: *did it reach the payer, and what did they pay?* 340B: *did it qualify, and did the manufacturer pay?* Bank: *did the cash actually land?* | Turns a protocol inventory into a product diagram. This is the single highest-value change on the page. |
| Add under Bank Statements: **"the only proof any of the other three actually landed."** | It is the business point and it is currently missing. |
| Add one line across the bottom: **"No field is shared across all four."** | This is where the tension should land visually. |

### Diagram 3 — rework

| Change | Reason |
|---|---|
| **12M → 12,093,235,200.** Label it "possible outcomes per claim". | It is twelve **billion**. A 1000× error on your headline number, on the one diagram that is about rigour. |
| **4,224**, never "~4,000". | The precision *is* the argument. A round number reads as an estimate. |
| Relabel the middle arrow **"enumerated, not estimated"**. | "Distill into classes" reads as hand-waving. The actual claim is much stronger. |
| **Add a fourth node on the right: "50 RULES — 31 reimbursement + 12 rebate + 7 cross-track".** | Right now the diagram ends at 372 with no *so what*. 50 is the punchline and it is not on the page. |
| Reimbursement box: **"31 codes — 16 pharmacy (A-xx), 15 medical (B-xx)"**. | The ranges A-01..A-17 and B-01..B-16 have retired gaps in them. Quote counts; ranges invite a nitpick that costs you a minute. |
| Small caption under 372: **"first hand-count said 510 — and was wrong in four ways."** | The most credible thing in the whole pitch for this audience. Put it where you'll remember to say it. |

### Diagram 4 — approved, one edit

- Header on the 18-dimension box currently reads "18 DIMENSIONS". Say **"18 plain questions"** on the diagram too. "Dimensions" is an internal word.

---

## Part 6 — The four questions you will get

**"How is this different from what [Craneware / Kodiak / a rev-cycle vendor] already sells?"**
> Those tools reconcile within one rail. The pharmacy tool reconciles pharmacy. The 340B tool reconciles 340B. Neither one can tell you that a claim was denied on the medical side while a rebate was paid on it anyway — because that fact does not exist inside either system. It only exists when you hold both tracks against the same dispense. That is the seven cross-track rules on the last diagram, and it is the part nobody else is positioned to do.

**"Where does the AI actually add value, if it can't compute anything?"**
> It compresses the read. An analyst looking at an exception has to reconstruct a story out of six records across four formats — that's ten minutes a claim. The AI layer writes that story in four paragraphs with every figure linked back to the literal line it came from, and proposes the next action. The analyst edits it and commits it. We took the arithmetic away from the model on purpose, because a reconciliation number a model produced is a number you cannot defend in an audit.

**"What does this take to deploy at a real health system?"**
> The connector layer is the work, and it's the work regardless of who builds the rest — every TPA has its own export shape and its own access process. The engine doesn't change per customer; the 50 rules are the industry's rules, not a customer's. The honest gate is vendor access: you cannot build a connector against a spec you've been given but not shown running. That is a first-three-weeks procurement problem, not an engineering one.

**"How long did this take?"**
> Answer plainly, no hedging, and immediately point at what it bought: *"and the reason it went that fast is that the state space was enumerated before a single rule was written. We never debugged a rule against an opinion."*

---

## Part 7 — The last thirty seconds

After the demo, before questions. Say exactly this and then stop talking:

> Three ideas, and they're the thing to take out of the room.
>
> **There's no shared claim ID.** So this is built out of named bridges, each with a failure mode we can name in advance — not one join pretending to be reliable.
>
> **It never guesses.** An ambiguous match parks. A missing key parks. An unpriceable claim says so out loud. Every inferred cash match is recorded as inferred, permanently distinguishable from a known one.
>
> **Everything is a replay.** Raw lines are immutable, answers are appended, age is not an input, and time is a parameter. So "what did we believe in March" and "what do we believe now" come out of the same function.
>
> The deterministic layer decides what is true. The AI layer decides what is worth saying about it. A human decides what to do.
