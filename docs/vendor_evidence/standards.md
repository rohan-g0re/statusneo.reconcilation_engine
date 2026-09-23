# Evidence: public standards

Retrieved: 2026-09-16.

The `STANDARD` tier of requirement V3: *"taken from a public standard (X12, NCPDP, ISO
20022, FHIR), cited to that standard."*

These are a different kind of source from the vendor pages. A vendor page tells us what
one company does; a standard tells us what a whole industry agreed to, and the existing
feeds in `src/recon/generators/` were already built to these four. They are recorded here
so that a field lifted from a standard can be tagged `STANDARD` and cited, rather than
being lumped in with `INVENTED` — which would overstate how much of the build is our own
construction.

**A note on retrieval.** The standards bodies below publish their transaction sets and
implementation guides commercially; the full segment-level specifications are paywalled
and were not retrieved. What is cited here is the *existence and identity* of each
standard and the specific semantics the existing codebase already implements and tests.
Every entry is therefore `SECOND_HAND` rather than `RETRIEVED` — we are citing the
standard, not quoting its text. That distinction matters and is the reason none of these
carries a verbatim specification excerpt.

Requirement §0 of the requirements document already ruled true X12 segment-level parsing
out of scope: *"Our EDI-equivalent feeds already exercise the semantics. Fidelity work
with no new reconciliation behaviour behind it."* These entries support the semantics we
do model, not a claim to full conformance.

---

## STD-X12-835 — ASC X12N 835 Health Care Claim Payment/Advice

- **Body:** Accredited Standards Committee X12, Insurance Subcommittee (X12N)
- **URL:** https://x12.org/products/transaction-sets
- **Identity:** `835 Health Care Claim Payment/Advice`
- **What the codebase models from it:** the `BPR` financial-information segment, the `TRN`
  reassociation trace segment, `CLP` claim-level payment segments with `CLP01`–`CLP07`,
  service-line `SVC` detail, `CAS` adjustment group and reason codes (`CO-45`, `PR-3`,
  `CO-97`, `OA-94`, `CO-50`, `PR-2`, `CO-197`), and `PLB` provider-level adjustments
  including `WO` clawbacks and `FB` forward balances.
- **Where:** `src/recon/generators/pbm.py` and `medical.py` emit these; the 835 arithmetic
  tests in `tests/test_generators.py` assert `clp03 = clp04 + Σadjustments` and
  `BPR = Σclp04 − ΣPLB` by re-deriving both from the wire.
- **Use in the connectivity layer:** `TRN02` is already the crosswalk key joining a
  remittance to a bank deposit. Nothing new is taken from this standard; it is cited so
  that fields carried into vendor mappings can be tagged `STANDARD` rather than
  `INVENTED`.

## STD-X12-837 — ASC X12N 837 Health Care Claim

- **Body:** Accredited Standards Committee X12, Insurance Subcommittee (X12N)
- **URL:** https://x12.org/products/transaction-sets
- **Identity:** `837 Health Care Claim`
- **What the codebase models from it:** `CLM01` patient control number, `CLM05-3` claim
  frequency code (including frequency `7`, the replacement claim that models an appeal),
  `REF*F8` original-ICN reference, loop `2010BB` payer identification, loop `2410`
  drug-identification with `LIN02`/`NDC11`/`CTP04`/`CTP05`, and `SVC01` composite
  procedure identifiers carrying HCPCS J-codes with modifiers.
- **Use in the connectivity layer:** requirement E2 adds HCPCS alongside NDC as a
  crosswalk key. The J-code and its modifier are `STANDARD`, cited here — they are not our
  invention, and tagging them `INVENTED` would be inaccurate in the opposite direction.
  `BEACON-008`'s partial medical key names "HCPCS modifier code," which is this standard's
  vocabulary.

## STD-NCPDP-TELECOM — NCPDP Telecommunication Standard

- **Body:** National Council for Prescription Drug Programs
- **URL:** https://standards.ncpdp.org/Standards-Info.aspx
- **Identity:** `NCPDP Telecommunication Standard`
- **What the codebase models from it:** transaction code `B1` (billing) and `B2`
  (reversal); field `442-E7` Quantity Dispensed carried with **three implied decimals**,
  which is why every quantity in this system is an integer in milli-units rather than a
  float; prescription reference number and fill number; service provider and prescriber id
  qualifiers; response status and reject codes.
- **Use in the connectivity layer:** the `B1`/`B2` pair is the pharmacy-benefit reversal
  representation, and requirement B3 requires each vendor to declare its own. Ours is
  `STANDARD` for the pharmacy track and `INVENTED` for Verity and Craneware, because
  neither publishes theirs (`DOC2-011`).

## STD-NACHA-CCD — ACH CCD+ with addenda

- **Body:** Nacha
- **URL:** https://www.nacha.org/rules
- **Identity:** `CCD+ Corporate Credit or Debit Entry with Addenda`
- **What the codebase models from it:** the ACH trace number, the company id and company
  entry description (`HCCLAIMPMT`), the effective entry date, and the single addenda
  record carrying the `TRN02` reassociation trace number that links a deposit back to its
  remittance.
- **The reassociation expectation** is CAQH CORE Operating Rule 370, which requires the
  835's `TRN02` to be reproduced in the CCD+ addenda so a provider can match payment to
  advice. The existing generator deliberately **drops that addenda on a fraction of rows**,
  which is what makes the two-hop bank resolution and the amount-and-date fallback
  necessary rather than decorative.
- **Use in the connectivity layer:** requirement E3 adds payment references as crosswalk
  keys. The manufacturer rebate payment reference is `INVENTED`; the ACH mechanics it
  travels on are `STANDARD`, cited here.
