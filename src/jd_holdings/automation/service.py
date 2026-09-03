from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    marked_managed_equity,
    raw_managed_cash_balance,
    record_v322_equity,
)
from jd_holdings.application.operational_safety import (
    BUY_EXECUTION_LOCK,
    OPERATOR_BUY_HALT_AT_KEY,
    OPERATOR_BUY_HALT_KEY,
)
from jd_holdings.core.enums import DecisionType
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS
from jd_holdings.infrastructure.market_clock import MarketClock

from . import AUTO_VERSION

AUTO_ENABLED_KEY = "jh_auto_enabled"
AUTO_VERSION_KEY = "jh_auto_version"
AUTO_BASE_CAPITAL_KEY = "jh_auto_base_capital"
AUTO_RATIO_KEY = "jh_auto_ratio"
AUTO_TARGET_PRINCIPAL_KEY = "jh_auto_target_principal"
AUTO_EFFECTIVE_PRINCIPAL_KEY = "jh_auto_effective_principal"
AUTO_ACCOUNTING_STARTED_AT_KEY = "jh_auto_accounting_started_at"
AUTO_LAUNCH_AUTHORIZED_KEY = "jh_auto_launch_authorized"
AUTO_LAUNCH_AUTHORIZED_AT_KEY = "jh_auto_launch_authorized_at"
AUTO_QUARANTINE_KEY = "jh_auto_startup_quarantine"
AUTO_STATE_KEY = "jh_auto_state"
AUTO_OPERATOR_HALT_LATCH_KEY = "jh_auto_operator_halt_latched"
AUTO_RAMP_STAGE_KEY = "jh_auto_ramp_stage"
AUTO_RAMP_BASE_KEY = "jh_auto_ramp_base_principal"
AUTO_RAMP_TARGET_KEY = "jh_auto_ramp_target_principal"
AUTO_RAMP_STAGE_STARTED_KEY = "jh_auto_ramp_stage_started_trade_date"
AUTO_RAMP_STAGE_FILLED_KEY = "jh_auto_ramp_stage_filled_trade_date"
AUTO_UNITS_KEY = "jh_auto_performance_units"
AUTO_NAV_KEY = "jh_auto_performance_nav"
AUTO_LAST_ERROR_KEY = "jh_auto_last_error"
AUTO_LAST_CYCLE_KEY = "jh_auto_last_cycle_id"
TARGET_QTY_GENERATION_KEY = "v322_target_qty_generation"
V322_HWM_KEY = "v322_high_water_equity"
V322_RISK_BUDGET_KEY = "v322_risk_budget"

RAMP_FRACTIONS = {
    1: Decimal("0.50"),
    2: Decimal("0.75"),
    3: Decimal("1.00"),
}
MINIMUM_STAGE_SESSIONS = 3
MIN_BASE_CAPITAL = Decimal("100")
MAX_BASE_CAPITAL = Decimal("10000000")
MIN_RATIO = Decimal("0.01")
MAX_RATIO = Decimal("1.00")
AUTO_RETRY_COOLDOWN = timedelta(minutes=5)


@dataclass(frozen=True)
class AutoSettings:
    version: str
    base_capital: Decimal | None
    ratio: Decimal | None
    target_principal: Decimal
    effective_principal: Decimal
    launch_authorized: bool
    quarantine: bool
    operator_halt_latched: bool
    state: str
    ramp_stage: int
    ramp_base_principal: Decimal
    ramp_target_principal: Decimal

    @property
    def configured(self) -> bool:
        return self.base_capital is not None and self.ratio is not None and self.target_principal > 0

    @property
    def ratio_percent(self) -> Decimal | None:
        if self.ratio is None:
            return None
        return self.ratio * Decimal("100")


@dataclass(frozen=True)
class AutoPerformance:
    equity: Decimal
    cash: Decimal
    invested: Decimal
    net_profit: Decimal
    cumulative_return: Decimal
    nav: Decimal
    high_water: Decimal
    risk_budget: Decimal
    effective_principal: Decimal


