from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_runtime_is_explicit_and_default_strategy_lock_stays_closed():
    strategy = (ROOT / "strategy.yaml").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = (ROOT / "src/jd_holdings/runtime.py").read_text(encoding="utf-8")
    live_bot = (ROOT / "src/jd_holdings/live_bot.py").read_text(encoding="utf-8")

    assert "live_enabled: false" in strategy
    assert 'jdss-bot = "jd_holdings.runtime:main"' in project
    assert 'settings.trading_mode == "live"' in runtime
    assert "arm_live_startup_buy_halt(repository)" in live_bot
    assert "broker = TossClient()" in live_bot


def test_live_commissioning_is_fresh_db_first_and_buy_halt_first():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert "StrictHostKeyChecking=yes" in script
    assert 'live_db="$shared_dir/data/jdss-live.db"' in script
    assert "live-preflight --arm-buy-halt" in script
    assert "LIVE_COMMISSIONED" not in script  # no bypass via environment variable
    assert "ENABLE_JDSS_LIVE_ORDERS" in script
    assert "live-release-check" in script
    assert "toss-smoke" in script
    assert "place_order" not in script
    assert "operator_buy_halt" in script
    assert "live_commissioned" in script
    assert "mv \"$candidate_db\" \"$live_db\"" in script
    assert "LIVE-ARMED commissioning 실패: dry-run 환경으로 복구" in script


def test_live_commissioning_updates_installed_unit_not_default_template():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")
    unit = (ROOT / "systemd/jh_holdings_bot.service.template").read_text(encoding="utf-8")

    assert "Environment=JDSS_DB_PATH=__TARGET_DIR__/shared/data/jdss.db" in unit
    assert "sudo sed -i" in script
    assert "Environment=JDSS_DB_PATH=$live_db" in script
    assert "unit_backup" in script
    assert 'sudo cp "$unit_backup" "$service_unit"' in script


def test_live_commissioning_workflow_is_owner_only_latest_main_and_test_gated():
    workflow = (ROOT / ".github/workflows/commission-oracle-live-armed.yml").read_text(
        encoding="utf-8"
    )

    assert "ref: main" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "[commission-oracle-live-armed]" in workflow
    assert "tests/test_live_runtime_safety.py" in workflow
    assert "tests/test_live_commissioning.py" in workflow
    assert "bash ./deploy.sh" in workflow
    assert "bash ./commission_live_armed.sh" in workflow
    assert "live_enabled: false" in workflow
    assert "gh issue comment" in workflow


def test_hourly_health_watch_understands_live_ledger():
    workflow = (ROOT / ".github/workflows/oracle-health-watch.yml").read_text(
        encoding="utf-8"
    )

    assert "dry_run|live" not in workflow  # explicit mode branches are easier to audit
    assert "JDSS_TRADING_MODE" in workflow
    assert "jdss-live.db" in workflow
    assert "live_commissioned" in workflow
    assert "operator_buy_halt" in workflow
    assert "PRAGMA quick_check" in workflow
    assert "toss-smoke" in workflow


def test_weekly_runtime_verifier_never_auto_restarts_live():
    workflow = (ROOT / ".github/workflows/verify-oracle-v322-runtime.yml").read_text(
        encoding="utf-8"
    )

    assert "Detect Oracle runtime mode" in workflow
    assert "PASS_LIVE_NO_RESTART" in workflow
    assert "steps.runtime.outputs.mode == 'dry_run'" in workflow
    assert "Restart only during closed market and verify recovery" in workflow
    assert "jdss-live.db" in workflow
