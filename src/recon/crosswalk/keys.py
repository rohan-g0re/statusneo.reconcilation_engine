"""Canonical key forms.  The one place a ``key_value`` is ever built.

Every crosswalk lookup is a point seek on ``(key_type, key_value)`` (Decision A24), so
the string form has to be identical on the write side and the read side.  Building it
in two places is how you get a crosswalk that works for pharmacy claims and silently
misses every medical one.

The separator is ``|`` and it is not negotiable: a component containing ``|`` would
make two different keys collide onto one string, so :func:`_component` refuses it.

═══ What this module deliberately does NOT do ═══════════════════════════════════════

**No identifier normalisation (Decision A23).**  ``rx_number`` is used exactly as the
feed spelled it.  The generators inject an identifier-drift defect — ``"07845102"`` in
one feed against ``"7845102"`` in another — and that drift is *meant* to miss.  The
miss **is** the D-6 crosswalk-failure exception, which is one of the things this
system exists to surface.  Stripping the leading zero here would resolve the record,
delete the exception, and leave the engine quietly covering up the defect it was built
to find.  The claim is not missing; the *mapping* failed, which is a different fix with
a different owner.

So: no ``lstrip("0")``, no ``upper()``, no whitespace trimming, no case folding.  If a
tiered fuzzy fallback is ever wanted it belongs in the engine as an explicitly
low-confidence second pass, flagged as such — never on the primary match path, and
never in here.

**Date format parsing is not normalisation.**  ``CCYYMMDD`` -> ``YYYY-MM-DD`` is a
lossless, bijective re-rendering of the same value, and every feed writes dates in
``CCYYMMDD`` anyway.  It is how the wire format is read, not a judgement that two
different values are "really" the same.  That is the line: re-rendering one value is
parsing, deciding two values match is normalisation.
"""

from __future__ import annotations

from recon.domain.enums import KeyType

__all__ = [
    "SEPARATOR",
    "FILL_MARKER",
    "ncpdp_claim",
    "medical_clm01",
    "payer_icn",
    "trn02",
    "allocation_code",
    "natural_340b_pharmacy",
    "natural_340b_medical",
    "pbm_auth",
    "beacon_id",
    "covered_entity_340b",
    "hcpcs_medical",
    "payment_reference",
    "parse_clp01_pharmacy",
    "format_clp01_pharmacy",
    "wire_date_to_iso",
    "iso_date_to_wire",
    "KeyError_",
]

SEPARATOR = "|"

#: NCPDP's documented convention for the pharmacy 835's CLP01: the Rx number with the
#: fill number glued on after the literal word ``FILL``.
FILL_MARKER = "FILL"


class MalformedKeyComponentError(ValueError):
    """A key component is absent, or contains the separator."""


#: Exported under a trailing underscore so ``from ... import *`` cannot shadow the
#: builtin ``KeyError``.
KeyError_ = MalformedKeyComponentError


def _component(value: object, field: str) -> str:
    """Validate one component of a composite key.

    Refuses ``None``, the empty string and anything containing the separator.  A
    composite key with a missing component is not a weaker key — it is a *different*
    key that would collide with some other record's, so the caller must park the
    record rather than build a partial key.
    """
    if value is None:
        raise MalformedKeyComponentError(
            f"key component {field!r} is None; a composite key with a missing "
            "component would collide with other records. Park the record instead."
        )
    text = str(value)
    if text == "":
        raise MalformedKeyComponentError(f"key component {field!r} is empty")
    if SEPARATOR in text:
        raise MalformedKeyComponentError(
            f"key component {field}={text!r} contains the separator {SEPARATOR!r}; "
            "two distinct keys would collide onto one string"
        )
    return text


def _join(*parts: str) -> str:
    return SEPARATOR.join(parts)


# ═══ the eight key types ════════════════════════════════════════════════════


def ncpdp_claim(
    pharmacy_npi: str, rx_number: str, fill_number: str, date_of_service: str
) -> tuple[KeyType, str]:
    """``NCPDP_CLAIM`` — the pharmacy transaction key.

    The NCPDP composite that a B2 reversal repeats instead of carrying an identity of
    its own, and the key a pharmacy 835 reconstructs by parsing ``CLP01`` apart.

    ``date_of_service`` is ``YYYY-MM-DD``; use :func:`wire_date_to_iso` on the
    ``CCYYMMDD`` the wire carries.
    """
    return KeyType.NCPDP_CLAIM, _join(
        _component(pharmacy_npi, "pharmacy_npi"),
        _component(rx_number, "rx_number"),
        _component(fill_number, "fill_number"),
        _component(date_of_service, "date_of_service"),
    )


