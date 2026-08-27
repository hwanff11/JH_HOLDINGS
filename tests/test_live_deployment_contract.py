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


def test_live_portfolio_adapter_never_mutates_mode_to_dry_run():
    source = (ROOT / "src/jd_holdings/application/live_runtime_services.py").read_text(
        encoding="utf-8"
    )
    assert 'self.trading_mode = "dry_run"' not in source
    assert "_explicit_live_runtime_enabled" in source


def test_live_commissioning_is_fresh_db_first_and_buy_halt_first():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in script
    assert "StrictHostKeyChecking=yes" in script
    assert 'live_db="$shared_dir/data/jdss-live.db"' in script
    assert "live-preflight --arm-buy-halt" in script
    assert "LIVE_COMMISSIONED" not in script
    assert "ENABLE_JDSS_LIVE_ORDERS" in script
    assert "live-release-check" in script
    assert "toss-smoke" in script
    assert "place_order" not in script
    assert "operator_buy_halt" in script
    assert "live_commissioned" in script
    assert "mv \"$candidate_db\" \"$live_db\"" in script
    assert "LIVE-ARMED commissioning 실패: dry-run 환경으로 복구" in script


def test_live_commissioning_loads_toss_credentials_before_preflight():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")
    first_env_source = script.index('source "$env_file"')
    preflight = script.index("live-preflight --arm-buy-halt")
    assert first_env_source < preflight
    assert ': "${TOSS_APP_KEY:?Oracle .env에 TOSS_APP_KEY가 필요합니다}"' in script
    assert ': "${TOSS_APP_SECRET:?Oracle .env에 TOSS_APP_SECRET가 필요합니다}"' in script
    assert script.rfind("set -a", 0, first_env_source) != -1
    assert script.find("set +a", first_env_source, preflight) != -1


def test_live_commissioning_updates_installed_unit_not_default_template():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")
    unit = (ROOT / "systemd/jh_holdings_bot.service.template").read_text(encoding="utf-8")
    assert "Environment=JDSS_DB_PATH=__TARGET_DIR__/shared/data/jdss.db" in unit
    assert "sudo sed -i" in script
    assert "Environment=JDSS_DB_PATH=$live_db" in script
    assert "unit_backup" in script
    assert 'sudo cp "$unit_backup" "$service_unit"' in script


def test_live_commissioning_rollback_secret_copies_are_ephemeral():
    script = (ROOT / "commission_live_armed.sh").read_text(encoding="utf-8")
    assert 'env_backup="/tmp/jdss-env-before-live-armed-' in script
    assert 'unit_backup="/tmp/${service_name}-before-live-armed-' in script
    assert "shared/backups/env-before-live-armed" not in script
    assert "cleanup_temp" in script
    assert 'rm -f "$env_backup"' in script
    assert 'sudo rm -f "$unit_backup"' in script


def test_live_update_workflow_is_owner_only_latest_main_and_test_gated():
    workflow = (ROOT / ".github/workflows/deploy-oracle-live-armed.yml").read_text(encoding="utf-8")
    assert "ref: main" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "[deploy-oracle-live-armed]" in workflow
    assert "tests/test_live_runtime_safety.py" in workflow
    assert "tests/test_live_deployment_contract.py" in workflow
    assert "bash ./deploy_live_armed.sh" in workflow
    assert "gh issue comment" in workflow


def test_hourly_health_watch_understands_live_ledger():
    workflow = (ROOT / ".github/workflows/oracle-health-watch.yml").read_text(encoding="utf-8")
    assert "dry_run|live" not in workflow
    assert "JDSS_TRADING_MODE" in workflow
    assert "jdss-live.db" in workflow
    assert "live_commissioned" in workflow
    assert "operator_buy_halt" in workflow
    assert "PRAGMA quick_check" in workflow
    assert "toss-smoke" in workflow
