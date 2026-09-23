// Plain-English names for every code this UI puts on screen.
//
// The screens carry four separate code namespaces and, before this file existed, showed all
// four as bare tokens: `B-10`, `C-08`, `X-1`, `D-3`, `REBATE_NO_CASH`, `NO_KEY_MATCH`. All of
// them are meaningful and none of them is readable without the state-space document open in
// another window. A reader who has learned that A, B and C are tracks will also reasonably
// assume there is a track D. There is not — see NAMESPACES below.
//
// Two rules hold everywhere this module is used:
//
//   1. The label is the primary text and the code is a muted suffix, never a replacement. An
//      operator quotes the code to a payer, and the agent layer's grounding tables key on it.
//   2. Nothing here is authoritative over the engine. `detail` is copied verbatim from
//      VERDICT_DESCRIPTIONS in src/recon/domain/verdicts.py so the two cannot say different
//      things about the same code; `label` is the short form written for this screen. The API
//      does not serve either, which is why they live here rather than arriving in a payload --
//      and why a verdict code added to the engine has to be added here too. `unknown()` below
//      is what stops that gap from rendering as a blank cell.

// ── the four namespaces ────────────────────────────────────────────────────
// Rendered on the verdict-library screen, because "where does D come from" is the single
// most common question this vocabulary provokes.
export const NAMESPACES = [
  {
    key: 'A',
    title: 'Insurance — pharmacy benefit',
    blurb: 'A claim billed to a PBM: a drug picked up at a counter.',
  },
  {
    key: 'B',
    title: 'Insurance — medical benefit',
    blurb: 'The same drug administered by a clinician, billed to an insurer.',
  },
  {
    key: 'C',
    title: '340B rebate',
    blurb: 'The manufacturer rebate that sits on top of either insurance road, optionally.',
  },
  {
    key: 'X',
    title: 'Cross-track flags',
    blurb: 'A problem you can only see by looking at both tracks at once.',
  },
  {
    key: 'D',
    title: 'Data exceptions',
    blurb: 'A fault in the data we received, not a fact about any claim. There is no track D.',
  },
]

// A and B are two halves of ONE track -- a claim travels one road or the other, never both.
export const TRACK_NOTE =
  'A and B are the insurance track: a claim is billed to a PBM or to a medical insurer, ' +
  'never both. C is the 340B rebate track, which sits on top of either. X marks a problem ' +
  'visible only across both. D is not a track at all — it is a fault in the data we received.'

