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
