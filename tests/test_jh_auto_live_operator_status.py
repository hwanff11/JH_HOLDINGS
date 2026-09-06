from types import SimpleNamespace

from jd_holdings.infrastructure.jh_auto_live_display import LiveJHAutoTelegramBotApp


class _Thread:
    def __init__(self, alive: bool):
        self.alive = alive

    def is_alive(self):
        return self.alive


def test_scheduler_status_reports_starting_healthy_and_dead():
    app = object.__new__(LiveJHAutoTelegramBotApp)
    state = {"last_ok": None}
    app.repository = SimpleNamespace(
        get_system_value=lambda key: state["last_ok"]
    )

    app._scheduler_thread = None
    assert app._scheduler_status_text() == "준비 중"

    app._scheduler_thread = _Thread(True)
    assert app._scheduler_status_text() == "기동 후 안전점검 대기"

    state["last_ok"] = "2026-09-06T10:00:00+00:00"
    assert app._scheduler_status_text() == "정상"

    app._scheduler_thread = _Thread(False)
    assert app._scheduler_status_text() == "중단 · 신규매수 차단 필요"


def test_operator_action_prioritizes_setup_start_halt_and_regular_running():
    app = object.__new__(LiveJHAutoTelegramBotApp)
    state = {
        "settings": SimpleNamespace(
            configured=False,
            launch_authorized=False,
            operator_halt_latched=False,
            quarantine=True,
        )
    }
    app.auto_service = SimpleNamespace(settings=lambda: state["settings"])
    app._portfolio_safe_mode = lambda: False
    app.repository = SimpleNamespace(open_orders=lambda: [])
    app.market_clock = SimpleNamespace(classify_session=lambda: "regular")

    assert "/auto" in app._operator_action()

    state["settings"] = SimpleNamespace(
        configured=True,
        launch_authorized=False,
        operator_halt_latched=False,
        quarantine=True,
    )
    assert "/auto start" in app._operator_action()

    state["settings"] = SimpleNamespace(
        configured=True,
        launch_authorized=True,
        operator_halt_latched=True,
        quarantine=False,
    )
    assert "/resume" in app._operator_action()

    state["settings"] = SimpleNamespace(
        configured=True,
        launch_authorized=True,
        operator_halt_latched=False,
        quarantine=False,
    )
    assert "자동운용 중" in app._operator_action()
