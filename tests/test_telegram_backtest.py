from __future__ import annotations

import tomllib
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from conftest import make_score, make_snapshot

from jd_holdings import __version__
from jd_holdings.core.enums import DecisionType
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.telegram_bot import (
    IDLE_CASH_COMMANDS,
    BacktestCommandError,
    TelegramBotApp,
    _daily_analysis_is_due,
    _execute_buy_button_text,
    _format_idle_cash_event,
    _guide_cards,
    _is_network_error,
    _is_toss_order_maintenance_window,
    _profit_loss,
    _regime_label,
    _review_buy_button_text,
    _telegram_message_chunks,
    _won,
    parse_backtest_request,
    parse_history_request,
)
from jd_holdings.infrastructure.telegram_bot_v322 import (
    V322TelegramBotApp,
    _v322_bot_commands,
)

SYMBOLS = ("TQQQ", "SOXL")
LATEST = date(2026, 8, 4)
SEOUL_TZ = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 11, 8, 49, tzinfo=SEOUL_TZ), False),
        (datetime(2026, 8, 11, 8, 50, tzinfo=SEOUL_TZ), True),
        (datetime(2026, 8, 11, 8, 59, tzinfo=SEOUL_TZ), True),
        (datetime(2026, 8, 11, 9, 0, tzinfo=SEOUL_TZ), False),
    ],
)
def test_toss_order_maintenance_window(now, expected):
    assert _is_toss_order_maintenance_window(now) is expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 22, 6, 59, tzinfo=SEOUL_TZ), False),
        (datetime(2026, 8, 22, 7, 0, tzinfo=SEOUL_TZ), True),
        (datetime(2026, 8, 21, 22, 0, tzinfo=ZoneInfo("UTC")), True),
    ],
)
def test_daily_analysis_is_fixed_at_0700_kst(now, expected):
    scheduled = datetime.strptime("07:00", "%H:%M").time()
    assert _daily_analysis_is_due(now, scheduled) is expected


def test_history_command_defaults_to_all_symbols_and_seven_days():
    assert parse_history_request("/history", ("TQQQ", "SOXL")) == (("TQQQ", "SOXL"), 7)
    assert parse_history_request("/h 10", ("TQQQ", "SOXL")) == (("TQQQ", "SOXL"), 10)


def test_history_command_accepts_symbol_and_days():
    assert parse_history_request("/history TQQQ 14", ("TQQQ", "SOXL")) == (("TQQQ",), 14)


@pytest.mark.parametrize("command", ["/h 0", "/h 91", "/h TQQQ 7 extra", "/h NVDA 7"])
def test_history_command_rejects_invalid_input(command):
    with pytest.raises(ValueError):
        parse_history_request(command, ("TQQQ", "SOXL"))


def test_score_history_format_lists_date_score_grade_and_regime():
    message = TelegramBotApp._format_score_history(
        "TQQQ",
        [
            {
                "trade_date": date(2026, 8, 10),
                "score": 72,
                "grade": "WATCH",
                "regime": "YELLOW",
            }
        ],
        7,
    )
    assert "TQQQ 최근 7거래일" in message
    assert "2026-08-10" in message
    assert "72점" in message
    assert "WATCH" in message
    assert "YELLOW" in message


