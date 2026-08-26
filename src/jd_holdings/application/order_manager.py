from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.toss_client import TossApiError, receipt_from_order
from jd_holdings.settings import RuntimeSettings

from .broker import Broker
from .database import ALL_ORDER_STATUSES, SQLiteRepository
from .managed_account import reserve_buy_order_with_managed_cash
from .operational_safety import BUY_EXECUTION_LOCK, OPERATOR_BUY_HALT_KEY

LOGGER = logging.getLogger(__name__)


class BrokerReceiptValidationError(RuntimeError):
    """The broker response cannot be proven to belong to the reserved order."""


def build_client_order_id(
    *, symbol: str, purpose: str, signal_id: int | None, unique_context: str
) -> str:
    source = f"JDSS|{symbol}|{purpose}|{signal_id}|{unique_context}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    purpose_short = purpose.replace("ENTRY_", "E")[:5]
    return f"JDSS-{symbol[:5]}-{purpose_short}-{digest}"[:36]


class OrderManager:
    def __init__(
        self,
        repository: SQLiteRepository,
        broker: Broker,
        settings: RuntimeSettings,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.settings = settings

    def _buy_is_halted(self) -> bool:
        return self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"

    def submit(
        self,
        request: OrderRequest,
        *,
        cycle_id: str | None,
    ) -> OrderReceipt:
        existing = self.repository.get_order_by_client_id(request.client_order_id)
        if existing:
            if existing.get("broker_order_id"):
                try:
                    receipt = receipt_from_order(
                        self.broker.get_order(str(existing["broker_order_id"])),
                        request.client_order_id,
                    )
                    self._validate_receipt(
                        receipt,
                        expected_client_order_id=request.client_order_id,
                        expected_symbol=request.symbol,
                        expected_side=request.side,
                        expected_quantity=request.quantity,
                    )
                except BrokerReceiptValidationError as exc:
                    self.repository.update_order(
                        request.client_order_id,
                        status="UNKNOWN",
                        raw={"error": type(exc).__name__, "message": str(exc)},
                    )
                    raise
                except Exception as exc:
                    LOGGER.warning("주문 상태 최신화 중 오류 발생: %s", exc)
                else:
                    self.repository.update_order(
                        request.client_order_id,
                        status=receipt.status,
                        broker_order_id=receipt.broker_order_id,
                        filled_qty=receipt.filled_quantity,
                        average_fill_price=receipt.average_fill_price,
                        raw=receipt.raw,
                    )
                    return receipt
            return OrderReceipt(
                client_order_id=request.client_order_id,
                broker_order_id=str(existing.get("broker_order_id") or ""),
                status=str(existing["status"]),
                quantity=int(existing["qty"]),
                filled_quantity=int(existing["filled_qty"]),
                average_fill_price=Decimal(existing["average_fill_price"])
                if existing.get("average_fill_price")
                else None,
            )

        is_buy = request.side.upper() == "BUY"
        if is_buy and self._buy_is_halted():
            raise RuntimeError("운영자 긴급정지 상태라 신규 BUY가 차단되어 있습니다")
        if self.settings.trading_mode == "live":
            # Fail before touching the order ledger. A missing/incorrect live
            # confirmation must not leave a stranded CREATED order behind.
            self.settings.require_live_trading()

        if is_buy:
            if request.price is None:
                raise RuntimeError("JDSS 매수는 관리현금 검증 가능한 지정가만 허용합니다")
            reserved = reserve_buy_order_with_managed_cash(
                self.repository.config,
                self.repository,
                self.broker,
                client_order_id=request.client_order_id,
                signal_id=request.signal_id,
                cycle_id=cycle_id,
                symbol=request.symbol,
                order_type=request.order_type,
                price=request.price,
                quantity=request.quantity,
                purpose=request.purpose,
            )
        else:
            reserved = self.repository.reserve_order(
                client_order_id=request.client_order_id,
                signal_id=request.signal_id,
                cycle_id=cycle_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                price=request.price,
                quantity=request.quantity,
                purpose=request.purpose,
            )
        if not reserved:
            raise RuntimeError("주문 멱등키 예약에 실패했습니다")

        try:
            if is_buy:
                # Serialize only the final risk-increasing broker boundary with
                # /halt. SELLs remain available during an operator BUY halt.
                with BUY_EXECUTION_LOCK:
                    if self._buy_is_halted():
                        self.repository.update_order(
                            request.client_order_id,
                            status="REJECTED",
                            raw={"error": "OPERATOR_BUY_HALT"},
                        )
                        raise RuntimeError(
                            "운영자 긴급정지가 활성화되어 BUY 제출을 중단했습니다"
                        )
                    receipt = self.broker.place_order(request)
            else:
                receipt = self.broker.place_order(request)
            self._validate_receipt(
                receipt,
                expected_client_order_id=request.client_order_id,
                expected_symbol=request.symbol,
                expected_side=request.side,
                expected_quantity=request.quantity,
            )
        except TossApiError as exc:
            status = "UNKNOWN" if exc.retryable else "REJECTED"
            self.repository.update_order(
                request.client_order_id,
                status=status,
                raw={
                    "error": str(exc),
                    "code": exc.code,
                    "request_id": exc.request_id,
                },
            )
            raise
        except Exception as exc:
            local = self.repository.get_order_by_client_id(request.client_order_id)
            if local is not None and str(local["status"]) == "REJECTED":
                raise
            self.repository.update_order(
                request.client_order_id,
                status="UNKNOWN",
                raw={"error": type(exc).__name__, "message": str(exc)},
            )
            raise
        self.repository.update_order(
            request.client_order_id,
            status=receipt.status,
            broker_order_id=receipt.broker_order_id,
            filled_qty=receipt.filled_quantity,
            average_fill_price=receipt.average_fill_price,
            raw=receipt.raw,
        )
        return receipt

    def refresh_order(self, client_order_id: str) -> OrderReceipt:
        local = self.repository.get_order_by_client_id(client_order_id)
        if not local or not local.get("broker_order_id"):
            raise KeyError(client_order_id)
        raw = self.broker.get_order(str(local["broker_order_id"]))
        receipt = receipt_from_order(raw, client_order_id)
        try:
            self._validate_receipt(
                receipt,
                expected_client_order_id=client_order_id,
                expected_symbol=str(local["symbol"]),
                expected_side=str(local["side"]),
                expected_quantity=int(local["qty"]),
            )
        except Exception as exc:
            self.repository.update_order(
                client_order_id,
                status="UNKNOWN",
                raw={"error": type(exc).__name__, "message": str(exc)},
            )
            raise
        self.repository.update_order(
            client_order_id,
            status=receipt.status,
            filled_qty=receipt.filled_quantity,
            average_fill_price=receipt.average_fill_price,
            raw=receipt.raw,
        )
        return receipt

    @staticmethod
    def _validate_receipt(
        receipt: OrderReceipt,
        *,
        expected_client_order_id: str,
        expected_symbol: str,
        expected_side: str,
        expected_quantity: int,
    ) -> None:
        """Reject a broker response that cannot be tied to the reserved order."""
        if receipt.client_order_id != expected_client_order_id:
            raise BrokerReceiptValidationError(
                "브로커 응답 clientOrderId가 예약 주문과 다릅니다"
            )
        if not receipt.broker_order_id:
            raise BrokerReceiptValidationError("브로커 응답에 orderId가 없습니다")
        if receipt.status.upper() not in ALL_ORDER_STATUSES:
            raise BrokerReceiptValidationError(
                f"브로커 응답 주문상태가 잘못됐습니다: {receipt.status}"
            )
        if receipt.quantity != expected_quantity:
            raise BrokerReceiptValidationError(
                "브로커 응답 주문수량이 예약 수량과 다릅니다"
            )
        if not 0 <= receipt.filled_quantity <= expected_quantity:
            raise BrokerReceiptValidationError(
                "브로커 응답 체결수량이 유효하지 않습니다"
            )
        if receipt.filled_quantity > 0 and (
            receipt.average_fill_price is None or receipt.average_fill_price <= 0
        ):
            raise BrokerReceiptValidationError(
                "체결된 주문의 평균체결가가 유효하지 않습니다"
            )

        raw = receipt.raw if isinstance(receipt.raw, dict) else {}
        raw_client_id = raw.get("clientOrderId")
        if raw_client_id is not None and str(raw_client_id) != expected_client_order_id:
            raise BrokerReceiptValidationError(
                "브로커 raw clientOrderId가 예약 주문과 다릅니다"
            )
        raw_symbol = raw.get("symbol") or raw.get("stockCode")
        if raw_symbol is not None and str(raw_symbol).upper() != expected_symbol.upper():
            raise BrokerReceiptValidationError(
                "브로커 응답 종목이 예약 주문과 다릅니다"
            )
        raw_side = raw.get("side")
        if raw_side is not None and str(raw_side).upper() != expected_side.upper():
            raise BrokerReceiptValidationError(
                "브로커 응답 매매방향이 예약 주문과 다릅니다"
            )
        raw_quantity = raw.get("quantity")
        if raw_quantity is not None and Decimal(str(raw_quantity)) != expected_quantity:
            raise BrokerReceiptValidationError(
                "브로커 raw 주문수량이 예약 수량과 다릅니다"
            )
