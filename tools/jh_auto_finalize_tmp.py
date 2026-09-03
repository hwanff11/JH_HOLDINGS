from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def insert_after(path: str, marker: str, block: str, sentinel: str) -> None:
    text = read(path)
    if sentinel in text:
        return
    if marker not in text:
        raise SystemExit(f"missing insert marker in {path}: {marker!r}")
    write(path, text.replace(marker, marker + block, 1))


def append_once(path: str, block: str, sentinel: str) -> None:
    text = read(path)
    if sentinel in text:
        return
    write(path, text.rstrip() + "\n\n" + block.strip() + "\n")


# ---------------------------------------------------------------------------
# Final BUY boundary and capital-accounting hardening.
# ---------------------------------------------------------------------------
path = "src/jd_holdings/application/managed_account.py"
text = read(path)
if 'AUTO_STATE_KEY = "jh_auto_state"' not in text:
    text = text.replace(
        'AUTO_OPERATOR_HALT_LATCH_KEY = "jh_auto_operator_halt_latched"\n',
        'AUTO_OPERATOR_HALT_LATCH_KEY = "jh_auto_operator_halt_latched"\n'
        'AUTO_STATE_KEY = "jh_auto_state"\n',
        1,
    )
old_gate = '''        if _auto_enabled(connection):
            if _system_text(connection, AUTO_LAUNCH_AUTHORIZED_KEY) != "1":
                raise RuntimeError("JH AUTO 최초 시작승인 전에는 실제 매수를 할 수 없습니다")
            if _system_text(connection, AUTO_QUARANTINE_KEY) != "0":
                raise RuntimeError("JH AUTO 임시격리 상태라 실제 매수가 차단되어 있습니다")
            if _system_text(connection, AUTO_OPERATOR_HALT_LATCH_KEY) == "1":
                raise RuntimeError("대표 긴급정지 상태라 JH AUTO 실제 매수가 차단되어 있습니다")
'''
new_gate = '''        if _auto_enabled(connection):
            if _system_text(connection, AUTO_LAUNCH_AUTHORIZED_KEY) != "1":
                raise RuntimeError("JH AUTO 최초 시작승인 전에는 실제 매수를 할 수 없습니다")
            if _system_text(connection, AUTO_QUARANTINE_KEY) != "0":
                raise RuntimeError("JH AUTO 임시격리 상태라 실제 매수가 차단되어 있습니다")
            if _system_text(connection, AUTO_OPERATOR_HALT_LATCH_KEY) != "0":
                raise RuntimeError(
                    "JH AUTO 대표 긴급정지 상태가 ON이거나 누락·손상되어 실제 매수가 차단되어 있습니다"
                )
            if _system_text(connection, AUTO_STATE_KEY) != "RUNNING":
                raise RuntimeError("JH AUTO 실행상태가 RUNNING이 아니라 실제 매수가 차단되어 있습니다")
            effective = _system_text(connection, AUTO_EFFECTIVE_PRINCIPAL_KEY)
            try:
                effective_principal = Decimal(str(effective))
            except Exception as exc:
                raise RuntimeError("JH AUTO 현재 허용원금 상태가 누락·손상되었습니다") from exc
            if not effective_principal.is_finite() or effective_principal <= 0:
                raise RuntimeError("JH AUTO 현재 허용원금이 0 이하라 실제 매수가 차단되어 있습니다")
'''
if old_gate in text:
    text = text.replace(old_gate, new_gate, 1)
elif new_gate not in text:
    raise SystemExit("managed_account AUTO final gate marker changed unexpectedly")