def test_backtest_command_defaults_to_full_portfolio_and_300_trading_days():
    request = parse_backtest_request("/bt", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    calendar = MarketClock().calendar
    assert len(calendar.sessions_in_range(request.start, request.end)) == 300
    assert request.end == LATEST


def test_backtest_command_accepts_full_portfolio_date_range():
    request = parse_backtest_request(
        "/backtest 2021-01-01 2024-12-31",
        SYMBOLS,
        "2011-01-01",
        LATEST,
    )
    assert request.symbols == SYMBOLS
    assert request.start == date(2021, 1, 1)
    assert request.end == date(2024, 12, 31)


@pytest.mark.parametrize("alias", ["full", "FULL", "all"])
def test_backtest_command_accepts_full_history_alias(alias):
    request = parse_backtest_request(f"/bt {alias}", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    assert request.start == date(2011, 1, 1)
    assert request.end == LATEST


def test_backtest_command_accepts_recent_trading_days():
    request = parse_backtest_request("/bt 100", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    assert request.start == date(2026, 3, 12)
    assert request.end == LATEST


def test_profit_loss_uses_after_cost_values():
    value = {
        "amount": "12.345",
        "amountAfterCost": "10.126",
        "rate": "0.01234",
        "rateAfterCost": "0.01018",
    }
    assert _profit_loss(value) == ("$10.13", "+1.02%")


def test_won_formats_with_thousands_separator():
    assert _won("1234567.4") == "₩1,234,567"


def test_dry_run_sgov_fill_alert_is_explicitly_labeled():
    event = "SGOV 유휴자금 예치 196주 (FILLED)"

    assert _format_idle_cash_event(event, "dry_run") == (
        "🧪 모의체결 — SGOV 유휴자금 예치 196주 (FILLED)"
    )
    assert _format_idle_cash_event(event, "live") == (
        "💵 SGOV 유휴자금 예치 196주 (FILLED)"
    )


def test_dry_run_sgov_non_fill_event_is_labeled_as_simulation():
    event = "SGOV SGOV_SWEEP_BUY 미체결 잔량 재가격 준비"

    assert _format_idle_cash_event(event, "dry_run").startswith("🧪 모의처리 —")


@pytest.mark.parametrize(
    ("regime", "label"),
    [
        ("GREEN", "🟢 강세장"),
        ("YELLOW", "🟡 중립장"),
        ("RED", "🔴 약세장"),
    ],
)
def test_regime_label_has_visible_color(regime, label):
    assert _regime_label(regime) == label


def test_backtest_timeline_includes_signal_buy_and_take_profit_sales():
    result = SimpleNamespace(
        signals=(
            {
                "trade_date": "2026-07-01",
                "action": "FIRST_ENTRY_CANDIDATE",
                "score": 88,
                "signal_close": 50.125,
            },
        ),
        trades=(
            {
                "date": "2026-07-02",
                "side": "BUY",
                "purpose": "FIRST_ENTRY_CANDIDATE",
                "quantity": 10,
                "price": 50.255,
            },
            {
                "date": "2026-07-10",
                "side": "SELL",
                "purpose": "TP1",
                "quantity": 5,
                "price": 53.125,
            },
            {
                "date": "2026-07-15",
                "side": "SELL",
                "purpose": "TP2",
                "quantity": 5,
                "price": 55.555,
            },
            {
                "date": "2026-07-16",
                "side": "SELL",
                "purpose": "REMAINDER_EXIT",
                "quantity": 1,
                "price": 52.0,
            },
        ),
        skipped_signals=(
            {
                "execution_date": "2026-07-03",
                "reason": "SKIPPED_BY_CHASE_RULE",
            },
        ),
    )
    timeline = TelegramBotApp._format_trade_timeline(result)
    assert "1차매수" in timeline[0]
    assert "매수미체결" in timeline[1]
    assert "1차익절" in timeline[2]
    assert "2차완청" in timeline[3]
    assert "잔여청산" in timeline[4]


def test_final_code_version_matches_strategy_release(config):
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert __version__ == "3.2.2"
    assert project["version"] == __version__
    assert config.config_version == __version__


def test_final_bot_uses_v3_portfolio_backtest_path():
    assert V322TelegramBotApp._run_backtest_and_send is TelegramBotApp._run_backtest_and_send


def test_sgov_legacy_command_parser_remains_backward_compatible():
    assert IDLE_CASH_COMMANDS == ("sgov",)


def test_telegram_guide_matches_current_contract():
    cards = _guide_cards()
    guide = "\n".join(cards)
    for expected in (
        "V3.2.2",
        "QQQ",
        "0.5 / 1.0 / 1.25 / 1.5x",
        "30%",
        "SOXX",
        "126거래일",
        "$50,000",
        "75%",
        "최대 5%",
        "2단계 승인",
        "/onboarding",
        "50% → 75% → 100%",
        "3 미국 거래일",
        "매수 주문 검토하기",
        "forced dry-run",
        "SAFE_MODE",
    ):
        assert expected in guide
    assert len(cards) == 6
    assert all(len(card) < 4096 for card in cards)
    for rejected in (
        "10개월 이동평균",
        "V3.1.1",
        "$8,000",
        "$7,200",
        "-7%",
        "+6%",
        "20거래일 경과",
        "/sgov",
    ):
        assert rejected not in guide


def test_v322_command_menu_exposes_onboarding_and_clear_buy_language():
    commands = {command.command: command.description for command in _v322_bot_commands()}

    assert commands["onboarding"] == "첫 투자 단계 확인"
    assert commands["today"] == "오늘 주문 확인"
    assert commands["portfolio"] == "목표비중·종목상세·점수"
    assert commands["backtest"] == "과거검증 결과 확인"
    assert "signal" not in commands
    assert "resume" not in commands


def test_buy_buttons_distinguish_review_from_order_submission():
    assert _review_buy_button_text("QQQ") == "🛒 QQQ 매수 주문 검토하기"
    assert _execute_buy_button_text("QQQ", 12, "dry_run") == (
        "✅ QQQ 12주 모의매수 실행"
    )
    assert _execute_buy_button_text("TQQQ", 3, "live") == (
        "✅ TQQQ 3주 실매수 실행"
    )


def test_score_message_explains_indicators_and_final_gates():
    result = SimpleNamespace(
        symbol="TQQQ",
        snapshot=make_snapshot(),
        score=make_score(55, reversal=5),
        decision=SimpleNamespace(action=DecisionType.NO_ACTION, allowed=False),
    )

    message = TelegramBotApp._format_score_message(result)

    for expected in (
        "강한 과매도",
        "단기 반등 미확인",
        "하단 접촉·이탈",
        "거래 활발",
        "전략 적정 변동성",
        "당일 고가권 마감",
        "총점 55점 이상 : ✅ 충족",
        "반등 5점 이상 : ✅ 충족",
        "RED 국면 아님 : ✅ 충족",
        "가상 신호",
        "비활성",
        "독립 매수 신호가 아닙니다",
    ):
        assert expected in message
    assert len(message) < 4096


def test_backtest_timeline_defaults_to_latest_15_events():
    result = SimpleNamespace(
        signals=(),
        skipped_signals=(),
        trades=tuple(
            {
                "date": f"2026-07-{day:02d}",
                "side": "BUY",
                "purpose": "FIRST_ENTRY_CANDIDATE",
                "quantity": 1,
                "price": 10,
                "score": 55,
                "cycle_id": str(day),
            }
            for day in range(1, 17)
        ),
    )
    timeline = TelegramBotApp._format_trade_timeline(result)
    assert len(timeline) == 16
    assert "전체 16건 중 최근 15건" in timeline[0]


@pytest.mark.parametrize("command", ["/bt NVDA 100", "/bt TQQQ 100", "/bt ALL 100"])
def test_backtest_command_rejects_ticker_overlay(command):
    with pytest.raises(BacktestCommandError, match="전체 포트폴리오 전용"):
        parse_backtest_request(command, SYMBOLS, "2011-01-01", LATEST)


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/bt VERYLONGSYMBOLNAME12345", "전체 포트폴리오 전용"),
        ("/bt 2025/01/01 2026-01-01", "날짜"),
        ("/bt 2010-12-31 2025-01-01", "시작일"),
        ("/bt 2025-01-01 2024-01-01", "늦을 수 없습니다"),
        ("/bt 2025-01-01 2026-08-05", "최신 완결 거래일"),
        ("/bt 2025-01-01 2026-08-04 extra", "형식"),
        ("/bt 0", "2~5000"),
        ("/bt 5001", "2~5000"),
        ("/bt 2026-08-04 2026-08-04", "2개 이상"),
    ],
)
def test_backtest_command_rejects_unsafe_or_invalid_input(command, message):
    with pytest.raises(BacktestCommandError, match=message):
        parse_backtest_request(command, SYMBOLS, "2011-01-01", LATEST)


def test_telegram_message_chunks_stay_below_limit_and_keep_markup_lines():
    text = "\n".join(f"<b>{index:03d}</b> " + "x" * 20 for index in range(30))

    chunks = _telegram_message_chunks(text, limit=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk, _ in chunks)
    assert all(parse_mode == "HTML" for _, parse_mode in chunks)


def test_telegram_message_chunks_strip_tags_before_hard_split():
    chunks = _telegram_message_chunks("<b>" + "x" * 250 + "</b>", limit=100)

    assert [len(chunk) for chunk, _ in chunks] == [100, 100, 50]
    assert all(parse_mode is None for _, parse_mode in chunks)


def test_telegram_authorization_requires_private_chat_and_matching_sender():
    app = TelegramBotApp.__new__(TelegramBotApp)
    app.allowed_chat_id = 123
    allowed = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=123),
    )
    group = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="group"),
        from_user=SimpleNamespace(id=123),
    )
    forwarded = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=456),
    )

    assert app._authorized_message(allowed)
    assert not app._authorized_message(group)
    assert not app._authorized_message(forwarded)


