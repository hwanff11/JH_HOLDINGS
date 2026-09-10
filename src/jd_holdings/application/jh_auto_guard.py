from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from jd_holdings.automation import AUTO_VERSION
from jd_holdings.infrastructure.market_clock import MarketClock

AUTO_ENABLED_KEY = "jh_auto_enabled"
AUTO_VERSION_KEY = "jh_auto_version"
AUTO_BASE_CAPITAL_KEY = "jh_auto_base_capital"
AUTO_RATIO_KEY = "jh_auto_ratio"
AUTO_TARGET_PRINCIPAL_KEY = "jh_auto_target_principal"
AUTO_EFFECTIVE_PRINCIPAL_KEY = "jh_auto_effective_principal"
AUTO_LAUNCH_AUTHORIZED_KEY = "jh_auto_launch_authorized"
AUTO_QUARANTINE_KEY = "jh_auto_startup_quarantine"
AUTO_OPERATOR_HALT_LATCH_KEY = "jh_auto_operator_halt_latched"
AUTO_STATE_KEY = "jh_auto_state"
V322_HWM_KEY = "v322_high_water_equity"
V322_RISK_BUDGET_KEY = "v322_risk_budget"

MIN_BASE_CAPITAL = Decimal("100")
MAX_BASE_CAPITAL = Decimal("10000000")
MIN_RATIO = Decimal("0.01")
MAX_RATIO = Decimal("1.00")
HWM_REINVESTMENT = Decimal("0.75")


def mark_repository_as_jh_auto_live(repository: Any) -> None:
    """Mark this process' live repository as requiring the JH AUTO contract.

    This process-local marker prevents a damaged database that loses every AUTO key
    from being mistaken for a legacy fixed-$50k ledger. It is set by the commissioned
    JH AUTO live entrypoint before any broker object can submit an order.
    """

    repository._jh_auto_live_required = True


def _text(repository: Any, key: str) -> str | None:
    value = repository.get_system_value(key)
    return None if value is None else str(value)


def _decimal(repository: Any, key: str) -> Decimal:
    raw = _text(repository, key)
    if raw in (None, ""):
        raise RuntimeError(f"JH AUTO 상태가 누락되었습니다: {key}")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"JH AUTO 숫자 상태가 손상되었습니다: {key}") from exc
    if not value.is_finite():
        raise RuntimeError(f"JH AUTO 숫자 상태가 유한값이 아닙니다: {key}")
    return value


def auto_contract_is_present(repository: Any) -> bool:
    return bool(
        getattr(repository, "_jh_auto_live_required", False)
        or _text(repository, AUTO_VERSION_KEY) is not None
        or _text(repository, AUTO_ENABLED_KEY) is not None
    )


def require_live_auto_buy_contract(
    repository: Any,
    *,
    require_regular_session: bool,
) -> None:
    """Fail closed on every JH AUTO invariant at the final live BUY boundary."""

    if not auto_contract_is_present(repository):
        return

    if _text(repository, AUTO_ENABLED_KEY) != "1":
        raise RuntimeError("JH AUTO 실행계층 활성상태가 누락·손상되었습니다")
    if _text(repository, AUTO_VERSION_KEY) != AUTO_VERSION:
        raise RuntimeError("JH AUTO 실행계층 버전이 현재 코드와 일치하지 않습니다")
    if _text(repository, AUTO_LAUNCH_AUTHORIZED_KEY) != "1":
        raise RuntimeError("JH AUTO 최초 시작승인 전에는 실제 매수를 할 수 없습니다")
    if _text(repository, AUTO_QUARANTINE_KEY) != "0":
        raise RuntimeError("JH AUTO 임시격리 상태라 실제 매수가 차단되어 있습니다")
    if _text(repository, AUTO_OPERATOR_HALT_LATCH_KEY) != "0":
        raise RuntimeError("JH AUTO 대표 긴급정지 상태가 ON이거나 누락·손상되었습니다")
    if _text(repository, AUTO_STATE_KEY) != "RUNNING":
        raise RuntimeError("JH AUTO 실행상태가 RUNNING이 아닙니다")

    base = _decimal(repository, AUTO_BASE_CAPITAL_KEY)
    ratio = _decimal(repository, AUTO_RATIO_KEY)
    target = _decimal(repository, AUTO_TARGET_PRINCIPAL_KEY)
    effective = _decimal(repository, AUTO_EFFECTIVE_PRINCIPAL_KEY)
    high_water = _decimal(repository, V322_HWM_KEY)
    risk_budget = _decimal(repository, V322_RISK_BUDGET_KEY)

    if not MIN_BASE_CAPITAL <= base <= MAX_BASE_CAPITAL:
        raise RuntimeError("JH AUTO 총 투자금액 상태가 허용범위를 벗어났습니다")
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise RuntimeError("JH AUTO 자동운용비율 상태가 허용범위를 벗어났습니다")

    expected_target = (base * ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if target != expected_target:
        raise RuntimeError(
            "JH AUTO 목표 자동원금이 총 투자금액×자동운용비율과 일치하지 않습니다"
        )
    if effective <= 0 or effective > target:
        raise RuntimeError("JH AUTO 현재 허용원금이 목표 자동원금 범위를 벗어났습니다")
    if high_water < effective:
        raise RuntimeError("JH AUTO HWM이 현재 허용원금보다 작아 회계상태가 일관되지 않습니다")

    maximum_hwm_budget = effective + HWM_REINVESTMENT * max(
        Decimal("0"), high_water - effective
    )
    if risk_budget < 0 or risk_budget > maximum_hwm_budget + Decimal("0.01"):
        raise RuntimeError("JH AUTO HWM75 위험예산이 독립 재계산 한도를 초과했습니다")

    if require_regular_session:
        current = datetime.now(UTC)
        if MarketClock().classify_session(current) != "regular":
            raise RuntimeError("JH AUTO 자동매수는 실제 제출 시점의 미국 정규장에서만 허용합니다")