text = text.replace(
    '"V3.2.2/JH AUTO 목표수량을 초과하는 코어 매수를 차단했습니다 "',
    '"V3.2.2 목표수량을 초과하는 코어 매수를 JH AUTO 경계에서 차단했습니다 "',
)
text = text.replace(
    '"JDSS HWM75/JH AUTO 위험예산이 부족하여 매수 주문을 차단했습니다 "',
    '"JDSS HWM75 위험예산 또는 JH AUTO 위임한도가 부족하여 매수 주문을 차단했습니다 "',
)
old_equity = '''    return managed_cash_balance(config, repository) + managed_market_value(
        config, repository, broker
    )
'''
new_equity = '''    # Logical AUTO cash may become temporarily negative after the operator
    # reduces delegated principal while risk-reducing SELLs are still pending. That
    # negative amount is a capital-withdrawal liability, not spendable cash. Keep it
    # in marked equity so a capital reduction cannot be mistaken for investment
    # profit, while managed_cash_balance() remains clamped for BUY availability.
    raw_cash = raw_managed_cash_balance(config, repository)
    market_value = managed_market_value(config, repository, broker)
    return max(Decimal("0"), raw_cash + market_value)
'''
if old_equity in text:
    text = text.replace(old_equity, new_equity, 1)
elif new_equity not in text:
    raise SystemExit("managed_account marked equity marker changed unexpectedly")
write(path, text)


# UNKNOWN receipt itself must stop further autonomous BUYs.
path = "src/jd_holdings/automation/service.py"
text = read(path)
marker = '''        status = str(receipt.status).upper()
        if status in {"CANCELED", "REJECTED", "REPLACED"} and receipt.filled_quantity < receipt.quantity:
'''
replacement = '''        status = str(receipt.status).upper()
        if status == "UNKNOWN":
            self.quarantine("AUTO_ORDER_UNKNOWN")
        if status in {"CANCELED", "REJECTED", "REPLACED"} and receipt.filled_quantity < receipt.quantity:
'''
if marker in text:
    text = text.replace(marker, replacement, 1)
elif replacement not in text:
    raise SystemExit("AUTO UNKNOWN hardening marker changed unexpectedly")
write(path, text)


# Telegram should show logical negative cash as pending capital reduction.
path = "src/jd_holdings/infrastructure/jh_auto_telegram.py"
text = read(path)
old_perf = '''        cash = Decimal(str(snapshot.get("cash", 0)))
        invested = Decimal(str(snapshot.get("invested_market_value", equity - cash)))
'''
new_perf = '''        raw_cash = Decimal(str(snapshot.get("cash", 0)))
        cash = max(Decimal("0"), raw_cash)
        pending_reduction = max(Decimal("0"), -raw_cash)
        invested = Decimal(str(snapshot.get("invested_market_value", equity - raw_cash)))
'''
if old_perf in text:
    text = text.replace(old_perf, new_perf, 1)
elif new_perf not in text:
    raise SystemExit("JH AUTO Telegram performance marker changed unexpectedly")
old_return = '''            "cash": cash,
            "invested": invested,
            "profit": equity - settings.effective_principal if settings.launch_authorized else Decimal("0"),
'''
new_return = '''            "cash": cash,
            "pending_reduction": pending_reduction,
            "invested": invested,
            "profit": equity - settings.effective_principal if settings.launch_authorized else Decimal("0"),
'''
if old_return in text:
    text = text.replace(old_return, new_return, 1)
elif new_return not in text:
    raise SystemExit("JH AUTO Telegram return marker changed unexpectedly")
dashboard_line = '''                f"• 투자중 : <code>{_money(perf['invested'])}</code> · 현금 <code>{_money(perf['cash'])}</code>",
'''
if text.count("자금축소 회수대기") < 1:
    if dashboard_line not in text:
        raise SystemExit("dashboard cash line not found")
    text = text.replace(
        dashboard_line,
        dashboard_line
        + '''                f"• 자금축소 회수대기 : <code>{_money(perf['pending_reduction'])}</code>",
''',
        1,
    )
portfolio_line = '''                f"• 현재 허용원금 : <code>{_money(settings.effective_principal)}</code>",
'''
if text.count("자금축소 회수대기") < 2:
    if portfolio_line not in text:
        raise SystemExit("portfolio principal line not found")
    text = text.replace(
        portfolio_line,
        portfolio_line
        + '''                f"• 자금축소 회수대기 : <code>{_money(perf['pending_reduction'])}</code>",
''',
        1,
    )
write(path, text)


