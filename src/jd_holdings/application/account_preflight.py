from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .database import SQLiteRepository

REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY = "v322_real_account_preflight_safe_mode"
REAL_ACCOUNT_PREFLIGHT_AT_KEY = "last_real_account_preflight_at"


@dataclass(frozen=True)
class AccountPreflightResult:
    checked_at: datetime
    issues: tuple[str, ...]
    buying_power: Decimal | None
    managed_holdings: tuple[tuple[str, int], ...] = ()

    @property
    def safe(self) -> bool:
        return not self.issues



def _lookup_failure(step: str, exc: Exception) -> str:
    """Return a public-log-safe error fingerprint without account/balance data."""
    parts = [f"REAL_ACCOUNT_{step}_FAILED", type(exc).__name__]
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    if status_code is not None:
        parts.append(f"HTTP{status_code}")
    if code:
        normalized = "".join(ch for ch in str(code) if ch.isalnum() or ch in "-_./")
        if normalized:
            parts.append(normalized[:80])
    return ":".join(parts)


class RealAccountPreflight:
    """Read-only guard for the real Toss account boundary.

    By default any pre-existing QQQ/TQQQ/SOXL holding is rejected. The only exception
    is the explicit first-live commissioning path, which may request those integer
    quantities as an *external baseline*. They remain outside JDSS ownership and are
    still subject to duplicate/fractional/invalid/open-order fail-closed checks.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        account_client: Any,
        *,
        allow_managed_holdings_as_external: bool = False,
    ) -> None:
        self.repository = repository
        self.account_client = account_client
        self.allow_managed_holdings_as_external = allow_managed_holdings_as_external

    def run(self) -> AccountPreflightResult:
        checked_at = datetime.now(UTC)
        issues: list[str] = []
        accounts: list[dict[str, Any]] | None = None
        holdings: list[dict[str, Any]] | None = None
        open_orders: list[dict[str, Any]] | None = None
        buying_power: Decimal | None = None
        managed_holdings: tuple[tuple[str, int], ...] = ()

        try:
            accounts = self.account_client.get_accounts()
        except Exception as exc:
            issues.append(_lookup_failure("ACCOUNTS", exc))
        else:
            configured = str(getattr(self.account_client, "account_seq", "")).strip()
            available = {
                str(item.get("accountSeq", "")).strip()
                for item in accounts
                if item.get("accountSeq") is not None
            }
            if not configured or configured not in available:
                issues.append("REAL_ACCOUNT_CONFIGURED_SEQ_NOT_FOUND")

        try:
            holdings = self.account_client.get_holdings()
        except Exception as exc:
            issues.append(_lookup_failure("HOLDINGS", exc))

        try:
            open_orders = self.account_client.list_orders(status="OPEN")
        except Exception as exc:
            issues.append(_lookup_failure("OPEN_ORDERS", exc))

        try:
            buying_power = self.account_client.get_buying_power("USD")
        except Exception as exc:
            issues.append(_lookup_failure("BUYING_POWER", exc))

        if holdings is not None and open_orders is not None and buying_power is not None:
            inspect_issues, managed_holdings = self._inspect(
                holdings,
                open_orders,
                buying_power,
                allow_managed_holdings_as_external=self.allow_managed_holdings_as_external,
            )
            issues.extend(inspect_issues)

        unique = tuple(dict.fromkeys(issues))
        self.repository.set_system_value(
            REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY,
            "0" if not unique else "1",
        )
        self.repository.set_system_value(
            REAL_ACCOUNT_PREFLIGHT_AT_KEY,
            checked_at.isoformat(),
        )
        if unique:
            self.repository.log_event(
                "SAFE_MODE",
                "REAL_ACCOUNT_PREFLIGHT_FAILED",
                ";".join(unique),
                context={"check": "read_only_account_boundary"},
            )
        return AccountPreflightResult(
            checked_at,
            unique,
            buying_power,
            managed_holdings,
        )

    @staticmethod
    def _inspect(
        holdings: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        buying_power: Decimal,
        *,
        allow_managed_holdings_as_external: bool = False,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, int], ...]]:
        issues: list[str] = []
        managed = set(ALLOCATION_SYMBOLS)
        seen_holdings: set[str] = set()
        managed_quantities: dict[str, int] = {}
        managed_rows: list[str] = []

        for item in holdings:
            symbol = str(item.get("symbol") or item.get("stockCode") or "").upper()
            if symbol not in managed:
                continue
            seen_holdings.add(symbol)
            managed_rows.append(symbol)
            raw_quantity = item.get("quantity", item.get("holdingQuantity", "0"))
            try:
                quantity = Decimal(str(raw_quantity))
            except (InvalidOperation, ValueError):
                issues.append(f"REAL_ACCOUNT_INVALID_QUANTITY:{symbol}")
                continue
            if not quantity.is_finite() or quantity < 0:
                issues.append(f"REAL_ACCOUNT_INVALID_QUANTITY:{symbol}")
                continue
            if quantity != quantity.to_integral_value():
                issues.append(f"REAL_ACCOUNT_FRACTIONAL_HOLDING:{symbol}")
                continue

            integer_quantity = int(quantity)
            managed_quantities[symbol] = integer_quantity
            if integer_quantity > 0 and not allow_managed_holdings_as_external:
                issues.append(f"REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:{symbol}")

        for item in open_orders:
            symbol = str(item.get("symbol") or item.get("stockCode") or "").upper()
            if symbol in managed:
                issues.append(f"REAL_ACCOUNT_OPEN_ORDER_PRESENT:{symbol}")

        try:
            normalized_buying_power = Decimal(str(buying_power))
        except (InvalidOperation, ValueError):
            issues.append("REAL_ACCOUNT_INVALID_BUYING_POWER")
        else:
            if not normalized_buying_power.is_finite() or normalized_buying_power < 0:
                issues.append("REAL_ACCOUNT_INVALID_BUYING_POWER")

        # Multiple API rows for one managed ticker make the account response ambiguous.
        for symbol in seen_holdings:
            if managed_rows.count(symbol) > 1:
                issues.append(f"REAL_ACCOUNT_DUPLICATE_HOLDING_ROWS:{symbol}")

        normalized_holdings = tuple(
            sorted(
                (symbol, quantity)
                for symbol, quantity in managed_quantities.items()
                if quantity > 0
            )
        )
        return tuple(dict.fromkeys(issues)), normalized_holdings