// ── verdict codes ──────────────────────────────────────────────────────────
// `detail` is verbatim from src/recon/domain/verdicts.py. `label` is the short form.
const VERDICTS = {
  'A-01': ['Refused at the counter', 'Rejected at point of sale; drug not dispensed; expected 0'],
  'A-02': ['Waiting for payment', 'Adjudication accepted, awaiting payment; no defect'],
  'A-04': ['Paid in full and settled', 'Fully reconciled: paid in full, cash matched, settlement confirmed'],
  'A-05': ['Paperwork says paid, no money arrived', 'Remittance claims payment, bank shows no matching deposit'],
  'A-06': ['Money arrived, confirmation missing', 'Paid and cash-matched, settlement confirmation missing'],
  'A-07': ['Underpaid', 'Underpayment, cash matched the short amount'],
  'A-08': ['Underpaid and no money arrived', 'Underpayment and no cash: compound failure'],
  'A-09': ['Overpaid — we owe money back', 'Overpayment; refund liability'],
  'A-10': ['Cancelled, money returned', 'Reversed by pharmacy, money returned; net zero'],
  'A-11': ['Cancelled, but we still hold the cash', 'Reversal recorded, money not returned'],
  'A-12': ['Clawed back, and we traced it', 'Recouped after audit, netted and matched'],
  'A-13': ['Clawback we cannot trace', 'Recoupment not traceable to any deposit'],
  'A-14': ['Contract explains the gap', 'Contractual adjustment explains the shortfall; remainder settled'],
  'A-15': ['Contract explains only part of the gap', 'Adjustment applied, variance still unexplained'],
  'A-16': ['Cancelled before any money moved', 'Reversed before payment; expected falls to zero'],
  'A-17': ['Paid twice', 'Duplicate payment: two payment events for one claim'],

  'B-01': ['Bounced by the clearinghouse', 'Clearinghouse rejection; 837 never reached the payer'],
  'B-02': ['Waiting on the insurer', 'Submitted and accepted, awaiting 835; no defect'],
  'B-04': ['Paid in full', 'Paid in full, cash matched'],
  'B-05': ['Insurer says paid, no money arrived', '835 reports payment, no cash arrived'],
  'B-06': ['Paid short, no appeal filed', 'Partial payment, no appeal filed'],
  'B-07': ['Paid short, appeal in progress', 'Partial payment, appeal pending'],
  'B-08': ['Appeal won, balance paid', 'Partial, appeal won, balance paid'],
  'B-09': ['Appeal lost', 'Partial, appeal lost; balance written off'],
  'B-10': ['Denied, no appeal filed', 'Denied, no appeal filed'],
  'B-11': ['Denied, appeal in progress', 'Denied, appeal pending'],
  'B-12': ['Denial overturned, paid', 'Denied, appeal won, paid and matched'],
  'B-13': ['Denied, appeal lost', 'Denied, appeal lost; terminal loss'],
  'B-14': ['Appeal won, payment never arrived', 'Appeal won, payment never arrived'],
  'B-15': ['Paid, then taken back', 'Paid, then payer takeback'],
  'B-16': ['The same remittance arrived twice', 'Duplicate 835 for the same claim'],

  'C-00': ['No 340B rebate', 'No 340B track on this episode; absence is not an exception'],
  'C-01': ['Waiting on the eligibility ruling', 'Qualification pending at the TPA; no defect'],
  'C-02': ['Ruled not eligible', 'Not qualified; expected rebate 0; a correct outcome, not a failure'],
  'C-03': ['Eligible, not yet requested', 'Qualified, rebate request not yet submitted'],
  'C-05': ['Manufacturer has not answered', 'Request submitted, manufacturer decision pending'],
  'C-07': ['Manufacturer refused', 'Manufacturer rejected the request'],
  'C-08': ['Approved, paid, cash matched', 'Approved, paid, cash matched'],
  'C-09': ['Says paid, bank shows nothing', 'Approved and paid per the TPA, no cash in the bank'],
  'C-10': ['Paid less rebate than expected', 'Approved, partial rebate paid'],
  'C-11': ['Approved, awaiting payment', 'Approved, awaiting payment; no defect'],
  // Not "Rebate clawed back": the queue prefixes this line with "Rebate:", and the episode
  // panel labels the row "340B rebate". Both would read "Rebate: Rebate clawed back".
  'C-13': ['Clawed back', 'Rebate clawed back; net rebate zero'],
  'C-14': ['Two rebates for one dispense', 'Duplicate rebate payment'],
}

// ── cross-track flags ──────────────────────────────────────────────────────
// These are compliance findings, not diagnostics, and the UI gives them their own treatment.
const FLAGS = {
  'X-1': [
    'Denied, but we kept the rebate',
    'The insurer refused to pay for the drug and the manufacturer paid the 340B rebate anyway. Net position is negative and the rebate rests on a claim the payer refused.',
  ],
  'X-2': [
    'Rebate on a drug never dispensed',
    'The sale was reversed, so the drug never reached the patient — which makes any rebate claimed against it invalid.',
  ],
  'X-3': [
    'Clawback undercuts the rebate',
    'Money was recouped because the patient turned out ineligible, and the 340B rebate rests on the same now-wrong facts.',
  ],
  'X-4': [
    'Rebate refused, insurance clean',
    'Only the rebate was refused. Deliberately not a compliance flag — it escalates nothing.',
  ],
  'X-5': [
    'Neither track has cash',
    'Both tracks report paid on paper with nothing in the bank. Almost certainly one cause on our side, not two payers failing at once.',
  ],
  'X-6': ['Every channel failed', 'Total loss on the drug.'],
  'X-7': [
    'Appeal won after a clawback',
    'A medical appeal was won after the rebate had been clawed back — the reason for the clawback may no longer hold. Recoverable money nobody is watching.',
  ],
}