# ---------------------------------------------------------------------------
# Live entrypoint tests follow the new production AUTO components.
# ---------------------------------------------------------------------------
path = "tests/test_live_entrypoints.py"
text = read(path)
helper_marker = '''def _config(*, portfolio_enabled: bool = True):
    return SimpleNamespace(portfolio=SimpleNamespace(enabled=portfolio_enabled))
'''
helper_block = '''

class _AutoServiceStub:
    @classmethod
    def bootstrap_repository(cls, _repository):
        return None

    def __init__(self, *_args, **_kwargs):
        pass

    def arm_startup_quarantine(self):
        return None
'''
if "class _AutoServiceStub:" not in text:
    if helper_marker not in text:
        raise SystemExit("live entrypoint helper marker missing")
    text = text.replace(helper_marker, helper_marker + helper_block, 1)
text = text.replace('"LiveAllocationTradingService"', '"JHAutoLiveAllocationTradingService"')
text = text.replace('"HardenedOperationalSafetyTelegramBotApp"', '"JHAutoTelegramBotApp"')
text = text.replace("lambda *_args: app,", "lambda *_args, **_kwargs: app,")
repo_mock = '    monkeypatch.setattr(live_bot, "SQLiteRepository", lambda *_args: repository)\n'
auto_mock = repo_mock + '    monkeypatch.setattr(live_bot, "ProductionJHAutoService", _AutoServiceStub)\n'
if text.count('"ProductionJHAutoService", _AutoServiceStub') < 2:
    text = text.replace(repo_mock, auto_mock)
write(path, text)


# ---------------------------------------------------------------------------
# Regression tests for autonomous safety/accounting and Telegram UX.
# ---------------------------------------------------------------------------
path = "tests/test_jh_auto.py"
text = read(path)
old_import = '''from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    reserve_buy_order_with_managed_cash,
)
'''
new_import = '''from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    managed_cash_balance,
    marked_managed_equity,
    raw_managed_cash_balance,
    reserve_buy_order_with_managed_cash,
)
'''
if old_import in text:
    text = text.replace(old_import, new_import, 1)
if "from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp" not in text:
    text = text.replace(
        "from jd_holdings.core.models import OrderReceipt\n",
        "from jd_holdings.core.models import OrderReceipt\n"
        "from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp\n",
        1,
    )
