from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS
from jd_holdings.infrastructure.toss_client import receipt_from_order

from .broker import Broker
from .database import SQLiteRepository
from .order_manager import OrderManager

CORE_ORDER_PURPOSES = {"CORE_REBALANCE_BUY", "CORE_REBALANCE_SELL"}


def _missing_broker_id_issue(
    orders: list[dict], prefix: str = "", *, exclude_unknown: bool = False
) -> str | None:
    stranded = [
        str(order["status"])
        for order in orders
        if not order.get("broker_order_id")
        and (not exclude_unknown or str(order["status"]) != "UNKNOWN")
    ]
    if not stranded:
        return None
    return f"{prefix}OPEN_ORDER_WITHOUT_BROKER_ID:{','.join(sorted(set(stranded)))}"


class ReconciliationService:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self._ensure_allocation_rows()

    def _ensure_allocation_rows(self) -> None:
        underlyings = {"QQQ": "QQQ", "TQQQ": "QQQ", "SOXL": "SOXX"}
        now = datetime.now(UTC).isoformat()
        with self.repository.transaction() as connection:
            for symbol in ALLOCATION_SYMBOLS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO core_positions(
                        symbol, underlying, target_weight, updated_at
                    ) VALUES (?, ?, '0', ?)
                    """,
                    (symbol, underlyings[symbol], now),
                )

    def _refresh_open_core_orders(self) -> None:
        """Apply broker-confirmed core fills before holdings reconciliation.

        A broker fill can become visible in holdings a few moments before the normal
        order monitor persists it. Refreshing every known core order first closes
        that false-mismatch window without ever resubmitting an order.
        """
        for local in list(self.repository.open_orders()):
            if str(local["purpose"]) not in CORE_ORDER_PURPOSES:
                continue
            broker_order_id = str(local.get("broker_order_id") or "")
            if not broker_order_id:
                continue
            client_order_id = str(local["client_order_id"])
            try:
                raw = self.broker.get_order(broker_order_id)
                receipt = receipt_from_order(raw, client_order_id)
                OrderManager._validate_receipt(
                    receipt,
                    expected_client_order_id=client_order_id,
                    expected_symbol=str(local["symbol"]),
                    expected_side=str(local["side"]),
                    expected_quantity=int(local["qty"]),
                )
                self.repository.update_order(
                    client_order_id,
                    status=receipt.status,
                    broker_order_id=receipt.broker_order_id,
                    filled_qty=receipt.filled_quantity,
                    average_fill_price=receipt.average_fill_price,
                    raw=receipt.raw,
                )
                if receipt.filled_quantity > 0:
                    self.repository.apply_core_fill(client_order_id)
            except Exception as exc:
                self.repository.set_system_value("v322_portfolio_safe_mode", "1")
                self.repository.log_event(
                    "SAFE_MODE",
                    "CORE_ORDER_REFRESH_FAILED",
                    "계좌 대조 전 열린 코어 주문 상태를 확정하지 못했습니다",
                    symbol=str(local["symbol"]),
                    context={
                        "client_order_id": client_order_id,
                        "broker_order_id": broker_order_id,
                        "exception": type(exc).__name__,
                    },
                )
                raise RuntimeError(
                    "열린 코어 주문 상태 확인 실패로 SAFE_MODE입니다"
                ) from exc

    @staticmethod
    def _core_fill_transition_explains_quantity(
        expected_total: int,
        broker_qty: Decimal,
        local_orders: list[dict],
    ) -> bool:
        """Allow only a directionally possible in-flight core fill delta.

        The order snapshot may become stale immediately after it is read. If holdings
        already include a newly filled slice, defer the mismatch for one monitor cycle
        only when that delta fits inside the still-open BUY/SELL quantity envelope.
        """
        if Decimal(expected_total) == broker_qty:
            return False
        remaining_buy = 0
        remaining_sell = 0
        has_core_order = False
        for order in local_orders:
            if str(order["purpose"]) not in CORE_ORDER_PURPOSES:
                continue
            if not order.get("broker_order_id") or str(order["status"]) == "UNKNOWN":
                continue
            remaining = max(0, int(order["qty"]) - int(order.get("filled_qty") or 0))
            if remaining <= 0:
                continue
            has_core_order = True
            if str(order["side"]).upper() == "BUY":
                remaining_buy += remaining
            elif str(order["side"]).upper() == "SELL":
                remaining_sell += remaining
        if not has_core_order:
            return False
        lower = Decimal(expected_total - remaining_sell)
        upper = Decimal(expected_total + remaining_buy)
        return lower <= broker_qty <= upper

    def run(self) -> dict[str, list[str]]:
        self._refresh_open_core_orders()
        try:
            raw_holdings = self.broker.get_holdings()
        except Exception as exc:
            self.repository.set_system_value("v322_portfolio_safe_mode", "1")
            self.repository.log_event(
                "SAFE_MODE",
                "BROKER_HOLDINGS_LOOKUP_FAILED",
                "브로커 보유수량을 확인하지 못해 신규 BUY를 차단합니다",
                context={"exception": type(exc).__name__},
            )
            raise RuntimeError("브로커 보유수량 조회 실패로 SAFE_MODE입니다") from exc

        broker_holdings: dict[str, dict] = {}
        duplicate_symbols: set[str] = set()
        for item in raw_holdings:
            symbol = str(item.get("symbol") or item.get("stockCode") or "").upper()
            if symbol not in ALLOCATION_SYMBOLS:
                continue
            if symbol in broker_holdings:
                duplicate_symbols.add(symbol)
            broker_holdings[symbol] = item
        result: dict[str, list[str]] = {}
        allocation_problem = False
        for symbol in ALLOCATION_SYMBOLS:
            core = self.repository.get_core_position(symbol)
            core_qty = int(core["qty"])
            booster_qty = 0
            booster_state = PositionState.EMPTY
            if symbol in self.config.enabled_symbols:
                position = self.repository.get_position(symbol)
                booster_qty = position.quantity
                booster_state = position.state
            holding = broker_holdings.get(symbol)
            raw_broker_qty = (
                holding.get("quantity", holding.get("holdingQuantity", "0"))
                if holding
                else "0"
            )
            try:
                broker_qty = Decimal(str(raw_broker_qty))
            except Exception:
                broker_qty = Decimal("0")
                quantity_invalid = True
            else:
                quantity_invalid = not broker_qty.is_finite() or broker_qty < 0
            expected_total = core_qty + booster_qty
            issues: list[str] = []
            local_orders = self.repository.open_orders(symbol)
            transition_explains_qty = (
                not quantity_invalid
                and broker_qty == broker_qty.to_integral_value()
                and self._core_fill_transition_explains_quantity(
                    expected_total, broker_qty, local_orders
                )
            )

            if symbol in duplicate_symbols:
                issues.append("BROKER_DUPLICATE_HOLDING_ROWS")
            if quantity_invalid:
                issues.append("BROKER_INVALID_HOLDING_QUANTITY")
            elif broker_qty != broker_qty.to_integral_value():
                issues.append(f"BROKER_FRACTIONAL_HOLDING:{broker_qty}")
            if booster_qty > 0 or booster_state not in {
                PositionState.EMPTY,
                PositionState.SAFE_MODE,
            }:
                issues.append(
                    f"V322_DIRECT_BOOSTER_STATE_PRESENT:{booster_state.value}:{booster_qty}"
                )
            if Decimal(expected_total) != broker_qty and not transition_explains_qty:
                issues.append(f"BROKER_DB_QTY_MISMATCH:{broker_qty}!={expected_total}")
            if expected_total == 0 and broker_qty > 0 and not transition_explains_qty:
                issues.append("UNMANAGED_PERSONAL_ALLOCATION_SYMBOL")
            if expected_total > 0 and broker_qty == 0 and not transition_explains_qty:
                issues.append("DB_POSITION_BROKER_EMPTY")

            plan = (
                self.repository.active_tp_plan(symbol)
                if symbol in self.config.enabled_symbols
                else None
            )
            if plan is not None:
                issues.append("V322_DIRECT_TP_PLAN_PRESENT")

            unknown_orders = [
                order for order in local_orders if str(order["status"]) == "UNKNOWN"
            ]
            if unknown_orders:
                issues.append("UNKNOWN_ORDER_STATUS")
                if any(not order.get("broker_order_id") for order in unknown_orders):
                    issues.append("UNKNOWN_ORDER_WITHOUT_BROKER_ID")
            missing_id = _missing_broker_id_issue(local_orders, exclude_unknown=True)
            if missing_id:
                issues.append(missing_id)
            try:
                broker_orders = self.broker.list_orders(status="OPEN", symbol=symbol)
                local_by_broker_id = {
                    str(order["broker_order_id"]): order
                    for order in local_orders
                    if order.get("broker_order_id")
                }
                local_broker_ids = set(local_by_broker_id)
                broker_ids = {
                    str(order["orderId"])
                    for order in broker_orders
                    if order.get("orderId")
                }
                unexpected_broker_ids = broker_ids - local_broker_ids
                missing_local_ids = local_broker_ids - broker_ids
                transient_missing_ids = {
                    order_id
                    for order_id in missing_local_ids
                    if str(local_by_broker_id[order_id]["purpose"])
                    in CORE_ORDER_PURPOSES
                    and str(local_by_broker_id[order_id]["status"]) != "UNKNOWN"
                }
                if unexpected_broker_ids or missing_local_ids - transient_missing_ids:
                    issues.append("BROKER_DB_OPEN_ORDER_MISMATCH")
            except Exception as exc:
                issues.append(f"BROKER_OPEN_ORDER_LOOKUP_FAILED:{type(exc).__name__}")

            if issues:
                allocation_problem = True
                result[symbol] = issues
                if symbol in self.config.enabled_symbols:
                    position = self.repository.get_position(symbol)
                    if position.state != PositionState.SAFE_MODE:
                        self.repository.transition_position(
                            symbol,
                            expected_state=position.state,
                            new_state=PositionState.SAFE_MODE,
                            reason_code="BROKER_DB_MISMATCH",
                            expected_version=position.version,
                        )
                self.repository.log_event(
                    "SAFE_MODE",
                    "RECONCILIATION_FAILED",
                    ";".join(issues),
                    symbol=symbol,
                )

        if allocation_problem:
            self.repository.set_system_value("v322_portfolio_safe_mode", "1")
        if self.config.idle_cash.enabled:
            cash_symbol = self.config.idle_cash.symbol
            state = self.repository.get_idle_cash_state()
            holding = broker_holdings.get(cash_symbol)
            broker_qty = int(Decimal(str(holding["quantity"]))) if holding else 0
            issues: list[str] = []
            if state.managed_quantity > broker_qty:
                issues.append(
                    f"SGOV_MANAGED_QTY_EXCEEDS_BROKER:{state.managed_quantity}>{broker_qty}"
                )
            local_orders = [
                order
                for order in self.repository.open_orders(cash_symbol)
                if str(order["purpose"]).startswith("SGOV_")
            ]
            unknown_orders = [
                order for order in local_orders if str(order["status"]) == "UNKNOWN"
            ]
            if unknown_orders:
                issues.append("SGOV_UNKNOWN_ORDER_STATUS")
                if any(not order.get("broker_order_id") for order in unknown_orders):
                    issues.append("SGOV_UNKNOWN_ORDER_WITHOUT_BROKER_ID")
            missing_id = _missing_broker_id_issue(
                local_orders, "SGOV_", exclude_unknown=True
            )
            if missing_id:
                issues.append(missing_id)
            try:
                broker_orders = self.broker.list_orders(
                    status="OPEN", symbol=cash_symbol
                )
                local_ids = {
                    str(order["broker_order_id"])
                    for order in local_orders
                    if order.get("broker_order_id")
                }
                broker_ids = {
                    str(order["orderId"])
                    for order in broker_orders
                    if order.get("orderId")
                }
                if local_ids != broker_ids:
                    issues.append("SGOV_BROKER_DB_OPEN_ORDER_MISMATCH")
            except Exception as exc:
                issues.append(f"SGOV_OPEN_ORDER_LOOKUP_FAILED:{type(exc).__name__}")
            self.repository.set_system_value(
                "idle_cash_safe_mode", "1" if issues else "0"
            )
            if issues:
                result[cash_symbol] = issues
                self.repository.log_event(
                    "SAFE_MODE",
                    "SGOV_RECONCILIATION_FAILED",
                    ";".join(issues),
                    symbol=cash_symbol,
                )
        self.repository.set_system_value(
            "last_reconciliation", datetime.now(UTC).isoformat()
        )
        return result
