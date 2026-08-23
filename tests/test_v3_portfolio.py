from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.core.models import OrderRequest
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def settings(tmp_path, mode: str = "dry_run") -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode=mode,
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


class StubPortfolioService(PortfolioService):
    def __init__(self, *args, target: pd.DataFrame, marked_equity=Decimal("50000"), **kwargs):
        self._target = target
        self._marked = marked_equity
        super().__init__(*args, **kwargs)

    def _calculate_target(self, completed):
        del completed
        return {}, self._target

    def _completed_marked_equity(self, raw, timestamp):
        del raw, timestamp
        return self._marked


def test_target_quantity_accounts_for_buy_fee():
    assert target_quantity(
        Decimal("50000"), Decimal("0.10"), Decimal("100"), Decimal("0.001")
    ) == 49


def test_v322_allocation_run_is_idempotent_and_creates_approval_signals(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    runtime = settings(tmp_path)
    order_manager = OrderManager(repository, broker, runtime)
    market_clock = MarketClock()
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    completed = market_clock.latest_completed_session(
        now, delay_minutes=config.scheduler.signal_delay_minutes
    )
    timestamp = pd.Timestamp(completed)
    target = pd.DataFrame(
        [
            {
                "trade_date": completed.isoformat(),
                "leverage": 1.5,
                "semiconductor_active": True,
                "jdss_tqqq_active": False,
                "jdss_soxl_active": False,
                "QQQ": 0.75,
                "TQQQ": 0.125,
                "SOXL": 0.125,
            }
        ],
        index=[timestamp],
    )
    service = StubPortfolioService(
        config,
        repository,
        broker,
        order_manager,
        object(),
        market_clock,
        trading_mode="dry_run",
        target=target,
    )

    result = service.run_allocation(now)

    assert result is not None
    assert result.trade_date == completed.isoformat()
    assert result.signals == ()
    assert repository.get_core_position("QQQ")["target_weight"] == "0.75"
    assert repository.get_core_position("QQQ")["target_qty"] == 0
    assert repository.get_core_position("TQQQ")["target_weight"] == "0.125"
    assert repository.get_core_position("SOXL")["target_weight"] == "0.125"
    assert repository.open_orders() == []
    assert service.run_allocation(now) is None

    next_session_closed = datetime(2026, 8, 3, 6, tzinfo=UTC)
    assert service.run_allocation(next_session_closed) is None
    assert repository.get_core_position("QQQ")["target_qty"] == 0
    assert repository.open_orders() == []

    next_session_premarket = datetime(2026, 8, 3, 12, tzinfo=UTC)
    execution = service.run_allocation(next_session_premarket)
    assert execution is not None
    assert len(execution.signals) == 3
    assert repository.get_core_position("QQQ")["target_qty"] == 74
    assert service.run_allocation(next_session_premarket) is None


def test_target_change_cancels_stale_core_orders_before_rebalancing(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("49500"),
    )
    manager = OrderManager(repository, broker, settings(tmp_path))
    market_clock = MarketClock()
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.10"),
        signal_trade_date=datetime(2026, 7, 31).date(),
    )
    pending_buy = OrderRequest(
        client_order_id="STALE-CORE-BUY",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=10,
        price=Decimal("99"),
        purpose="CORE_REBALANCE_BUY",
    )
    assert manager.submit(pending_buy, cycle_id=None).status == "PENDING"

    seed_id = "SOXL-SEED"
    assert repository.reserve_order(
        client_order_id=seed_id,
        signal_id=None,
        cycle_id=None,
        symbol="SOXL",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("50"),
        quantity=10,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        seed_id,
        status="FILLED",
        broker_order_id="DRY-SEED-SOXL",
        filled_qty=10,
        average_fill_price=Decimal("50"),
    )
    repository.apply_core_fill(seed_id)
    broker.holdings["SOXL"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("50"),
    }
    pending_sell = OrderRequest(
        client_order_id="STALE-CORE-SELL",
        symbol="SOXL",
        side="SELL",
        order_type="LIMIT",
        quantity=5,
        price=Decimal("55"),
        purpose="CORE_REBALANCE_SELL",
    )
    assert manager.submit(pending_sell, cycle_id=None).status == "PENDING"

    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    completed = market_clock.latest_completed_session(
        now, delay_minutes=config.scheduler.signal_delay_minutes
    )
    target = pd.DataFrame(
        [
            {
                "trade_date": completed.isoformat(),
                "leverage": 1.0,
                "semiconductor_active": False,
                "jdss_tqqq_active": False,
                "jdss_soxl_active": False,
                "QQQ": 1.0,
                "TQQQ": 0.0,
                "SOXL": 0.0,
            }
        ],
        index=[pd.Timestamp(completed)],
    )
    service = StubPortfolioService(
        config,
        repository,
        broker,
        manager,
        object(),
        market_clock,
        trading_mode="dry_run",
        target=target,
    )

    result = service.run_allocation(now)

    assert result is not None
    assert repository.get_order_by_client_id("STALE-CORE-BUY")["status"] == "CANCELED"
    assert repository.get_order_by_client_id("STALE-CORE-SELL")["status"] == "CANCELED"
    assert repository.get_core_position("SOXL")["qty"] == 10
    assert any("기존 CORE_REBALANCE_BUY 주문 취소" in event for event in result.events)
    assert any("기존 CORE_REBALANCE_SELL 주문 취소" in event for event in result.events)

    execution = service.run_allocation(datetime(2026, 8, 3, 12, tzinfo=UTC))
    assert execution is not None
    assert repository.get_core_position("SOXL")["qty"] == 0
    assert len(execution.signals) == 1