extra_tests = r'''


def test_auto_final_buy_gate_fails_closed_when_halt_latch_is_missing(tmp_path, config):
    repository, broker, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    assert service.try_release_quarantine(safety_ready=True)
    with repository.transaction() as connection:
        connection.execute(
            "DELETE FROM system_state WHERE key = ?",
            (AUTO_OPERATOR_HALT_LATCH_KEY,),
        )

    with pytest.raises(RuntimeError, match="누락·손상"):
        reserve_buy_order_with_managed_cash(
            config,
            repository,
            broker,
            client_order_id="AUTO-MISSING-LATCH",
            signal_id=None,
            cycle_id=None,
            symbol="QQQ",
            order_type="LIMIT",
            price=Decimal("500"),
            quantity=1,
            purpose="CORE_REBALANCE_BUY",
        )


def test_capital_reduction_keeps_withdrawal_liability_in_marked_equity(tmp_path, config):
    repository, broker, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    service._apply_external_flow(
        Decimal("10000"),
        event_type="TEST_COMPLETE_INITIAL_RAMP",
        note="test",
    )
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("1"),
        signal_trade_date=date(2026, 9, 2),
        target_qty=20,
    )
    assert repository.reserve_order(
        client_order_id="AUTO-CAPITAL-REDUCTION-BUY",
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("500"),
        quantity=20,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        "AUTO-CAPITAL-REDUCTION-BUY",
        status="FILLED",
        broker_order_id="DRY-AUTO-CAPITAL-REDUCTION-BUY",
        filled_qty=20,
        average_fill_price=Decimal("500"),
    )
    repository.apply_core_fill("AUTO-CAPITAL-REDUCTION-BUY")
    assert marked_managed_equity(config, repository, broker) == Decimal("10000")

    reduced = service.set_ratio_percent("10")

    assert reduced.effective_principal == Decimal("5000.00")
    assert raw_managed_cash_balance(config, repository) == Decimal("-5000.000")
    assert managed_cash_balance(config, repository) == Decimal("0")
    assert marked_managed_equity(config, repository, broker) == Decimal("5000.000")


class _UnknownTradingService(_FakeTradingService):
    def execute(self, approval_id, token, *, now=None):
        signal_id = approval_id - 200
        self.executed.append(signal_id)
        return OrderReceipt(
            client_order_id=f"AUTO-{signal_id}",
            broker_order_id=f"BROKER-{signal_id}",
            status="UNKNOWN",
            quantity=1,
            filled_quantity=0,
            average_fill_price=None,
        )


def test_auto_unknown_receipt_immediately_enters_quarantine(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config, clock=_Clock("regular"))
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_OPERATOR_HALT_LATCH_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value("operator_buy_halt", "0")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")

    result = service.execute_one(
        _UnknownTradingService(),
        now=datetime(2026, 9, 3, 15, 0, tzinfo=UTC),
    )

    assert result is not None and result.status == "UNKNOWN"
    assert repository.get_system_value(AUTO_QUARANTINE_KEY) == "1"
    assert repository.get_system_value(AUTO_STATE_KEY) == "AUTO_QUARANTINE"
    assert repository.get_system_value("operator_buy_halt") == "1"
    assert repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "0"


def test_auto_telegram_views_and_launch_confirmation_are_order_free(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")

    rows = []
    for symbol, price in (("QQQ", "500"), ("TQQQ", "100"), ("SOXL", "50")):
        rows.append(
            {
                "symbol": symbol,
                "core_quantity": 0,
                "core_market_value": Decimal("0"),
                "price": Decimal(price),
                "cost_basis": Decimal("0"),
                "unrealized_profit": Decimal("0"),
                "target_weight": Decimal("0"),
            }
        )
    snapshot = {
        "equity": Decimal("0"),
        "cash": Decimal("0"),
        "invested_market_value": Decimal("0"),
        "high_water": Decimal("0"),
        "risk_budget": Decimal("0"),
        "rows": rows,
    }
    app = object.__new__(JHAutoTelegramBotApp)
    app.auto_service = service
    app.repository = repository
    app.portfolio_service = SimpleNamespace(snapshot=lambda: snapshot)
    app.market_clock = _Clock("regular")
    app.reconciliation_service = _CleanReconciliation()
    app._auto_pending = {}
    app._portfolio_safe_mode = lambda: False

    dashboard = app._format_auto_dashboard()
    portfolio = app._format_portfolio_message()
    today, markup = app._prepare_today_buy_batch()
    control, control_markup = app._format_auto_control()
    start_review, start_markup = app._review_change("start", "1")

    assert "JDSS 3.2.2" in dashboard and "JH AUTO 1.0.0" in dashboard
    assert "누적 운용수익률" in dashboard
    assert "QQQ" in portfolio and "보유수익률" in portfolio
    assert "최초 시작승인 전" in today and markup is None
    assert "운용 기준자금" in control and control_markup.keyboard
    assert "이 확인 버튼 자체는 주문을 보내지 않습니다" in start_review
    assert start_markup.keyboard
    assert repository.open_orders() == []

    token = next(reversed(app._auto_pending))
    result = app._confirm_pending(token)

    assert "이 버튼에서는 0건" in result
    assert repository.open_orders() == []
    assert service.settings().launch_authorized
    assert service.settings().quarantine

    app.notify_portfolio_buy_batch_ready((101, 102))
    events = repository.recent_events(limit=5)
    assert any(event["event_type"] == "JH_AUTO_SIGNAL_READY" for event in events)
'''
if "test_auto_final_buy_gate_fails_closed_when_halt_latch_is_missing" not in text:
    text = text.rstrip() + extra_tests + "\n"
write(path, text)


