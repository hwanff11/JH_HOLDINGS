from pathlib import Path

from jd_holdings.infrastructure.telegram_bot import (
    OPERATOR_ERROR_LIMIT,
    _operator_error_summary,
    _redact_operator_text,
)


def test_operator_error_summary_redacts_credentials_and_infrastructure_details():
    error = RuntimeError(
        "Authorization: Bearer-secret access_token=abc123 password=hunter2 "
        "https://internal.example.test/path?key=value "
        "/home/ubuntu/JH_HOLDINGS/.env account=123456789012"
    )

    result = _operator_error_summary(error)

    assert "Bearer-secret" not in result
    assert "abc123" not in result
    assert "hunter2" not in result
    assert "internal.example.test" not in result
    assert "/home/ubuntu" not in result
    assert "123456789012" not in result
    assert "[보호됨]" in result
    assert "[외부주소 생략]" in result
    assert "[서버경로 생략]" in result
    assert "[식별번호 생략]" in result


def test_operator_error_summary_preserves_actionable_text_and_bounds_length():
    actionable = "가격 또는 수량이 바뀌어 최신 조건으로 다시 확인해 주세요."
    assert _operator_error_summary(RuntimeError(actionable)) == actionable

    result = _operator_error_summary(RuntimeError("x" * (OPERATOR_ERROR_LIMIT + 50)))
    assert len(result) == OPERATOR_ERROR_LIMIT
    assert result.endswith("…")


def test_operator_error_summary_handles_empty_and_multiline_messages():
    assert _operator_error_summary(RuntimeError()) == "RuntimeError"
    assert _redact_operator_text("첫 줄\n  둘째 줄") == "첫 줄 둘째 줄"


def test_telegram_exception_paths_do_not_render_raw_exception_text():
    root = Path(__file__).resolve().parents[1]
    infrastructure = root / "src" / "jd_holdings" / "infrastructure"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in infrastructure.glob("*telegram*.py")
    )

    assert "html.escape(str(exc))" not in source
    assert "answer_callback_query(call.id, str(exc)" not in source