// ── data exceptions ────────────────────────────────────────────────────────
// Faults in what arrived, orthogonal to any claim's state. Not a fourth track.
const DATA_EXCEPTIONS = {
  'D-1': ['The same file delivered twice', 'An SFTP rerun, or a retry after an ambiguous timeout.'],
  'D-2': ['Arrived after the event it explains', 'Not an error — it becomes matchable later.'],
  'D-3': ['Cash we cannot attribute', 'Money arrived and nothing explains it.'],
  'D-4': ['Rebate money we cannot tie to a dispense', 'Rebate cash with no qualified dispense behind it.'],
  'D-6': ['Documents whose ID matched nothing', 'Held, not discarded, and re-checked on every later arrival.'],
  'D-7': ['A deposit that did not fully allocate', 'The deposit is right; our split of it is incomplete.'],
}

// ── dispositions ───────────────────────────────────────────────────────────
// The queue buckets, under the engine's own names.
//
// These used to be renamed for the action -- "Needs work", "Waiting on someone", "Settled" --
// on the reasoning that an operator is deciding what to *do* and the enum only says what a row
// *is*. Two labels then had to sit side by side on every tile, the invented one loud and the
// real one as a muted suffix, and the invented one was not reliably true: a PENDING row is not
// always waiting on a person, and a CLOSED row is not always settled in any sense an accountant
// would accept -- 512 of them came back. A label that overstates is worse than a label that
// only names the bucket, because the sentence underneath is free to say what the bucket means
// and can be corrected without renaming anything.
//
// `detail` is one line on the tile, so it is one sentence here. PENDING's used to run to three
// -- "Waiting on an external party. No defect. Ranked by age." -- and wrapped, which made it the
// only tile whose note was a paragraph. The two clauses that went are both recoverable where
// they matter: "no defect" is the whole distinction between this bucket and EXCEPTION, which the
// three tiles side by side already make, and the sort order is named in the queue's own
// order-by control rather than asserted on a card above it.
const DISPOSITIONS = {
  EXCEPTION: ['Exception', 'A defect exists. Work it.'],
  PENDING: ['Pending', 'Waiting on an external party.'],
  CLOSED: ['Closed', 'Nothing to do.'],
}

