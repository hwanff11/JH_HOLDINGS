from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .database import SQLiteRepository

EXTERNAL_BASELINE_FROZEN_KEY = "live_external_baseline_frozen"
EXTERNAL_BASELINE_CAPTURED_AT_KEY = "live_external_baseline_captured_at"
EXTERNAL_BASELINE_QTY_PREFIX = "live_external_baseline_qty:"


def _quantity_key(symbol: str) -> str:
    return f"{EXTERNAL_BASELINE_QTY_PREFIX}{symbol.upper()}"


def external_baseline_quantity(repository: SQLiteRepository, symbol: str) -> int:
    normalized = symbol.upper()
    if normalized not in ALLOCATION_SYMBOLS:
        return 0
    raw = repository.get_system_value(_quantity_key(normalized))
    if raw in (None, ""):
        return 0
    try:
        quantity = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"외부 기준보유 수량이 올바르지 않습니다: {normalized}") from exc
    if quantity < 0:
        raise RuntimeError(f"외부 기준보유 수량이 음수입니다: {normalized}")
    return quantity


def external_baseline(repository: SQLiteRepository) -> dict[str, int]:
    return {
        symbol: external_baseline_quantity(repository, symbol)
        for symbol in ALLOCATION_SYMBOLS
    }


def external_baseline_symbols(repository: SQLiteRepository) -> tuple[str, ...]:
    return tuple(
        symbol for symbol, quantity in external_baseline(repository).items() if quantity > 0
    )


def capture_external_baseline(
    repository: SQLiteRepository,
    quantities: Mapping[str, int],
) -> tuple[str, ...]:
    """Persist a one-time baseline for pre-existing allocation-symbol holdings.

    These shares are outside JDSS ownership. Reconciliation subtracts this frozen
    baseline from the broker total before comparing the JDSS ledger. The baseline is
    intentionally immutable after first capture; a later personal-account change must
    fail reconciliation instead of silently rebasing JDSS ownership.
    """
    normalized = {symbol: 0 for symbol in ALLOCATION_SYMBOLS}
    for raw_symbol, raw_quantity in quantities.items():
        symbol = str(raw_symbol).upper()
        if symbol not in ALLOCATION_SYMBOLS:
            raise ValueError(f"외부 기준보유 대상 종목이 아닙니다: {symbol}")
        if isinstance(raw_quantity, bool):
            raise ValueError(f"외부 기준보유 수량이 올바르지 않습니다: {symbol}")
        quantity = int(raw_quantity)
        if quantity != raw_quantity or quantity < 0:
            raise ValueError(f"외부 기준보유 수량이 올바르지 않습니다: {symbol}")
        normalized[symbol] = quantity

    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        frozen_row = connection.execute(
            "SELECT value FROM system_state WHERE key = ?",
            (EXTERNAL_BASELINE_FROZEN_KEY,),
        ).fetchone()
        frozen = frozen_row is not None and str(frozen_row["value"]) == "1"
        if frozen:
            existing: dict[str, int] = {}
            for symbol in ALLOCATION_SYMBOLS:
                row = connection.execute(
                    "SELECT value FROM system_state WHERE key = ?",
                    (_quantity_key(symbol),),
                ).fetchone()
                existing[symbol] = int(row["value"]) if row is not None else 0
            if existing != normalized:
                raise RuntimeError("외부 기준보유는 commissioning 후 자동 변경할 수 없습니다")
            return tuple(symbol for symbol, quantity in existing.items() if quantity > 0)

        for symbol, quantity in normalized.items():
            connection.execute(
                """
                INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (_quantity_key(symbol), str(quantity), now),
            )
        for key, value in (
            (EXTERNAL_BASELINE_FROZEN_KEY, "1"),
            (EXTERNAL_BASELINE_CAPTURED_AT_KEY, now),
        ):
            connection.execute(
                """
                INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    symbols = tuple(
        symbol for symbol, quantity in normalized.items() if quantity > 0
    )
    repository.log_event(
        "INFO",
        "LIVE_EXTERNAL_BASELINE_CAPTURED",
        "실거래 시작 전 기존 allocation 종목 보유분을 JDSS 외부 기준보유로 봉인했습니다",
        context={"symbols": list(symbols), "private_quantities_redacted": True},
    )
    return symbols
