from __future__ import annotations

from dataclasses import replace
from datetime import time
from decimal import Decimal

import pytest

from jd_holdings.config import ConfigError, PositionConfig, validate_config
from jd_holdings.core.v322_allocation import V322Policy


def test_default_config_is_valid_and_complete(config):
    assert config.version == "JDSS-3.2.2-RS6M-ONEWAY-HWM75"
    assert config.config_version == "3.2.2"
    assert config.enabled_symbols == ("TQQQ", "SOXL")
    assert config.position.stage_weights == (
        Decimal("0.40"),
        Decimal("0.30"),
        Decimal("0.20"),
    )
    assert config.position.cumulative_weights == (
        Decimal("0.40"),
        Decimal("0.70"),
        Decimal("0.90"),
    )
    assert config.global_.stop_loss_enabled is False
    assert config.global_.approval_required is True
    assert config.global_.entry_score == 55
    assert config.global_.minimum_reversal_score == 5
    assert config.scoring["grades"] == {"S": 90, "A": 82, "B": 72, "WATCH": 50}
    assert config.scoring["calibration"]["exponents"] == {
        "regime": 1.0,
        "oversold": 0.45,
        "reversal": 0.55,
        "volume": 0.65,
        "atr": 0.9,
    }
    assert config.market_regime["soxl_sector_guard"]["enabled"] is True
    assert config.market_regime["soxl_sector_guard"]["blocked_stages"] == [1, 3]
    assert config.rebuy.enabled is False
    assert config.take_profit.use_atr is False
    assert config.take_profit.tp1_base == Decimal("0.04")
    assert config.take_profit.tp1_fraction == Decimal("0.30")
    assert config.take_profit.tp2_base == Decimal("0.10")
    assert config.take_profit.remainder_exit.enabled is False
    assert config.idle_cash.enabled is False
    assert config.idle_cash.symbol == "SGOV"
    assert config.idle_cash.cash_buffer == Decimal("0")
    assert config.portfolio.enabled is True
    assert config.portfolio.total_capital == Decimal("50000")
    assert config.portfolio.live_enabled is False
    assert config.global_.trading_sessions == {
        "regular": True,
        "after_hours": True,
        "pre_market": True,
    }
    assert config.portfolio.rebalance_tolerance_weight == Decimal("0")
    assert config.portfolio.core_underlyings == {
        "QQQ": "QQQ",
        "TQQQ": "QQQ",
        "SOXL": "SOXX",
    }
    assert config.global_.capital_per_symbol == Decimal("20000")
    assert config.scheduler.daily_analysis_time_kst == time(7, 0)
    assert tuple(config.additional_entry.stages) == (2, 3)

    policy = V322Policy.from_config(config)
    assert policy.initial_capital == Decimal("50000")
    assert policy.hwm_reinvestment_fraction == Decimal("0.75")
    assert policy.volatility_brake == pytest.approx(0.30)
    assert policy.rs_benchmark == "SOXX"
    assert policy.rs_lookback == 126
    assert policy.rs_sleeve_fraction == pytest.approx(0.50)
    assert policy.jdss_overlay_weight == pytest.approx(0.05)


def test_invalid_stage_weights_are_rejected(config):
    broken = replace(
        config,
        position=PositionConfig(
            stage_weights=(Decimal("0.5"), Decimal("0.5")),
            cumulative_weights=(Decimal("0.5"), Decimal("0.9")),
        ),
    )
    with pytest.raises(ConfigError):
        validate_config(broken)


def test_invalid_calibration_exponent_is_rejected(config):
    scoring = {
        **config.scoring,
        "calibration": {
            **config.scoring["calibration"],
            "exponents": {
                **config.scoring["calibration"]["exponents"],
                "volume": 0,
            },
        },
    }
    with pytest.raises(ConfigError, match="volume 보정 지수"):
        validate_config(replace(config, scoring=scoring))


def test_v3_live_flag_is_rejected(config):
    with pytest.raises(ConfigError, match="live_enabled"):
        validate_config(
            replace(config, portfolio=replace(config.portfolio, live_enabled=True))
        )
