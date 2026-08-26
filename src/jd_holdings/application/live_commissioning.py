from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .account_preflight import RealAccountPreflight
from .database import SQLiteRepository
from .external_baseline import capture_external_baseline
from .operational_safety import (
    OPERATOR_BUY_HALT_AT_KEY,
    OPERATOR_BUY_HALT_KEY,
)
from .reconciliation import ReconciliationService

LIVE_COMMISSIONED_KEY = "live_commissioned"
LIVE_COMMISSIONED_AT_KEY = "live_commissioned_at"


@dataclass(frozen=True)
class LiveCommissioningResult:
    issues: tuple[str, ...]
    buying_power: Decimal | None
    external_baseline_symbols: tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class LiveReleaseGateResult:
    issues: tuple[str, ...]

    @property
    def safe(self) -> bool:
        return not self.issues


class LiveCommissioningPreflight:
    """Fail-closed checks for a fresh, separate live ledger before first activation.

    This never places an order. It proves that the prospective live DB is fresh, the
    configured real account is readable, managed-symbol open orders are absent, and
    enough USD buying power is visible. Pre-existing whole-share QQQ/TQQQ/SOXL
    holdings are captured once as an immutable *external baseline*: they remain
    outside JDSS ownership, HWM accounting and strategy SELL sizing. A successful
    commissioning arms the persistent BUY halt; the operator must later release that
    halt explicitly through Telegram.
    """

    def __init__(self, repository: SQLiteRepository, account_client: Any) -> None:
        self.repository = repository
        self.account_client = account_client

    def run(self) -> LiveCommissioningResult:
        issues = list(self._inspect_local_ledger())
        account = RealAccountPreflight(
            self.repository,
            self.account_client,
            allow_managed_holdings_as_external=True,
        ).run()
        issues.extend(account.issues)

        buying_power = account.buying_power
        if buying_power is not None:
            try:
                normalized = Decimal(str(buying_power))
            except (InvalidOperation, ValueError):
                issues.append("LIVE_INVALID_BUYING_POWER")
            else:
                required = Decimal(str(self.repository.config.total_strategy_capital))
                if normalized < required:
                    # Do not put a private account balance into public Actions logs.
                    issues.append("LIVE_BUYING_POWER_BELOW_CAPITAL")

        baseline_symbols: tuple[str, ...] = ()
        if not issues:
            try:
                baseline_symbols = capture_external_baseline(
                    self.repository,
                    dict(account.managed_holdings),
                )
            except Exception as exc:
                issues.append(
                    f"LIVE_EXTERNAL_BASELINE_CAPTURE_FAILED:{type(exc).__name__}"
                )

        unique = tuple(dict.fromkeys(issues))
        if unique:
            self.repository.log_event(
                "SAFE_MODE",
                "LIVE_COMMISSIONING_PREFLIGHT_FAILED",
                ";".join(unique),
                context={"database": self.repository.db_path.name},
            )
        else:
            self.repository.log_event(
                "INFO",
                "LIVE_COMMISSIONING_PREFLIGHT_PASSED",
                "실계좌 최초 기동 사전점검을 통과했습니다",
                context={
                    "database": self.repository.db_path.name,
                    "external_baseline_symbols": list(baseline_symbols),
                    "private_quantities_redacted": True,
                },
            )
        return LiveCommissioningResult(unique, buying_power, baseline_symbols)

    def arm_buy_halt(self) -> None:
        """Mark this fresh ledger as commissioned and start it with BUY blocked."""
        now = datetime.now(UTC).isoformat()
        self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
        self.repository.set_system_value(OPERATOR_BUY_HALT_AT_KEY, now)
        self.repository.set_system_value(LIVE_COMMISSIONED_KEY, "1")
        self.repository.set_system_value(LIVE_COMMISSIONED_AT_KEY, now)
        self.repository.log_event(
            "SAFE_MODE",
            "LIVE_COMMISSIONING_BUY_HALT_ARMED",
            "실거래 최초 기동 전 운영자 BUY 차단을 설정했습니다",
        )

    def _inspect_local_ledger(self) -> tuple[str, ...]:
        issues: list[str] = []
        count_queries = (
            ("orders", "SELECT COUNT(*) FROM orders"),
            ("signals", "SELECT COUNT(*) FROM signals"),
            ("approvals", "SELECT COUNT(*) FROM approvals"),
            ("trades", "SELECT COUNT(*) FROM trades"),
        )
        with self.repository.transaction() as connection:
            for table, query in count_queries:
                count = int(connection.execute(query).fetchone()[0])
                if count:
                    issues.append(f"LIVE_DB_NOT_FRESH:{table}:{count}")

            position_rows = connection.execute(
                "SELECT symbol, state, qty FROM positions WHERE qty <> 0 OR state <> 'EMPTY'"
            ).fetchall()
            if position_rows:
                issues.append(f"LIVE_DB_ACTIVE_POSITIONS:{len(position_rows)}")

            core_rows = connection.execute(
                "SELECT symbol, qty FROM core_positions WHERE qty <> 0"
            ).fetchall()
            if core_rows:
                issues.append(f"LIVE_DB_ACTIVE_CORE_POSITIONS:{len(core_rows)}")

        return tuple(issues)


