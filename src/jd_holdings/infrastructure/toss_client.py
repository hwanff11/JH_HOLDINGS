from __future__ import annotations

import logging
import os
import re
import threading
from decimal import Decimal
from typing import Any

import requests

from jd_holdings.core.models import OrderReceipt, OrderRequest

LOGGER = logging.getLogger(__name__)
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
ORDER_SIDES = {"BUY", "SELL"}
ORDER_TYPES = {"LIMIT", "MARKET"}


class TossApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retryable = retryable


class TossClient:
    """Toss Securities OpenAPI adapter based on the current official schema."""

    BASE_URL = "https://openapi.tossinvest.com"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        account_seq: str | None = None,
        timeout: tuple[float, float] = (5.0, 15.0),
        session: requests.Session | None = None,
    ) -> None:
        self.client_id = client_id or os.getenv("TOSS_APP_KEY")
        self.client_secret = client_secret or os.getenv("TOSS_APP_SECRET")
        self.account_seq = account_seq or os.getenv("TOSS_ACCOUNT_SEQ", "1")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._access_token: str | None = None
        self._token_lock = threading.Lock()

    def _validate_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise TossApiError("TOSS_APP_KEY/TOSS_APP_SECRET이 설정되지 않았습니다")

    def authenticate(self, *, force: bool = False) -> str:
        self._validate_credentials()
        with self._token_lock:
            if self._access_token and not force:
                return self._access_token
            try:
                response = self.session.post(
                    f"{self.BASE_URL}/oauth2/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise TossApiError("토스 인증 요청 네트워크 오류", retryable=True) from exc
            if not response.ok:
                raise self._api_error(response)
            payload = self._json_object(response, "토스 인증")
            token = payload.get("access_token")
            if not token:
                raise TossApiError("토스 인증 응답에 access_token이 없습니다")
            self._access_token = str(token)
            return self._access_token

    def _headers(self, *, require_account: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.authenticate()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if require_account:
            headers["X-Tossinvest-Account"] = str(self.account_seq)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        require_account: bool = False,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=self._headers(require_account=require_account),
                timeout=self.timeout,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise TossApiError("토스 API 요청 시간초과", retryable=True) from exc
        except requests.RequestException as exc:
            raise TossApiError("토스 API 네트워크 오류", retryable=True) from exc
        if response.status_code == 401 and retry_auth:
            self.authenticate(force=True)
            return self._request(
                method,
                path,
                require_account=require_account,
                retry_auth=False,
                **kwargs,
            )
        if not response.ok:
            raise self._api_error(response)
        return self._json_object(response, "토스 API")

    @staticmethod
    def _json_object(response: requests.Response, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TossApiError(
                f"{context} 응답이 JSON 형식이 아닙니다",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise TossApiError(
                f"{context} 응답 형식이 올바르지 않습니다",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _api_error(response: requests.Response) -> TossApiError:
        code = None
        request_id = None
        message = f"토스 API 오류 (HTTP {response.status_code})"
        try:
            payload = response.json()
            error = payload.get("error", payload)
            code = error.get("code") or error.get("error")
            request_id = error.get("requestId")
            message = error.get("message") or error.get("error_description") or message
        except (ValueError, AttributeError):
            pass
        retryable = response.status_code in {408, 429, 500, 502, 503, 504}
        return TossApiError(
            message,
            status_code=response.status_code,
            code=code,
            request_id=request_id,
            retryable=retryable,
        )

    def get_prices(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Decimal]:
        if not symbols:
            return {}
        payload = self._request("GET", "/api/v1/prices", params={"symbols": ",".join(symbols)})
        return {
            str(item["symbol"]).upper(): Decimal(str(item["lastPrice"]))
            for item in payload.get("result", [])
        }

    def get_price(self, symbol: str) -> Decimal:
        prices = self.get_prices([symbol.upper()])
        if symbol.upper() not in prices:
            raise TossApiError(f"현재가 응답에 {symbol.upper()}이 없습니다")
        return prices[symbol.upper()]

    def get_candles(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        count: int = 200,
        adjusted: bool = True,
    ) -> list[dict[str, Any]]:
        if count < 1:
            return []
        remaining = count
        before: str | None = None
        seen_timestamps: set[str] = set()
        candles: list[dict[str, Any]] = []
        while remaining > 0:
            params: dict[str, Any] = {
                "symbol": symbol.upper(),
                "interval": interval,
                "count": min(200, remaining),
                "adjusted": str(adjusted).lower(),
            }
            if before:
                params["before"] = before
            payload = self._request("GET", "/api/v1/candles", params=params)
            result = payload.get("result", {})
            page = result.get("candles", [])
            for candle in page:
                timestamp = str(candle.get("timestamp"))
                if timestamp not in seen_timestamps:
                    candles.append(candle)
                    seen_timestamps.add(timestamp)
            remaining = count - len(candles)
            next_before = result.get("nextBefore")
            if not page or not next_before or next_before == before:
                break
            before = str(next_before)
        return candles[:count]

    def get_holdings(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        payload = self._request("GET", "/api/v1/holdings", require_account=True, params=params)
        return list(payload.get("result", {}).get("items", []))

    def get_buying_power(self, currency: str = "USD") -> Decimal:
        payload = self._request(
            "GET",
            "/api/v1/buying-power",
            require_account=True,
            params={"currency": currency.upper()},
        )
        return Decimal(str(payload.get("result", {}).get("cashBuyingPower", "0")))

    def get_market_calendar(self, market_date: str | None = None) -> dict[str, Any]:
        params = {"date": market_date} if market_date else None
        payload = self._request("GET", "/api/v1/market-calendar/US", params=params)
        return dict(payload.get("result", {}))

    def get_orderbook(self, symbol: str) -> dict[str, Any]:
        payload = self._request(
            "GET", "/api/v1/orderbook", params={"symbol": symbol.upper()}
        )
        return dict(payload.get("result", {}))

    def list_orders(
        self,
        *,
        status: str,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": status.upper(), "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        payload = self._request("GET", "/api/v1/orders", require_account=True, params=params)
        return list(payload.get("result", {}).get("orders", []))

    def get_order(self, order_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/v1/orders/{order_id}", require_account=True)
        return dict(payload.get("result", {}))

    def place_order(self, request: OrderRequest) -> OrderReceipt:
        if not CLIENT_ORDER_ID_PATTERN.fullmatch(request.client_order_id):
            raise ValueError("client_order_id는 36자 이하 영숫자, -, _만 사용할 수 있습니다")
        symbol = request.symbol.upper()
        side = request.side.upper()
        order_type = request.order_type.upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("지원하지 않는 종목 코드 형식입니다")
        if side not in ORDER_SIDES:
            raise ValueError("주문 방향은 BUY 또는 SELL이어야 합니다")
        if order_type not in ORDER_TYPES:
            raise ValueError("주문 유형은 LIMIT 또는 MARKET이어야 합니다")
        if request.quantity <= 0:
            raise ValueError("주문 수량은 양수여야 합니다")
        if order_type == "LIMIT" and (
            request.price is None or not request.price.is_finite() or request.price <= 0
        ):
            raise ValueError("지정가 주문에는 0보다 큰 유한 price가 필요합니다")
        payload: dict[str, Any] = {
            "clientOrderId": request.client_order_id,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "timeInForce": "DAY",
            "quantity": str(request.quantity),
        }
        if order_type == "LIMIT":
            payload["price"] = format(request.price, "f")
        response = self._request("POST", "/api/v1/orders", require_account=True, json=payload)
        result = response.get("result", {})
        return OrderReceipt(
            client_order_id=str(result.get("clientOrderId") or ""),
            broker_order_id=str(result["orderId"]),
            status="SUBMITTED",
            quantity=request.quantity,
            raw=dict(result),
        )

    def cancel_order(self, order_id: str) -> str:
        payload = self._request(
            "POST", f"/api/v1/orders/{order_id}/cancel", require_account=True, json={}
        )
        return str(payload.get("result", {}).get("orderId", order_id))


def receipt_from_order(order: dict[str, Any], client_order_id: str = "") -> OrderReceipt:
    execution = order.get("execution") or {}
    average = execution.get("averageFilledPrice")
    return OrderReceipt(
        client_order_id=client_order_id,
        broker_order_id=str(order.get("orderId", "")),
        status=str(order.get("status", "UNKNOWN")),
        quantity=int(Decimal(str(order.get("quantity", "0")))),
        filled_quantity=int(Decimal(str(execution.get("filledQuantity", "0")))),
        average_fill_price=Decimal(str(average)) if average is not None else None,
        raw=order,
    )
