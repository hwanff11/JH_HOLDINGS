from decimal import Decimal

import pytest

from jd_holdings.core.models import OrderRequest
from jd_holdings.core.twin_core import target_quantity


def _request(quantity) -> OrderRequest:
    return OrderRequest(
        client_order_id="WHOLE-SHARE-TEST",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=quantity,
        price=Decimal("50.00"),
        purpose="CORE_REBALANCE_BUY",
    )


def test_target_quantity_rounds_down_to_whole_shares():
    quantity = target_quantity(
        Decimal("1000"),
        Decimal("0.50"),
        Decimal("130"),
        Decimal("0.001"),
    )
    assert quantity == 3
    assert isinstance(quantity, int)


def test_order_request_accepts_positive_whole_share_quantity():
    request = _request(3)
    assert request.quantity == 3
    assert isinstance(request.quantity, int)


@pytest.mark.parametrize(
    "quantity",
    [Decimal("1.5"), 1.5, Decimal("2.0"), True],
)
def test_order_request_rejects_fractional_or_non_integer_quantity(quantity):
    with pytest.raises(ValueError, match="정수 주"):
        _request(quantity)


@pytest.mark.parametrize("quantity", [0, -1])
def test_order_request_rejects_non_positive_quantity(quantity):
    with pytest.raises(ValueError, match="1주 이상"):
        _request(quantity)