def medical_clm01(clm01: str) -> tuple[KeyType, str]:
    """``MEDICAL_CLM01`` — the provider-assigned claim key, echoed back as ``CLP01``.

    Stable across the whole life of a claim, including reprocessing.  This is the
    medical side's only durable identity.
    """
    return KeyType.MEDICAL_CLM01, _component(clm01, "clm01")


def payer_icn(clp07: str) -> tuple[KeyType, str]:
    """``PAYER_ICN`` — the payer's own internal control number (``CLP07``).

    Recorded because ``REF*F8`` on a replacement or void points at it, so it has to be
    resolvable.  It is emphatically **not** claim identity: a reprocessed claim gets a
    brand-new ICN while ``CLM01`` never changes, so a connector that keys on this forks
    one claim's history into unrelated pieces the first time it is reprocessed.
    """
    return KeyType.PAYER_ICN, _component(clp07, "clp07")


def trn02(trace_number: str) -> tuple[KeyType, str]:
    """``TRN02`` — the payer-assigned reassociation reference.

    The only business link between a remittance and a bank deposit, and it resolves to
    a *remittance*, never to a claim (Decision A21 — the bank line is two hops).

    Semantically the column is "the payer-assigned business reference from the CCD+
    addenda".  For a claim payment the issuer is the PBM or health plan and the value
    is the 835 trace number; for a manufacturer rebate the issuer is the manufacturer
    and the value is the batch's ``allocation_code``.  Same column, same ~20% addenda
    loss rate, different issuer — which is why rebate deposits use
    :func:`allocation_code` as their key type while riding the same bank column.
    """
    return KeyType.TRN02, _component(trace_number, "trn02")


def allocation_code(code: str) -> tuple[KeyType, str]:
    """``ALLOCATION_CODE`` — the manufacturer rebate batch reference.

    Resolves to a ``REBATE_BATCH``, which holds the dispense list.  The rebate side's
    exact analogue of ``TRN02``: two hops, and the same splitter fans the total back
    out across the dispenses (Decision C7).
    """
    return KeyType.ALLOCATION_CODE, _component(code, "allocation_code")


def natural_340b_pharmacy(
    pharmacy_npi: str, rx_number: str, ndc11: str, fill_date: str
) -> tuple[KeyType, str]:
    """``NATURAL_340B_PHARMACY`` — the only bridge from a TPA record to a PBM claim.

    The PBM's ``authorization_number`` never reaches the TPA; the two systems do not
    talk to each other.  So this natural key is all there is, and it breaks on TPA lag
    and on retroactive eligibility flips.

    Note it keys on the **NDC** where :func:`ncpdp_claim` keys on the fill number: the
    TPA's export has no fill number, because a rebate is about which drug was bought,
    not which refill it was.
    """
    return KeyType.NATURAL_340B_PHARMACY, _join(
        _component(pharmacy_npi, "pharmacy_npi"),
        _component(rx_number, "rx_number"),
        _component(ndc11, "ndc11"),
        _component(fill_date, "fill_date"),
    )


def natural_340b_medical(
    provider_npi: str, ndc11: str, service_date: str
) -> tuple[KeyType, str]:
    """``NATURAL_340B_MEDICAL`` — the medical 340B bridge (Decision C9 / 37).

    Deliberately weaker than its pharmacy sibling, and the weakness is the real state
    of the industry rather than a modelling shortcut.  A clinic-infused drug has no
    prescription, so there is no Rx number to key on — which means two administrations
    of the same drug at the same site on the same day are **genuinely
    indistinguishable**.

    The connector must treat a multi-hit on this key as ``AMBIGUOUS_KEY_MATCH`` and
    park it.  Picking one is a coin flip that attributes real money to the wrong
    episode, and it would look identical to a correct match in every report.
    """
    return KeyType.NATURAL_340B_MEDICAL, _join(
        _component(provider_npi, "provider_npi"),
        _component(ndc11, "ndc11"),
        _component(service_date, "service_date"),
    )


