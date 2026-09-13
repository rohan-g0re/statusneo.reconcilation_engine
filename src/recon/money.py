"""Money primitives.  Integer cents, everywhere, with no exceptions.

This module is the only place in the codebase that converts between the decimal
text that appears on a wire format and the integer cents that everything above the
adapter boundary uses.

Why integer cents: this is a reconciliation engine and variance is the product.  A
float round-trip through ``47218.40`` leaves a residue indistinguishable from a real
cash gap, and the engine would report it as one.  ``to_cents`` therefore refuses a
bare ``float`` outright — that refusal is what keeps the rule enforceable rather than
advisory.

Why one rounding rule: if the generator rounds a contracted rate one way and the
engine rounds it another, every claim carries a one-cent variance and nothing can
tell rounding from underpayment.  ``round_half_up`` is that single rule, and both
sides import it from here.

Reading JSON money:  ``json.loads(line, parse_float=Decimal)`` then
``parse_json_money``.  Never ``float(x) * 100``.
Writing JSON money:  ``format_amount(cents)``.  Never ``str(float(...))``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Union

__all__ = [
    "to_cents",
    "from_cents",
    "format_amount",
    "round_half_up",
    "apply_bps",
    "parse_json_money",
]

MoneyInput = Union[str, Decimal, int]

_CENTS = Decimal(100)


def to_cents(value: MoneyInput) -> int:
    """Convert a currency amount to integer cents.

    The input is a *currency* amount, not a cent count: ``to_cents("2840.00")``,
    ``to_cents(Decimal("2840"))`` and ``to_cents(2840)`` all return ``284000``.

    Raises:
        TypeError: on ``float`` (and on ``bool``, which is an ``int`` subclass).
            This is the guard that makes the integer-cents rule structural.
        ValueError: on a value carrying more than two decimal places.  Three-decimal
            money is a feed defect, not a rounding opportunity.
    """
    if isinstance(value, bool):
        raise TypeError("to_cents() rejects bool; pass a str, Decimal or int amount")
    if isinstance(value, float):
        raise TypeError(
            "to_cents() rejects float: binary floats cannot represent cents exactly "
            "and a round-trip residue is indistinguishable from a real variance. "
            "Parse with json.loads(..., parse_float=Decimal) and pass the Decimal."
        )
    if isinstance(value, int):
        return value * 100
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal money literal: {value!r}") from exc
    else:
        raise TypeError(f"to_cents() accepts str | Decimal | int, got {type(value).__name__}")

    if not dec.is_finite():
        raise ValueError(f"money must be finite, got {value!r}")

    scaled = dec * _CENTS
    whole = scaled.to_integral_value()
    if scaled != whole:
        raise ValueError(
            f"money carries sub-cent precision: {value!r}. "
            "Amounts must have at most two decimal places."
        )
    return int(whole)


def from_cents(cents: int) -> Decimal:
    """Exact ``Decimal`` with two decimal places."""
    _require_int(cents, "cents")
    return (Decimal(cents) / _CENTS).quantize(Decimal("0.01"))


def format_amount(cents: int) -> str:
    """Render cents as fixed two-decimal text: ``284000 -> "2840.00"``.

    Every generator renders money through this function.  Negative amounts keep the
    sign on the left (``-4230 -> "-42.30"``), which is what both the 835 PLB segments
    and the bank CSV expect.
    """
    _require_int(cents, "cents")
    sign = "-" if cents < 0 else ""
    magnitude = abs(cents)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}"


def round_half_up(numerator: int, denominator: int) -> int:
    """Divide two integers, rounding halves away from zero.

    Integer arithmetic only — no float, no ``Decimal``, no rounding-mode context to
    get wrong.  Half-away-from-zero, symmetric about zero, so that
    ``round_half_up(5, 10) == 1`` and ``round_half_up(-5, 10) == -1``.  This is
    deliberately *not* banker's rounding: half-to-even would make a fee schedule's
    cent depend on the parity of the cent below it, which no payer does.

    Raises:
        ZeroDivisionError: if ``denominator`` is zero.
        TypeError: if either argument is not an ``int``.
    """
    _require_int(numerator, "numerator")
    _require_int(denominator, "denominator")
    if denominator == 0:
        raise ZeroDivisionError("round_half_up() denominator must be non-zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    if numerator >= 0:
        return (2 * numerator + denominator) // (2 * denominator)
    return -((-2 * numerator + denominator) // (2 * denominator))


def apply_bps(cents: int, bps: int) -> int:
    """Apply a rate in basis points: ``apply_bps(284000, 1200)`` is 12% of 2840.00.

    Every contracted rate, coinsurance rate and percentage discount goes through
    here, so that all of them share one rounding rule.
    """
    _require_int(cents, "cents")
    _require_int(bps, "bps")
    return round_half_up(cents * bps, 10_000)


def parse_json_money(obj: MoneyInput) -> int:
    """Convert a value produced by ``json.loads(..., parse_float=Decimal)`` to cents.

    Identical to :func:`to_cents`; it exists as a named adapter-boundary call so a
    reader can see where wire money enters the system.
    """
    return to_cents(obj)


def _require_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int (integer cents), got {type(value).__name__}")
