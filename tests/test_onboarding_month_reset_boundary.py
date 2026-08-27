from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.initial_onboarding_portfolio import (
    ONBOARDING_STAGE_FILLED_KEY,
    InitialOnboardingPortfolioService,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import TARGET_QTY_GENERATION_KEY
from jd_holdings.core.initial_onboarding import STATUS_ACTIVE, scaled_target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


class StubInitialOnboardingService(InitialOnboardingPortfolioService):
    def __init__(self, *args, target: pd.DataFrame, **kwargs):
        self._target = target
        super().__init__(*args, **kwargs)

    def _calculate_target(self, completed):
        del completed
        return {}, self._target

    def _completed_marked_equity(self, raw, timestamp):
        del raw, timestamp
        return Decimal("50000")


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


def _service(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    market_clock = MarketClock()
    completed = date(2026, 8, 31)
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
        index=[pd.Timestamp(completed)],
    )
    service = StubInitialOnboardingService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
        object(),
        market_clock,
        trading_mode="dry_run",
        target=target,
    )
    return service, repository


def _seed_targets(repository: SQLiteRepository, signal_date: date) -> dict[str, int]:
    full = {"QQQ": 74, "TQQQ": 62, "SOXL": 124}
    weights = {
        "QQQ": Decimal("0.75"),
        "TQQQ": Decimal("0.125"),
        "SOXL": Decimal("0.125"),
    }
    for symbol, quantity in full.items():
        repository.set_core_target(
            symbol,
            active=True,
            target_weight=weights[symbol],
            signal_trade_date=signal_date,
            target_qty=quantity,
        )
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, signal_date.isoformat())
    return full


def _set_quantities(repository: SQLiteRepository, quantities: dict[str, int]) -> None:
    with repository.transaction() as connection:
        for symbol, quantity in quantities.items():
            connection.execute(
                "UPDATE core_positions SET qty = ? WHERE symbol = ?",
                (quantity, symbol),
            )


def test_month_reset_target_increase_reopens_stage_fill_clock(tmp_path, config):
    service, repository = _service(tmp_path, config)
    full = _seed_targets(repository, date(2026, 8, 31))

    snapshot = service.start_onboarding(datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert snapshot["status"] == STATUS_ACTIVE
    assert snapshot["stage"] == 1
    assert snapshot["fraction"] == Decimal("0.5")

    stage1 = {
        symbol: scaled_target_quantity(quantity, Decimal("0.5"))
        for symbol, quantity in full.items()
    }
    _set_quantities(repository, stage1)
    service._refresh_onboarding_progress(date(2026, 9, 1))
    assert repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) == "2026-09-01"

    qqq = repository.get_core_position("QQQ")
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("0.80"),
        signal_trade_date=date(2026, 9, 1),
        target_qty=100,
    )
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-01")

    service._refresh_onboarding_progress(date(2026, 9, 2))

    assert service.onboarding_status() == STATUS_ACTIVE
    assert service.onboarding_stage() == 1
    assert repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) == ""
    assert int(qqq["qty"]) == stage1["QQQ"]
    assert service._effective_target_quantity(repository.get_core_position("QQQ")) == 50

    refreshed_targets = {
        core["symbol"]: service._effective_target_quantity(core)
        for core in repository.core_positions()
    }
    _set_quantities(repository, refreshed_targets)
    service._refresh_onboarding_progress(date(2026, 9, 2))
    assert repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) == "2026-09-02"

    with pytest.raises(RuntimeError, match="기다려야"):
        service.advance_onboarding(datetime(2026, 9, 8, 12, tzinfo=UTC))

    stage2 = service.advance_onboarding(datetime(2026, 9, 9, 12, tzinfo=UTC))
    assert stage2["stage"] == 2
    assert stage2["fraction"] == Decimal("0.75")
