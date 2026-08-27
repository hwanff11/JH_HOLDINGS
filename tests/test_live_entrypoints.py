from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jd_holdings import live_bot, runtime
from jd_holdings.settings import LIVE_CONFIRMATION_PHRASE, RuntimeSettings


def _settings(tmp_path: Path, *, mode: str = "live", confirmation: str | None = None):
    return RuntimeSettings(
        trading_mode=mode,
        live_confirmation=(
            LIVE_CONFIRMATION_PHRASE if confirmation is None and mode == "live" else confirmation or ""
        ),
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "jdss-live.db",
        log_path=tmp_path / "live.log",
        cache_path=tmp_path / "cache",
        config_path=tmp_path / "strategy.yaml",
    )


def _config(*, portfolio_enabled: bool = True):
    return SimpleNamespace(portfolio=SimpleNamespace(enabled=portfolio_enabled))


def test_runtime_routes_dry_run_to_default_bot(monkeypatch, tmp_path):
    called: list[str] = []
    monkeypatch.setattr(runtime, "load_runtime_settings", lambda: _settings(tmp_path, mode="dry_run"))

    import jd_holdings.bot as default_bot

    monkeypatch.setattr(default_bot, "main", lambda: called.append("dry_run"))
    runtime.main()

    assert called == ["dry_run"]


def test_runtime_routes_live_to_live_bot(monkeypatch, tmp_path):
    called: list[str] = []
    monkeypatch.setattr(runtime, "load_runtime_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(live_bot, "main", lambda: called.append("live"))

    runtime.main()

    assert called == ["live"]


def test_live_bot_rejects_non_live_mode_before_any_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(live_bot, "load_runtime_settings", lambda: _settings(tmp_path, mode="dry_run"))
    monkeypatch.setattr(
        live_bot,
        "load_config",
        lambda *_args, **_kwargs: pytest.fail("config must not load for non-live runtime"),
    )

    with pytest.raises(RuntimeError, match="live runtime entrypoint"):
        live_bot.main()


def test_live_bot_rejects_bad_confirmation_before_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        live_bot,
        "load_runtime_settings",
        lambda: _settings(tmp_path, confirmation="WRONG"),
    )
    monkeypatch.setattr(
        live_bot,
        "load_config",
        lambda *_args, **_kwargs: pytest.fail("config must not load before live confirmation"),
    )

    with pytest.raises(PermissionError, match="실주문 잠금"):
        live_bot.main()


def test_live_bot_rejects_disabled_portfolio_before_broker(monkeypatch, tmp_path):
    monkeypatch.setattr(live_bot, "load_runtime_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(live_bot, "load_config", lambda _path: _config(portfolio_enabled=False))
    monkeypatch.setattr(
        live_bot,
        "TossClient",
        lambda: pytest.fail("broker must not be created when portfolio is disabled"),
    )

    with pytest.raises(RuntimeError, match="portfolio가 비활성화"):
        live_bot.main()


def test_live_bot_arms_buy_halt_before_broker_and_starts_app(monkeypatch, tmp_path):
    sequence: list[str] = []
    settings = _settings(tmp_path)
    config = _config()
    repository = SimpleNamespace()
    broker = SimpleNamespace()
    reconciliation = SimpleNamespace(run=lambda: {})
    app = SimpleNamespace(run=lambda: sequence.append("app.run"))

    monkeypatch.setattr(live_bot, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(live_bot, "load_config", lambda _path: config)
    monkeypatch.setattr(live_bot, "configure_logging", lambda _path: sequence.append("logging"))
    monkeypatch.setattr(live_bot, "SQLiteRepository", lambda *_args: repository)
    monkeypatch.setattr(
        live_bot,
        "arm_live_startup_buy_halt",
        lambda repo: sequence.append("buy_halt") if repo is repository else None,
    )
    monkeypatch.setattr(live_bot, "recover_unapplied_core_fills", lambda _repo: [])
    monkeypatch.setattr(live_bot, "YFinanceDataSource", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "MarketClock", lambda: SimpleNamespace())

    def make_broker():
        sequence.append("broker")
        return broker

    monkeypatch.setattr(live_bot, "TossClient", make_broker)
    monkeypatch.setattr(live_bot, "OrderManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "PositionManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "TakeProfitManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "LiveAllocationTradingService", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "OrderMonitor", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        live_bot,
        "HardenedLiveInitialOnboardingPortfolioService",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(live_bot, "ReconciliationService", lambda *_args: reconciliation)
    monkeypatch.setattr(live_bot, "AnalysisService", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        live_bot,
        "HardenedOperationalSafetyTelegramBotApp",
        lambda *_args: app,
    )

    live_bot.main()

    assert sequence.index("buy_halt") < sequence.index("broker")
    assert sequence[-1] == "app.run"


def test_live_bot_notifies_startup_reconciliation_mismatch(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    config = _config()
    repository = SimpleNamespace()
    broker = SimpleNamespace()
    mismatch = {"TQQQ": ["BROKER_DB_QTY_MISMATCH"]}
    notices: list[object] = []
    app = SimpleNamespace(
        notify_startup_reconciliation=lambda value: notices.append(value),
        run=lambda: notices.append("run"),
    )

    monkeypatch.setattr(live_bot, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(live_bot, "load_config", lambda _path: config)
    monkeypatch.setattr(live_bot, "configure_logging", lambda _path: None)
    monkeypatch.setattr(live_bot, "SQLiteRepository", lambda *_args: repository)
    monkeypatch.setattr(live_bot, "arm_live_startup_buy_halt", lambda _repo: None)
    monkeypatch.setattr(live_bot, "recover_unapplied_core_fills", lambda _repo: [])
    monkeypatch.setattr(live_bot, "YFinanceDataSource", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "MarketClock", lambda: SimpleNamespace())
    monkeypatch.setattr(live_bot, "TossClient", lambda: broker)
    monkeypatch.setattr(live_bot, "OrderManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "PositionManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "TakeProfitManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "LiveAllocationTradingService", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(live_bot, "OrderMonitor", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        live_bot,
        "HardenedLiveInitialOnboardingPortfolioService",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        live_bot,
        "ReconciliationService",
        lambda *_args: SimpleNamespace(run=lambda: mismatch),
    )
    monkeypatch.setattr(live_bot, "AnalysisService", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        live_bot,
        "HardenedOperationalSafetyTelegramBotApp",
        lambda *_args: app,
    )

    live_bot.main()

    assert notices == [mismatch, "run"]
