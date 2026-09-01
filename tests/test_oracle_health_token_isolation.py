from pathlib import Path


def test_live_health_does_not_mint_independent_toss_token():
    workflow = Path(".github/workflows/oracle-health-watch.yml").read_text(encoding="utf-8")

    live_guard = 'if [[ "${JDSS_TRADING_MODE}" == "live" ]]; then'
    skip_marker = "TOSS_SMOKE=SKIP mode=live reason=runtime-token-isolation"
    dry_run_marker = "TOSS_SMOKE=PASS mode=dry_run"
    smoke_command = '"$current/.venv/bin/jdss" --config "$current/strategy.yaml" toss-smoke'

    assert live_guard in workflow
    assert skip_marker in workflow
    assert dry_run_marker in workflow
    assert workflow.index(live_guard) < workflow.index(skip_marker)
    assert workflow.index(skip_marker) < workflow.index(smoke_command)
    assert workflow.index(smoke_command) < workflow.index(dry_run_marker)
