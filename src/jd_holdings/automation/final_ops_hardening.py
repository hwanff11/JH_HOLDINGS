from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from jd_holdings.application.operational_safety import (
    OPERATOR_BUY_HALT_AT_KEY,
    OPERATOR_BUY_HALT_KEY,
)
from jd_holdings.core.enums import DecisionType
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .prelive_hardening import (
    MAX_AUTO_BUY_ATTEMPTS_PER_DAY,
    MAX_AUTO_SIGNAL_ATTEMPTS_PER_DAY,
    HardenedProductionJHAutoService,
)
from .service import (
    AUTO_ACCOUNTING_STARTED_AT_KEY,
    AUTO_EFFECTIVE_PRINCIPAL_KEY,
    AUTO_LAST_CYCLE_KEY,
    AUTO_LAUNCH_AUTHORIZED_AT_KEY,
    AUTO_LAUNCH_AUTHORIZED_KEY,
    AUTO_NAV_KEY,
    AUTO_QUARANTINE_KEY,
    AUTO_RAMP_BASE_KEY,
    AUTO_RAMP_STAGE_FILLED_KEY,
    AUTO_RAMP_STAGE_KEY,
    AUTO_RAMP_STAGE_STARTED_KEY,
    AUTO_RAMP_TARGET_KEY,
    AUTO_STATE_KEY,
    AUTO_UNITS_KEY,
    RAMP_FRACTIONS,
    TARGET_QTY_GENERATION_KEY,
    V322_HWM_KEY,
    V322_RISK_BUDGET_KEY,
    AutoExecutionResult,
)

AUTO_RAMP_STAGE_AUTO_FILL_KEY = "jh_auto_ramp_stage_auto_fill_seen"
NEW_YORK_TZ = ZoneInfo("America/New_York")
TERMINAL_AUTO_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED", "UNKNOWN"}