# ---------------------------------------------------------------------------
# Markdown synchronization and durable document ownership.
# ---------------------------------------------------------------------------
append_once(
    "docs/README.md",
    """## 현행 문서 갱신 원칙

현행 사양과 운영 가이드는 새 버전마다 복사본을 만들지 않고 **제자리 갱신**합니다. 현행 파일명은 고정하고, 과거 상태는 Git tag·병합 PR·`HISTORY.md`에서 복구합니다. 전략과 자동운용은 문서를 분리하되 같은 기능 변경이 양쪽에 영향을 주면 한 PR에서 함께 동기화합니다.""",
    "## 현행 문서 갱신 원칙",
)
append_once(
    "docs/JDSS_FINAL_SPEC.md",
    """## JH AUTO 도입 후 최초진입 호환계약

JDSS V3.2.2의 기존 실거래 도입 원칙인 **최초진입 50% → 75% → 100%**는 새 위험을 한 번에 열지 않는 전략 안전원칙으로 유지합니다. JH AUTO 1.0.0에서는 이 실행 책임을 `/onboarding` 수동 버튼이 아니라 `JH_AUTO_SPEC.md`의 자동운용 자금단계가 소유합니다.

- 50% → 75% → 100% 비율과 최소 3 미국 거래세션 대기는 유지
- 전략 목표가 낮아지면 단계보다 위험축소 SELL이 우선
- JH AUTO 정상운영에서는 `/auto`가 자금단계를 표시·관리
- 기존 `/onboarding` 호환 화면의 **stale 버튼**은 현재 DB 단계와 다르면 계속 거부
- 최초 시작 확인 callback 자체에서는 주문을 만들지 않음

즉 전략의 단계투입 원칙은 JDSS에 남고, 실제 자동 개방·회계·주문 권한은 JH AUTO에 있습니다.""",
    "## JH AUTO 도입 후 최초진입 호환계약",
)
append_once(
    "docs/JH_AUTO_SPEC.md",
    """## 자금축소 중 회계상 회수대기

자동운용 원금을 줄였지만 기존 보유분의 위험축소 매도가 아직 끝나지 않았다면, 내부 자동운용 현금은 일시적으로 음수의 **회수대기 부채**가 될 수 있습니다. 이는 Toss 실제 계좌의 마이너스 현금을 뜻하지 않습니다.

성과 평가액은 `회수대기 부채 + 관리 포지션 평가액`으로 계산해 원금 축소를 투자수익으로 오인하지 않습니다. BUY 가능현금은 계속 0 이상으로 제한하며, Telegram에는 음수 현금 대신 `자금축소 회수대기` 금액을 별도로 표시합니다. 회수대기 상태에서는 새 위험증가 BUY를 열지 않고 목표 위험축소를 우선합니다.""",
    "## 자금축소 중 회계상 회수대기",
)

