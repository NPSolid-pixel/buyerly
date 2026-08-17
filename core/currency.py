import math
import re
from typing import Any, Optional


UNKNOWN_CURRENCY = "UNKNOWN"

# Meta returns management budgets in the account currency's minor unit. These
# currencies from Meta's supported set do not have fractional minor units.
ZERO_DECIMAL_CURRENCIES = frozenset({"CLP", "ISK", "JPY", "KRW", "PYG", "VND"})


def normalize_currency(value: Any) -> str:
    """Return a safe ISO-like currency code without guessing USD."""

    code = str(value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else UNKNOWN_CURRENCY


def currency_minor_unit_factor(currency: Any) -> int:
    """Convert between Meta integer budget units and account currency units."""

    return 1 if normalize_currency(currency) in ZERO_DECIMAL_CURRENCIES else 100


def from_meta_budget_units(value: Any, currency: Any) -> float:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return amount / currency_minor_unit_factor(currency)


def to_meta_budget_units(value: Any, currency: Any) -> int:
    code = normalize_currency(currency)
    if code == UNKNOWN_CURRENCY:
        raise ValueError("Account currency is unknown; budget mutation is blocked.")
    try:
        amount = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Budget must be a finite number.") from error
    if not math.isfinite(amount):
        raise ValueError("Budget must be a finite number.")
    units = int(round(amount * currency_minor_unit_factor(code)))
    if units < 1:
        raise ValueError("Budget must be at least one minor currency unit.")
    return units


def format_money(value: Optional[float], currency: Any) -> str:
    """Unambiguous server-side display used by Telegram and logs."""

    if value is None:
        return "—"
    code = normalize_currency(currency)
    digits = 0 if code in ZERO_DECIMAL_CURRENCIES else 2
    suffix = code if code != UNKNOWN_CURRENCY else "валюта не определена"
    return f"{float(value):.{digits}f} {suffix}"