def test_wrapped_network_error_is_classified_separately_from_bad_data():
    try:
        raise ConnectionError("DNS lookup failed")
    except ConnectionError as cause:
        wrapped = ValueError("yfinance 일봉 조회 실패")
        wrapped.__cause__ = cause

    assert _is_network_error(wrapped)
    assert not _is_network_error(ValueError("OHLCV 필수 컬럼 누락"))


def test_portfolio_backtest_formatter_uses_v322_allocation_fill_key():
    result = SimpleNamespace(
        start_date=date(2025, 1, 2),
        end_date=date(2026, 8, 4),
        slippage=0.001,
        metrics={
            "initial_equity": 50000.0,
            "final_equity": 61000.0,
            "total_return_pct": 22.0,
            "cagr_pct": 13.0,
            "mdd_pct": -18.0,
            "sharpe": 1.1,
            "sortino": 1.4,
            "average_exposure_pct": 98.0,
            "final_cash": 9000.0,
            "final_holdings_value": 52000.0,
            "total_fees": 123.45,
            "net_profit": 11000.0,
            "final_holdings": {
                "QQQ": {"quantity": 50, "market_value": 35000.0},
                "TQQQ": {"quantity": 100, "market_value": 10000.0},
                "SOXL": {"quantity": 40, "market_value": 7000.0},
            },
            "initial_onboarding_minimum_sessions": 3,
            "component_fills": {"allocation": 17},
            "hwm_reinvestment_fraction": 0.75,
            "maximum_sizing_base": 58000.0,
        },
        trades=(),
    )

    message = TelegramBotApp._format_portfolio_backtest_result(result)

    assert "V3.2.2" in message
    assert "allocation 체결: <code>17회</code>" in message
    assert "50% → 75% → 100%" in message
    assert "SGOV: <code>OFF</code>" in message
    assert "남은 현금 : <code>$9,000.00</code>" in message
    assert "보유 평가액 : <code>$52,000.00</code>" in message
    assert "누적 수수료 : <code>$123.45</code>" in message
    assert "QQQ 50주 · <code>$35,000.00</code>" in message