def arm_live_startup_buy_halt(repository: SQLiteRepository) -> None:
    """Fail closed on every live process start/restart.

    A process restart must never inherit a previously released BUY state. This helper
    is called before constructing the live broker so a newly started process always
    requires a fresh Telegram resume before any risk-increasing order can cross the
    OrderManager boundary.
    """
    if repository.get_system_value(LIVE_COMMISSIONED_KEY) != "1":
        raise RuntimeError("live 전용 원장이 commissioning 완료 상태가 아닙니다")
    now = datetime.now(UTC).isoformat()
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value(OPERATOR_BUY_HALT_AT_KEY, now)
    repository.log_event(
        "SAFE_MODE",
        "LIVE_STARTUP_BUY_HALT_ARMED",
        "live 서비스 시작/재시작으로 신규 BUY를 자동 차단했습니다",
    )


class LiveReleaseGate:
    """Order-write-free gate for a routine live release switch or restart.

    An established live ledger may legitimately contain JDSS-managed positions plus
    the immutable external baseline captured at first commissioning. A release is
    switchable only while BUY is halted, there are no outstanding orders/active
    approvals, and broker state reconciles with both ledgers. This class never submits
    or cancels an order.
    """

    def __init__(self, repository: SQLiteRepository, broker: Any) -> None:
        self.repository = repository
        self.broker = broker

    def run(self) -> LiveReleaseGateResult:
        issues: list[str] = []

        if self.repository.get_system_value(LIVE_COMMISSIONED_KEY) != "1":
            issues.append("LIVE_RELEASE_NOT_COMMISSIONED")
        if self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) != "1":
            issues.append("LIVE_RELEASE_BUY_HALT_NOT_ARMED")

        local_open_orders = self.repository.open_orders()
        if local_open_orders:
            issues.append(f"LIVE_RELEASE_LOCAL_OPEN_ORDERS:{len(local_open_orders)}")

        with self.repository.transaction() as connection:
            active_approvals = int(
                connection.execute(
                    "SELECT COUNT(*) FROM approvals WHERE status = 'ACTIVE'"
                ).fetchone()[0]
            )
        if active_approvals:
            issues.append(f"LIVE_RELEASE_ACTIVE_APPROVALS:{active_approvals}")

        managed_symbols = list(ALLOCATION_SYMBOLS)
        if self.repository.config.idle_cash.enabled:
            managed_symbols.append(self.repository.config.idle_cash.symbol)
        for symbol in dict.fromkeys(managed_symbols):
            try:
                broker_open_orders = self.broker.list_orders(
                    status="OPEN", symbol=symbol, limit=100
                )
            except Exception as exc:
                issues.append(
                    f"LIVE_RELEASE_BROKER_OPEN_ORDER_LOOKUP_FAILED:{symbol}:{type(exc).__name__}"
                )
            else:
                if broker_open_orders:
                    issues.append(
                        f"LIVE_RELEASE_BROKER_OPEN_ORDERS:{symbol}:{len(broker_open_orders)}"
                    )

        try:
            mismatches = ReconciliationService(
                self.repository.config,
                self.repository,
                self.broker,
            ).run()
        except Exception as exc:
            issues.append(f"LIVE_RELEASE_RECONCILIATION_ERROR:{type(exc).__name__}")
        else:
            for symbol, symbol_issues in sorted(mismatches.items()):
                issues.append(
                    f"LIVE_RELEASE_RECONCILIATION_FAILED:{symbol}:{'|'.join(symbol_issues)}"
                )

        unique = tuple(dict.fromkeys(issues))
        if unique:
            self.repository.log_event(
                "SAFE_MODE",
                "LIVE_RELEASE_GATE_FAILED",
                ";".join(unique),
                context={"database": self.repository.db_path.name},
            )
        else:
            self.repository.log_event(
                "INFO",
                "LIVE_RELEASE_GATE_PASSED",
                "BUY HALT·미체결 0·정합성 조건을 확인했습니다",
                context={"database": self.repository.db_path.name},
            )
        return LiveReleaseGateResult(unique)