class FinalOpsProductionJHAutoService(HardenedProductionJHAutoService):
    """Final live-operation guards without changing JDSS 3.2.2 strategy math."""

    @staticmethod
    def _upsert_state(connection: Any, key: str, value: object, now: str) -> None:
        connection.execute(
            """
            INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, str(value), now),
        )

    @classmethod
    def bootstrap_repository(cls, repository: Any) -> None:
        super().bootstrap_repository(repository)
        if repository.get_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY) is None:
            repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0")
        cls._repair_incomplete_launch(repository)

    @classmethod
    def _repair_incomplete_launch(cls, repository: Any) -> None:
        """Repair a torn first-launch write only in the risk-reducing direction.

        A first launch is invalid when authorization exists but no positive effective
        principal exists.  Older code wrote those fields in separate transactions, so
        a crash could leave that half-state behind.  Recovery never opens capital: it
        returns to pre-launch quarantine, arms the low-level BUY halt and clears stale
        generated targets.  Any real position/order left behind still prevents a later
        launch through the normal preflight/reconciliation gates.
        """
        if repository.get_system_value(AUTO_LAUNCH_AUTHORIZED_KEY) != "1":
            return
        raw_effective = repository.get_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY)
        try:
            effective = Decimal(str(raw_effective or "0"))
        except (InvalidOperation, ValueError):
            effective = Decimal("0")
        if effective.is_finite() and effective > 0:
            return

        now = datetime.now(UTC).isoformat()
        base = repository.get_system_value("jh_auto_base_capital")
        ratio = repository.get_system_value("jh_auto_ratio")
        target = repository.get_system_value("jh_auto_target_principal") or "0"
        state = "READY" if base not in (None, "") and ratio not in (None, "") else "SETUP"
        with repository.transaction() as connection:
            for key, value in (
                (AUTO_ACCOUNTING_STARTED_AT_KEY, ""),
                (AUTO_LAUNCH_AUTHORIZED_KEY, "0"),
                (AUTO_LAUNCH_AUTHORIZED_AT_KEY, ""),
                (AUTO_EFFECTIVE_PRINCIPAL_KEY, "0"),
                (AUTO_QUARANTINE_KEY, "1"),
                (AUTO_STATE_KEY, state),
                (AUTO_RAMP_STAGE_KEY, "0"),
                (AUTO_RAMP_BASE_KEY, "0"),
                (AUTO_RAMP_TARGET_KEY, target),
                (AUTO_RAMP_STAGE_STARTED_KEY, ""),
                (AUTO_RAMP_STAGE_FILLED_KEY, ""),
                (AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0"),
                (AUTO_UNITS_KEY, "0"),
                (AUTO_NAV_KEY, "1"),
                (V322_HWM_KEY, "0"),
                (V322_RISK_BUDGET_KEY, "0"),
                (TARGET_QTY_GENERATION_KEY, ""),
                (OPERATOR_BUY_HALT_KEY, "1"),
                (OPERATOR_BUY_HALT_AT_KEY, now),
            ):
                cls._upsert_state(connection, key, value, now)
            connection.execute("UPDATE core_positions SET target_qty = 0")
            placeholders = ",".join("?" for _ in ALLOCATION_SYMBOLS)
            connection.execute(
                f"""
                UPDATE signals
                SET status = 'INVALID', processed = 1,
                    expired_reason = 'INCOMPLETE_AUTO_LAUNCH_RECOVERY', updated_at = ?
                WHERE status = 'ACTIVE' AND processed = 0
                  AND symbol IN ({placeholders})
                """,  # nosec B608 - placeholders are fixed SQL bind markers.
                (now, *ALLOCATION_SYMBOLS),
            )
        repository.log_event(
            "SAFE_MODE",
            "JH_AUTO_INCOMPLETE_LAUNCH_REPAIRED",
            "불완전한 최초 시작상태를 감지해 시작 전 안전상태로 되돌리고 신규매수를 차단했습니다",
        )

    def _before_launch_commit(self, connection: Any) -> None:
        """Test seam for proving the first-launch transaction rolls back atomically."""
        del connection

    def authorize_launch(self):
        """Atomically persist first-launch consent and stage-1 capital; never order here."""
        self._preflight_launch()
        before = self.settings()
        if before.launch_authorized:
            return before

        target = before.target_principal
        first = (target * RAMP_FRACTIONS[1]).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        if first <= 0:
            raise RuntimeError("최초 자동운용 1단계 허용원금이 0 이하입니다")
        now = datetime.now(UTC).isoformat()
        started_trade_date = self._latest_completed_date().isoformat()

        # The complete first-launch state transition is one SQLite transaction.  A
        # process crash or injected DB failure therefore leaves launch_authorized=0
        # rather than a half-authorized account with zero/partial capital state.
        with self.repository.transaction() as connection:
            for key, value in (
                (AUTO_ACCOUNTING_STARTED_AT_KEY, now),
                (AUTO_LAUNCH_AUTHORIZED_KEY, "1"),
                (AUTO_LAUNCH_AUTHORIZED_AT_KEY, now),
                (AUTO_QUARANTINE_KEY, "1"),
                (AUTO_STATE_KEY, "STARTUP_QUARANTINE"),
                ("v322_initial_onboarding_status", "COMPLETED"),
                ("v322_initial_onboarding_stage", "3"),
                ("v322_initial_onboarding_stage_filled_trade_date", ""),
                (AUTO_RAMP_BASE_KEY, "0"),
                (AUTO_RAMP_TARGET_KEY, target),
                (AUTO_RAMP_STAGE_KEY, "1"),
                (AUTO_RAMP_STAGE_FILLED_KEY, ""),
                (AUTO_RAMP_STAGE_STARTED_KEY, started_trade_date),
                (AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0"),
                (AUTO_EFFECTIVE_PRINCIPAL_KEY, first),
                (AUTO_UNITS_KEY, first),
                (AUTO_NAV_KEY, "1"),
                (V322_HWM_KEY, first),
                (V322_RISK_BUDGET_KEY, first),
                (TARGET_QTY_GENERATION_KEY, ""),
                (OPERATOR_BUY_HALT_KEY, "1"),
                (OPERATOR_BUY_HALT_AT_KEY, now),
            ):
                self._upsert_state(connection, key, value, now)

            connection.execute("UPDATE core_positions SET target_qty = 0")
            placeholders = ",".join("?" for _ in ALLOCATION_SYMBOLS)
            connection.execute(
                f"""
                UPDATE signals
                SET status = 'INVALID', processed = 1,
                    expired_reason = 'JH_AUTO_LAUNCH_CAPITAL_CHANGED', updated_at = ?
                WHERE status = 'ACTIVE' AND processed = 0
                  AND symbol IN ({placeholders})
                """,  # nosec B608 - placeholders are fixed SQL bind markers.
                (now, *ALLOCATION_SYMBOLS),
            )
            connection.execute(
                """
                INSERT INTO jh_auto_capital_events(
                    event_type, old_base_capital, new_base_capital,
                    old_ratio, new_ratio, old_target_principal,
                    new_target_principal, old_effective_principal,
                    new_effective_principal, external_flow, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "LAUNCH_STAGE_1",
                    str(before.base_capital) if before.base_capital is not None else None,
                    str(before.base_capital) if before.base_capital is not None else None,
                    str(before.ratio) if before.ratio is not None else None,
                    str(before.ratio) if before.ratio is not None else None,
                    str(before.target_principal),
                    str(target),
                    str(before.effective_principal),
                    str(first),
                    str(first - before.effective_principal),
                    "대표 최초 시작승인 후 자동운용 원금의 50%만 열었습니다",
                    now,
                ),
            )
            self._before_launch_commit(connection)

        self.repository.log_event(
            "INFO",
            "JH_AUTO_LAUNCH_AUTHORIZED",
            "대표가 JH AUTO 최초 시작을 승인했습니다. 승인 처리 자체는 주문을 제출하지 않습니다",
            context={
                "target_principal": str(target),
                "effective_principal": str(first),
            },
        )
        self._validate_capital_contract()
        return self.settings()

    def _set_operator_config(self, *, base, ratio):
        before = self.settings()
        updated = super()._set_operator_config(base=base, ratio=ratio)
        if updated.ramp_stage > 0 and (
            before.ramp_stage != updated.ramp_stage
            or before.ramp_target_principal != updated.ramp_target_principal
        ):
            self.repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0")
        return self.settings()

    def _stage_filled(self) -> bool:
        if not super()._stage_filled():
            return False
        # Integer rounding alone must never promote capital. Every 50 -> 75 -> 100
        # stage must prove at least one real AUTO fill under that stage's capital.
        return self.repository.get_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY) == "1"

    def advance_ramp_if_ready(self) -> bool:
        settings = self.settings()
        # `/halt` is not only an order barrier.  While the operator has explicitly
        # paused risk taking, no new delegated capital may silently open in the
        # background and surprise the operator after `/resume`.
        if (
            settings.operator_halt_latched
            or settings.quarantine
            or settings.state != "RUNNING"
            or self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) != "0"
        ):
            return False
        before = settings
        changed = super().advance_ramp_if_ready()
        after = self.settings()
        if changed and before.ramp_stage != after.ramp_stage:
            self.repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0")
        return changed

    def _enforce_attempt_limits(self, current: datetime) -> bool:
        """Count attempts by the New York trading date, not UTC midnight."""
        eastern = current.astimezone(NEW_YORK_TZ)
        day_start_local = eastern.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day_local = day_start_local + timedelta(days=1)
        day_start = day_start_local.astimezone(UTC)
        next_day = next_day_local.astimezone(UTC)

        with self.repository.transaction() as connection:
            rows = connection.execute(
                """
                SELECT signal_id, COUNT(*) AS cnt
                FROM jh_auto_cycles
                WHERE started_at >= ? AND started_at < ?
                GROUP BY signal_id
                """,
                (day_start.isoformat(), next_day.isoformat()),
            ).fetchall()
        total = sum(int(row["cnt"]) for row in rows)
        if total >= MAX_AUTO_BUY_ATTEMPTS_PER_DAY:
            self.quarantine("AUTO_DAILY_BUY_ATTEMPT_LIMIT")
            return False

        for row in rows:
            signal_id = row["signal_id"]
            if signal_id is None or int(row["cnt"]) < MAX_AUTO_SIGNAL_ATTEMPTS_PER_DAY:
                continue
            self.repository.set_system_value(
                f"jh_auto_retry_after:{int(signal_id)}", next_day.isoformat()
            )
        return True

    def execute_one(self, trading_service: Any, *, now: datetime | None = None):
        """Execute at most one BUY and bind its broker order to the AUTO cycle."""
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if not self._enforce_attempt_limits(current):
            return None

        settings = self.settings()
        if (
            not settings.launch_authorized
            or settings.quarantine
            or settings.operator_halt_latched
            or settings.state != "RUNNING"
            or self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) != "0"
            or self._portfolio_safe_mode()
            or self.repository.open_orders()
        ):
            return None
        if self.market_clock.classify_session(current) != "regular":
            return None
        if not self.repository.get_system_value(TARGET_QTY_GENERATION_KEY):
            return None

        signals = [
            signal
            for signal in trading_service.active_signals()
            if str(signal.get("action")) == DecisionType.CORE_REBALANCE_BUY.value
            and self._retry_allowed(int(signal["signal_id"]), current)
        ]
        if not signals:
            return None
        symbol_order = {symbol: index for index, symbol in enumerate(ALLOCATION_SYMBOLS)}
        signals.sort(key=lambda item: symbol_order.get(str(item["symbol"]), 99))
        signal = signals[0]
        signal_id = int(signal["signal_id"])
        symbol = str(signal["symbol"])
        cycle_id = f"JHAUTO-{uuid4().hex[:20]}"
        with self.repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jh_auto_cycles(
                    cycle_id, signal_id, symbol, status, started_at
                ) VALUES (?, ?, ?, 'REVIEWING', ?)
                """,
                (cycle_id, signal_id, symbol, current.isoformat()),
            )
        self.repository.set_system_value(AUTO_LAST_CYCLE_KEY, cycle_id)

        had_cycle_attr = hasattr(trading_service, "_jh_auto_cycle_id")
        previous_cycle = getattr(trading_service, "_jh_auto_cycle_id", None)
        trading_service._jh_auto_cycle_id = cycle_id
        try:
            review_id, review_token = trading_service.create_review_approval(
                signal_id, now=current
            )
            quote = trading_service.consume_review(review_id, review_token, now=current)
            if quote.execution_approval_id is None or not quote.execution_token:
                raise RuntimeError("자동 최종승인 토큰이 생성되지 않았습니다")
            receipt = trading_service.execute(
                quote.execution_approval_id,
                quote.execution_token,
                now=current,
            )
        except Exception as exc:
            name = type(exc).__name__
            self._set_retry_cooldown(signal_id, current)
            with self.repository.transaction() as connection:
                connection.execute(
                    """
                    UPDATE jh_auto_cycles
                    SET status = 'ERROR', detail_json = ?, completed_at = ?
                    WHERE cycle_id = ?
                    """,
                    (
                        json.dumps(
                            {"exception": name, "message": str(exc)}, ensure_ascii=False
                        ),
                        datetime.now(UTC).isoformat(),
                        cycle_id,
                    ),
                )
            self.repository.log_event(
                "WARNING",
                "JH_AUTO_EXECUTION_ERROR",
                "자동매수 검토/실행 중 오류가 발생했습니다",
                symbol=symbol,
                context={
                    "cycle_id": cycle_id,
                    "signal_id": signal_id,
                    "exception": name,
                },
            )
            if name not in {"ApprovalError", "QuoteChangedError", "IdleCashReleasePending"}:
                self.quarantine(f"AUTO_EXECUTION_ERROR:{name}")
            return None
        finally:
            if had_cycle_attr:
                trading_service._jh_auto_cycle_id = previous_cycle
            else:
                try:
                    del trading_service._jh_auto_cycle_id
                except AttributeError:
                    pass

        status = str(receipt.status).upper()
        if status == "UNKNOWN":
            self.quarantine("AUTO_ORDER_UNKNOWN")
        if status in {"CANCELED", "REJECTED", "REPLACED"} and (
            receipt.filled_quantity < receipt.quantity
        ):
            self._set_retry_cooldown(signal_id, current)
        with self.repository.transaction() as connection:
            connection.execute(
                """
                UPDATE jh_auto_cycles
                SET status = ?, requested_qty = ?, filled_qty = ?,
                    average_fill_price = ?, broker_order_id = ?, detail_json = ?,
                    completed_at = ?
                WHERE cycle_id = ?
                """,
                (
                    status,
                    int(receipt.quantity),
                    int(receipt.filled_quantity),
                    str(receipt.average_fill_price)
                    if receipt.average_fill_price is not None
                    else None,
                    str(receipt.broker_order_id or ""),
                    json.dumps(
                        {"client_order_id": receipt.client_order_id},
                        ensure_ascii=False,
                    ),
                    datetime.now(UTC).isoformat()
                    if status in TERMINAL_AUTO_STATUSES
                    else None,
                    cycle_id,
                ),
            )
        if receipt.filled_quantity > 0 and self.settings().ramp_stage > 0:
            self.repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "1")
        self.repository.log_event(
            "INFO",
            "JH_AUTO_ORDER_RESULT",
            f"{symbol} 자동매수 주문 상태 {status}",
            symbol=symbol,
            context={
                "cycle_id": cycle_id,
                "signal_id": signal_id,
                "quantity": int(receipt.quantity),
                "filled_quantity": int(receipt.filled_quantity),
            },
        )
        return AutoExecutionResult(
            cycle_id=cycle_id,
            signal_id=signal_id,
            symbol=symbol,
            status=status,
            quantity=int(receipt.quantity),
            filled_quantity=int(receipt.filled_quantity),
            average_fill_price=receipt.average_fill_price,
        )