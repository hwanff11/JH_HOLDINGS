from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_update_requires_existing_commissioned_halted_runtime():
    script = (ROOT / "deploy_live_armed.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert "StrictHostKeyChecking=yes" in script
    assert 'live_db="$shared_dir/data/jdss-live.db"' in script
    assert 'test "${JDSS_TRADING_MODE:-}" = "live"' in script
    assert 'test "${JDSS_LIVE_CONFIRMATION:-}" = "ENABLE_JDSS_LIVE_ORDERS"' in script
    assert 'test "${JDSS_DB_PATH:-}" = "$live_db"' in script
    assert 'values.get("live_commissioned") == "1"' in script
    assert 'values.get("operator_buy_halt") == "1"' in script
    assert "live-release-check" in script
    assert "place_order" not in script


def test_live_update_preserves_environment_and_existing_live_ledger():
    script = (ROOT / "deploy_live_armed.sh").read_text(encoding="utf-8")

    assert "JDSS_TRADING_MODE=dry_run" not in script
    assert 'sed -i \'/^JDSS_TRADING_MODE=' not in script
    assert 'candidate_db=' not in script
    assert 'mv "$candidate_db" "$live_db"' not in script
    assert 'Environment=JDSS_DB_PATH=$live_db' in script
    assert 'backup_path="$shared_dir/backups/jdss-live-' in script
    assert "source.backup(target)" in script
    assert "LIVE_STRATEGY_CONTRACT=PASS" in script
    assert (
        '"$release_dir/.venv/bin/python" - \\\n'
        '  "$previous_current/strategy.yaml" "$release_dir/strategy.yaml"'
    ) in script
    assert '"regular": False' in script
    assert '"regular": True' in script
    assert "previous_without_sessions != candidate_without_sessions" in script
    assert '"$previous_current/src/jd_holdings/application/database.py"' in script
    assert "DB 스키마 코드 변경은 금지" in script


def test_live_update_prepares_and_checks_release_before_stopping_service():
    script = (ROOT / "deploy_live_armed.sh").read_text(encoding="utf-8")

    install = script.index('"$remote_python" -m venv "$release_dir/.venv"')
    candidate_gate = script.index(
        '"$release_dir/.venv/bin/jdss" --config "$release_dir/strategy.yaml" live-release-check'
    )
    stop = script.index('sudo systemctl stop "$service_name"', candidate_gate)
    snapshot = script.index('source.backup(target)', stop)
    switch = script.index('mv -Tf "$target_dir/current.new" "$target_dir/current"', snapshot)
    assert install < candidate_gate < stop < snapshot < switch


def test_live_update_rollback_never_rewinds_db_after_service_start():
    script = (ROOT / "deploy_live_armed.sh").read_text(encoding="utf-8")

    preserve = script.index("restore_db_allowed=0")
    start = script.index('sudo systemctl start "$service_name"', preserve)
    assert preserve < start
    assert 'if [[ "$restore_db_allowed" == "1"' in script
    assert "실거래 DB를 과거로 되돌리지 않습니다" in script
    assert 'ln -sfn "$previous_current" "$target_dir/current.rollback"' in script
    assert 'sudo install -m 0644 "$unit_backup" "$service_unit"' in script


def test_live_update_verifies_exact_operator_menu_after_restart():
    script = (ROOT / "deploy_live_armed.sh").read_text(encoding="utf-8")

    assert "getMyCommands" in script
    assert '"dashboard", "today", "account", "portfolio"' in script
    assert '"backtest", "onboarding", "halt", "help"' in script
    assert "TELEGRAM_OPERATOR_MENU=PASS" in script


def test_live_update_workflow_is_owner_only_latest_main_and_test_gated():
    workflow = (
        ROOT / ".github/workflows/deploy-oracle-live-armed.yml"
    ).read_text(encoding="utf-8")

    assert "ref: main" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "[deploy-oracle-live-armed]" in workflow
    assert "group: oracle-jdss-live-update" in workflow
    assert "tests/test_live_runtime_update_contract.py" in workflow
    assert "tests/test_live_runtime_safety.py" in workflow
    assert "bash ./deploy_live_armed.sh" in workflow
    assert 'SKIP_LOCAL_CHECKS: "1"' in workflow
    assert "gh issue comment" in workflow