def test_v322_refuses_live_mode_before_any_data_or_order_access(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(buying_power=Decimal("50000"))
    service = StubPortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, settings(tmp_path, "live")),
        object(),
        MarketClock(),
        trading_mode="live",
        target=pd.DataFrame(),
    )

    with pytest.raises(RuntimeError, match="live 모드가 잠겨"):
        service.run_allocation(datetime(2026, 8, 1, 12, tzinfo=UTC))


def test_v322_portfolio_backtest_uses_hwm75_and_no_sgov_frame(config):
    index = pd.bdate_range("2010-01-04", "2026-08-05")
    base = pd.Series(range(len(index)), index=index, dtype=float) / 20 + 100
    frames = {
        "TQQQ": pd.DataFrame({"open": base * 1.2, "close": base * 1.2}),
        "SOXL": pd.DataFrame({"open": base * 0.8, "close": base * 0.8}),
        "QQQ": pd.DataFrame({"open": base, "close": base}),
        "SOXX": pd.DataFrame({"open": base * 1.01, "close": base * 1.01}),
    }
    booster_results = {
        symbol: BacktestResult(
            symbol=symbol,
            start_date=pd.Timestamp("2011-01-03").date(),
            end_date=index[-1].date(),
            strategy_version=config.version,
            config_version=config.config_version,
            slippage=0.001,
            metrics={},
            trades=(),
            signals=(),
            skipped_signals=(),
            closed_cycles=(),
            open_position={"quantity": 0},
            equity_curve=pd.Series(1000.0, index=index),
        )
        for symbol in config.enabled_symbols
    }

    result = PortfolioBacktestEngine(config).run(
        frames,
        booster_results,
        start="2011-01-03",
        end=index[-1].date(),
        slippage=0.001,
    )

    assert result.trades
    assert {trade["component"] for trade in result.trades} == {"allocation"}
    assert result.metrics["initial_risk_budget"] == 50000.0
    assert result.metrics["hwm_reinvestment_fraction"] == 0.75
    assert result.metrics["profit_reinvestment"] == "HWM75_CONTROLLED"
    assert result.metrics["maximum_sizing_base"] >= 50000.0
    assert result.metrics["idle_cash_enabled"] is False
    assert result.metrics["idle_cash_income"] == 0.0
    assert result.metrics["initial_onboarding_enabled"] is True
    assert result.metrics["initial_onboarding_fractions_pct"] == [50.0, 75.0, 100.0]
    assert result.metrics["initial_onboarding_minimum_sessions"] == 3
    assert result.metrics["final_cash"] + result.metrics["final_holdings_value"] == pytest.approx(
        result.metrics["final_equity"], abs=0.02
    )
    assert set(result.metrics["final_holdings"]) == {"QQQ", "TQQQ", "SOXL"}
    assert result.metrics["total_fees"] > 0


def test_portfolio_backtest_stages_initial_entry_by_requested_session(config):
    engine = PortfolioBacktestEngine(config)
    full_target = {"QQQ": 0.75, "TQQQ": 0.125, "SOXL": 0.125}

    expected_fractions = (0.50, 0.50, 0.50, 0.75, 0.75, 0.75, 1.00)
    for execution_index, fraction in enumerate(expected_fractions):
        staged = engine._apply_initial_onboarding(
            full_target,
            execution_index=execution_index,
        )
        assert staged == pytest.approx(
            {symbol: weight * fraction for symbol, weight in full_target.items()}
        )


def test_default_sizing_hook_preserves_hwm75_contract(config):
    engine = PortfolioBacktestEngine(config)
    assert engine._sizing_equity(
        initial_capital=50_000.0,
        high_water=60_000.0,
        open_equity=80_000.0,
    ) == pytest.approx(57_500.0)
    assert engine._sizing_equity(
        initial_capital=50_000.0,
        high_water=100_000.0,
        open_equity=70_000.0,
    ) == pytest.approx(70_000.0)