// ── reason codes ───────────────────────────────────────────────────────────
// The engine has 37 and carries no label table for them: their meaning is defined by which
// verdict maps to which code. These are written for this screen.
const REASONS = {
  REJECTED_AT_POS: 'Refused at the counter',
  AWAITING_REMITTANCE: 'Waiting for the payment decision',
  AWAITING_CASH: 'Decision made, waiting for the money',
  UNDERPAID: 'Paid less than the contract allows',
  OVERPAID: 'Paid more than the contract allows',
  DUPLICATE_PAYMENT: 'Paid twice for one claim',
  CASH_MISMATCH: 'The money does not match the paperwork',
  NO_CASH: 'Paperwork says paid; no money arrived',
  SETTLEMENT_MISSING: 'Money arrived, closing confirmation did not',
  REVERSAL_CASH_NOT_RETURNED: 'Cancelled, but we still hold the money',
  RECOUPMENT_UNTRACEABLE: 'A clawback we cannot tie to any deposit',
  ADJUSTMENT_RESIDUAL: 'The discount explains only part of the gap',
  CLEARINGHOUSE_REJECTED: 'Bounced before the insurer saw it',
  DENIED: 'The insurer refused to pay',
  APPEAL_PENDING: 'Appeal in progress',
  APPEAL_LOST: 'The appeal failed',
  APPEAL_WON_NO_CASH: 'They agreed they owe us and still have not paid',
  DUPLICATE_REMITTANCE: 'The same remittance arrived twice',
  REBATE_AWAITING_QUALIFICATION: 'Waiting on the 340B eligibility ruling',
  REBATE_NOT_QUALIFIED: 'Ruled not eligible for 340B',
  REBATE_AWAITING_SUBMISSION: 'Eligible; the rebate has not been requested yet',
  REBATE_AWAITING_MANUFACTURER: 'Requested; the manufacturer has not answered',
  REBATE_REJECTED: 'The manufacturer refused the rebate',
  REBATE_AWAITING_PAYMENT: 'Approved; the manufacturer has not paid',
  REBATE_UNDERPAID: 'Less rebate paid than expected',
  REBATE_NO_CASH: 'The administrator says paid; the bank shows nothing',
  REBATE_CLAWED_BACK: 'A rebate we received was reversed',
  DUPLICATE_REBATE: 'Two rebates for one dispense',
  REBATE_ON_UNDISPENSED_CLAIM: 'A rebate stands on a claim where the drug never went out',
  DENIED_WITH_REBATE_PAID: 'The insurer refused, the manufacturer paid anyway',
  QUALIFICATION_UNDERMINED_BY_RECOUPMENT: 'A clawback undercuts the rebate’s grounds',
  CORRELATED_CASH_GAP: 'Neither track has cash — likely one cause, not two',
  TOTAL_LOSS: 'Every collection path is exhausted',
  REBATE_RE_REQUEST_AVAILABLE: 'The rebate can be requested again',
  // The five below are feed-level faults that can also land on an episode as a reason.
  DUPLICATE_DELIVERY: 'The same file arrived twice',
  ORPHAN_DEPOSIT: 'Cash we cannot attribute to any claim',
  ORPHAN_REBATE: 'Rebate money with no dispense behind it',
  CROSSWALK_MISS: 'A document’s identifiers matched nothing',
  MALFORMED_RECORD: 'A record we could not read',
  ALLOCATION_RESIDUAL: 'A deposit that did not fully allocate',
  INSUFFICIENT_DATA: 'Not enough evidence to decide',
}

// ── why a document did not match ───────────────────────────────────────────
// Four different problems with four different owners. Kept distinct on purpose.
const PARK_REASONS = {
  NO_KEY_MATCH: 'Its identifiers matched nothing we hold',
  AMBIGUOUS_KEY_MATCH: 'It matched more than one claim — we will not guess',
  NO_KEYS_PRESENT: 'It carried no usable identifier',
  COVERED_ENTITY_MISMATCH: 'It names a different 340B entity',
}

// ── what a human can commit ────────────────────────────────────────────────
const ACTIONS = {
  RESUBMIT: 'Fix and send again',
  APPEAL: 'Challenge the decision',
  WRITE_OFF: 'Accept the loss',
  ESCALATE: 'Hand to someone who can chase',
  INVESTIGATE_CROSSWALK: 'The ID match failed — the money may be here',
  AWAIT_PAYER: 'Wait on the other side',
  ABSTAIN: 'Not enough evidence to recommend',
}

// ── identifiers that resolve a claim ───────────────────────────────────────
const KEY_TYPES = {
  NCPDP_CLAIM: 'Pharmacy claim key',
  MEDICAL_CLM01: 'Provider claim number',
  PAYER_ICN: 'Payer control number',
  TRN02: 'Remittance trace number',
  ALLOCATION_CODE: 'Rebate allocation code',
  NATURAL_340B_PHARMACY: 'Pharmacy natural key',
  NATURAL_340B_MEDICAL: 'Medical natural key',
  PBM_AUTH: 'PBM authorisation number',
  BEACON_ID: 'Beacon submission id',
  COVERED_ENTITY_340B: '340B covered entity',
  HCPCS: 'Procedure code',
  PAYMENT_REFERENCE: 'Payment reference',
}