def pbm_auth(authorization_number: str) -> tuple[KeyType, str]:
    """``PBM_AUTH`` — the PBM-assigned authorization number.

    Links a pharmacy 835 claim line back to its adjudication (and a B2 reversal to the
    claim it cancels) without going through the composite key.  Present on the PBM's
    own records only: it never reaches the TPA and never reaches the bank.
    """
    return KeyType.PBM_AUTH, _component(authorization_number, "authorization_number")


# ═══ the four connector key types (requirement §4.7) ════════════════════════
#
# The eight above are Decision A24 and are about what a *claim* joins on.  These four are
# about what a *connector* joins on, and ``DOC2-002`` step 4 names every one of them:
# *"Business mapping: Crosswalk 340B IDs, NDC/HCPCS, claim/Rx/fill IDs, provider/site,
# payer/PBM, manufacturer, transaction and payment references."*
#
# They live here for the same reason the eight do.  ``beacon_id`` and ``payment_reference``
# spent wave 4 in ``connectors/vendors/beacon.py`` — see that module, which said at the time
# that here was where they belonged.  A second construction site is exactly how the write
# side and the read side come to disagree about a string, and that disagreement is silent.


def beacon_id(identifier: str) -> tuple[KeyType, str]:
    """``BEACON_ID`` — the identifier Beacon assigns on submission.  Requirement C5.

    A single-component key, so the canonical form *is* the component: there is no separator
    to place and nothing to order.  What still has to hold is the separator prohibition,
    which is why this goes through :func:`_component` like everything else rather than
    returning the string untouched.

    Beacon issues the ID when it accepts a submission and carries it on claim-level
    submission history afterwards (``BEACON-013``), which makes it the join from our episode
    to the manufacturer's rebate decision.  The ID's **format** is not known — ``BEACON-013``
    establishes that the identifier exists, not what it looks like — so nothing here assumes
    a prefix, a length or a character set.

    Exactly one record may publish this key.  Two publishers of one ``(key_type, key_value)``
    pair turn every lookup into a multi-hit, which the connector parks as
    ``AMBIGUOUS_KEY_MATCH`` — so a second publisher does not produce a wrong answer, it
    produces no answer, for every rebate at once.  That rule is enforced by the connector,
    not by this function; a key builder cannot see how many records call it.
    """
    return KeyType.BEACON_ID, _component(identifier, "beacon_id")


def covered_entity_340b(
    covered_entity_id: str, scoped_key_value: str
) -> tuple[KeyType, str]:
    """``COVERED_ENTITY_340B`` — another key scoped to the covered entity that owns it.  E1.

    Doc 2 lists 340B IDs first in its crosswalk list (``DOC2-002`` step 4), Verity's own
    mapping row opens with *"Map 340B ID, site / contract pharmacy, ..."* (``DOC2-010``), and
    Beacon grants partner permissions per 340B ID (``BEACON-012``).  The covered entity is
    therefore a key, not a column nobody populates.

    **This is a scoped key, and the scoping is the whole point.**  A *bare* covered-entity id
    is useless and actively dangerous as a crosswalk key: every episode of one covered entity
    would collide onto one string, every lookup would return hundreds of episodes, and
    ``_attach`` would turn that into ``AMBIGUOUS_KEY_MATCH`` for the entire entity — one
    defect that silently parks a whole health system.  So what is keyed is the covered entity
    *joined to an already-canonical key value*: ``covered_entity_id|<some other key value>``,
    in practice the string half of :func:`natural_340b_pharmacy` or
    :func:`natural_340b_medical`.

    **What this key does NOT currently do, corrected after review.**  An earlier version of
    this docstring claimed the scoping made requirement E1's acceptance true *by
    construction* — that a wrong-entity record would build a different string, resolve to
    nothing, and park as ``NO_KEY_MATCH``, with "no equality check to forget".  That was
    false, and it was the most misleading sentence in this module, because it described a
    guarantee a reader would then stop looking for.

    What actually happens: this key type is **published and never looked up**.
    ``_tpa_lookup_keys`` builds only ``NATURAL_340B_PHARMACY``, ``NATURAL_340B_MEDICAL`` or
    ``HCPCS``, so a wrong-entity record resolves *cleanly* on the bare natural key and is
    then caught by precisely the comparison the old text said did not exist —
    ``pipeline._contradicts_covered_entity`` — parking as ``COVERED_ENTITY_MISMATCH``.

    So E1's acceptance does hold, and there is a two-sided test for it.  It holds by an
    equality check that can be forgotten, on the pharmacy track only, and this key is
    currently a scoped identity written down for a lookup side that is not built yet.  The
    construction below is still the right one for that lookup when it arrives; the claim
    about what it guarantees today was not.

    **Why ``scoped_key_value`` does not go through :func:`_component`.**  It is itself a
    ``|``-joined composite, so ``_component`` would refuse it — that function's separator ban
    exists to stop *one component* smuggling in a separator and colliding two keys, and here
    the separators are the deliberate structure of a value that was already built by a
    builder in this module.  It is validated by its own explicit check instead: present and
    non-empty.  Doing this silently — catching the refusal, or stripping the separators —
    is the failure mode, which is why the two halves are validated by visibly different
    means and the test suite pins the asymmetry.

    Scoping does not weaken the inner key.  ``covered_entity_id`` is used exactly as the feed
    spelled it, like every other component (Decision A23): a 340B ID that drifts between the
    TPA export and our episode is a real defect and is meant to miss.
    """
    entity = _component(covered_entity_id, "covered_entity_id")
    # Deliberately *not* _component(): a canonical key value is a composite and contains the
    # separator by design.  Checked on its own terms so the exemption is explicit.
    if scoped_key_value is None:
        raise MalformedKeyComponentError(
            "key component 'scoped_key_value' is None; a covered-entity key with nothing "
            "to scope is a bare entity id, which collides every episode of that entity "
            "onto one string. Park the record instead."
        )
    scoped = str(scoped_key_value)
    if scoped == "":
        raise MalformedKeyComponentError(
            "key component 'scoped_key_value' is empty; see above — an unscoped "
            "covered-entity key is not a weaker key, it is a collision"
        )
    return KeyType.COVERED_ENTITY_340B, _join(entity, scoped)


