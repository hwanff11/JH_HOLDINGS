from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from jd_holdings.application.operational_safety import (
    BUY_EXECUTION_LOCK,
    OPERATOR_BUY_HALT_AT_KEY,
    OPERATOR_BUY_HALT_KEY,
)

from . import AUTO_VERSION
from .live_service import ProductionJHAutoService
from .service import (
    AUTO_BASE_CAPITAL_KEY,
    AUTO_EFFECTIVE_PRINCIPAL_KEY,
    AUTO_ENABLED_KEY,
    AUTO_QUARANTINE_KEY,
    AUTO_RATIO_KEY,
    AUTO_RAMP_BASE_KEY,
    AUTO_STATE_KEY,
    AUTO_TARGET_PRINCIPAL_KEY,
    AUTO_VERSION_KEY,
    MAX_BASE_CAPITAL,
    MAX_RATIO,
    MIN_BASE_CAPITAL,
    MIN_RATIO,
)

MAX_AUTO_BUY_ATTEMPTS_PER_DAY = 5
MAX_AUTO_SIGNAL_ATTEMPTS_PER_DAY = 3


class HardenedProductionJHAutoService(ProductionJHAutoService):
    """Pre-live hardening without changing JDSS 3.2.2 strategy mathematics.

    Capital-setting writes are serialized against the final BUY boundary. A live
    configuration change temporarily closes BUY and re-enters quarantine, so a
    process crash or partial SQLite update can never continue trading from a mixed
    old/new capital state. The next clean safety cycle must prove consistency again.
    """

    def _validate_capital_contract(self) -> None:
        settings = self.settings()
        if self.repository.get_system_value(AUTO_ENABLED_KEY) != "1":
            raise RuntimeError("JH AUTO 활성상태가 누락·손상되었습니다")
        if self.repository.get_system_value(AUTO_VERSION_KEY) != AUTO_VERSION:
            raise RuntimeError("JH AUTO 버전 상태가 현재 코드와 일치하지 않습니다")
        if settings.base_capital is None or not MIN_BASE_CAPITAL <= settings.base_capital <= MAX_BASE_CAPITAL:
            raise RuntimeError("JH AUTO 총 투자금액 상태가 유효하지 않습니다")
        if settings.ratio is None or not MIN_RATIO <= settings.ratio <= MAX_RATIO:
            raise RuntimeError("JH AUTO 자동운용비율 상태가 유효하지 않습니다")
        expected = (settings.base_capital * settings.ratio).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        if settings.target_principal != expected:
            raise RuntimeError("JH AUTO 목표 자동원금이 총 투자금액×자동운용비율과 다릅니다")
        if settings.launch_authorized and (
            settings.effective_principal <= 0
            or settings.effective_principal > settings.target_principal
        ):
            raise RuntimeError("JH AUTO 현재 허용원금이 목표 자동원금 범위를 벗어났습니다")

    def _set_operator_config(
        self,
        *,
        base: Decimal | None,
        ratio: Decimal | None,
    ):
        before = self.settings()
        if not before.launch_authorized:
            updated = super()._set_operator_config(base=base, ratio=ratio)
            self._validate_capital_contract()
            return updated

        # Once live authorization exists, capital changes are a safety-critical state
        # transition. Block broker BUY under the same lock before the first state write.
        with BUY_EXECUTION_LOCK:
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
            self.repository.set_system_value(
                OPERATOR_BUY_HALT_AT_KEY, datetime.now(UTC).isoformat()
            )
            self.repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
            self.repository.set_system_value(AUTO_STATE_KEY, "CONFIGURING")

        try:
            updated = super()._set_operator_config(base=base, ratio=ratio)
            self._validate_capital_contract()
        except Exception as exc:
            self.quarantine(f"AUTO_CONFIG_CHANGE_FAILED:{type(exc).__name__}")
            raise

        # Do not reopen BUY from the settings callback. Reconciliation, open-order
        # checks and every capital invariant must pass on a later independent cycle.
        with BUY_EXECUTION_LOCK:
            self.repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
            self.repository.set_system_value(AUTO_STATE_KEY, "AUTO_QUARANTINE")
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
            self.repository.set_system_value(
                OPERATOR_BUY_HALT_AT_KEY, datetime.now(UTC).isoformat()
            )
        return updated

    def try_release_quarantine(self, *, safety_ready: bool) -> bool:
        settings = self.settings()
        if settings.launch_authorized:
            self._validate_capital_contract()
        return super().try_release_quarantine(safety_ready=safety_ready)

    def _stage_filled(self) -> bool:
        if not super()._stage_filled():
            return False
        settings = self.settings()
        # The first pilot stage must prove the real automated write path at least once.
        # Otherwise a tiny all-zero integer target could silently advance to 100%.
        if settings.ramp_stage != 1 or settings.ramp_base_principal != 0:
            return True
        with self.repository.transaction() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM jh_auto_cycles
                WHERE filled_qty > 0 AND completed_at IS NOT NULL
                """
            ).fetchone()
        return bool(row and int(row["cnt"]) > 0)

    def _enforce_attempt_limits(self, current: datetime) -> bool:
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.repository.transaction() as connection:
            rows = connection.execute(
                """
                SELECT signal_id, COUNT(*) AS cnt
                FROM jh_auto_cycles
                WHERE started_at >= ?
                GROUP BY signal_id
                """,
                (day_start.isoformat(),),
            ).fetchall()
        total = sum(int(row["cnt"]) for row in rows)
        if total >= MAX_AUTO_BUY_ATTEMPTS_PER_DAY:
            self.quarantine("AUTO_DAILY_BUY_ATTEMPT_LIMIT")
            return False

        next_day = day_start + timedelta(days=1)
        for row in rows:
            signal_id = row["signal_id"]
            if signal_id is None or int(row["cnt"]) < MAX_AUTO_SIGNAL_ATTEMPTS_PER_DAY:
                continue
            self.repository.set_system_value(
                f"jh_auto_retry_after:{int(signal_id)}", next_day.isoformat()
            )
        return True

    def execute_one(self, trading_service: Any, *, now: datetime | None = None):
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if not self._enforce_attempt_limits(current):
            return None
        return super().execute_one(trading_service, now=current)
