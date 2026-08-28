from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.operational_safety import (
    OPERATOR_BUY_HALT_KEY,
    OperatorSafetyService,
)
from jd_holdings.core.enums import PositionState


class CleanReconciliation:
    def run(self):
        return {}


class DirtyReconciliation:
    def run(self):
        return {"SOXL": ["BROKER_DB_QTY_MISMATCH"]}


def _enter_safe_mode(repository, symbol: str) -> None:
    position = repository.get_position(symbol)
    repository.transition_position(
        symbol,
        expected_state=position.state,
        new_state=PositionState.SAFE_MODE,
        reason_code="TEST_RECONCILIATION_FAILURE",
        expected_version=position.version,
    )


def test_resume_recovers_empty_sticky_safe_mode_after_clean_reconciliation(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "resume-recovery.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value("v322_portfolio_safe_mode", "1")
    _enter_safe_mode(repository, "SOXL")

    result = OperatorSafetyService(repository, broker, CleanReconciliation()).resume()

    assert result["resumed"] is True
    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "0"
    assert repository.get_system_value("v322_portfolio_safe_mode") == "0"
    assert repository.get_position("SOXL").state == PositionState.EMPTY


def test_resume_never_clears_safe_mode_when_reconciliation_is_still_dirty(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "resume-dirty.db", config)
    broker = DryRunBroker({"SOXL": Decimal("50")})
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value("v322_portfolio_safe_mode", "1")
    _enter_safe_mode(repository, "SOXL")

    with pytest.raises(RuntimeError, match="정합성 오류"):
        OperatorSafetyService(repository, broker, DirtyReconciliation()).resume()

    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"
    assert repository.get_position("SOXL").state == PositionState.SAFE_MODE


def test_resume_refuses_to_auto_recover_nonempty_safe_mode(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "resume-active.db", config)
    broker = DryRunBroker({"SOXL": Decimal("50")})
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE positions SET qty = 1, state = 'SAFE_MODE' WHERE symbol = 'SOXL'"
        )

    with pytest.raises(RuntimeError, match="실제 보유/TP 상태"):
        OperatorSafetyService(repository, broker, CleanReconciliation()).resume()

    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
    assert repository.get_position("SOXL").state == PositionState.SAFE_MODE


def test_resume_recovers_all_empty_enabled_symbol_safe_modes_atomically(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "resume-multi.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100"), "SOXL": Decimal("50")})
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value("v322_portfolio_safe_mode", "1")
    for symbol in config.enabled_symbols:
        _enter_safe_mode(repository, symbol)

    result = OperatorSafetyService(repository, broker, CleanReconciliation()).resume()

    assert result["resumed"] is True
    assert all(
        repository.get_position(symbol).state == PositionState.EMPTY
        for symbol in config.enabled_symbols
    )
    assert repository.get_system_value("v322_portfolio_safe_mode") == "0"