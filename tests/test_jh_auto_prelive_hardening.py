from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.jh_auto_guard import (
    mark_repository_as_jh_auto_live,
    require_live_auto_buy_contract,
)
from jd_holdings.automation.prelive_hardening import HardenedProductionJHAutoService
from jd_holdings.automation.service import (
    AUTO_ENABLED_KEY,
    AUTO_QUARANTINE_KEY,
    AUTO_STATE_KEY,
    TARGET_QTY_GENERATION_KEY,
)


class _CleanReconciliation:
    def run(self):
        return {}


class _Clock:
    def latest_completed_session(self, *args, **kwargs):
        return date(2026, 9, 2)

    def classify_session(self, now=None):
        return "regular"

    def completed_sessions_since(self, start, now=None):
        return 3


def _service(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "prelive-hardening.db", config)
    broker = DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
        },
        buying_power=Decimal("1000000"),
    )
    service = HardenedProductionJHAutoService(
        config,
        repository,
        broker,
        _CleanReconciliation(),
        _Clock(),
    )
    mark_repository_as_jh_auto_live(repository)
    return repository, broker, service


def _launch(repository, service, *, capital="50000", ratio="20"):
    service.set_base_capital(capital)
    service.set_ratio_percent(ratio)
    service.authorize_launch()
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value("operator_buy_halt", "0")


def test_live_guard_never_falls_back_to_reference_50k_when_auto_key_is_missing(
    tmp_path, config
):
    repository, _broker, service = _service(tmp_path, config)
    _launch(repository, service)
    with repository.transaction() as connection:
        connection.execute("DELETE FROM system_state WHERE key = ?", (AUTO_ENABLED_KEY,))

    with pytest.raises(RuntimeError, match="활성상태"):
        require_live_auto_buy_contract(repository, require_regular_session=False)


def test_live_guard_independently_recomputes_operator_capital_contract(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _launch(repository, service, capital="70000", ratio="30")

    settings = service.settings()
    assert settings.target_principal == Decimal("21000.00")
    assert settings.effective_principal == Decimal("10500.00")
    require_live_auto_buy_contract(repository, require_regular_session=False)

    repository.set_system_value("jh_auto_target_principal", "50000")
    with pytest.raises(RuntimeError, match="총 투자금액×자동운용비율"):
        require_live_auto_buy_contract(repository, require_regular_session=False)


def test_live_guard_rejects_effective_principal_above_operator_target(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _launch(repository, service)
    repository.set_system_value("jh_auto_effective_principal", "15000")

    with pytest.raises(RuntimeError, match="허용원금"):
        require_live_auto_buy_contract(repository, require_regular_session=False)


def test_live_capital_change_is_buy_blocked_until_next_clean_safety_cycle(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _launch(repository, service)

    updated = service.set_ratio_percent("30")

    assert updated.target_principal == Decimal("15000.00")
    assert repository.get_system_value("operator_buy_halt") == "1"
    assert repository.get_system_value(AUTO_QUARANTINE_KEY) == "1"
    assert repository.get_system_value(AUTO_STATE_KEY) == "AUTO_QUARANTINE"


def test_initial_ramp_cannot_promote_without_a_real_auto_fill(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _launch(repository, service, capital="100", ratio="1")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")
    for symbol in ("QQQ", "TQQQ", "SOXL"):
        repository.set_core_target(
            symbol,
            active=False,
            target_weight=Decimal("0"),
            signal_trade_date=date(2026, 9, 2),
            target_qty=0,
        )

    assert not service.advance_ramp_if_ready()
    assert service.settings().ramp_stage == 1


def test_live_workflow_rechecks_jh_auto_suite_and_owner_gate():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy-oracle-live-armed.yml").read_text(
        encoding="utf-8"
    )
    assert "github.actor == github.repository_owner" in workflow
    assert "tests/test_jh_auto.py" in workflow
    assert "tests/test_jh_auto_prelive_hardening.py" in workflow
    assert "tests/test_live_entrypoints.py" in workflow
