from __future__ import annotations

from jd_holdings.infrastructure.live_runtime_resilience import ResilientReadTossClient
from jd_holdings.infrastructure.toss_client import TossApiError, TossClient


def test_token_revoked_read_is_bounded_retryable(monkeypatch):
    monkeypatch.delenv("TOSS_SHARED_TOKEN_CACHE", raising=False)
    client = ResilientReadTossClient(client_id="key", client_secret="secret")
    calls = 0

    def fake_get_holdings(self, symbol=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TossApiError(
                "새 토큰으로 대체되어 폐기된 토큰입니다",
                status_code=401,
                code="token-revoked",
                retryable=False,
            )
        return []

    monkeypatch.setattr(TossClient, "get_holdings", fake_get_holdings)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_resilience.time.sleep",
        lambda _seconds: None,
    )

    assert client.get_holdings() == []
    assert calls == 2


def test_shared_token_cache_converges_two_local_process_clients(monkeypatch, tmp_path):
    cache = tmp_path / "toss-auth" / "token.json"
    monkeypatch.setenv("TOSS_SHARED_TOKEN_CACHE", str(cache))
    issued: list[str] = []

    def fake_base_authenticate(self, *, force=False):
        if self._access_token and not force:
            return self._access_token
        token = f"token-{len(issued) + 1}"
        issued.append(token)
        self._access_token = token
        return token

    monkeypatch.setattr(TossClient, "authenticate", fake_base_authenticate)
    first = ResilientReadTossClient(client_id="same-key", client_secret="same-secret")
    second = ResilientReadTossClient(client_id="same-key", client_secret="same-secret")

    assert first.authenticate() == "token-1"
    assert second.authenticate() == "token-1"
    assert issued == ["token-1"]

    # The second process sees token-1 rejected and becomes the one issuer while
    # holding the cross-process lock.
    assert second.authenticate(force=True) == "token-2"
    assert issued == ["token-1", "token-2"]

    # The first process still has token-1 in memory.  Its force refresh must adopt
    # token-2 from the shared cache instead of issuing token-3 and revoking token-2.
    assert first.authenticate(force=True) == "token-2"
    assert issued == ["token-1", "token-2"]
    assert cache.exists()


def test_shared_token_cache_is_namespaced_by_credentials(monkeypatch, tmp_path):
    cache = tmp_path / "toss-auth" / "token.json"
    monkeypatch.setenv("TOSS_SHARED_TOKEN_CACHE", str(cache))
    issued: list[str] = []

    def fake_base_authenticate(self, *, force=False):
        if self._access_token and not force:
            return self._access_token
        token = f"token-{self.client_id}-{len(issued) + 1}"
        issued.append(token)
        self._access_token = token
        return token

    monkeypatch.setattr(TossClient, "authenticate", fake_base_authenticate)
    first = ResilientReadTossClient(client_id="app-a", client_secret="secret-a")
    second = ResilientReadTossClient(client_id="app-b", client_secret="secret-b")

    assert first.authenticate().startswith("token-app-a-")
    assert second.authenticate().startswith("token-app-b-")
    assert len(issued) == 2