def hcpcs_medical(
    hcpcs: str, provider_npi: str, service_date: str
) -> tuple[KeyType, str]:
    """``HCPCS`` — the medical 340B bridge for a record that carries no NDC.  E2.

    :func:`natural_340b_medical` with the J-code standing where the ``ndc11`` stands.  Note
    the argument order leads with ``hcpcs`` — the thing that makes this key different — while
    the *component* order matches its sibling exactly: ``provider_npi|hcpcs|service_date``.
    The two orders differ on purpose and the test suite pins the rendered string, because
    positional arguments that quietly transpose would build a plausible key that resolves to
    nothing.

    **Why it exists.**  ``natural_340b_medical`` needs an ``ndc11``, and a medical-benefit
    drug is billed by HCPCS J-code.  ``DOC2-002`` step 4 reads "NDC/HCPCS", not "NDC";
    Beacon's medical template matches on a combination that includes both NDC and HCPCS
    (``BEACON-008`` — a search-index summary, not a verbatim field list, so nothing here
    assumes which of the two Beacon requires).  In our own feeds the gap is concrete: the
    835 service line writes ``svc01_composite`` as ``"HC:J9035:JW"`` and carries no NDC at
    all, and the CPT administration line that bills the act of infusing legitimately has no
    NDC because no drug was dispensed on it.  A J-code-only record can build no key today, so
    it parks — not because the data is bad but because the model is short a key type.

    **Same weakness as its sibling, and the same stance on it.**  A clinic-infused drug has
    no prescription, so there is no Rx number to key on, and two administrations of the same
    drug at the same site on the same day are genuinely indistinguishable.  A J-code is if
    anything *coarser* than an NDC — one J-code covers every manufacturer's version of the
    drug — so this key is weaker still.  A multi-hit must be parked as
    ``AMBIGUOUS_KEY_MATCH``.  Picking one is a coin flip that attributes real money to the
    wrong episode, and it would look identical to a correct match in every report.

    ``service_date`` is ``YYYY-MM-DD``; use :func:`wire_date_to_iso` on the ``CCYYMMDD`` the
    wire carries.  The J-code is used exactly as the feed spelled it — no ``upper()``, no
    modifier stripping (Decision A23).  ``"HC:J9035:JW"`` is a composite the *adapter* takes
    apart; what reaches here is the J-code alone.
    """
    return KeyType.HCPCS, _join(
        _component(provider_npi, "provider_npi"),
        _component(hcpcs, "hcpcs"),
        _component(service_date, "service_date"),
    )


