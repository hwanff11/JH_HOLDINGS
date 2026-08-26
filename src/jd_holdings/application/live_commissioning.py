from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .account_preflight import RealAccountPreflight
from .database import SQLiteRepository
from .operational_safety import OPERATOR_BUY_HALT_KEY


@dataclass(frozen=True)
class LiveCommissioningResult:
    issues: tuple[str, ...]
    buying_power: Decimal | None

    @property
    def safe(self) -> bool:
        return not self.issues


class LiveCommissioningPreflight:
    """Fail-closed checks for a fresh, separate live ledger before first activation.

    This does not enable live trading. It proves only that the prospective live DB
    is clean, the real account has no unmanaged JDSS symbols/open orders, and enough
    USD buying power is visible. The actual live unlock remains a separate change.
    """

    def __init__(self, repository: SQLiteRepository, account_client: Any) -> None:
        self.repository = repository
        self.account_client = account_client

    def run(self) -> LiveCommissioningResult:
        issues = list(self._inspect_local_ledger())
        account = RealAccountPreflight(self.repository, self.account_client).run()
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
                    issues.append(
                        f"LIVE_BUYING_POWER_BELOW_CAPITAL:{normalized}<{required}"
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
                context={"database": self.repository.db_path.name},
            )
        return LiveCommissioningResult(unique, buying_power)

    def arm_buy_halt(self) -> None:
        """Force the prospective live runtime to boot with risk-increasing BUY blocked."""
        self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
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