insert_after(
    "AGENTS.md",
    "# AGENTS.md\n",
    """

## JDSS 전략과 JH AUTO 실행계층

- **JDSS 3.2.2**는 시장판단·목표비중·RS6M·HWM75 전략 수학을 소유합니다.
- **JH AUTO 1.0.0**은 운용 기준자금·자동운용비율·성과회계·자동승인·자동주문·최초 시작·긴급정지를 소유합니다.
- 전략 변경은 `strategy.yaml` + `docs/JDSS_FINAL_SPEC.md`를, 자동운용 변경은 `docs/JH_AUTO_SPEC.md`를 반드시 기준으로 확인합니다.
- JH AUTO가 설치돼도 `launch_authorized=1`이 운영자 확인으로 기록되기 전 실제 BUY는 0건이어야 합니다.
- 배포·재시작·정상화 작업은 최초 시작승인으로 해석하지 않습니다.
- 운영자 `/halt`의 durable latch는 시스템이 자동해제할 수 없습니다.
- 자동 BUY는 기존 `TradingService → OrderManager` 최종 안전경계를 우회해서 구현하지 않습니다.
""",
    "## JDSS 전략과 JH AUTO 실행계층",
)
insert_after(
    "README.md",
    "# JH_HOLDINGS\n",
    """

## 두 개의 버전을 따로 관리합니다

- **투자전략 `JDSS 3.2.2`**: 무엇을 얼마나 보유할지 결정합니다.
- **자동매매 `JH AUTO 1.0.0`**: 대표가 위임한 자금 범위에서 그 목표를 어떤 안전조건으로 실제 실행할지 관리합니다.

자동매매 상세는 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), 전략 상세는 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다. 현재 실제 배포·시작승인 상태는 오직 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서 확인합니다.
""",
    "## 두 개의 버전을 따로 관리합니다",
)
insert_after(
    "SECURITY.md",
    "# Security Policy\n",
    """

## JH AUTO 안전 진입점

자동매매 실행계층의 상세 불변식은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)와 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md)를 따릅니다. 특히 최초 시작승인 전 BUY 0건, 운영자 `/halt` 자동해제 금지, UNKNOWN 주문 재전송 금지는 배포·편의 기능보다 우선합니다.
""",
    "## JH AUTO 안전 진입점",
)
insert_after(
    "docs/infra/DEVELOPMENT_WORKFLOW.md",
    "## 목적\n",
    """

### 전략 버전과 자동매매 버전 분리

JH_HOLDINGS는 `JDSS 3.2.2`와 `JH AUTO 1.0.0`을 별도 버전으로 관리합니다. 전략 수학 변경이 없으면 자동매매 기능 개발만으로 JDSS 버전을 올리지 않습니다. 반대로 전략 변경만으로 JH AUTO 버전을 올리지 않습니다.

자동운용 변경 PR은 최소한 `JH_AUTO_SPEC`, Telegram, `infra/SECURITY`, `LIVE_COMMISSIONING`, 관련 테스트를 함께 검토합니다. **배포 성공은 자동운용 시작승인이 아니며**, 최초 JH AUTO 배포 후 실제 BUY는 운영자의 Telegram 시작확인 전까지 계속 차단되어야 합니다.
""",
    "### 전략 버전과 자동매매 버전 분리",
)
insert_after(
    "docs/infra/SECURITY.md",
    "#",
    """

## JH AUTO 1.0.0 자동매수 불변식

JH AUTO의 위험증가 BUY는 최종 주문예약 경계에서 다음 상태를 모두 **정확한 정상값**으로 다시 확인합니다.

- 최초 시작승인 `ON`
- 시스템 임시격리 `OFF`
- 대표 긴급정지 latch `OFF` — 누락·손상도 차단
- 자동운용 상태 `RUNNING`
- 현재 허용원금 `> 0`
- 기존 `operator_buy_halt=0`
- SAFE_MODE 없음
- HWM75/현재 허용원금/미체결 예약/브로커 매수가능금액 범위 안

Telegram 시작확인 callback 자체는 주문을 제출하지 않습니다. 첫 BUY는 다음 독립 안전주기에서만 가능합니다. 자동 BUY는 미국 정규장·한 안전주기 최대 한 종목으로 시작하며, 주문 쓰기 결과가 UNKNOWN이면 즉시 신규 BUY를 격리하고 같은 주문을 자동 재전송하지 않습니다.

운영자 `/halt`는 시스템 임시격리와 별도의 durable latch를 남깁니다. 시스템은 계좌가 다시 정상으로 보여도 이 latch를 자동해제하지 않습니다.
""",
    "## JH AUTO 1.0.0 자동매수 불변식",
)
insert_after(
    "docs/infra/DEPLOYMENT.md",
    "#",
    """

## JH AUTO 배포와 시작은 별개입니다

JH AUTO 코드가 포함된 LIVE-ARMED 배포에서도 기존 배포 전 BUY halt를 먼저 ON으로 만들고 `/resume`을 자동 실행하지 않습니다. 신규 AUTO 상태는 fail-closed로 초기화되며 최초 시작승인·현재 허용원금은 운영자 동의 없이 열리지 않습니다.

배포 후 추가 확인:

1. JDSS 전략버전과 JH AUTO 실행버전을 각각 확인
2. `launch_authorized=0`이면 현재 허용원금 0·신규 BUY 차단 확인
3. 시스템 임시격리와 대표 긴급정지 latch를 구분해 표시
4. Telegram `/auto`, `/dashboard`, `/today`, `/portfolio` 조회 확인
5. 계좌·원장 대조와 Toss 조회전용 상태 확인

**배포 완료를 이유로 `/auto start`를 대신 수행하지 않습니다.** 최초 시작은 운영자가 Telegram의 2단계 확인으로 별도 승인합니다.
""",
    "## JH AUTO 배포와 시작은 별개입니다",
)
insert_after(
    "docs/infra/LIVE_COMMISSIONING.md",
    "#",
    """

## JH AUTO 최초 활성화 단계

기존 LIVE commissioning은 실계좌 연결 자격을 증명하고, JH AUTO 시작승인은 자동 위험증가 BUY 권한을 별도로 여는 절차입니다. 둘은 같은 사건이 아닙니다.

```text
LIVE commissioned
→ JH AUTO 코드 배포
→ BUY halt / startup quarantine
→ 기준자금 설정
→ 자동운용비율 설정
→ 운영자 최초 시작 2단계 확인
→ 시작 callback에서는 주문 0건
→ 다음 독립 안전주기 재대조
→ 미국 정규장 + 모든 안전조건 통과 시 첫 AUTO BUY 가능
```

최초 활성화 전 기존 관리 포지션·미체결·계좌 불일치가 있으면 시작을 거부합니다. 배포나 서버 재시작은 기존 운영자 시작승인을 새로 만들지 않으며, 운영자 `/halt` latch는 자동 복구 대상이 아닙니다.
""",
    "## JH AUTO 최초 활성화 단계",
)
insert_after(
    "docs/ONE_PAGE_REPORT.md",
    "#",
    """

> **버전 구분:** 이 문서는 투자전략 `JDSS 3.2.2`의 요약입니다. 실제 기준자금·자동운용비율·자동주문·최초 시작·수익률 회계는 별도 실행계층 `JH AUTO 1.0.0`과 [`JH_AUTO_SPEC.md`](JH_AUTO_SPEC.md)가 담당합니다.
""",
    "**버전 구분:** 이 문서는 투자전략",
)
insert_after(
    "docs/STRATEGY_GUIDE.md",
    "#",
    """

> **전략/실행 분리:** 아래 시장판단과 목표비중은 `JDSS 3.2.2`입니다. JH AUTO 1.0.0이 활성화된 실거래에서는 기존 ‘주문 묶음 검토·최종 승인’의 사람 클릭을 자동 안전정책이 대신하되, 동일한 `TradingService → OrderManager` 검증경계를 그대로 통과합니다. 자동운용 상세는 [`JH_AUTO_SPEC.md`](JH_AUTO_SPEC.md)를 봅니다.
""",
    "**전략/실행 분리:** 아래 시장판단",
)
append_once(
    "docs/HISTORY.md",
    """## 2026-09-03 — JH AUTO 1.0.0 자동운용 계층 채택

- JDSS 3.2.2 전략 수학은 유지하고 자동매매 실행계층 버전을 `JH AUTO 1.0.0`으로 분리했습니다.
- 운용 기준자금과 자동운용비율을 Telegram에서 운영자가 정하며, 위험증가는 추가 원금 기준 50→75→100 단계로 엽니다.
- 전체 성과는 자금 유입·회수 영향을 분리하는 단위가치 방식으로 계산합니다.
- 최초 시작승인 전 실제 BUY 0건, 시작 callback 자체 주문 0건, 미국 정규장·한 안전주기 최대 한 BUY를 초기 안전계약으로 채택했습니다.
- 운영자 `/halt`는 자동해제 불가 durable latch, 시스템 재시작은 별도 임시격리로 구분합니다.
- 실제 배포·최초 시작 여부는 이 역사 문서가 아니라 `CURRENT_WORK.md`의 현재 상태를 따릅니다.""",
    "## 2026-09-03 — JH AUTO 1.0.0 자동운용 계층 채택",
)

# Future documentation tests must include the AUTO spec as a first-class source.
path = "tests/test_documentation_contract.py"
text = read(path)
if '"JH_AUTO_SPEC.md"' not in text:
    text = text.replace(
        '        "JDSS_FINAL_SPEC.md",\n        "TELEGRAM_BOT_GUIDE.md",\n',
        '        "JDSS_FINAL_SPEC.md",\n        "JH_AUTO_SPEC.md",\n        "TELEGRAM_BOT_GUIDE.md",\n',
        2,
    )
write(path, text)

print("JH AUTO finalization patch applied")
