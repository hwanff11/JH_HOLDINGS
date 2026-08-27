from __future__ import annotations

import inspect
from decimal import Decimal

import pandas as pd
import pytest

import jd_holdings.application.portfolio_service as live_module
import jd_holdings.backtest.portfolio_engine as backtest_module
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.core.initial_onboarding import InitialOnboardingPolicy, scaled_target_quantity
from jd_holdings.core.twin_core import target_quantity


@pytest.mark.parametrize(
    ("equity", "weight", "price", "fee"),
    [
        ("50000", "0.75", "500", "0.001"),
        ("81132.74", "0.125", "70.38", "0.001"),
        ("81132.74", "0.125", "131.64", "0.001"),
    ],
)
def test_backtest_and_live_share_full_target_quantity_contract(
    equity, weight, price, fee
):
    """동일 가격/자본/비중이면 백테스트와 실운영 목표수량이 같아야 한다."""
    expected = target_quantity(
        Decimal(equity), Decimal(weight), Decimal(price), Decimal(fee)
    )
    quantities = {symbol: 0 for symbol in ("QQQ", "TQQQ", "SOXL")}
    prices = {"QQQ": float(price), "TQQQ": 1_000_000.0, "SOXL": 1_000_000.0}
    trades: list[dict] = []

    PortfolioBacktestEngine._rebalance(
        {"QQQ": float(weight)},
        quantities,
        prices,
        float(equity),
        sizing_equity=float(equity),
        timestamp=pd.Timestamp("2026-08-26"),
        buy_fee=float(fee),
        sell_fee=float(fee),
        slippage=0.0,
        trades=trades,
    )

    assert quantities["QQQ"] == expected


def test_backtest_and_live_use_same_v322_target_replay_function():
    """시장판단/목표비중 계산 함수가 백테스트와 운영에서 갈라지는 것을 막는다."""
    assert live_module.replay_targets is backtest_module.replay_targets


def test_backtest_and_live_keep_same_initial_entry_stage_contract(config):
    """최초진입 50→75→100 및 3거래일 계약의 공통 정책을 보호한다."""
    policy = InitialOnboardingPolicy.from_config(config)
    engine = PortfolioBacktestEngine(config)

    assert policy.cumulative_fractions == (
        Decimal("0.50"),
        Decimal("0.75"),
        Decimal("1.00"),
    )
    assert policy.minimum_sessions_between_stages == 3
    assert [
        scaled_target_quantity(100, policy.fraction_for_stage(stage))
        for stage in range(1, policy.total_stages + 1)
    ] == [50, 75, 100]

    full_weights = {"QQQ": 0.75, "TQQQ": 0.125, "SOXL": 0.125}
    for execution_index, fraction in ((0, 0.50), (3, 0.75), (6, 1.00)):
        assert engine._apply_initial_onboarding(
            full_weights, execution_index=execution_index
        ) == pytest.approx(
            {symbol: weight * fraction for symbol, weight in full_weights.items()}
        )


def test_execution_order_contract_stays_sell_first_and_next_session():
    """운영은 신호일 당일 주문이 아니라 다음 세션에서 SELL→검증→BUY 순서여야 한다."""
    source = inspect.getsource(live_module.PortfolioService.run_allocation)

    next_session_gate = source.index("next_session_has_started")
    sell_phase = source.index("allow_buy=False")
    post_sell_reconciliation = source.index("post_sell_mismatches")
    buy_phase = source.index("allow_buy=True")

    assert next_session_gate < sell_phase < post_sell_reconciliation < buy_phase


def test_backtest_keeps_completed_signal_then_next_session_execution():
    """백테스트도 현재 완결봉 목표를 다음 반복(다음 거래세션)에 pending으로 넘겨야 한다."""
    source = inspect.getsource(backtest_module.PortfolioBacktestEngine.run)

    execute_pending = source.index("if pending != current")
    update_from_completed_bar = source.index("pending = self._apply_initial_onboarding", execute_pending)

    assert execute_pending < update_from_completed_bar
