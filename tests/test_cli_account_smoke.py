from __future__ import annotations

from decimal import Decimal

from jd_holdings import cli
from jd_holdings.infrastructure.toss_client import TossApiError


class _SafeAccountClient:
    account_seq = "1"

    def get_accounts(self):
        return [
            {
                "accountNo": "12345678901",
                "accountSeq": 1,
                "accountType": "BROKERAGE",
            }
        ]

    def get_holdings(self):
        return [{"symbol": "TQQQ", "quantity": "17"}]

    def list_orders(self, *, status):
        assert status == "OPEN"
        return [{"orderId": "PRIVATE-ORDER-ID"}]

    def get_buying_power(self, currency="USD"):
        assert currency == "USD"
        return Decimal("54321.99")


class _FailingAccountClient(_SafeAccountClient):
    def get_holdings(self):
        raise TossApiError(
            "private broker message with account detail",
            status_code=403,
            code="forbidden",
            request_id="PRIVATE-REQUEST-ID",
        )


def test_toss_account_smoke_redacts_financial_and_account_values(monkeypatch, capsys):
    monkeypatch.setattr(cli, "TossClient", lambda: _SafeAccountClient())

    assert cli._toss_account_smoke() == 0
    output = capsys.readouterr().out

    assert '"safe": true' in output
    assert '"private_financial_values_redacted": true' in output
    for private_value in (
        "12345678901",
        "TQQQ",
        "17",
        "PRIVATE-ORDER-ID",
        "54321.99",
    ):
        assert private_value not in output


def test_toss_account_smoke_reports_safe_error_fingerprint_only(monkeypatch, capsys):
    monkeypatch.setattr(cli, "TossClient", lambda: _FailingAccountClient())

    assert cli._toss_account_smoke() == 1
    output = capsys.readouterr().out

    assert '"http_status": 403' in output
    assert '"code": "forbidden"' in output
    assert "private broker message" not in output
    assert "PRIVATE-REQUEST-ID" not in output
    assert "12345678901" not in output