@dataclass(frozen=True)
class AutoExecutionResult:
    cycle_id: str
    signal_id: int
    symbol: str
    status: str
    quantity: int
    filled_quantity: int
    average_fill_price: Decimal | None


class JHAutoService:
    """Capital control, accounting and autonomous execution for JH AUTO 1.0.0.

    The JDSS 3.2.2 strategy still owns market/weight decisions.  This service owns
    how much capital is delegated, staged capital opening, time-weighted performance,
    launch authorization and the replacement of human BUY approvals with the exact
    existing review -> execution path.
    """

    def __init__(
        self,
        config: Any,
        repository: Any,
        broker: Any,
        reconciliation_service: Any,
        market_clock: MarketClock,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.reconciliation_service = reconciliation_service
        self.market_clock = market_clock
        self.bootstrap_repository(repository)

    @classmethod
    def bootstrap_repository(cls, repository: Any) -> None:
        """Create additive AUTO tables/keys without authorizing a single order."""
        now = datetime.now(UTC).isoformat()
        with repository.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jh_auto_capital_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    old_base_capital TEXT,
                    new_base_capital TEXT,
                    old_ratio TEXT,
                    new_ratio TEXT,
                    old_target_principal TEXT NOT NULL,
                    new_target_principal TEXT NOT NULL,
                    old_effective_principal TEXT NOT NULL,
                    new_effective_principal TEXT NOT NULL,
                    external_flow TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jh_auto_performance_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    equity TEXT NOT NULL,
                    cash TEXT NOT NULL,
                    invested TEXT NOT NULL,
                    effective_principal TEXT NOT NULL,
                    high_water TEXT NOT NULL,
                    risk_budget TEXT NOT NULL,
                    units TEXT NOT NULL,
                    nav TEXT NOT NULL,
                    cumulative_return TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jh_auto_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    signal_id INTEGER,
                    symbol TEXT,
                    status TEXT NOT NULL,
                    requested_qty INTEGER NOT NULL DEFAULT 0,
                    filled_qty INTEGER NOT NULL DEFAULT 0,
                    average_fill_price TEXT,
                    broker_order_id TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )

        defaults = {
            AUTO_ENABLED_KEY: "1",
            AUTO_VERSION_KEY: AUTO_VERSION,
            AUTO_TARGET_PRINCIPAL_KEY: "0",
            AUTO_EFFECTIVE_PRINCIPAL_KEY: "0",
            AUTO_LAUNCH_AUTHORIZED_KEY: "0",
            AUTO_QUARANTINE_KEY: "1",
            AUTO_STATE_KEY: "SETUP",
            AUTO_OPERATOR_HALT_LATCH_KEY: "0",
            AUTO_RAMP_STAGE_KEY: "0",
            AUTO_RAMP_BASE_KEY: "0",
            AUTO_RAMP_TARGET_KEY: "0",
            AUTO_UNITS_KEY: "0",
            AUTO_NAV_KEY: "1",
            AUTO_LAST_ERROR_KEY: "",
            AUTO_LAST_CYCLE_KEY: "",
        }
        for key, value in defaults.items():
            if repository.get_system_value(key) is None:
                repository.set_system_value(key, value)
        # Always keep the runtime version current, but never reset authorization.
        repository.set_system_value(AUTO_ENABLED_KEY, "1")
        repository.set_system_value(AUTO_VERSION_KEY, AUTO_VERSION)
        repository.log_event(
            "INFO",
            "JH_AUTO_BOOTSTRAPPED",
            "JH AUTO 자동운용 계층을 준비했습니다. 최초 시작 승인 전 실제 매수는 차단됩니다",
            context={"auto_version": AUTO_VERSION, "at": now},
        )

    def arm_startup_quarantine(self) -> None:
        """Every process start enters a self-check quarantine, not a manual halt."""
        self.repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
        state = "STARTUP_QUARANTINE" if self.settings().launch_authorized else "SETUP"
        self.repository.set_system_value(AUTO_STATE_KEY, state)
        self.repository.log_event(
            "SAFE_MODE",
            "JH_AUTO_STARTUP_QUARANTINE",
            "서비스 시작/재시작으로 JH AUTO 신규매수를 임시 차단했습니다",
        )

    @staticmethod
    def _decimal_or_none(raw: str | None) -> Decimal | None:
        if raw in (None, ""):
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError(f"JH AUTO 숫자 상태가 손상되었습니다: {raw}") from exc
        if not value.is_finite():
            raise RuntimeError("JH AUTO 숫자 상태가 유한값이 아닙니다")
        return value

    def _decimal(self, key: str, default: Decimal = Decimal("0")) -> Decimal:
        value = self._decimal_or_none(self.repository.get_system_value(key))
        return default if value is None else value

    def settings(self) -> AutoSettings:
        base = self._decimal_or_none(self.repository.get_system_value(AUTO_BASE_CAPITAL_KEY))
        ratio = self._decimal_or_none(self.repository.get_system_value(AUTO_RATIO_KEY))
        return AutoSettings(
            version=self.repository.get_system_value(AUTO_VERSION_KEY) or AUTO_VERSION,
            base_capital=base,
            ratio=ratio,
            target_principal=self._decimal(AUTO_TARGET_PRINCIPAL_KEY),
            effective_principal=self._decimal(AUTO_EFFECTIVE_PRINCIPAL_KEY),
            launch_authorized=self.repository.get_system_value(AUTO_LAUNCH_AUTHORIZED_KEY) == "1",
            quarantine=self.repository.get_system_value(AUTO_QUARANTINE_KEY) == "1",
            operator_halt_latched=(self.repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "1"),
            state=self.repository.get_system_value(AUTO_STATE_KEY) or "SETUP",
            ramp_stage=int(self.repository.get_system_value(AUTO_RAMP_STAGE_KEY) or "0"),
            ramp_base_principal=self._decimal(AUTO_RAMP_BASE_KEY),
            ramp_target_principal=self._decimal(AUTO_RAMP_TARGET_KEY),
        )

    @staticmethod
    def _normalize_money(value: Decimal | str | int | float) -> Decimal:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("운용 기준자금은 유한값이어야 합니다")
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if not MIN_BASE_CAPITAL <= amount <= MAX_BASE_CAPITAL:
            raise ValueError(
                f"운용 기준자금은 {MIN_BASE_CAPITAL:.0f}~{MAX_BASE_CAPITAL:.0f} USD 범위여야 합니다"
            )
        return amount

    @staticmethod
    def _normalize_ratio_percent(value: Decimal | str | int | float) -> Decimal:
        percent = Decimal(str(value))
        if not percent.is_finite():
            raise ValueError("자동운용비율은 유한값이어야 합니다")
        ratio = percent / Decimal("100")
        if not MIN_RATIO <= ratio <= MAX_RATIO:
            raise ValueError("자동운용비율은 1~100% 범위여야 합니다")
        return ratio.quantize(Decimal("0.0001"))

    @staticmethod
    def _target_principal(base: Decimal | None, ratio: Decimal | None) -> Decimal:
        if base is None or ratio is None:
            return Decimal("0")
        return (base * ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def set_base_capital(self, value: Decimal | str | int | float) -> AutoSettings:
        return self._set_operator_config(base=self._normalize_money(value), ratio=None)

    def set_ratio_percent(self, value: Decimal | str | int | float) -> AutoSettings:
        return self._set_operator_config(base=None, ratio=self._normalize_ratio_percent(value))

    def _set_operator_config(
        self,
        *,
        base: Decimal | None,
        ratio: Decimal | None,
    ) -> AutoSettings:
        before = self.settings()
        new_base = base if base is not None else before.base_capital
        new_ratio = ratio if ratio is not None else before.ratio
        new_target = self._target_principal(new_base, new_ratio)
        if before.launch_authorized and self.repository.open_orders():
            raise RuntimeError("진행 중 주문이 있어 자금 설정을 바꿀 수 없습니다")
        if before.launch_authorized and new_target > before.target_principal and before.ramp_stage != 0:
            raise RuntimeError("현재 자금확대 단계가 끝난 뒤 추가 확대할 수 있습니다")

        if new_base is not None:
            self.repository.set_system_value(AUTO_BASE_CAPITAL_KEY, str(new_base))
        if new_ratio is not None:
            self.repository.set_system_value(AUTO_RATIO_KEY, str(new_ratio))
        self.repository.set_system_value(AUTO_TARGET_PRINCIPAL_KEY, str(new_target))

        if before.launch_authorized:
            self._retarget_live_principal(before, new_target)
        else:
            self.repository.set_system_value(
                AUTO_STATE_KEY,
                "READY" if new_base is not None and new_ratio is not None else "SETUP",
            )
            self._insert_capital_event(
                "CONFIG_CHANGE",
                before,
                new_base,
                new_ratio,
                new_target,
                before.effective_principal,
                Decimal("0"),
                "최초 시작 전 운영자가 자동운용 설정을 변경했습니다",
            )
        return self.settings()

    def _retarget_live_principal(self, before: AutoSettings, new_target: Decimal) -> None:
        old_target = before.target_principal
        if new_target == old_target:
            return
        if new_target < before.effective_principal:
            # Risk reduction is never delayed behind a staged ramp.
            self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
            self.repository.set_system_value(AUTO_RAMP_BASE_KEY, str(new_target))
            self.repository.set_system_value(AUTO_RAMP_TARGET_KEY, str(new_target))
            self._apply_external_flow(
                new_target,
                event_type="CAPITAL_REDUCTION",
                note="자동운용 원금 축소를 즉시 위험축소 목표에 반영했습니다",
            )
            return

        if new_target < old_target:
            # Target fell but current authorized principal is already below it (rare
            # during a ramp).  Keep authorization unchanged and cancel further opening.
            self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
            self.repository.set_system_value(AUTO_RAMP_BASE_KEY, str(before.effective_principal))
            self.repository.set_system_value(AUTO_RAMP_TARGET_KEY, str(new_target))
            return

        # New risk is opened only in 50 -> 75 -> 100% steps on the added capital.
        base_principal = before.effective_principal
        self.repository.set_system_value(AUTO_RAMP_BASE_KEY, str(base_principal))
        self.repository.set_system_value(AUTO_RAMP_TARGET_KEY, str(new_target))
        self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, "1")
        self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "")
        self.repository.set_system_value(
            AUTO_RAMP_STAGE_STARTED_KEY,
            self._latest_completed_date().isoformat(),
        )
        first = base_principal + (new_target - base_principal) * RAMP_FRACTIONS[1]
        self._apply_external_flow(
            first.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            event_type="CAPITAL_INCREASE_STAGE_1",
            note="추가 자동운용 자금의 50%를 먼저 열었습니다",
        )

    def _latest_completed_date(self) -> date:
        return self.market_clock.latest_completed_session(delay_minutes=0)

    def _preflight_launch(self) -> None:
        settings = self.settings()
        if not settings.configured:
            raise RuntimeError("운용 기준자금과 자동운용비율을 먼저 설정해야 합니다")
        if settings.launch_authorized:
            return
        if self.repository.open_orders():
            raise RuntimeError("미체결 주문이 남아 있어 최초 자동운용을 시작할 수 없습니다")
        dirty = [
            str(row["symbol"])
            for row in self.repository.core_positions()
            if int(row["qty"]) != 0 or Decimal(str(row["cost_basis"] or "0")) != 0
        ]
        if dirty:
            raise RuntimeError(
                f"최초 JH AUTO 시작은 관리종목 보유수량이 0인 청정 원장에서만 허용합니다 ({', '.join(dirty)})"
            )
        mismatches = self.reconciliation_service.run()
        if mismatches:
            raise RuntimeError("실계좌와 JDSS 원장이 일치하지 않아 자동운용을 시작할 수 없습니다")
        buying_power = Decimal(str(self.broker.get_buying_power("USD")))
        if buying_power < settings.target_principal:
            raise RuntimeError("토스 USD 매수가능금액이 목표 자동운용 원금보다 작습니다")
        if self._portfolio_safe_mode():
            raise RuntimeError("안전정지(SAFE_MODE)가 남아 있어 자동운용을 시작할 수 없습니다")

    def authorize_launch(self) -> AutoSettings:
        """Persist operator consent only; this method never places an order."""
        self._preflight_launch()
        before = self.settings()
        if before.launch_authorized:
            return before
        now = datetime.now(UTC).isoformat()
        self.repository.set_system_value(AUTO_ACCOUNTING_STARTED_AT_KEY, now)
        self.repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
        self.repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_AT_KEY, now)
        self.repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
        self.repository.set_system_value(AUTO_STATE_KEY, "STARTUP_QUARANTINE")

        # JH AUTO owns capital staging.  Mark the legacy manual onboarding as complete
        # so the same 50/75/100 rule is not accidentally applied twice.
        self.repository.set_system_value("v322_initial_onboarding_status", "COMPLETED")
        self.repository.set_system_value("v322_initial_onboarding_stage", "3")
        self.repository.set_system_value("v322_initial_onboarding_stage_filled_trade_date", "")

        target = before.target_principal
        self.repository.set_system_value(AUTO_RAMP_BASE_KEY, "0")
        self.repository.set_system_value(AUTO_RAMP_TARGET_KEY, str(target))
        self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, "1")
        self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "")
        self.repository.set_system_value(
            AUTO_RAMP_STAGE_STARTED_KEY,
            self._latest_completed_date().isoformat(),
        )
        first = (target * RAMP_FRACTIONS[1]).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        self._apply_external_flow(
            first,
            event_type="LAUNCH_STAGE_1",
            note="대표 최초 시작승인 후 자동운용 원금의 50%만 열었습니다",
        )
        self.repository.log_event(
            "INFO",
            "JH_AUTO_LAUNCH_AUTHORIZED",
            "대표가 JH AUTO 최초 시작을 승인했습니다. 승인 처리 자체는 주문을 제출하지 않습니다",
            context={
                "auto_version": AUTO_VERSION,
                "target_principal": str(target),
                "effective_principal": str(first),
            },
        )
        return self.settings()

    def _insert_capital_event(
        self,
        event_type: str,
        before: AutoSettings,
        new_base: Decimal | None,
        new_ratio: Decimal | None,
        new_target: Decimal,
        new_effective: Decimal,
        external_flow: Decimal,
        note: str,
    ) -> None:
        with self.repository.transaction() as connection:
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
                    event_type,
                    str(before.base_capital) if before.base_capital is not None else None,
                    str(new_base) if new_base is not None else None,
                    str(before.ratio) if before.ratio is not None else None,
                    str(new_ratio) if new_ratio is not None else None,
                    str(before.target_principal),
                    str(new_target),
                    str(before.effective_principal),
                    str(new_effective),
                    str(external_flow),
                    note,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _apply_external_flow(
        self,
        new_effective: Decimal,
        *,
        event_type: str,
        note: str,
    ) -> None:
        before = self.settings()
        old_effective = before.effective_principal
        new_effective = max(Decimal("0"), new_effective.quantize(Decimal("0.01")))
        delta = new_effective - old_effective
        if delta == 0:
            return

        accounting_started = self.repository.get_system_value(AUTO_ACCOUNTING_STARTED_AT_KEY)
        units = self._decimal(AUTO_UNITS_KEY)
        nav = self._decimal(AUTO_NAV_KEY, Decimal("1"))
        if accounting_started and old_effective > 0:
            pre_equity = marked_managed_equity(self.config, self.repository, self.broker)
            if units > 0 and pre_equity >= 0:
                nav = pre_equity / units
            if nav <= 0:
                nav = Decimal("1")
        else:
            pre_equity = old_effective
            nav = Decimal("1")

        unit_delta = delta / nav if nav > 0 else delta
        new_units = units + unit_delta
        if new_units < Decimal("-0.000001"):
            raise RuntimeError("자금 회수 후 JH AUTO 성과 단위가 음수가 됩니다")
        new_units = max(Decimal("0"), new_units)

        old_hwm = self._decimal(V322_HWM_KEY, old_effective)
        adjusted_hwm = max(new_effective, old_hwm + delta)
        post_flow_equity = max(Decimal("0"), pre_equity + delta)
        gain = max(Decimal("0"), adjusted_hwm - new_effective)
        risk_budget = min(
            post_flow_equity,
            new_effective + Decimal("0.75") * gain,
        )

        self.repository.set_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY, str(new_effective))
        self.repository.set_system_value(AUTO_UNITS_KEY, str(new_units))
        self.repository.set_system_value(AUTO_NAV_KEY, str(nav))
        self.repository.set_system_value(V322_HWM_KEY, str(adjusted_hwm))
        self.repository.set_system_value(V322_RISK_BUDGET_KEY, str(max(Decimal("0"), risk_budget)))
        self._invalidate_target_generation("JH_AUTO_CAPITAL_CHANGED")

        updated = self.settings()
        self._insert_capital_event(
            event_type,
            before,
            updated.base_capital,
            updated.ratio,
            updated.target_principal,
            new_effective,
            delta,
            note,
        )
        self.repository.log_event(
            "INFO",
            event_type,
            note,
            context={
                "external_flow": str(delta),
                "effective_principal": str(new_effective),
                "target_principal": str(updated.target_principal),
            },
        )

    def _invalidate_target_generation(self, reason: str) -> None:
        for symbol in ALLOCATION_SYMBOLS:
            self.repository.invalidate_active_signals(symbol, reason=reason)
        with self.repository.transaction() as connection:
            connection.execute("UPDATE core_positions SET target_qty = 0")
        self.repository.set_system_value(TARGET_QTY_GENERATION_KEY, "")

    def _portfolio_safe_mode(self) -> bool:
        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            return True
        for symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            value = position.state.value if hasattr(position.state, "value") else str(position.state)
            if value == "SAFE_MODE":
                return True
        return False

    def quarantine(self, reason: str) -> None:
        """Block BUY without converting the condition into a permanent manual halt."""
        with BUY_EXECUTION_LOCK:
            self.repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
            self.repository.set_system_value(AUTO_STATE_KEY, "AUTO_QUARANTINE")
            self.repository.set_system_value(AUTO_LAST_ERROR_KEY, reason)
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
            self.repository.set_system_value(OPERATOR_BUY_HALT_AT_KEY, datetime.now(UTC).isoformat())
        self.repository.log_event(
            "SAFE_MODE",
            "JH_AUTO_QUARANTINED",
            "JH AUTO가 신규매수를 임시 차단했습니다",
            context={"reason": reason},
        )

    def try_release_quarantine(self, *, safety_ready: bool) -> bool:
        settings = self.settings()
        if not settings.launch_authorized:
            self.repository.set_system_value(AUTO_STATE_KEY, "READY" if settings.configured else "SETUP")
            return False
        if settings.operator_halt_latched:
            self.repository.set_system_value(AUTO_STATE_KEY, "OPERATOR_HALT")
            return False
        if self._portfolio_safe_mode():
            self.repository.set_system_value(AUTO_STATE_KEY, "SAFE_MODE")
            return False
        if not safety_ready or self.repository.open_orders():
            return False

        changed = settings.quarantine or self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
        with BUY_EXECUTION_LOCK:
            if self.repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "1":
                self.repository.set_system_value(AUTO_STATE_KEY, "OPERATOR_HALT")
                return False
            if self._portfolio_safe_mode() or self.repository.open_orders():
                return False
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "0")
            self.repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
            self.repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
            self.repository.set_system_value(AUTO_LAST_ERROR_KEY, "")
        if changed:
            self.repository.log_event(
                "INFO",
                "JH_AUTO_QUARANTINE_RELEASED",
                "계좌·원장·주문 안전조건을 다시 확인해 자동운용 임시격리를 해제했습니다",
            )
        return True

    def _stage_filled(self) -> bool:
        if not self.repository.get_system_value(TARGET_QTY_GENERATION_KEY):
            return False
        if self.repository.open_orders():
            return False
        cores = {
            str(row["symbol"]): row
            for row in self.repository.core_positions()
            if str(row["symbol"]) in ALLOCATION_SYMBOLS
        }
        if set(cores) != set(ALLOCATION_SYMBOLS):
            return False
        return all(int(row["qty"]) >= int(row["target_qty"]) for row in cores.values())

    def advance_ramp_if_ready(self) -> bool:
        settings = self.settings()
        if not settings.launch_authorized or settings.ramp_stage == 0:
            return False
        if self._portfolio_safe_mode() or self.repository.open_orders():
            return False

        completed = self._latest_completed_date()
        filled = self._stage_filled()
        filled_raw = self.repository.get_system_value(AUTO_RAMP_STAGE_FILLED_KEY) or ""
        if not filled:
            if filled_raw:
                self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "")
            return False
        if not filled_raw:
            self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, completed.isoformat())
            filled_raw = completed.isoformat()
            self.repository.log_event(
                "INFO",
                "JH_AUTO_RAMP_STAGE_FILLED",
                f"JH AUTO 자금투입 {settings.ramp_stage}/3단계 목표수량 체결을 확인했습니다",
            )

        if settings.ramp_stage == 3:
            self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
            self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "")
            self.repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
            self.repository.log_event(
                "INFO",
                "JH_AUTO_RAMP_COMPLETED",
                "JH AUTO 50→75→100 자금투입을 완료했습니다",
            )
            return True

        filled_date = date.fromisoformat(filled_raw)
        if self.market_clock.completed_sessions_since(filled_date) < MINIMUM_STAGE_SESSIONS:
            return False

        next_stage = settings.ramp_stage + 1
        base = settings.ramp_base_principal
        target = settings.ramp_target_principal
        next_effective = base + (target - base) * RAMP_FRACTIONS[next_stage]
        self.repository.set_system_value(AUTO_RAMP_STAGE_KEY, str(next_stage))
        self.repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "")
        self.repository.set_system_value(AUTO_RAMP_STAGE_STARTED_KEY, completed.isoformat())
        self._apply_external_flow(
            next_effective.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            event_type=f"CAPITAL_RAMP_STAGE_{next_stage}",
            note=f"JH AUTO 추가 자금투입을 {RAMP_FRACTIONS[next_stage] * 100:.0f}% 단계로 확대했습니다",
        )
        return True

    def performance(self, *, record: bool = False) -> AutoPerformance:
        settings = self.settings()
        if not settings.launch_authorized or settings.effective_principal <= 0:
            return AutoPerformance(
                equity=Decimal("0"),
                cash=Decimal("0"),
                invested=Decimal("0"),
                net_profit=Decimal("0"),
                cumulative_return=Decimal("0"),
                nav=Decimal("1"),
                high_water=Decimal("0"),
                risk_budget=Decimal("0"),
                effective_principal=settings.effective_principal,
            )

        equity = marked_managed_equity(self.config, self.repository, self.broker)
        cash = max(Decimal("0"), raw_managed_cash_balance(self.config, self.repository))
        invested = max(Decimal("0"), equity - cash)
        if record:
            high_water, risk_budget = record_v322_equity(self.config, self.repository, equity)
        else:
            high_water, risk_budget = current_v322_capital_state(self.config, self.repository)
        units = self._decimal(AUTO_UNITS_KEY)
        nav = equity / units if units > 0 else Decimal("1")
        cumulative = nav - Decimal("1")
        if record:
            self.repository.set_system_value(AUTO_NAV_KEY, str(nav))
            with self.repository.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO jh_auto_performance_snapshots(
                        equity, cash, invested, effective_principal,
                        high_water, risk_budget, units, nav,
                        cumulative_return, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(equity),
                        str(cash),
                        str(invested),
                        str(settings.effective_principal),
                        str(high_water),
                        str(risk_budget),
                        str(units),
                        str(nav),
                        str(cumulative),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return AutoPerformance(
            equity=equity,
            cash=cash,
            invested=invested,
            net_profit=equity - settings.effective_principal,
            cumulative_return=cumulative,
            nav=nav,
            high_water=high_water,
            risk_budget=risk_budget,
            effective_principal=settings.effective_principal,
        )

    def symbol_performance(self) -> dict[str, dict[str, Decimal | int]]:
        values: dict[str, dict[str, Decimal | int]] = {}
        for row in self.repository.core_positions():
            symbol = str(row["symbol"])
            if symbol not in ALLOCATION_SYMBOLS:
                continue
            quantity = int(row["qty"])
            cost_basis = Decimal(str(row["cost_basis"] or "0"))
            price = Decimal(str(self.broker.get_price(symbol)))
            market_value = Decimal(quantity) * price
            pnl = market_value - cost_basis
            return_rate = pnl / cost_basis if cost_basis > 0 else Decimal("0")
            values[symbol] = {
                "quantity": quantity,
                "cost_basis": cost_basis,
                "price": price,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "holding_return": return_rate,
                "target_weight": Decimal(str(row["target_weight"])),
                "target_qty": int(row["target_qty"]),
            }
        return values

    def _retry_allowed(self, signal_id: int, current: datetime) -> bool:
        raw = self.repository.get_system_value(f"jh_auto_retry_after:{signal_id}")
        if not raw:
            return True
        try:
            return current >= datetime.fromisoformat(raw)
        except ValueError:
            return False

    def _set_retry_cooldown(self, signal_id: int, current: datetime) -> None:
        self.repository.set_system_value(
            f"jh_auto_retry_after:{signal_id}",
            (current + AUTO_RETRY_COOLDOWN).isoformat(),
        )

    def execute_one(self, trading_service: Any, *, now: datetime | None = None) -> AutoExecutionResult | None:
        """Automatically approve at most one BUY after a fully clean regular-session cycle."""
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
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
        started = current.isoformat()
        with self.repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jh_auto_cycles(
                    cycle_id, signal_id, symbol, status, started_at
                ) VALUES (?, ?, ?, 'REVIEWING', ?)
                """,
                (cycle_id, signal_id, symbol, started),
            )
        self.repository.set_system_value(AUTO_LAST_CYCLE_KEY, cycle_id)

        try:
            review_id, review_token = trading_service.create_review_approval(signal_id, now=current)
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
                        json.dumps({"exception": name, "message": str(exc)}, ensure_ascii=False),
                        datetime.now(UTC).isoformat(),
                        cycle_id,
                    ),
                )
            self.repository.log_event(
                "WARNING",
                "JH_AUTO_EXECUTION_ERROR",
                "자동매수 검토/실행 중 오류가 발생했습니다",
                symbol=symbol,
                context={"cycle_id": cycle_id, "signal_id": signal_id, "exception": name},
            )
            if name not in {"ApprovalError", "QuoteChangedError", "IdleCashReleasePending"}:
                self.quarantine(f"AUTO_EXECUTION_ERROR:{name}")
            return None

        status = str(receipt.status).upper()
        if status in {"CANCELED", "REJECTED", "REPLACED"} and receipt.filled_quantity < receipt.quantity:
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
                    str(receipt.average_fill_price) if receipt.average_fill_price is not None else None,
                    str(receipt.broker_order_id or ""),
                    json.dumps({"client_order_id": receipt.client_order_id}, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    cycle_id,
                ),
            )
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

    def recent_cycles(self, limit: int = 5) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 20))
        with self.repository.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jh_auto_cycles
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