// ── what happened, on a timeline ───────────────────────────────────────────
// An explicit map rather than string-mangling the enum. The mangling this replaces lowercased
// the whole tag and capitalised the first letter, which rendered TPA_QUALIFICATION as
// "Tpa qualification" on every episode carrying a rebate.
const RECORD_KINDS = {
  PHARMACY_CLAIM: 'Claim filed at the counter',
  PHARMACY_REVERSAL: 'Pharmacy cancelled the sale',
  REMITTANCE: 'Payer sent a remittance',
  REMITTANCE_CLAIM_LINE: 'Payer’s payment decision',
  PROVIDER_LEVEL_ADJUSTMENT: 'Payer clawback',
  MEDICAL_SUBMISSION: 'Claim filed to the insurer',
  MEDICAL_ACKNOWLEDGMENT: 'Clearinghouse accepted the claim',
  TPA_QUALIFICATION: '340B administrator ruled on eligibility',
  TPA_REBATE_REQUEST: 'Rebate requested from the manufacturer',
  TPA_MANUFACTURER_DECISION: 'Manufacturer ruled on the rebate',
  TPA_REVERSAL: '340B claim reversed',
  TPA_INVOICE_LINE: 'Administrator invoice line',
  REBATE_BATCH: 'Manufacturer paid a rebate batch',
  REBATE_DISPENSE_LINE: 'Rebate line for this dispense',
  BANK_TRANSACTION: 'Money moved at the bank',
  BEACON_ACKNOWLEDGMENT: 'Beacon received the submission',
  BEACON_PAYMENT_REFERENCE: 'Beacon payment reference',
  BEACON_VALIDATION_OUTCOME: 'Beacon validated the submission',
  CASH: 'Cash',
  // "Verdict", not "The answer changed". A verdict is the system's own noun for the thing it
  // produces -- it is what the engine writes, what the queue sorts on, what the log records and
  // what every document here calls it. Paraphrasing it into a sentence renamed the central
  // object of the product on the one screen where a reader watches it being built.
  VERDICT: 'Verdict',
}

// ── lookup ─────────────────────────────────────────────────────────────────

// A code with no entry renders as itself rather than as blank or as "undefined". The engine
// owns these vocabularies and can grow one without this file noticing; showing the raw token
// is wrong but legible, and a reader can still look it up. Silently rendering nothing is not.
function unknown(code) {
  return { label: String(code ?? '—'), detail: '' }
}

function pick(table, code) {
  const hit = table[code]
  if (!hit) return unknown(code)
  return Array.isArray(hit) ? { label: hit[0], detail: hit[1] } : { label: hit, detail: '' }
}

export const verdict = (code) => pick(VERDICTS, code)
export const flag = (code) => pick(FLAGS, code)
export const dataException = (code) => pick(DATA_EXCEPTIONS, code)
export const disposition = (code) => pick(DISPOSITIONS, code)
export const reason = (code) => pick(REASONS, code)
export const parkReason = (code) => pick(PARK_REASONS, code)
export const action = (code) => pick(ACTIONS, code)
export const keyType = (code) => pick(KEY_TYPES, code)
export const recordKind = (code) => pick(RECORD_KINDS, code)

/** Which of the two tracks a verdict code belongs to. Drives the label beside it. */
export function trackOf(code) {
  if (typeof code !== 'string') return null
  if (code.startsWith('C-')) return 'rebate'
  if (code.startsWith('A-') || code.startsWith('B-')) return 'insurance'
  return null
}

/** True when this rebate verdict means the track is simply absent, not unresolved. */
export const isAbsentRebate = (code) => code === 'C-00'