def payment_reference(reference: str) -> tuple[KeyType, str]:
    """``PAYMENT_REFERENCE`` — the rebate-status source's reference for a settlement.  E3.

    Named in its own right by ``DOC2-002`` step 4 ("... manufacturer, transaction and payment
    references"), and it resolves to a ``REBATE_BATCH`` — a remittance-side target, never to
    a claim.  Two hops, exactly like :func:`allocation_code` and :func:`trn02`.

    **What distinguishes it from those two.**  :func:`trn02`'s docstring sets out the "same
    column, different issuer" idea: the bank column holds *the reference the payer or
    manufacturer assigned for reassociation*, spelled ``TRN02`` when a PBM or health plan
    issued it and ``ALLOCATION_CODE`` when a manufacturer issued it.  This is a third fact
    about the same settlement: the reference **the rebate-status source reports** for it.
    Beacon is authoritative for rebate status and Beacon-side reconciliation data
    (``DOC2-004``), so its reference is the one that arrives with the status, and it is not
    ours to reconcile against the batch reference by assuming they are the same string.

    That is also why a Beacon batch publishes this key and **not** ``ALLOCATION_CODE``, even
    where the two values happen to agree: the 340B feed's own ``REBATE_PAYMENT_BATCH``
    already publishes the allocation code, and two records publishing one
    ``(key_type, key_value)`` pair make every bank deposit that looks it up a multi-hit the
    crosswalk parks as ``AMBIGUOUS_KEY_MATCH``.
    """
    return KeyType.PAYMENT_REFERENCE, _component(reference, "payment_reference")


# ═══ the one structural parse the crosswalk owns ════════════════════════════


def parse_clp01_pharmacy(clp01: str) -> tuple[str, str]:
    """Split a pharmacy 835's ``CLP01`` into ``(rx_number, fill_number)``.

    ``"7845102FILL00"`` -> ``("7845102", "00")``.  This is NCPDP's documented
    convention for pharmacy 835s, and splitting it is a real crosswalk step rather
    than a join: the value is not a claim identifier, it is two identifiers
    concatenated.

    The Rx number is returned **exactly as it appeared**, leading zeros and all
    (Decision A23).  If the remittance says ``"07845102FILL00"`` where the claim event
    said ``"7845102"``, the resulting key genuinely does not match, the record parks,
    and that is the D-6 exception doing its job.

    Raises:
        MalformedKeyComponentError: if the marker is absent or either side is empty.
            An unparseable ``CLP01`` resolves to nothing, so the record parks; it must
            not fall through to a partial key.
    """
    text = _component(clp01, "clp01")
    marker_at = text.find(FILL_MARKER)
    if marker_at == -1:
        raise MalformedKeyComponentError(
            f"pharmacy CLP01 {text!r} carries no {FILL_MARKER!r} marker; expected "
            f"'<rx>{FILL_MARKER}<fill>' per NCPDP's pharmacy-835 convention"
        )
    rx_number = text[:marker_at]
    fill_number = text[marker_at + len(FILL_MARKER) :]
    if rx_number == "" or fill_number == "":
        raise MalformedKeyComponentError(
            f"pharmacy CLP01 {text!r} splits into an empty component: "
            f"rx={rx_number!r} fill={fill_number!r}"
        )
    return rx_number, fill_number


def format_clp01_pharmacy(rx_number: str, fill_number: str) -> str:
    """Inverse of :func:`parse_clp01_pharmacy`, for the generator's write side.

    Exported so the generator and the connector cannot disagree about the convention.
    A test round-trips the two.
    """
    return (
        f"{_component(rx_number, 'rx_number')}{FILL_MARKER}"
        f"{_component(fill_number, 'fill_number')}"
    )


# ═══ wire date formats ══════════════════════════════════════════════════════


def wire_date_to_iso(wire: str) -> str:
    """``"20260302"`` -> ``"2026-03-02"``.

    Every date on every feed is ``CCYYMMDD``; the database stores ``YYYY-MM-DD`` so
    that lexicographic order is chronological order.  Lossless and bijective, which is
    what makes this parsing rather than normalisation (see the module docstring).
    """
    text = _component(wire, "wire_date")
    if len(text) != 8 or not text.isdigit():
        raise MalformedKeyComponentError(
            f"expected a CCYYMMDD wire date, got {text!r}"
        )
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def iso_date_to_wire(iso: str) -> str:
    """``"2026-03-02"`` -> ``"20260302"``, for the generator's write side."""
    text = _component(iso, "iso_date")
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise MalformedKeyComponentError(f"expected a YYYY-MM-DD date, got {text!r}")
    return text[:4] + text[5:7] + text[8:]
